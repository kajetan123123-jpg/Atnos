"""An immutable, job-scoped local image action; admission happens at HTTP POST.

This supplements the existing order/Comfy path. It never resets historic counters
or changes global local-unlimited/paid policies. SQLite is authoritative; receipts
are hash-bound authorization and a readable audit of the same durable ledger.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, _event, _same_process, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json

VERSION = "local-image-dispatch-action-v1"
AUTH_FILE = "local_image_dispatch_authorization.json"
STATUS_FILE = "local_image_dispatch_status.json"


class LocalImageDispatchBlocked(ProductionBlocked):
    """Non-retryable admission/unknown outcome; not a Comfy backend error."""

    def __init__(self, message: str, *, category: str = "local_image_action_budget"):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class LocalImageDispatchScope:
    settings: object
    job_id: str
    worker_token: str
    stage: str
    fingerprint: str
    action_digest: str


_SCOPE: ContextVar[LocalImageDispatchScope | None] = ContextVar("atnos_local_image_dispatch", default=None)


def active_local_image_dispatch_scope() -> LocalImageDispatchScope | None:
    return _SCOPE.get()


def workflow_digest(workflow: dict) -> str:
    return hashlib.sha256(json.dumps(workflow, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def creative_workflow_digest(workflow: dict) -> str:
    """A new seed/output filename is another sample, not a corrected request."""
    graph = json.loads(json.dumps(workflow))
    for node in graph.values():
        if not isinstance(node, dict) or not isinstance(node.get('inputs'), dict):
            continue
        if node.get('class_type') in {'KSampler', 'KSamplerAdvanced'}:
            node['inputs'].pop('seed', None)
            node['inputs'].pop('noise_seed', None)
        if node.get('class_type') == 'SaveImage':
            node['inputs'].pop('filename_prefix', None)
    return workflow_digest(graph)


def _prior_creative_digest(dispatch: dict) -> str:
    evidence = dispatch['evidence']
    if evidence.get('creative_workflow_hash'):
        return evidence['creative_workflow_hash']
    # Read old completed receipts, never rewrite their historic ledger entries.
    receipt = Path(evidence.get('generation_evidence') or '')
    if not receipt.is_file() or file_hash(receipt) != evidence.get('generation_evidence_sha256'):
        raise LocalImageDispatchBlocked('Prior local image evidence is unavailable; request revision cannot be verified')
    generation = json.loads(receipt.read_text('utf-8'))
    graph = generation.get('workflow')
    if not isinstance(graph, dict) or workflow_digest(graph) != dispatch['workflow_hash']:
        raise LocalImageDispatchBlocked('Prior local image workflow integrity failed')
    return creative_workflow_digest(graph)


def _schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS production_local_image_actions ("
               "job_id TEXT PRIMARY KEY, action_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, "
               "digest TEXT NOT NULL, created_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS production_local_image_dispatches ("
               "job_id TEXT NOT NULL, stage TEXT NOT NULL, fingerprint TEXT NOT NULL, action_digest TEXT NOT NULL, "
               "workflow_hash TEXT NOT NULL, status TEXT NOT NULL, prompt_id TEXT, evidence_json TEXT NOT NULL, "
               "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(job_id,stage), UNIQUE(job_id,fingerprint))")


def _authorization(db, settings, job_id):
    try:
        row = db.execute("SELECT * FROM production_local_image_actions WHERE job_id=?", (job_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    path = ProductionOrderStore(settings).context(job_id).artifact_dir / AUTH_FILE
    if (content_hash(payload) != row["digest"] or payload.get("job_id") != job_id
            or payload.get("version") != VERSION or payload.get("provider") != "comfyui-local"
            or payload.get("reservation_usd_per_request") != 0
            or not 1 <= payload.get("max_new_requests", 0) <= 3
            or not path.is_file() or json.loads(path.read_text("utf-8")) != {**payload, "digest": row["digest"]}):
        raise LocalImageDispatchBlocked("Local image action authorization integrity failed")
    order = db.execute("SELECT request_json,profile_json FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
    if not order:
        raise LocalImageDispatchBlocked("Local image action order is missing")
    request, profile = json.loads(order["request_json"]), json.loads(order["profile_json"])
    if (request.get("project") != "wealth_blueprint" or request.get("delivery_mode") != "local_only"
            or content_hash(request) != payload["request_hash"] or content_hash(profile) != payload["profile_hash"]):
        raise LocalImageDispatchBlocked("Local image action no longer matches the frozen local order")
    from atnos.production.wb_image_conditioning import load_conditioning_amendment
    amendment=load_conditioning_amendment(settings,ProductionOrderStore(settings).context(job_id))
    if amendment is not None:
        if (payload.get('conditioning_amendment_digest')!=amendment['digest']
                or payload.get('conditioning_scene_id')!=amendment['scene_id'] or payload['max_new_requests']!=1):
            raise LocalImageDispatchBlocked('Local image action does not bind the current conditioning amendment')
    elif payload.get('conditioning_amendment_digest') is not None:
        raise LocalImageDispatchBlocked('Local image action refers to a missing conditioning amendment')
    return {**payload, "digest": row["digest"]}


def _status(db, action):
    rows = db.execute("SELECT * FROM production_local_image_dispatches WHERE job_id=? ORDER BY created_at,stage",
                      (action["job_id"],)).fetchall()
    dispatches = [{**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows]
    for row in dispatches:
        row.pop("evidence_json")
    used = len(dispatches)
    return {**action, "used_new_requests": used, "remaining_new_requests": max(0, action["max_new_requests"] - used),
            "pending_or_unknown": any(row["status"] in {"reserved", "submitted", "unknown"} for row in dispatches),
            "dispatches": dispatches}


def _write_status(db, settings, action):
    result = _status(db, action)
    write_json(ProductionOrderStore(settings).context(action["job_id"]).artifact_dir / STATUS_FILE, result)
    return result


def register_local_image_action(settings, job_id: str, *, action_key: str, max_new_requests: int = 3,
                                authorized: bool = False, reason: str = "",
                                conditioning_amendment_digest: str | None = None) -> dict:
    """Registration only; retry this exact key, never mint a key on worker resume."""
    if not authorized:
        raise ValueError("Explicit bounded local image action authorization is required")
    if (isinstance(max_new_requests, bool) or not isinstance(max_new_requests, int)
            or not 1 <= max_new_requests <= 3 or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", action_key)):
        raise ValueError("Local image action requires a stable action key and at most three new requests")
    from atnos.production.wb_image_api import load_image_lane

    store = ProductionOrderStore(settings)
    lane = load_image_lane(settings, job_id)
    if not lane or lane.get("provider") != "comfyui-local" or lane.get("reservation_usd_per_request") != 0:
        raise OrderConflict("Bounded local image action requires existing zero-cost local authorization")
    context = store.context(job_id)
    if context.artifact_dir.resolve().drive.casefold() != "e:":
        raise OrderConflict("Local image action artifacts must stay on E:")
    from atnos.production.wb_image_conditioning import load_conditioning_amendment
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        # Serialize with amendment registration before reading its identity.
        # Otherwise an amendment can commit between a stale None and INSERT,
        # leaving an immutable action that can never satisfy dispatch admission.
        amendment=load_conditioning_amendment(settings,context)
        if amendment is not None:
            if conditioning_amendment_digest!=amendment['digest']:
                raise OrderConflict('Separate dispatch must explicitly bind the conditioning amendment digest')
            if max_new_requests!=1:
                raise OrderConflict('Conditioning replacement allows exactly one new request in a single action')
        elif conditioning_amendment_digest is not None:
            raise OrderConflict('Cannot authorize a missing conditioning amendment')
        order = db.execute("SELECT * FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
        state = db.execute("SELECT status FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone()
        if (not order or not state or state["status"] not in {"queued", "blocked"}
                or _same_process(order["lease_pid"], order["lease_process_started"])):
            raise OrderConflict("Bounded local image action registration requires a stopped order")
        request, profile = json.loads(order["request_json"]), json.loads(order["profile_json"])
        if request.get("project") != "wealth_blueprint" or request.get("delivery_mode") != "local_only":
            raise OrderConflict("Bounded local image action is restricted to local_only Wealth Blueprint")
        payload = {"version": VERSION, "job_id": job_id, "action_key": action_key,
                   "max_new_requests": max_new_requests, "provider": "comfyui-local", "reservation_usd_per_request": 0,
                   "image_lane_digest": lane["digest"], "request_hash": content_hash(request),
                   "profile_hash": content_hash(profile), "execution_scope": "standard_local_comfy_candidates",
                   "reason": str(reason)[:600], "user_appearance_approval": False}
        if amendment is not None:
            payload.update(conditioning_amendment_digest=amendment['digest'],conditioning_scene_id=amendment['scene_id'])
        existing = _authorization(db, settings, job_id)
        if existing:
            if existing["digest"] != content_hash(payload):
                raise OrderConflict("Local image action is immutable; a new key cannot reset its consumed budget")
            return _status(db, existing)
        digest = content_hash(payload)
        write_json(context.artifact_dir / AUTH_FILE, {**payload, "digest": digest})
        db.execute("INSERT INTO production_local_image_actions VALUES (?,?,?,?,?)",
                   (job_id, action_key, json_dumps(payload), digest, utc_now_iso()))
        action = {**payload, "digest": digest}
        result = _write_status(db, settings, action)
        _event(db, job_id, None, "local_image_action_registered", {"digest": digest, "action_key": action_key,
               "max_new_requests": max_new_requests, "used_new_requests": 0, "remaining_new_requests": max_new_requests})
        db.commit()
        return result


def local_image_action_status(settings, job_id: str) -> dict | None:
    with connect(settings) as db:
        action = _authorization(db, settings, job_id)
        return _status(db, action) if action else None


def local_image_action_summary(settings, job_id: str) -> dict | None:
    """Read-only UI projection; no paths, approval, retry or dispatch side effects."""
    try:
        action = local_image_action_status(settings, job_id)
    except (ProductionBlocked, ValueError, OSError, KeyError):
        return {'status': 'integrity_blocked', 'provider': 'comfyui-local'}
    if action is None:
        return None
    return {key: action[key] for key in ('action_key', 'provider', 'max_new_requests',
            'used_new_requests', 'remaining_new_requests', 'pending_or_unknown')} | {'status': 'verified'}


@contextmanager
def local_image_dispatch_scope(settings, job_id, worker_token, stage, fingerprint):
    action = local_image_action_status(settings, job_id)
    if action is None:
        yield None  # Existing unscoped local/paid contracts stay unchanged.
        return
    scope = LocalImageDispatchScope(settings, job_id, worker_token, stage, fingerprint, action["digest"])
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def reserve_local_image_dispatch(scope: LocalImageDispatchScope, workflow: dict, *, reference_upload=None) -> dict:
    """Commit before POST; a crash even at this boundary remains fail-closed."""
    from atnos.production.wb_image_api import load_image_lane

    with connect(scope.settings) as db:
        db.execute("BEGIN IMMEDIATE")
        ProductionOrderStore._owner(db, scope.job_id, scope.worker_token)
        if scope.worker_token.startswith("script-only:") or not scope.stage.startswith("assets/image_local/"):
            raise LocalImageDispatchBlocked("Local image dispatch requires a normal media worker lease and stage")
        checkpoint = db.execute("SELECT status FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?",
                                (scope.job_id, scope.stage)).fetchone()
        if not checkpoint or checkpoint["status"] != "running":
            raise LocalImageDispatchBlocked("Local image dispatch requires its actual running production checkpoint")
        action = _authorization(db, scope.settings, scope.job_id)
        lane = load_image_lane(scope.settings, scope.job_id)
        if (not action or action["digest"] != scope.action_digest or not lane
                or lane.get("provider") != "comfyui-local" or lane["digest"] != action["image_lane_digest"]):
            raise LocalImageDispatchBlocked("Local image dispatch does not match its registered local authorization")
        if action.get('conditioning_amendment_digest'):
            prefix='assets/image_local/'+action['conditioning_scene_id']+'/'
            if not scope.stage.startswith(prefix):
                raise LocalImageDispatchBlocked('Conditioning dispatch action cannot be spent on a different scene/backend')
            from atnos.production.wb_image_conditioning import load_conditioning_amendment
            amendment=load_conditioning_amendment(scope.settings,ProductionOrderStore(scope.settings).context(scope.job_id))
            samplers=[node.get('inputs',{}) for node in workflow.values()
                      if isinstance(node,dict) and node.get('class_type') in {'KSampler','KSamplerAdvanced'}]
            if not samplers or any(item.get('seed',item.get('noise_seed'))!=amendment['seed'] for item in samplers):
                raise LocalImageDispatchBlocked('Conditioning workflow does not use the bound sampler seed')
            from atnos.production.wb_image_conditioning import verify_conditioning_dispatch_workflow
            try:
                verify_conditioning_dispatch_workflow(amendment,workflow,reference_upload)
            except ProductionBlocked as exc:
                raise LocalImageDispatchBlocked(str(exc)) from exc
        state = _status(db, action)
        if state["pending_or_unknown"]:
            raise LocalImageDispatchBlocked("Local image dispatch outcome unknown; reconciliation required before another POST",
                                            category="execution_outcome_unknown")
        if any(row["stage"] == scope.stage or row["fingerprint"] == scope.fingerprint for row in state["dispatches"]):
            raise LocalImageDispatchBlocked("Local image candidate already dispatched; cache or review may resume, never another POST")
        if state["remaining_new_requests"] <= 0:
            raise LocalImageDispatchBlocked("Bounded local image action exhausted: three new requests remain consumed across resume")
        creative_hash = creative_workflow_digest(workflow)
        scene_stage = scope.stage.rsplit('/', 1)[0]
        for prior in state['dispatches']:
            if (prior['status'] in {'completed', 'rejected'} and prior['stage'].rsplit('/', 1)[0] == scene_stage
                    and _prior_creative_digest(prior) == creative_hash):
                raise LocalImageDispatchBlocked(
                    'local_image_retry_needs_revision: this scene has already dispatched or rejected the same creative workflow; '
                    'changing only seed or output filename is not a correction. Inspect its QC and revise the request.',
                    category='local_image_retry_needs_revision')
        now = utc_now_iso()
        db.execute("INSERT INTO production_local_image_dispatches VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (scope.job_id, scope.stage, scope.fingerprint, action["digest"], workflow_digest(workflow),
                    "reserved", None, json_dumps({"admission": "durably_reserved_before_post",
                    "creative_workflow_hash": creative_hash}), now, now))
        result = _write_status(db, scope.settings, action)
        _event(db, scope.job_id, scope.stage, "local_image_dispatch_reserved",
               {"action_digest": action["digest"], "used_new_requests": result["used_new_requests"],
                "remaining_new_requests": result["remaining_new_requests"]})
        db.commit()
        return result


def record_local_image_dispatch(scope: LocalImageDispatchScope, *, status: str, prompt_id: str | None = None,
                                result=None, error_kind: str | None = None,
                                error_detail: str | None = None) -> dict:
    if status not in {"submitted", "unknown", "rejected", "completed"}:
        raise ValueError("Unsupported local image dispatch outcome")
    with connect(scope.settings) as db:
        db.execute("BEGIN IMMEDIATE")
        ProductionOrderStore._owner(db, scope.job_id, scope.worker_token)
        action = _authorization(db, scope.settings, scope.job_id)
        row = db.execute("SELECT * FROM production_local_image_dispatches WHERE job_id=? AND stage=?",
                         (scope.job_id, scope.stage)).fetchone()
        if not action or action["digest"] != scope.action_digest or not row or row["fingerprint"] != scope.fingerprint:
            raise LocalImageDispatchBlocked("Local image dispatch audit binding mismatch")
        if row["status"] == "completed":
            raise LocalImageDispatchBlocked("Completed local image dispatch is immutable")
        evidence = json.loads(row["evidence_json"])
        if prompt_id and row["prompt_id"] and row["prompt_id"] != prompt_id:
            raise LocalImageDispatchBlocked("Local image dispatch prompt identity changed")
        actual_prompt = prompt_id or row["prompt_id"]
        if status == "submitted" and not actual_prompt:
            raise LocalImageDispatchBlocked("Submitted local image dispatch needs an actual provider prompt_id")
        if status == "completed":
            if result is None or str(result.prompt_id) != actual_prompt:
                raise LocalImageDispatchBlocked("Completed local image dispatch needs matching generation output")
            output, receipt = Path(result.output_path), Path(result.evidence_path)
            generation = json.loads(receipt.read_text("utf-8"))
            if (not output.is_file() or not output.stat().st_size or generation.get("status") != "verified"
                    or generation.get("prompt_id") != actual_prompt or generation.get("artifact_sha256") != file_hash(output)
                    or generation.get("workflow_sha256") != row["workflow_hash"]):
                raise LocalImageDispatchBlocked("Local image generation output/evidence integrity failed")
            evidence.update(output_path=str(output), output_sha256=file_hash(output),
                            generation_evidence=str(receipt), generation_evidence_sha256=file_hash(receipt))
        if error_kind:
            evidence["error_kind"] = error_kind
        if error_detail:
            evidence["error_detail"] = str(error_detail)[:1_000]
        db.execute("UPDATE production_local_image_dispatches SET status=?,prompt_id=?,evidence_json=?,updated_at=? "
                   "WHERE job_id=? AND stage=?", (status, actual_prompt, json_dumps(evidence), utc_now_iso(), scope.job_id, scope.stage))
        result_state = _write_status(db, scope.settings, action)
        _event(db, scope.job_id, scope.stage, "local_image_dispatch_" + status,
               {"action_digest": action["digest"], "prompt_id": actual_prompt,
                "used_new_requests": result_state["used_new_requests"], "remaining_new_requests": result_state["remaining_new_requests"]})
        db.commit()
        return result_state
