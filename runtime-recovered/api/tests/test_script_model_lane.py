from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from atnos.intelligence.storage import connect
from atnos.production import script_model_lane as lane
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore
from atnos.production.project_profile import ProductionOrder, content_hash
from test_support import build_test_settings


_TEST_ROOT = Path(r"E:\Atnos\App\runtime-recovered\api\tests\.script_model_lane_runs")
_MODEL_PIN = lane.EXPECTED_MODEL_DIGEST


@pytest.fixture
def order():
    root = _TEST_ROOT / uuid4().hex
    root.mkdir(parents=True)
    settings = build_test_settings(root)
    with connect(settings) as db:
        db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')")
        db.commit()
    store = ProductionOrderStore(settings)
    job = store.create(ProductionOrder(project="Wealth Blueprint", topic="A measured local script trial",
                                       delivery_mode="local_only"))["id"]
    try:
        yield settings, store, job
    finally:
        resolved = root.resolve()
        if resolved.drive.casefold() == "e:" and resolved.is_relative_to(_TEST_ROOT.resolve()):
            shutil.rmtree(resolved, ignore_errors=True)


def _pin_model(monkeypatch, digest=_MODEL_PIN):
    monkeypatch.setattr(lane, "_installed_digest", lambda: digest)


def _register(settings, job):
    return lane.register_script_model_lane(settings, job, model=lane.MODEL,
                                           reason="Approved bounded local authoring trial for this order.",
                                           authorized=True, expected_model_digest=_MODEL_PIN)


def test_unregistered_load_returns_none_before_network(order, monkeypatch):
    settings, _, job = order
    monkeypatch.setattr(lane, "_runtime_model_available", lambda *a, **k: pytest.fail("must not inspect runtime"))
    monkeypatch.setattr(lane, "_installed_digest", lambda *a, **k: pytest.fail("must not inspect model files"))
    assert lane.load_script_model_lane(settings, job) is None


def test_registration_is_explicit_immutable_job_scoped_and_preserves_profile_budget(order, monkeypatch):
    settings, store, job = order
    _pin_model(monkeypatch)
    before = store.get(job)
    with pytest.raises(ProductionBlocked, match="explicit authorization"):
        lane.register_script_model_lane(settings, job, model=lane.MODEL, reason="A sufficiently detailed trial reason.")
    first = _register(settings, job)
    second = _register(settings, job)
    after = store.get(job)
    assert first["digest"] == second["digest"] == content_hash({k: v for k, v in first.items() if k != "digest"})
    assert first["model"] == lane.MODEL and first["endpoint"] == "http://127.0.0.1:11436"
    assert first["execution_scope"] == "normal_produce_script_authoring"
    assert (before["profile_hash"], before["provider_calls"], before["reserved_cost_units"]) == (
        after["profile_hash"], after["provider_calls"], after["reserved_cost_units"])
    with pytest.raises(OrderConflict, match="immutable"):
        lane.register_script_model_lane(settings, job, model=lane.MODEL,
                                        reason="A different approved reason with changed binding.", authorized=True)


def test_receipt_tampering_and_database_receipt_mismatch_block(order, monkeypatch):
    settings, store, job = order
    _pin_model(monkeypatch)
    lane_data = _register(settings, job)
    path = lane._registration_path(settings, job)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["lane"]["endpoint"] = "http://127.0.0.1:11434"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ProductionBlocked, match="tampered"):
        lane.load_script_model_lane(settings, job, verify_runtime=False)
    path.write_text(json.dumps({"lane": lane_data}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with connect(settings) as db:
        db.execute(f"UPDATE {lane._TABLE} SET payload_json=? WHERE job_id=?", ('{}', job))
        db.commit()
    with pytest.raises(ProductionBlocked, match="database binding"):
        lane.load_script_model_lane(settings, job, verify_runtime=False)


def test_changed_model_request_and_profile_are_terminal_and_runtime_is_read_only(order, monkeypatch):
    settings, store, job = order
    _pin_model(monkeypatch)
    _register(settings, job)
    runtime_calls = []
    monkeypatch.setattr(lane, "_runtime_model_available", lambda *a, **k: runtime_calls.append((a, k)) or {"available": True})
    assert lane.load_script_model_lane(settings, job)["model_digest"] == _MODEL_PIN
    assert len(runtime_calls) == 1

    monkeypatch.setattr(lane, "_installed_digest", lambda: "a" * 64)
    with pytest.raises(ProductionBlocked, match="installed local model changed"):
        lane.load_script_model_lane(settings, job, verify_runtime=False)

    _pin_model(monkeypatch)
    with connect(settings) as db:
        row = db.execute("SELECT request_json FROM pipeline_job_orders WHERE job_id=?", (job,)).fetchone()
        request = json.loads(row["request_json"])
        request["direction"] = "Changed after opt-in"
        db.execute("UPDATE pipeline_job_orders SET request_json=?,request_hash=? WHERE job_id=?",
                   (json.dumps(request), content_hash(request), job))
        db.commit()
    with pytest.raises(ProductionBlocked, match="request or frozen profile changed"):
        lane.load_script_model_lane(settings, job, verify_runtime=False)


def test_profile_content_cannot_change_behind_its_stored_hash(order, monkeypatch):
    settings, _, job = order
    _pin_model(monkeypatch)
    _register(settings, job)
    with connect(settings) as db:
        row = db.execute("SELECT profile_json FROM pipeline_job_orders WHERE job_id=?", (job,)).fetchone()
        profile = json.loads(row["profile_json"])
        profile["production"]["voice"]["provider"] = "elevenlabs-mutated"
        db.execute("UPDATE pipeline_job_orders SET profile_json=? WHERE job_id=?", (json.dumps(profile), job))
        db.commit()
    with pytest.raises(ProductionBlocked, match="stored request or frozen profile hash"):
        lane.load_script_model_lane(settings, job, verify_runtime=False)


def test_registration_rejects_wrong_model_nonlocal_order_and_completed_media(order, monkeypatch):
    settings, store, job = order
    _pin_model(monkeypatch)
    with pytest.raises(ProductionBlocked, match="only the explicitly supported"):
        lane.register_script_model_lane(settings, job, model="qwen3:14b", reason="A supported authoring pilot.", authorized=True)
    with connect(settings) as db:
        db.execute("UPDATE pipeline_stage_checkpoints SET status='done' WHERE job_id=? AND stage='voice'", (job,))
        db.execute("INSERT INTO pipeline_stage_checkpoints(job_id,stage,status,input_hash,attempts,updated_at) VALUES(?, 'voice', 'done', 'x', 1, 'now')", (job,))
        db.commit()
    with pytest.raises(ProductionBlocked, match="voice or finished-media"):
        _register(settings, job)


def test_runtime_inventory_checks_exact_endpoint_and_manifest_digest(monkeypatch):
    import httpx

    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            calls.append(("get", url))
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, json={"models": [{"name": lane.MODEL, "digest": _MODEL_PIN}]})

    monkeypatch.setattr(lane.httpx, "Client", FakeClient)
    assert lane._runtime_model_available(lane.MODEL, _MODEL_PIN)["available"] is True
    assert calls[0][1]["trust_env"] is False and calls[0][1]["follow_redirects"] is False
    assert calls[1][1] == "http://127.0.0.1:11436/api/tags"


def test_ollama_manifest_resolves_native_sha256_blob_names(order):
    settings, _, _ = order
    model_store = settings.app_root / "models" / "ollama"
    manifest_path = model_store / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5" / "9b"
    manifest_path.parent.mkdir(parents=True)
    blob_dir = model_store / "blobs"
    blob_dir.mkdir(parents=True)
    config_hash, model_hash = "a" * 64, "b" * 64
    (blob_dir / f"sha256-{config_hash}").write_bytes(b"cfg")
    (blob_dir / f"sha256-{model_hash}").write_bytes(b"weights")
    manifest = {
        "schemaVersion": 2,
        "config": {"digest": f"sha256:{config_hash}", "size": 3},
        "layers": [{"mediaType": "application/vnd.ollama.image.model",
                    "digest": f"sha256:{model_hash}", "size": 7}],
    }
    encoded = json.dumps(manifest, separators=(",", ":"))
    manifest_path.write_text(encoded, encoding="utf-8")
    assert manifest_path.parents[4] == model_store
    assert lane._installed_digest(manifest_path) == hashlib.sha256(encoded.encode()).hexdigest()

    (blob_dir / f"sha256-{model_hash}").write_bytes(b"short")
    with pytest.raises(ProductionBlocked, match="manifest or one of its layer files is invalid"):
        lane._installed_digest(manifest_path)
