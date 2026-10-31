"""Append-only finite extensions to an authorized local WB image lane.

The immutable image-lane receipt remains the original provider authorization.
This ledger can only add a bounded number of free ComfyUI requests for the
same local-only Wealth Blueprint order and is never an API or cost allowance.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from atnos.core.logging import redact_secrets
from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.order_store import (
    OrderConflict,
    ProductionBlocked,
    ProductionOrderStore,
    _same_process,
    _event,
)
from atnos.production.project_profile import content_hash

TABLE = "production_local_image_allowance_events"
MAX_LOCAL_IMAGE_REQUESTS = 100
SCOPE = "local_only_wealth_blueprint_comfyui"


def register_local_image_allowance(
    settings,
    job_id: str,
    maximum: int,
    *,
    reason: str,
    authorized: bool = False,
) -> dict[str, Any]:
    """Append one explicit extension while preserving the original lane/counters."""
    if authorized is not True:
        raise ValueError("Explicit authorization is required to extend local image requests")
    if type(maximum) is not int or not 1 <= maximum <= MAX_LOCAL_IMAGE_REQUESTS:
        raise ValueError("Local image maximum must be an integer from 1 to 100")
    if not isinstance(reason, str) or len(reason.strip()) < 20:
        raise ValueError("A clear reason is required for a bounded local image extension")

    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    _require_local_order(state)
    if state["worker_live"] or state["status"] not in {"queued", "blocked"}:
        raise OrderConflict("Local image allowance requires a stopped production order")
    lane = _current_local_lane(settings, job_id)
    original_maximum = _original_maximum(lane)
    consumed = _local_image_attempts(state)

    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        live = db.execute("""SELECT j.status,o.request_json,o.profile_json,o.lease_pid,o.lease_process_started
            FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?""", (job_id,)).fetchone()
        if (not live or _same_process(live["lease_pid"], live["lease_process_started"])
                or live["status"] not in {"queued", "blocked"}
                or content_hash(json.loads(live["request_json"])) != content_hash(state["request"])
                or content_hash(json.loads(live["profile_json"])) != content_hash(state["profile"])):
            raise OrderConflict("Order state changed or a worker became active before allowance registration")
        autonomy = _autonomy_row(db, job_id)
        rows = _load_rows(db, job_id)
        effective = _validate_rows(rows, state, lane, original_maximum, consumed)
        if maximum <= max(original_maximum, effective, consumed):
            raise ValueError("The new maximum must extend the current allowance and cover consumed attempts")

        previous_digest = rows[-1]["digest"] if rows else lane["digest"]
        sequence = len(rows) + 1
        payload = {
            "version": "local-image-allowance-v1",
            "job_id": job_id,
            "sequence": sequence,
            "scope": SCOPE,
            "provider": "comfyui-local",
            "request_hash": content_hash(state["request"]),
            "profile_hash": content_hash(state["profile"]),
            "lane_digest": lane["digest"],
            "original_maximum": original_maximum,
            "maximum": maximum,
            "consumed_attempts_at_registration": consumed,
            "previous_digest": previous_digest,
            "autonomy_enabled": True,
            "autonomy_max_recoveries": autonomy["max_recoveries"],
            "autonomy_recoveries_used": autonomy["recoveries_used"],
            "provider_calls_at_registration": state["provider_calls"],
            "reserved_cost_units_at_registration": state["reserved_cost_units"],
            "cost_per_request": 0,
            "provider_call_limit_changed": False,
            "reason": redact_secrets(reason.strip()),
        }
        digest = content_hash(payload)
        _initialize(db)
        db.execute(
            f"INSERT INTO {TABLE}(job_id,sequence,payload_json,previous_digest,digest,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (job_id, sequence, json_dumps(payload), previous_digest, digest, utc_now_iso()),
        )
        _event(db, job_id, "image_allowance", "local_image_allowance_extended", {
            "sequence": sequence,
            "maximum": maximum,
            "scope": SCOPE,
            "lane_digest": lane["digest"],
            "request_hash": payload["request_hash"],
            "profile_hash": payload["profile_hash"],
            "previous_digest": previous_digest,
            "digest": digest,
            "consumed_attempts_preserved": consumed,
            "provider_calls_at_registration": state["provider_calls"],
            "reserved_cost_units_at_registration": state["reserved_cost_units"],
            "cost_per_request": 0,
            "provider_call_limit_changed": False,
        })
        db.commit()
    return {**payload, "digest": digest}


def effective_local_image_allowance(settings, job_id: str, lane: dict) -> int | None:
    """Return original local cap or the latest intact append-only extension."""
    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    _require_local_order(state)
    current_lane = _current_local_lane(settings, job_id)
    if (not isinstance(lane, dict) or lane.get("provider") != "comfyui-local"
            or lane.get("digest") != current_lane.get("digest")):
        raise ProductionBlocked("Local image allowance lane binding changed")
    original_maximum = _original_maximum(current_lane)
    consumed = _local_image_attempts(state)
    with connect(settings) as db:
        rows = _load_rows(db, job_id)
    maximum = _validate_rows(rows, state, current_lane, original_maximum, consumed)
    return None if settings.atnos_local_work_unlimited else maximum


def local_image_allowance_recovery_evidence(store: ProductionOrderStore, state: dict[str, Any]) -> dict[str, Any] | None:
    """Prove one normal resume is justified by a newly extended local cap.

    The image-cap exception happens before a new image stage begins. This
    verifier accepts only that exact blocked state and rejects live/running or
    uncertain prior image dispatches. It performs no writes or dispatches.
    """
    if (state.get("status") != "blocked" or state.get("current_step") != "image_generation"
            or state.get("worker_live") is not False or state.get("can_resume") is not True):
        return None
    try:
        _require_local_order(state)
    except (OrderConflict, KeyError, TypeError):
        return None
    error = state.get("error")
    if isinstance(error, str):
        try:
            error = json.loads(error)
        except (ValueError, TypeError):
            return None
    if (not isinstance(error, dict) or error.get("category") != "execution_failure"
            or error.get("message") != "Authorized image request limit reached; history and cost remain preserved"):
        return None

    try:
        from atnos.production.wb_image_api import load_image_lane

        lane = load_image_lane(store.settings, state["id"])
        if not lane or lane.get("provider") != "comfyui-local":
            return None
        original_cap = _original_maximum(lane)
        consumed = _local_image_attempts(state)
        if consumed < original_cap:
            return None
        for row in state.get("checkpoints", []):
            stage = str(row.get("stage") or "")
            if not stage.startswith(("assets/image_local/", "assets/image_api/")):
                continue
            status = row.get("status")
            if status not in {"done", "failed"}:
                return None
            if stage.startswith("assets/image_local/flux2-trial/") and status != "done":
                return None
            if status == "failed":
                failure = json.loads(row.get("error_json") or "{}")
                if not isinstance(failure, dict):
                    return None
                message = str(failure.get("message") or "").casefold()
                if any(marker in message for marker in (
                        "uncertain", "dispatch_intent", "no second post", "inspect provider evidence")):
                    return None
        cap = effective_local_image_allowance(store.settings, state["id"], lane)
        if cap is not None and (cap <= original_cap or cap <= consumed):
            return None
        from atnos.production.order_autonomy import policy

        autonomy = policy(store.settings, state["id"])
        if (not autonomy or not autonomy.get("enabled")
                or not store.settings.atnos_local_work_unlimited and autonomy.get("recoveries_used", 0) >= autonomy.get("max_recoveries", 0)):
            return None
    except (ProductionBlocked, OrderConflict, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None
    return {
        "kind": "extended_local_image_allowance",
        "stage": "image_generation",
        "lane_digest": lane["digest"],
        "original_maximum": original_cap,
        "effective_maximum": cap,
        "consumed_attempts": consumed,
        "uncertain_prior_dispatch": False,
        "normal_dispatch_only": True,
    }


def _initialize(db) -> None:
    db.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
        job_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        previous_digest TEXT NOT NULL,
        digest TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(job_id,sequence),
        UNIQUE(job_id,digest)
    )""")
    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update
        BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT,'append-only local image allowance ledger'); END""")
    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete
        BEFORE DELETE ON {TABLE} BEGIN SELECT RAISE(ABORT,'append-only local image allowance ledger'); END""")


def _load_rows(db, job_id: str) -> list[dict[str, Any]]:
    try:
        rows = db.execute(f"SELECT * FROM {TABLE} WHERE job_id=? ORDER BY sequence", (job_id,)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    return [dict(row) for row in rows]


def _validate_rows(rows, state, lane, original_maximum, consumed) -> int:
    maximum = original_maximum
    previous = lane["digest"]
    prior_consumed = 0
    for sequence, row in enumerate(rows, start=1):
        try:
            payload = json.loads(row["payload_json"])
        except (ValueError, TypeError, KeyError):
            raise ProductionBlocked("Local image allowance audit is corrupt") from None
        digest = content_hash(payload) if isinstance(payload, dict) else ""
        if (row.get("sequence") != sequence or row.get("digest") != digest
                or row.get("previous_digest") != previous
                or payload.get("version") != "local-image-allowance-v1"
                or payload.get("sequence") != sequence
                or payload.get("previous_digest") != previous
                or payload.get("job_id") != state["id"]
                or payload.get("scope") != SCOPE
                or payload.get("provider") != "comfyui-local"
                or payload.get("lane_digest") != lane.get("digest")
                or payload.get("request_hash") != content_hash(state["request"])
                or payload.get("profile_hash") != content_hash(state["profile"])
                or payload.get("original_maximum") != original_maximum
                or type(payload.get("maximum")) is not int
                or not maximum < payload["maximum"] <= MAX_LOCAL_IMAGE_REQUESTS
                or type(payload.get("consumed_attempts_at_registration")) is not int
                or payload["consumed_attempts_at_registration"] < 0
                or payload["consumed_attempts_at_registration"] > consumed
                or payload.get("autonomy_enabled") is not True
                or type(payload.get("autonomy_max_recoveries")) is not int
                or not 1 <= payload["autonomy_max_recoveries"] <= 40
                or type(payload.get("autonomy_recoveries_used")) is not int
                or not 0 <= payload["autonomy_recoveries_used"] <= payload["autonomy_max_recoveries"]
                or type(payload.get("provider_calls_at_registration")) is not int
                or payload["provider_calls_at_registration"] < 0
                or type(payload.get("reserved_cost_units_at_registration")) is not int
                or payload["reserved_cost_units_at_registration"] < 0
                or payload.get("cost_per_request") != 0
                or payload.get("provider_call_limit_changed") is not False
                or not isinstance(payload.get("reason"), str)
                or len(payload["reason"].strip()) < 20
                or payload.get("consumed_attempts_at_registration", -1) < prior_consumed):
            raise ProductionBlocked("Local image allowance binding or append-only audit integrity failed")
        maximum = payload["maximum"]
        prior_consumed = payload["consumed_attempts_at_registration"]
        previous = digest
    return maximum


def _require_local_order(state: dict[str, Any]) -> None:
    if (state["request"].get("project") != "wealth_blueprint"
            or state["request"].get("delivery_mode") != "local_only"
            or state["request"].get("production_scope", "film") != "film"):
        raise OrderConflict("Image allowance extension is limited to local-only Wealth Blueprint film orders")


def _current_local_lane(settings, job_id: str) -> dict:
    from atnos.production.wb_image_api import load_image_lane

    lane = load_image_lane(settings, job_id)
    if not lane or lane.get("provider") != "comfyui-local":
        raise OrderConflict("A valid local ComfyUI image lane is required")
    return lane


def _original_maximum(lane: dict) -> int:
    maximum = lane.get("max_requests")
    if type(maximum) is not int or not 1 <= maximum <= MAX_LOCAL_IMAGE_REQUESTS:
        raise ProductionBlocked("The original local image request cap is invalid")
    return maximum


def _local_image_attempts(state: dict[str, Any]) -> int:
    attempts = 0
    for row in state.get("checkpoints", []):
        if str(row.get("stage", "")).startswith("assets/image_local/"):
            value = row.get("attempts")
            if type(value) is not int or value < 0:
                raise ProductionBlocked("Local image attempt history is invalid")
            attempts += value
    return attempts


def _autonomy_row(db, job_id: str) -> dict[str, Any]:
    try:
        row = db.execute("SELECT enabled,max_recoveries,recoveries_used FROM production_order_autonomy WHERE job_id=?",
                         (job_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            row = None
        else:
            raise
    if not row or row["enabled"] != 1:
        raise OrderConflict("Enabled autonomous recovery is required for a local image allowance extension")
    return dict(row)
