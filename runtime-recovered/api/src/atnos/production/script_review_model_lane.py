"""Independent, immutable job-scoped reviewer model trial."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production import script_model_lane as shared_lane
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore
from atnos.production.project_profile import content_hash


MODEL = shared_lane.MODEL
ENDPOINT = shared_lane.ENDPOINT
EXECUTION_SCOPE = "normal_produce_script_review"
_TABLE = "production_script_review_model_lanes"


def _block(message: str) -> ProductionBlocked:
    return ProductionBlocked(f"script_review_model_lane: {message}")


def _receipt_path(settings, job_id: str) -> Path:
    return ProductionOrderStore(settings).context(job_id).artifact_dir / "script" / "script_review_model_lane.json"


def _body(settings, job_id: str, *, model: str, reason: str,
          expected_model_digest: str | None, require_idle: bool) -> dict[str, Any]:
    if model != MODEL:
        raise _block("only the pinned qwen3.5:9b reviewer is supported")
    if not isinstance(reason, str) or len(reason.strip()) < 12:
        raise _block("a specific reviewer-trial reason is required")
    _, binding = shared_lane._order_binding(settings, job_id, require_idle=require_idle)
    digest = shared_lane._installed_digest()
    if expected_model_digest and expected_model_digest.removeprefix("sha256:").casefold() != digest.casefold():
        raise _block("the installed reviewer model digest differs from the supplied pin")
    if digest != shared_lane.EXPECTED_MODEL_DIGEST:
        raise _block("the installed reviewer model differs from the approved local pin")
    return {**binding, "model": MODEL, "model_digest": digest, "endpoint": ENDPOINT,
            "execution_scope": EXECUTION_SCOPE, "reason": reason.strip(), "authorized": True}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        lane = value["lane"]
        if not isinstance(lane, dict) or lane.get("digest") != content_hash({k: v for k, v in lane.items() if k != "digest"}):
            raise ValueError("digest mismatch")
        return value
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise _block("the reviewer receipt is missing or tampered") from exc


def _db_payload(settings, job_id: str) -> str | None:
    with connect(settings) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone():
            return None
        row = db.execute(f"SELECT payload_json FROM {_TABLE} WHERE job_id=?", (job_id,)).fetchone()
    return row["payload_json"] if row else None


def register_script_review_model_lane(settings, job_id: str, *, model: str, reason: str,
                                      authorized: bool = False,
                                      expected_model_digest: str | None = None) -> dict[str, Any]:
    """Persist reviewer-specific authorization without changing authoring/default settings."""
    if authorized is not True:
        raise _block("explicit reviewer authorization is required")
    body = _body(settings, job_id, model=model, reason=reason,
                 expected_model_digest=expected_model_digest, require_idle=True)
    lane = {**body, "digest": content_hash(body)}
    record = {"lane": lane}
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = _receipt_path(settings, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT j.status,o.lease_pid,o.lease_process_started FROM pipeline_jobs j "
                           "JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?", (job_id,)).fetchone()
        if order is None:
            raise KeyError("Production order not found")
        if order["status"] not in {"queued", "blocked"} or shared_lane._same_process(
                order["lease_pid"], order["lease_process_started"]):
            raise OrderConflict("A reviewer model lane can only be registered for a stopped, idle order.")
        db.execute(f"CREATE TABLE IF NOT EXISTS {_TABLE}(job_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)")
        existing = db.execute(f"SELECT payload_json FROM {_TABLE} WHERE job_id=?", (job_id,)).fetchone()
        if existing and existing["payload_json"] != encoded:
            raise OrderConflict("An existing reviewer model registration is immutable.")
        if path.exists() and path.read_text(encoding="utf-8") != encoded:
            raise OrderConflict("An existing reviewer model receipt is immutable.")
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError as exc:
                raise OrderConflict("A concurrent reviewer registration won the write-once receipt.") from exc
        if not existing:
            db.execute(f"INSERT INTO {_TABLE}(job_id,payload_json,created_at) VALUES(?,?,?)",
                       (job_id, encoded, utc_now_iso()))
        db.commit()
    return lane


def load_script_review_model_lane(settings, job_id: str, *, verify_runtime: bool = True) -> dict[str, Any] | None:
    """Load only this job's reviewer opt-in and verify its original frozen bindings."""
    path = _receipt_path(settings, job_id)
    payload = _db_payload(settings, job_id)
    if not path.exists() and payload is None:
        return None
    if not path.is_file() or payload is None:
        raise _block("reviewer receipt and database binding are incomplete")
    record = _read(path)
    if json.loads(payload) != record:
        raise _block("reviewer database binding differs from its immutable receipt")
    lane = record["lane"]
    if (lane.get("job_id") != job_id or lane.get("model") != MODEL or lane.get("endpoint") != ENDPOINT
            or lane.get("execution_scope") != EXECUTION_SCOPE or lane.get("authorized") is not True):
        raise _block("registered reviewer model or scope is unsupported")
    _, binding = shared_lane._order_binding(settings, job_id, require_idle=False)
    if any(lane.get(key) != value for key, value in binding.items()):
        raise _block("job request or frozen profile changed after reviewer registration")
    current_digest = shared_lane._installed_digest()
    if lane.get("model_digest") != current_digest:
        raise _block("installed reviewer model changed after registration")
    if verify_runtime:
        shared_lane._runtime_model_available(MODEL, current_digest, ENDPOINT)
    return lane
