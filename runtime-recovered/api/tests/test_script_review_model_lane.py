from __future__ import annotations

import json

import pytest

from atnos.intelligence.storage import connect
from atnos.production import script_model_lane as author_lane
from atnos.production import script_review_model_lane as reviewer_lane
from atnos.production.order_store import OrderConflict, ProductionBlocked
from test_script_model_lane import _pin_model, order


def _register(settings, job):
    return reviewer_lane.register_script_review_model_lane(
        settings, job, model=reviewer_lane.MODEL,
        reason="Approved independent reviewer trial for this order.",
        authorized=True, expected_model_digest=reviewer_lane.shared_lane.EXPECTED_MODEL_DIGEST)


def test_reviewer_optin_is_separate_from_author_lane_and_defaults(order, monkeypatch):
    settings, store, job = order
    _pin_model(monkeypatch)
    before = store.get(job)
    with pytest.raises(ProductionBlocked, match="explicit reviewer authorization"):
        reviewer_lane.register_script_review_model_lane(settings, job, model=reviewer_lane.MODEL,
                                                        reason="A sufficiently specific review reason.")
    registered = _register(settings, job)
    assert reviewer_lane.load_script_review_model_lane(settings, job, verify_runtime=False) == registered
    assert author_lane.load_script_model_lane(settings, job, verify_runtime=False) is None
    after = store.get(job)
    assert (before["profile_hash"], before["provider_calls"], before["reserved_cost_units"]) == (
        after["profile_hash"], after["provider_calls"], after["reserved_cost_units"])
    assert registered["execution_scope"] == "normal_produce_script_review"
    assert registered["endpoint"] == "http://127.0.0.1:11436"


def test_reviewer_registration_is_write_once(order, monkeypatch):
    settings, _, job = order
    _pin_model(monkeypatch)
    first = _register(settings, job)
    assert _register(settings, job)["digest"] == first["digest"]
    with pytest.raises(OrderConflict, match="immutable"):
        reviewer_lane.register_script_review_model_lane(
            settings, job, model=reviewer_lane.MODEL,
            reason="A changed reason for the independent reviewer pilot.", authorized=True)


def test_reviewer_receipt_tampering_or_changed_pin_blocks(order, monkeypatch):
    settings, _, job = order
    _pin_model(monkeypatch)
    reviewer = _register(settings, job)
    path = reviewer_lane._receipt_path(settings, job)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["lane"]["execution_scope"] = "normal_produce_script_authoring"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ProductionBlocked, match="tampered"):
        reviewer_lane.load_script_review_model_lane(settings, job, verify_runtime=False)

    path.write_text(json.dumps({"lane": reviewer}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(reviewer_lane.shared_lane, "_installed_digest", lambda: "f" * 64)
    with pytest.raises(ProductionBlocked, match="installed reviewer model changed"):
        reviewer_lane.load_script_review_model_lane(settings, job, verify_runtime=False)


def test_reviewer_receipt_does_not_allow_completed_media_or_wrong_model(order, monkeypatch):
    settings, _, job = order
    _pin_model(monkeypatch)
    with pytest.raises(ProductionBlocked, match="only the pinned"):
        reviewer_lane.register_script_review_model_lane(
            settings, job, model="qwen3:14b", reason="Try a different reviewer model.", authorized=True)
    with connect(settings) as db:
        db.execute("INSERT INTO pipeline_stage_checkpoints(job_id,stage,status,input_hash,attempts,updated_at) "
                   "VALUES(?, 'voice', 'done', 'x', 1, 'now')", (job,))
        db.commit()
    with pytest.raises(ProductionBlocked, match="voice or finished-media"):
        _register(settings, job)


def test_unregistered_reviewer_load_does_not_touch_runtime(order, monkeypatch):
    settings, _, job = order
    monkeypatch.setattr(reviewer_lane.shared_lane, "_installed_digest", lambda: pytest.fail("unexpected model scan"))
    monkeypatch.setattr(reviewer_lane.shared_lane, "_runtime_model_available", lambda *a, **k: pytest.fail("unexpected network"))
    assert reviewer_lane.load_script_review_model_lane(settings, job) is None
