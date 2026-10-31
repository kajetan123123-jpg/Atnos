from __future__ import annotations

import json
import os

import psutil
import pytest
from fastapi.testclient import TestClient

from atnos.api.main import create_app
from atnos.intelligence.storage import connect
from atnos.production.order_recovery import recover_interrupted_orders
from test_production_order_store import complete_file, create, store


def _checkpoints(settings, job_id):
    with connect(settings) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? ORDER BY stage", (job_id,)
        )]


def _mark_dead_lease(settings, job_id):
    with connect(settings) as db:
        db.execute(
            """UPDATE pipeline_job_orders
               SET lease_token='dead-token', lease_pid=?, lease_process_started=?
               WHERE job_id=?""",
            (os.getpid(), psutil.Process().create_time() + 100, job_id),
        )
        db.commit()


def test_dead_worker_is_marked_interrupted_without_restarting_or_resetting_work(store, monkeypatch):
    job_id = create(store)["id"]
    monkeypatch.setattr(
        "atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
        lambda **_: {"blocked": False},
    )
    with store.lease(job_id) as token:
        complete_file(store, job_id, token, "preflight")
        store.begin(job_id, token, "remotion_render", {"manifest": "v1"})
    with connect(store.settings) as db:
        db.execute(
            """UPDATE pipeline_job_orders
               SET lease_token='dead-token', lease_pid=?, lease_process_started=?,
                   reserved_cost_units=250000, provider_calls=3
               WHERE job_id=?""",
            (os.getpid(), psutil.Process().create_time() + 100, job_id),
        )
        db.commit()

    checkpoints_before = _checkpoints(store.settings, job_id)
    result = recover_interrupted_orders(store.settings)
    state = store.get(job_id)

    assert result["recovered_job_ids"] == [job_id]
    assert state["status"] == "blocked"
    assert state["current_step"] == "remotion_render"
    assert json.loads(state["error"])["category"] == "worker_interrupted"
    assert state["worker_live"] is False
    assert state["can_resume"] is True
    assert state["reserved_cost_units"] == 250000
    assert state["provider_calls"] == 3
    assert _checkpoints(store.settings, job_id) == checkpoints_before
    assert next(row for row in state["checkpoints"] if row["stage"] == "remotion_render")["attempts"] == 1
    assert state["history"][-1]["event"] == "worker_interrupted"
    with connect(store.settings) as db:
        lease = db.execute(
            "SELECT lease_token,lease_pid,lease_process_started FROM pipeline_job_orders WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert tuple(lease) == (None, None, None)
    assert recover_interrupted_orders(store.settings)["recovered_count"] == 0


def test_live_worker_lease_is_preserved(store):
    job_id = create(store)["id"]
    with store.lease(job_id) as token:
        store.begin(job_id, token, "preflight", {})
        result = recover_interrupted_orders(store.settings)
        state = store.get(job_id)

        assert result == {"recovered_count": 0, "recovered_job_ids": [], "preserved_live_count": 1}
        assert state["status"] == "running"
        assert state["worker_live"] is True
        assert state["can_resume"] is False
        with connect(store.settings) as db:
            assert db.execute(
                "SELECT lease_token FROM pipeline_job_orders WHERE job_id=?", (job_id,)
            ).fetchone()[0] == token
        assert not any(event["event"] == "worker_interrupted" for event in state["history"])


def test_running_stage_without_pid_is_recovered_without_retrying_dispatch(store, monkeypatch):
    job_id = create(store)["id"]
    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate", lambda **_: {"blocked": False})
    # Mirrors the real interrupted worker: finally released its lease while
    # a dispatched image stage retained its in-flight checkpoint.
    with store.lease(job_id) as token:
        store.begin(job_id, token, "assets/image_local/flux2-trial/s01/test", {})
    before = store.get(job_id)
    checkpoints = _checkpoints(store.settings, job_id)
    result = recover_interrupted_orders(store.settings, job_ids=[job_id])
    after = store.get(job_id)
    assert result["recovered_job_ids"] == [job_id]
    assert after["status"] == "blocked"
    assert json.loads(after["error"])["category"] == "worker_interrupted"
    assert after["current_step"] == before["current_step"]
    assert after["provider_calls"] == before["provider_calls"]
    assert after["reserved_cost_units"] == before["reserved_cost_units"]
    assert _checkpoints(store.settings, job_id) == checkpoints
    assert after["history"][-1]["event"] == "worker_interrupted"
    assert recover_interrupted_orders(store.settings, job_ids=[job_id])["recovered_count"] == 0


def test_lost_worker_after_flux_dispatch_blocks_resume_and_preserves_evidence(store, monkeypatch):
    from atnos.production.project_profile import ProductionOrder
    job_id = store.create(ProductionOrder(topic="Isolated unknown local generation", delivery_mode="local_only"))["id"]
    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate", lambda **_: {"blocked": False})
    digest = "a" * 16
    with store.lease(job_id) as token:
        store.begin(job_id, token, "assets/image_local/flux2-trial/s01/" + digest, {})
    dispatch = store.context(job_id).artifact_dir / "assets/image_local/flux_trial" / digest / "dispatch.json"
    dispatch.parent.mkdir(parents=True)
    dispatch.write_text('{"status":"queued","prompt_id":"existing"}', encoding="utf8")
    checkpoints = _checkpoints(store.settings, job_id)
    recover_interrupted_orders(store.settings, job_ids=[job_id])
    state = store.get(job_id)
    assert state["status"] == "blocked"
    assert json.loads(state["error"])["category"] == "execution_outcome_unknown"
    assert not state["can_resume"]
    assert not state["can_prepare_script"]
    assert json.loads(dispatch.read_text("utf8"))["prompt_id"] == "existing"
    assert _checkpoints(store.settings, job_id) == checkpoints
    from atnos.production.order_dispatch import ProductionOrderDispatcher
    from atnos.production.order_store import ProductionBlocked
    from atnos.production.order_autonomy import enable, _recover_job
    def no_process(*args, **kwargs):
        pytest.fail("Unknown dispatch outcome must not spawn another worker")
    monkeypatch.setattr("atnos.production.order_dispatch.subprocess.Popen", no_process)
    with pytest.raises(ProductionBlocked, match="wynik wysłanej operacji"):
        ProductionOrderDispatcher(store.settings).dispatch(job_id)
    enable(store.settings, job_id, reason="Isolated regression for unknown local generation outcome")
    assert _recover_job(store.settings, store, {"job_id": job_id}) == []


def test_dispatched_worker_that_dies_before_lease_is_recovered(store):
    job_id = create(store)["id"]
    store.record_dispatched_worker(job_id, os.getpid())
    with connect(store.settings) as db:
        db.execute(
            """UPDATE pipeline_job_orders
               SET lease_process_started=?, reserved_cost_units=9000, provider_calls=2
               WHERE job_id=?""",
            (psutil.Process().create_time() + 100, job_id),
        )
        db.commit()
    before = _checkpoints(store.settings, job_id)

    result = recover_interrupted_orders(store.settings)
    state = store.get(job_id)

    assert result["recovered_job_ids"] == [job_id]
    assert state["status"] == "blocked"
    assert state["current_step"] == "worker_starting"
    assert state["can_resume"] is True
    assert state["reserved_cost_units"] == 9000
    assert state["provider_calls"] == 2
    assert _checkpoints(store.settings, job_id) == before
    assert state["history"][-1]["event"] == "worker_interrupted"
    with connect(store.settings) as db:
        lease = db.execute(
            "SELECT lease_token,lease_pid,lease_process_started FROM pipeline_job_orders WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert tuple(lease) == (None, None, None)


def test_live_dispatched_worker_is_preserved_before_lease(store):
    job_id = create(store)["id"]
    store.record_dispatched_worker(job_id, os.getpid())

    result = recover_interrupted_orders(store.settings)
    state = store.get(job_id)

    assert result == {"recovered_count": 0, "recovered_job_ids": [], "preserved_live_count": 1}
    assert state["status"] == "queued"
    assert state["current_step"] == "worker_starting"
    assert state["worker_live"] is True
    assert state["can_resume"] is False
    assert not any(event["event"] == "worker_interrupted" for event in state["history"])


def test_queue_only_order_without_recorded_worker_is_not_recovered(store):
    normal_job = create(store)["id"]
    starting_without_lease = create(store)["id"]
    with connect(store.settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET current_step='worker_starting' WHERE id=?",
            (starting_without_lease,),
        )
        db.commit()

    result = recover_interrupted_orders(store.settings)

    assert result["recovered_count"] == 0
    for job_id in (normal_job, starting_without_lease):
        state = store.get(job_id)
        assert state["status"] == "queued"
        assert not any(event["event"] == "worker_interrupted" for event in state["history"])


def test_api_startup_runs_order_recovery(store):
    job_id = create(store)["id"]
    with store.lease(job_id) as token:
        store.begin(job_id, token, "preflight", {})
    _mark_dead_lease(store.settings, job_id)
    early_job = create(store)["id"]
    store.record_dispatched_worker(early_job, os.getpid())
    _mark_dead_lease(store.settings, early_job)

    app = create_app(settings=store.settings)
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            state = store.get(job_id)
            assert state["status"] == "blocked"
            assert state["can_resume"] is True
            assert state["history"][-1]["event"] == "worker_interrupted"
            early_state = store.get(early_job)
            assert early_state["status"] == "blocked"
            assert early_state["current_step"] == "worker_starting"
            assert early_state["can_resume"] is True

            # The API remains up: the detail and list reads each reconcile only
            # the order IDs they expose, without starting another worker.
            detail_job = create(store)["id"]
            with store.lease(detail_job) as token:
                store.begin(detail_job, token, "preflight", {})
            _mark_dead_lease(store.settings, detail_job)
            detail = client.get(f"/api/production/orders/{detail_job}")
            assert detail.status_code == 200
            assert detail.json()["status"] == "blocked"
            assert detail.json()["can_resume"] is True

            list_job = create(store)["id"]
            with store.lease(list_job) as token:
                store.begin(list_job, token, "preflight", {})
            _mark_dead_lease(store.settings, list_job)
            orders = client.get("/api/production/orders?limit=5").json()["orders"]
            assert next(item for item in orders if item["id"] == list_job)["status"] == "blocked"

            live_job = create(store)["id"]
            with store.lease(live_job) as token:
                store.begin(live_job, token, "preflight", {})
                live = client.get(f"/api/production/orders/{live_job}")
                assert live.json()["status"] == "running"
                assert live.json()["worker_live"] is True
                orders = client.get("/api/production/orders?limit=5").json()["orders"]
                assert next(item for item in orders if item["id"] == live_job)["status"] == "running"
    finally:
        app.state.database.dispose()
