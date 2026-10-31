"""Explicit edited-script handoff. Retains failed attempts and still requires real QA."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, _event, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.script_adaptation import bind_claims
from atnos.production.steps import order_script
from atnos.production.steps.common import write_json


def candidate(context, document):
    matches = [item for item in document["scripts"] if item["job_id"] == context.job_id]
    if len(matches) != 1:
        raise ValueError("Exactly one edited script must match this existing job")
    item = matches[0]
    context.artifacts["editorial_source_facts"] = document.get("supplemental_facts", [])
    segments = []
    for index, row in enumerate(item["segments"], start=1):
        kind, text, headline, visual, claims, *facts = row
        segments.append({"id": f"s{index:02}", "type": kind, "text": text, "on_screen": headline,
                         "visual_intent": visual, "claim_ids": claims.split(",") if claims else [],
                         "fact_ids": facts[0].split(",") if facts and facts[0] else []})
    raw = bind_claims(context, {"title": item["title"], "description": item["description"], "segments": segments})
    script = order_script.EditorialScript.model_validate(raw)
    issues = order_script.script_issues(script, context.artifacts["project_profile"],
                                       trusted_facts=order_script.reviewed_source_facts(context), request=context.artifacts.get('production_order'))
    return script, issues


def load_accepted(store, job_id):
    with connect(store.settings) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_editorial_revisions'").fetchone():
            return None
        row = db.execute("SELECT * FROM production_editorial_revisions WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload_json"])
    path = store.context(job_id).artifact_dir / "editorial" / "accepted.json"
    if not path.is_file() or file_hash(path) != row["file_sha256"] or json.loads(path.read_text(encoding="utf-8")) != payload:
        raise ProductionBlocked("Edited script evidence is missing or changed")
    archive = store.context(job_id).artifact_dir / "editorial" / "superseded-checkpoints.json"
    if not archive.is_file() or file_hash(archive) != payload["archive_sha256"]:
        raise ProductionBlocked("Original failed-attempt archive is missing or changed")
    if payload["qa"]["status"] != "passed":
        raise ProductionBlocked("Edited script was not accepted by QA")
    return payload, path


async def admit(settings, job_id, source: Path, *, approved=False):
    if not approved:
        raise ValueError("An explicit editorial handoff is required")
    store = ProductionOrderStore(settings)
    raw = source.read_bytes()
    document = json.loads(raw)
    state, context = store.get(job_id), store.context(job_id)
    if state["worker_live"] or state["status"] in {"ready", "cancelled"}:
        raise OrderConflict("Cannot replace a live or completed production")
    if any(not row["stage"].startswith(("script", "editorial_review/")) for row in state["checkpoints"]):
        raise OrderConflict("This handoff is only for blocked pre-media scripts")
    script, issues = candidate(context, document)
    if issues:
        raise ValueError(json.dumps(issues))
    digest = content_hash(script.model_dump(mode="json"))
    previous = load_accepted(store, job_id)
    if previous:
        if previous[0]["script_hash"] != digest:
            raise OrderConflict("An edited script was already accepted; do not silently replace it")
        return {"job_id": job_id, "status": "already_accepted"}
    from atnos.production.order_execution import StageExecutor
    with store.lease(job_id) as token:
        executor = StageExecutor(store, job_id, token)
        executor.context.artifacts["editorial_source_facts"] = context.artifacts["editorial_source_facts"]
        async def check():
            verdict = await order_script.review(settings, executor.context, script)
            return write_json(context.artifact_dir / "editorial" / f"review-{digest[:12]}.json", verdict)
        verdict = await executor.run(f"editorial_review/{digest[:12]}",
            {"script": script.model_dump(mode="json"), "source_sha256": hashlib.sha256(raw).hexdigest(),
             "validator": order_script.SCRIPT_VALIDATOR_VERSION}, check, local_model_call=True)
        if verdict["status"] != "passed":
            store.fail(job_id, token, f"editorial_review/{digest[:12]}", category="editorial_quality", message=json.dumps(verdict))
            return {"job_id": job_id, "status": "rejected", "review": verdict}
        words = sum(len(s.text.split()) for s in script.segments)
        payload = {**script.model_dump(mode="json"), "script_hash": digest, "qa": verdict,
                   "text": " ".join(s.text for s in script.segments), "word_count": words,
                   "provenance": {"editor": document["editor"], "reason": document["reason"], "source_sha256": hashlib.sha256(raw).hexdigest(),
                                  "original_request_hash": content_hash(state["request"]), "attempts_reset": False},
                   "editorial_source_facts": context.artifacts["editorial_source_facts"]}
        archive = context.artifact_dir / "editorial" / "superseded-checkpoints.json"
        accepted = context.artifact_dir / "editorial" / "accepted.json"
        # Failed script checkpoints are archived intact, not re-labelled as passed.
        with connect(settings) as db:
            db.execute("BEGIN IMMEDIATE")
            store._owner(db, job_id, token)
            rows = [dict(row) for row in db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage LIKE 'script%'", (job_id,))]
            write_json(archive, {"superseded": rows, "reason": document["reason"], "replacement_hash": digest})
            payload["archive_sha256"] = file_hash(archive)
            write_json(accepted, payload)
            db.execute("CREATE TABLE IF NOT EXISTS production_editorial_revisions(job_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, file_sha256 TEXT NOT NULL, archive_json TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.execute("INSERT INTO production_editorial_revisions VALUES(?,?,?,?,?)", (job_id, json_dumps(payload), file_hash(accepted), json_dumps(rows), utc_now_iso()))
            db.execute("DELETE FROM pipeline_stage_checkpoints WHERE job_id=? AND stage LIKE 'script%'", (job_id,))
            db.execute("UPDATE pipeline_job_steps SET status='superseded' WHERE job_id=? AND step LIKE 'script%'", (job_id,))
            db.execute("UPDATE pipeline_jobs SET status='queued',current_step='editorial_accepted',error=NULL,updated_at=? WHERE id=?", (utc_now_iso(), job_id))
            _event(db, job_id, "editorial_accepted", "edited_script_accepted", {"hash": digest, "archived_attempts": {row["stage"]:row["attempts"] for row in rows}, "budgets_reset":False})
            db.commit()
        return {"job_id": job_id, "status": "accepted", "word_count": words, "script_hash": digest}


async def produce_accepted(executor, accepted):
    payload, path = accepted
    context = executor.context
    context.artifacts["editorial_source_facts"] = payload["editorial_source_facts"]
    async def stage():
        from atnos.production.models import StepResult
        result = write_json(context.artifact_dir / "script.json", payload)
        return StepResult(result.artifact_name, result.artifact_path, payload, result.message,
                          (path, context.artifact_dir / "editorial" / "superseded-checkpoints.json"))
    result = await executor.run("script", {"accepted_sha256": file_hash(path)}, stage)
    await asyncio.to_thread(executor.store.persist_legacy_artifact, context.job_id, executor.token, "script", result)
    context.artifacts["script"] = result
    return result
