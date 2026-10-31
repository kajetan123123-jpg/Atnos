from __future__ import annotations

from pathlib import Path
import asyncio
from types import SimpleNamespace
import os
import psutil

import pytest

from atnos.production.order_dispatch import ProductionOrderDispatcher
from atnos.production.order_store import ProductionBlocked
from test_production_order_store import create, store


def test_dispatch_starts_replaceable_worker_process(store, monkeypatch):
    job = create(store)["id"]
    calls = []

    class FakeProcess:
        pid = os.getpid()

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr("atnos.production.order_dispatch.subprocess.Popen", fake_popen)
    result = ProductionOrderDispatcher(store.settings).dispatch(job)

    assert result["id"] == job
    assert result["worker_requested"] is True
    assert result["launch_pid"] == os.getpid()
    assert result['worker_live'] is True
    assert result['can_resume'] is False
    again = ProductionOrderDispatcher(store.settings).dispatch(job)
    assert again['worker_live'] is True
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert Path(command[1]).name == "run_production_order.py"
    assert command[-1] == job
    assert kwargs["env"]["DATA_DIR"] == str(store.settings.data_dir.resolve())
    assert kwargs["stdin"] is not None


def test_starting_process_can_acquire_execution_lease_and_release_it(store):
    job = create(store)['id']
    store.record_dispatched_worker(job, os.getpid())
    assert store.get(job)['worker_live'] is True
    with store.lease(job) as token:
        store.begin(job, token, 'preflight', {})
    assert store.get(job)['worker_live'] is False
    assert store.get(job)['can_resume'] is True


def test_dispatched_hypothetical_worker_adopts_its_own_startup_record(store, monkeypatch):
    from atnos.production import order_execution
    from test_hypothetical_script_intake import order
    job = store.create(order())["id"]
    store.record_dispatched_worker(job, os.getpid())
    assert store.get(job)["worker_live"] and not store.get(job)["can_resume"]
    calls = []
    async def controlled_preflight(executor):
        calls.append(executor.job_id)
        raise ProductionBlocked("controlled preflight stop before GPU or model calls")
    monkeypatch.setattr(order_execution, "preflight_order", controlled_preflight)
    state = asyncio.run(order_execution.run_order(store.settings, job))
    assert calls == [job]
    assert state["current_step"] == "preflight" and state["status"] == "blocked"
    assert store.get(job)["worker_live"] is False
    assert store.get(job)["provider_calls"] == 0


def test_hypothetical_worker_never_adopts_another_live_execution_lease(store, monkeypatch):
    from atnos.production import order_execution
    from test_hypothetical_script_intake import order
    job = store.create(order())["id"]
    monkeypatch.setattr(order_execution, "preflight_order", lambda *_: pytest.fail("Another execution lease must be preserved"))
    with store.lease(job):
        state = asyncio.run(order_execution.run_order(store.settings, job))
        assert state["worker_live"] and state["provider_calls"] == 0


def test_late_dispatch_observation_does_not_replace_child_lease(store):
    job = create(store)['id']
    with store.lease(job) as token:
        store.record_dispatched_worker(job, os.getpid())
        store.begin(job, token, 'preflight', {})
        assert not any(item['event'] == 'worker_launched' for item in store.get(job)['history'])


def test_exited_or_reused_launch_pid_does_not_keep_job_running(store, monkeypatch):
    job = create(store)['id']
    store.record_dispatched_worker(job, os.getpid())
    real_started = psutil.Process().create_time()
    monkeypatch.setattr('atnos.production.order_store.psutil.Process',
        lambda *_: SimpleNamespace(is_running=lambda: True, create_time=lambda: real_started + 100))
    assert store.get(job)['worker_live'] is False
    assert store.get(job)['can_resume'] is True


def test_worker_that_exits_before_recording_is_not_reported_live(store, monkeypatch):
    job = create(store)['id']
    def gone(*_):
        raise psutil.NoSuchProcess(987654)
    monkeypatch.setattr('atnos.production.order_store.psutil.Process', gone)
    store.record_dispatched_worker(job, 987654)
    assert store.get(job)['worker_live'] is False


def test_dispatch_does_not_steal_live_worker(store, monkeypatch):
    job = create(store)["id"]
    started = []
    monkeypatch.setattr("atnos.production.order_dispatch.subprocess.Popen", lambda *a, **k: started.append(a))
    with store.lease(job) as token:
        result = ProductionOrderDispatcher(store.settings).dispatch(job)
        assert result["worker_live"] is True
        assert started == []


def test_dispatch_refuses_persistent_limit_block(store, monkeypatch):
    job = create(store)["id"]
    started = []
    monkeypatch.setattr("atnos.production.order_dispatch.subprocess.Popen", lambda *a, **k: started.append(a))
    with store.lease(job) as token:
        store.begin(job, token, "script", {})
        store.fail(job, token, "script", category="attempt_limit", message="test")
    with pytest.raises(ProductionBlocked):
        ProductionOrderDispatcher(store.settings).dispatch(job)
    assert started == []
