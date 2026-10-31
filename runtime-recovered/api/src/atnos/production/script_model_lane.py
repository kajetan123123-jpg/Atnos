"""Explicit, immutable job-scoped lane for local script authoring."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import httpx

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, _same_process
from atnos.production.project_profile import content_hash


MODEL = "qwen3.5:9b"
ENDPOINT = "http://127.0.0.1:11436"
MODEL_MANIFEST = Path(r"E:\Atnos\Models\ollama\manifests\registry.ollama.ai\library\qwen3.5\9b")
EXPECTED_MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
EXECUTION_SCOPE = "normal_produce_script_authoring"
_TABLE = "production_script_model_lanes"


def _block(message: str) -> ProductionBlocked:
    return ProductionBlocked(f"script_model_lane: {message}")


def _manifest_digest(path: Path = MODEL_MANIFEST) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise _block("the pinned local Ollama manifest is unavailable") from exc


def _installed_digest(path: Path = MODEL_MANIFEST) -> str:
    """Validate manifest and all referenced layer sizes without hashing 6.6GB per load."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        layers = manifest["layers"]
        if not isinstance(layers, list) or not isinstance(manifest.get("config"), dict):
            raise ValueError("invalid manifest structure")
        model_layer = next(layer for layer in layers if layer.get("mediaType") == "application/vnd.ollama.image.model")
        if not model_layer:
            raise ValueError("missing model layer")
        # Ollama stores manifest digests as `sha256:<hex>` but blob files as
        # `blobs/sha256-<hex>`; a colon is not a filename separator on Windows.
        model_store = path.parents[4]
        if model_store.name.casefold() != "ollama" or model_store.parent.name.casefold() != "models":
            raise ValueError("manifest is outside the pinned Ollama model store")
        blobs = model_store / "blobs"
        for layer in [manifest["config"], *layers]:
            if not isinstance(layer, dict):
                raise ValueError("invalid layer record")
            digest = str(layer["digest"])
            match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest)
            if not match:
                raise ValueError("invalid layer digest")
            size = layer["size"]
            if type(size) is not int or size < 0:
                raise ValueError("invalid layer size")
            blob = blobs / f"sha256-{match.group(1)}"
            if not blob.is_file() or blob.stat().st_size != size:
                raise ValueError("missing or changed layer")
        # The immutable model identity is the exact installed manifest bytes.
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, StopIteration, json.JSONDecodeError) as exc:
        raise _block("the installed Ollama manifest or one of its layer files is invalid") from exc


def _runtime_model_available(model: str, model_digest: str, endpoint: str = ENDPOINT) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=httpx.Timeout(2.0, connect=1.0), trust_env=False,
                          follow_redirects=False) as client:
            response = client.get(endpoint.rstrip("/") + "/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _block("the dedicated local Ollama inventory is unavailable") from exc
    models = payload.get("models") if isinstance(payload, dict) else None
    match = next((row for row in models or [] if isinstance(row, dict) and row.get("name") == model), None)
    if not match or str(match.get("digest", "")).removeprefix("sha256:").casefold() != model_digest.casefold():
        raise _block("the dedicated Ollama endpoint does not expose the pinned installed model")
    return {"available": True, "endpoint": endpoint, "model": model, "model_digest": model_digest}


def _order_binding(settings, job_id: str, *, require_idle: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    store = ProductionOrderStore(settings)
    order = store.get(job_id)
    request = order["request"]
    if request.get("project") != "wealth_blueprint" or request.get("delivery_mode") != "local_only":
        raise _block("only a Wealth Blueprint local_only order can use this lane")
    if Path(order["artifact_dir"]).resolve().drive.casefold() != "e:" or Path(settings.data_dir).resolve().drive.casefold() != "e:":
        raise _block("job and registration storage must remain on E:")
    if order.get("status") in {"ready", "script_ready", "cancelled"}:
        raise _block("the order is already completed or closed")
    if require_idle:
        if order.get("status") not in {"queued", "blocked"} or order.get("worker_live"):
            raise OrderConflict("A script model lane can only be registered for a stopped, idle order.")
        forbidden = ("voice", "edit", "remotion", "video", "assembly", "render", "package", "upload")
        if any(row.get("status") == "done" and
               (row.get("stage") in {"thumbnail", "qa"} or str(row.get("stage", "")).casefold().startswith(forbidden))
               for row in order.get("checkpoints", [])):
            raise _block("voice or finished-media work has already completed")
    actual_request_hash = content_hash(request)
    actual_profile_hash = content_hash(order["profile"])
    with connect(settings) as db:
        hashes = db.execute("SELECT request_hash,profile_hash FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
    if (hashes is None or hashes["request_hash"] != actual_request_hash
            or order.get("profile_hash") != actual_profile_hash):
        raise _block("the stored request or frozen profile hash does not match its contents")
    return order, {
        "job_id": job_id,
        "request_hash": actual_request_hash,
        "frozen_profile_hash": actual_profile_hash,
    }


def _registration_path(settings, job_id: str) -> Path:
    return ProductionOrderStore(settings).context(job_id).artifact_dir / "script" / "script_model_lane.json"


def _canonical_body(settings, job_id: str, *, model: str, reason: str, expected_model_digest: str | None,
                    require_idle: bool) -> dict[str, Any]:
    if model != MODEL:
        raise _block("only the explicitly supported qwen3.5:9b model is allowed")
    if not isinstance(reason, str) or len(reason.strip()) < 12:
        raise _block("a specific registration reason is required")
    _, binding = _order_binding(settings, job_id, require_idle=require_idle)
    digest = _installed_digest()
    if expected_model_digest and expected_model_digest.removeprefix("sha256:").casefold() != digest.casefold():
        raise _block("the installed model digest does not match the caller's pin")
    if digest != EXPECTED_MODEL_DIGEST:
        raise _block("the installed model manifest differs from the approved local pin")
    return {**binding, "model": MODEL, "model_digest": digest, "endpoint": ENDPOINT,
            "execution_scope": EXECUTION_SCOPE, "reason": reason.strip(), "authorized": True}


def _decode_record(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        lane = record["lane"]
        if not isinstance(lane, dict) or lane.get("digest") != content_hash({k: v for k, v in lane.items() if k != "digest"}):
            raise ValueError("digest mismatch")
        return record
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise _block("the durable job registration is missing or tampered") from exc


def _db_lane(settings, job_id: str) -> str | None:
    with connect(settings) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone():
            return None
        row = db.execute(f"SELECT payload_json FROM {_TABLE} WHERE job_id=?", (job_id,)).fetchone()
    return row["payload_json"] if row else None


def register_script_model_lane(settings, job_id: str, *, model: str, reason: str,
                               authorized: bool = False, expected_model_digest: str | None = None) -> dict[str, Any]:
    """Persist an explicit write-once lane; this does not mutate defaults or start inference."""
    if authorized is not True:
        raise _block("explicit authorization is required")
    body = _canonical_body(settings, job_id, model=model, reason=reason,
                           expected_model_digest=expected_model_digest, require_idle=True)
    lane = {**body, "digest": content_hash(body)}
    record = {"lane": lane}
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = _registration_path(settings, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT j.status,j.artifact_dir,o.request_json,o.profile_json,o.profile_hash,o.lease_pid,o.lease_process_started "
                           "FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?", (job_id,)).fetchone()
        if order is None:
            raise KeyError("Production order not found")
        if _same_process(order["lease_pid"], order["lease_process_started"]):
            raise OrderConflict("A script model lane cannot be registered while a worker is active.")
        if order["status"] not in {"queued", "blocked"}:
            raise _block("the order is no longer idle")
        db.execute(f"CREATE TABLE IF NOT EXISTS {_TABLE}(job_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)")
        existing = db.execute(f"SELECT payload_json FROM {_TABLE} WHERE job_id=?", (job_id,)).fetchone()
        if existing and existing["payload_json"] != encoded:
            raise OrderConflict("An existing script model lane registration is immutable.")
        if path.exists() and path.read_text(encoding="utf-8") != encoded:
            raise OrderConflict("An existing script model lane receipt is immutable.")
        if not path.exists():
            # O_EXCL ensures a concurrent registration cannot replace the receipt.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError as exc:
                raise OrderConflict("A concurrent script model lane registration won the write-once receipt.") from exc
        if not existing:
            db.execute(f"INSERT INTO {_TABLE}(job_id,payload_json,created_at) VALUES(?,?,?)",
                       (job_id, encoded, utc_now_iso()))
        db.commit()
    return lane


def load_script_model_lane(settings, job_id: str, *, verify_runtime: bool = True) -> dict[str, Any] | None:
    """Load and revalidate an existing opt-in. Unregistered jobs perform no network access."""
    path = _registration_path(settings, job_id)
    db_payload = _db_lane(settings, job_id)
    if not path.exists() and db_payload is None:
        return None
    if not path.is_file() or db_payload is None:
        raise _block("the durable receipt and database binding are incomplete")
    record = _decode_record(path)
    if json.loads(db_payload) != record:
        raise _block("the database binding differs from the immutable receipt")
    lane = record["lane"]
    if (lane.get("job_id") != job_id or lane.get("model") != MODEL or lane.get("endpoint") != ENDPOINT
            or lane.get("execution_scope") != EXECUTION_SCOPE or lane.get("authorized") is not True):
        raise _block("the registered model or execution scope is unsupported")
    _, binding = _order_binding(settings, job_id, require_idle=False)
    if any(lane.get(key) != value for key, value in binding.items()):
        raise _block("the job request or frozen profile changed after registration")
    current_digest = _installed_digest()
    if lane.get("model_digest") != current_digest:
        raise _block("the installed local model changed after registration")
    if verify_runtime:
        _runtime_model_available(MODEL, current_digest, ENDPOINT)
    return lane
