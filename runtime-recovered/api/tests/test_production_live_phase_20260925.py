"""Resume must report actual work without hiding failure history or billing again."""
import os
import pytest

from atnos.production.order_store import OrderConflict
from test_production_order_store import create, store, complete_file


def test_resume_start_and_cached_phase_preserve_failure_history_and_budget(store, monkeypatch):
    # This fixture stores a generic cache value, not a financial script/ledger.
    # Isolate the independent evidence gate; its production behavior is covered
    # by the financial evidence tests, not this phase/lease/accounting test.
    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
                        lambda **_: {"blocked": False})
    job = create(store)["id"]
    with store.lease(job) as token:
        cached = complete_file(store, job, token, "voice")
        store.fail(job, token, "qa", category="dependency_unavailable", message="model offline")
    before = store.get(job)
    store.record_dispatched_worker(job, os.getpid())
    launching = store.get(job)
    assert launching["status"] == "queued"
    assert launching["current_step"] == "worker_starting"
    assert launching["error"] is None
    with store.lease(job) as token:
        store.set_phase(job, token, "voice_generation")
        assert store.begin(job, token, "voice", {"revision": 1}) == cached
        running = store.get(job)
        assert running["status"] == "running"
        assert running["current_step"] == "voice_generation"
        assert running["worker_live"] and not running["can_resume"]
        assert running["provider_calls"] == before["provider_calls"]
        assert running["reserved_cost_units"] == before["reserved_cost_units"]
        assert any(e["detail"].get("message") == "model offline" for e in running["history"])


def test_image_generation_is_a_live_order_phase_without_accounting_side_effects(store):
    job = create(store)["id"]
    before = store.get(job)
    with store.lease(job) as token:
        store.set_phase(job, token, "image_generation")
        running = store.get(job)
        assert running["status"] == "running"
        assert running["current_step"] == "image_generation"
        assert running["worker_live"]
        assert running["provider_calls"] == before["provider_calls"]
        assert running["reserved_cost_units"] == before["reserved_cost_units"]


def test_phase_requires_live_execution_lease(store):
    job = create(store)["id"]
    with store.lease(job) as token:
        with pytest.raises(OrderConflict):
            store.set_phase(job, "other-worker", "rendering")
        with pytest.raises(ValueError):
            store.set_phase(job, token, "imaginary")
    with pytest.raises(OrderConflict):
        store.set_phase(job, token, "rendering")
