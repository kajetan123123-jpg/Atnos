"""Prepare and execute the same four supplied-script orders without duplicate jobs."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import psutil

from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production.order_dispatch import order_status
from atnos.production.order_intake import ScriptIntake, intake_blockers
from atnos.production.order_store import OrderConflict, ProductionOrderStore, _same_process, file_hash
from atnos.production.project_profile import ProductionOrder


BATCH_ID = "finance-four-ef879c99-20260906"
TARGETS = (60, 50, 75, 55)
TOPICS = (
    "What 100 PLN a month could become: compound growth",
    "When savings lose purchasing power: inflation",
    "Poland's IKE and IKZE: different tax rules",
    "Start small: a practical 500 PLN monthly DCA plan",
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def split_scenarios(text: str) -> list[str]:
    parts = re.split(r"(?m)(?=^Temat:)", text.lstrip("\ufeff"))
    parts = [part.strip() for part in parts if part.strip()]
    if len(parts) != 4 or any(not part.startswith("Temat:") for part in parts):
        raise ValueError("Expected exactly four scenario sections beginning with Temat:")
    for index, (part, duration) in enumerate(zip(parts, TARGETS, strict=True), start=1):
        if not re.search(rf"~\s*{duration}\s*sek\.", part.splitlines()[0]):
            raise ValueError(f"Scenario {index} does not declare its expected {duration}-second duration")
    return parts


def prepare_batch(settings, attachment: Path, factcheck_path: Path) -> dict:
    raw, fact_bytes = attachment.read_bytes(), factcheck_path.read_bytes()
    parts = split_scenarios(raw.decode("utf-8-sig"))
    facts = json.loads(fact_bytes.decode("utf-8-sig"))
    if facts.get("language") != "en" or not facts.get("verified_external_facts") or not facts.get("calculations"):
        raise ValueError("Expected the reviewed English brief with calculations and external facts")
    source_hash = hashlib.sha256(raw).hexdigest()
    facts_hash = hashlib.sha256(fact_bytes).hexdigest()
    source_urls = sorted({fact["source"] for fact in facts["verified_external_facts"]})
    orders = [ProductionOrder(
        topic=topic,
        direction="English Wealth Blueprint narration and captions. Preserve Polish jurisdiction and PLN. "
                  "Translate and fact-correct the supplied scenario, not a generic forty-second replacement.",
        optional_notes="Local results only. No email or upload by the production worker. "
                       "No guarantee of returns and no promises of nonexistent follow-up films.",
        series_key=BATCH_ID,
        intake=ScriptIntake(
            source_attachment_sha256=source_hash, factcheck_sha256=facts_hash, scenario_index=index,
            requested_language="en", target_duration_seconds=duration, original_scenario=part,
            corrected_brief=json.dumps(facts, ensure_ascii=False), fact_source_urls=source_urls,
        ),
    ) for index, (topic, duration, part) in enumerate(zip(TOPICS, TARGETS, parts, strict=True), start=1)]
    directory = settings.data_dir / "production-batches" / BATCH_ID
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (previous.get("source_sha256"), previous.get("factcheck_sha256")) != (source_hash, facts_hash):
            raise OrderConflict("Batch sources changed; retain the existing IDs and explicitly review a revision")
    store = ProductionOrderStore(settings)
    manifest = {
        "schema_version": "production-batch-intake-1", "batch_id": BATCH_ID,
        "source_sha256": source_hash, "factcheck_sha256": facts_hash,
        "language": "en", "currency": "PLN", "jurisdiction": "PL",
        "delivery_mode": "local_only", "recipient": facts.get("recipient"),
        "delivery_status": "not_attempted_parent_owned", "publication_requested": False,
        "concurrency": 1, "execution_status": "intake_partial", "job_ids": [], "orders": [],
        "manifest_path": str(manifest_path),
    }
    for index, order in enumerate(orders, start=1):
        key = f"{BATCH_ID}:reel-{index}"
        state = store.create(order, request_key=key)
        root = store.context(state["id"]).artifact_dir / "intake"
        atomic_json(root / "submission.json", state["request"]["intake"])
        atomic_json(root / "reviewed_factcheck.json", facts)
        diagnostic = {
            "status": "blocked", "phase": "intake_review", "job_id": state["id"],
            "language": "en", "target_duration_seconds": order.intake.target_duration_seconds,
            "duration_range": state["profile"]["production"]["duration_seconds"],
            "word_budget": state["profile"]["production"]["word_budget"],
            "blockers": intake_blockers(state["request"]["intake"]),
            "qa": "not_started", "audio": "not_generated", "video": "not_generated",
            "profile_hash": state["profile_hash"],
            "limits": {key: state["profile"]["production"][key] for key in
                       ("max_cost_usd", "max_provider_calls", "max_attempts_per_element")},
        }
        atomic_json(root / "preflight.json", diagnostic)
        files = [{"path": str(root / name), "sha256": file_hash(root / name)} for name in
                 ("submission.json", "reviewed_factcheck.json", "preflight.json")]
        manifest["job_ids"].append(state["id"])
        manifest["orders"].append({
            "ordinal": index, "job_id": state["id"], "request_key": key, "topic": order.topic,
            "target_duration_seconds": order.intake.target_duration_seconds,
            "status": state["status"], "current_step": state["current_step"],
            "can_resume": state["can_resume"], "artifact_dir": state["artifact_dir"],
            "reserved_cost_units": state["reserved_cost_units"], "provider_calls": state["provider_calls"],
            "intake_artifacts": files,
        })
        manifest["updated_at"] = utc_now_iso()
        manifest["execution_status"] = "blocked_intake" if index == 4 else "intake_partial"
        atomic_json(manifest_path, manifest)
    return manifest


def refresh_batch(settings, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["batch_id"] != BATCH_ID or len(manifest["job_ids"]) != 4:
        raise ValueError("Expected the same complete four-order intake manifest")
    store = ProductionOrderStore(settings)
    result = {"batch_id": BATCH_ID, "job_ids": manifest["job_ids"], "updated_at": utc_now_iso(),
              "orders": [order_status(store.get(job)) for job in manifest["job_ids"]]}
    atomic_json(manifest_path.with_name("progress.json"), result)
    return result


@contextmanager
def batch_lease(settings):
    token = uuid4().hex
    with connect(settings) as db:
        db.execute("CREATE TABLE IF NOT EXISTS production_batch_workers (batch_id TEXT PRIMARY KEY, token TEXT, pid INTEGER, started REAL)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM production_batch_workers WHERE batch_id=?", (BATCH_ID,)).fetchone()
        if row and _same_process(row["pid"], row["started"]):
            raise OrderConflict("The same batch already has a live production worker")
        db.execute("INSERT INTO production_batch_workers VALUES(?,?,?,?) ON CONFLICT(batch_id) DO UPDATE SET token=excluded.token,pid=excluded.pid,started=excluded.started",
                   (BATCH_ID, token, os.getpid(), psutil.Process().create_time()))
        db.commit()
    try:
        yield
    finally:
        with connect(settings) as db:
            db.execute("DELETE FROM production_batch_workers WHERE batch_id=? AND token=?", (BATCH_ID, token))
            db.commit()


async def run_batch(settings, manifest_path: Path, *, script_only=False) -> dict:
    """One bounded pass. Each order keeps its original budgets, QA and cached files."""
    from atnos.production.order_execution import run_order

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = manifest.get("job_ids", [])
    if manifest.get("batch_id") != BATCH_ID or len(ids) != 4 or len(set(ids)) != 4:
        raise ValueError("Expected the same four distinct finance orders")
    store = ProductionOrderStore(settings)
    for index, job in enumerate(ids, start=1):
        state = store.get(job)
        contract = state["request"].get("intake") or {}
        if (state["request"].get("series_key") != BATCH_ID or contract.get("scenario_index") != index
                or contract.get("source_attachment_sha256") != manifest.get("source_sha256")
                or contract.get("factcheck_sha256") != manifest.get("factcheck_sha256")
                or contract.get("delivery_mode") != "local_only"):
            raise OrderConflict("Batch identity no longer matches its immutable orders")
    with batch_lease(settings):
        for job in ids:
            # An interactive preview may already own the first order. Do not duplicate it.
            for _ in range(720):
                state = store.get(job)
                if not state["worker_live"]:
                    break
                await asyncio.sleep(5)
            else:
                raise OrderConflict("A production worker remained busy for one hour")
            should_run = state["can_prepare_script"] if script_only else state["can_resume"]
            if should_run:
                await run_order(settings, job, script_only=script_only)
            refresh_batch(settings, manifest_path)
        return refresh_batch(settings, manifest_path)


def probe_held_workers(settings, manifest_path: Path) -> dict:
    """Run the real entry point serially, but only while its intake hold is enforced."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["batch_id"] != BATCH_ID or len(set(manifest["job_ids"])) != 4:
        raise ValueError("Expected the same four-order batch")
    store = ProductionOrderStore(settings)
    api_root = Path(__file__).resolve().parents[3]
    receipts = []
    for job_id in manifest["job_ids"]:
        before = store.get(job_id)
        if (not before["request"].get("intake") or before["can_resume"] or before["worker_live"]
                or before["status"] != "blocked" or before["current_step"] != "intake_review"
                or before["provider_calls"] or before["reserved_cost_units"] or before["checkpoints"]):
            raise OrderConflict("Worker probe is only allowed on untouched held intake; no generation was requested")
        process = subprocess.run([sys.executable, str(api_root / "scripts" / "run_production_order.py"), "run", job_id],
            cwd=api_root, env={**os.environ, "DATA_DIR": str(settings.data_dir.resolve())},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result = json.loads(process.stdout.strip().splitlines()[-1])
        after = store.get(job_id)
        if (process.returncode != 2 or result.get("id") != job_id or result.get("status") != "blocked"
                or after != before):
            raise RuntimeError("The held worker did not return the expected unchanged blocked state")
        receipt = {
            "job_id": job_id, "entry_point": "scripts/run_production_order.py run", "exit_code": process.returncode,
            "status": result["status"], "current_step": result["current_step"],
            "invoked_at": utc_now_iso(), "serial_execution": True, "generation_started": False,
            "provider_calls": after["provider_calls"], "reserved_cost_units": after["reserved_cost_units"],
            "stdout_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
            "result": result,
        }
        path = store.context(job_id).artifact_dir / "intake" / "worker_probe.json"
        atomic_json(path, receipt)
        receipts.append({"job_id": job_id, "exit_code": process.returncode, "status": result["status"],
                         "current_step": result["current_step"], "path": str(path), "sha256": file_hash(path)})
        atomic_json(manifest_path.with_name("worker_probes.json"), {"batch_id": BATCH_ID, "workers": receipts})
    refresh_batch(settings, manifest_path)
    return {"batch_id": BATCH_ID, "workers": receipts}
