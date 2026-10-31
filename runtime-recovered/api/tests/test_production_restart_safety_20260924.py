"""Process-level recovery tests, isolated from production DBs and paid providers.

The worker uses the real store/executor with a local operation and a durable call
marker. Only PIDs created by this module may be stopped. Run with --basetemp on E:.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

import psutil
import pytest

from atnos.intelligence.storage import connect
from atnos.production.job_recovery import ProductionJobRecoveryService
from atnos.production.order_dispatch import ProductionOrderDispatcher, order_status
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder
from test_support import build_test_settings


WORKER = r'''
import asyncio, json, os, sys, time
from pathlib import Path
import psutil
from test_support import build_test_settings
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import OrderConflict, ProductionOrderStore
from atnos.production.steps.common import write_json

root, job, mode, nonce, marker = sys.argv[1:]
root, marker = Path(root), Path(marker)
store = ProductionOrderStore(build_test_settings(root))

def signal(**extra):
    payload = dict(pid=os.getpid(), started=psutil.Process().create_time(), nonce=nonce, **extra)
    tmp = marker.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload), encoding='utf-8')
    tmp.replace(marker)

async def paid_fixture():
    with (root / 'provider-calls.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(pid=os.getpid(), stage='voice', nonce=nonce)) + '\n')
    return write_json(store.context(job).artifact_dir / 'voice.json', {'voice': 'durable fixture, no provider'})

if mode == 'idle':
    signal()
    while True:
        time.sleep(.05)
elif mode == 'contender':
    try:
        with store.lease(job):
            signal(acquired=True)
    except OrderConflict:
        signal(acquired=False)
elif mode == 'dispatch_hold':
    from atnos.production.dispatch_lock import dispatch_lock
    with dispatch_lock(store.context(job).artifact_dir / 'worker_logs/dispatch.lock'):
        signal()
        while True:
            time.sleep(.05)
elif mode in {'hold', 'resume'}:
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        result = asyncio.run(executor.run('voice', {'revision': 1}, paid_fixture,
            provider_call=True, estimated_cost_usd=.25))
        if mode == 'hold':
            store.begin(job, token, 'remotion_render', {'revision': 1})
            signal(voice=result)
            while True:
                time.sleep(.05)
        else:
            async def render_fixture():
                return write_json(store.context(job).artifact_dir / 'render.json', {'render': 'resumed fixture'})
            asyncio.run(executor.run('remotion_render', {'revision': 1}, render_fixture))
            signal(voice=result)
'''


@pytest.fixture
def restart_store(tmp_path):
    if os.name == 'nt':
        assert tmp_path.drive.casefold() == 'e:', 'Use --basetemp on E: for Atnos tests'
    settings = build_test_settings(tmp_path)
    with connect(settings) as db:
        db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) "
                   "VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-24T00:00:00Z')")
        db.commit()
    return ProductionOrderStore(settings)


def _new_job(store):
    return store.create(ProductionOrder(topic='Isolated crash recovery fixture'), request_key='restart-fixture')


def _wait_marker(marker, process, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.is_file():
            return json.loads(marker.read_text(encoding='utf-8'))
        if process.poll() is not None:
            raise AssertionError(f'Fixture worker exited {process.returncode} before handshake; see {marker.with_suffix(".log")}')
        time.sleep(.05)
    raise AssertionError(f'Fixture worker did not send handshake: {marker}')


def _stop_owned(process, identity):
    """Windows venv launcher PID can differ from the worker PID: verify both."""
    if identity:
        try:
            worker = psutil.Process(identity['pid'])
            assert abs(worker.create_time() - identity['started']) < .01
            assert identity['nonce'] in worker.cmdline()
            worker.kill()
            worker.wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # This Popen handle was created here; never enumerate/kill by process name.
        process.kill()
        process.wait(timeout=10)


@contextmanager
def _fixture_worker(store, job, mode, *, popen=subprocess.Popen):
    nonce = f'recovery-fixture-{uuid4().hex}'
    marker = store.settings.app_root / f'{nonce}.json'
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
        TEMP=str(store.settings.app_root), TMP=str(store.settings.app_root),
        PYTHONPATH=os.pathsep.join((str(Path(__file__).parent), str(Path(__file__).parents[1] / 'src'))))
    identity = None
    with marker.with_suffix('.log').open('wb') as log:
        process = popen([sys.executable, '-c', WORKER, str(store.settings.app_root), job, mode, nonce, str(marker)],
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            identity = _wait_marker(marker, process)
            assert identity['nonce'] == nonce
            yield process, identity
        finally:
            _stop_owned(process, identity)


def test_killed_worker_retains_order_checkpoint_and_completed_cost_on_resume(restart_store, monkeypatch):
    store = restart_store
    original = _new_job(store)
    job = original['id']
    with _fixture_worker(store, job, 'hold') as (process, identity):
        live = order_status(ProductionOrderStore(store.settings).get(job))
        assert live['worker_live'] is True and live['can_resume'] is False
        assert live['completed_stages'] == ['voice']
        assert live['provider_calls'] == 1 and live['reserved_cost_units'] == 250_000
        with _fixture_worker(store, job, 'contender') as (contender, result):
            contender.wait(timeout=30)
            assert result['acquired'] is False
        with monkeypatch.context() as scoped:
            def forbidden_spawn(*args, **kwargs):
                pytest.fail('Dispatcher tried to launch a second worker while the lease was live')
            scoped.setattr('atnos.production.order_dispatch.subprocess.Popen', forbidden_spawn)
            assert ProductionOrderDispatcher(store.settings).dispatch(job)['worker_live'] is True
        _stop_owned(process, identity)  # Deliberate hard kill: worker's finally cannot clear the lease.

    fresh_store = ProductionOrderStore(store.settings)
    interrupted = order_status(fresh_store.get(job))
    assert interrupted['id'] == job and interrupted['profile_hash'] == original['profile_hash']
    assert interrupted['status'] == 'running'  # Persisted state; UI projects interruption using worker_live.
    assert interrupted['worker_live'] is False and interrupted['can_resume'] is True
    assert interrupted['completed_stages'] == ['voice']
    assert interrupted['ready_for_review'] is False
    assert store.create(ProductionOrder(topic='Isolated crash recovery fixture'), request_key='restart-fixture')['id'] == job

    with _fixture_worker(fresh_store, job, 'resume') as (resumed, result):
        resumed.wait(timeout=30)
        assert resumed.returncode == 0
        assert result['voice'] == {'voice': 'durable fixture, no provider'}
    final = ProductionOrderStore(store.settings).get(job)
    assert final['id'] == job and final['provider_calls'] == 1
    assert final['reserved_cost_units'] == 250_000
    assert {row['stage']: row['attempts'] for row in final['checkpoints']} == {'voice': 1, 'remotion_render': 2}
    assert len((store.settings.app_root / 'provider-calls.jsonl').read_text().splitlines()) == 1
    assert any(event['event'] == 'cache_reused' and event['stage'] == 'voice' for event in final['history'])
    assert final['worker_live'] is False
    assert final['status'] != 'ready'  # These fixture stages do not bypass the required QA gates.


@pytest.mark.parametrize('lease_token', [None, 'old-worker-token'])
def test_reused_pid_is_not_a_live_worker_or_a_resume_blocker(restart_store, lease_token):
    store = restart_store
    job = _new_job(store)['id']
    with _fixture_worker(store, job, 'idle') as (_, identity):
        with connect(store.settings) as db:
            db.execute('UPDATE pipeline_job_orders SET lease_token=?,lease_pid=?,lease_process_started=? WHERE job_id=?',
                (lease_token, identity['pid'], identity['started'] - 100, job))
            db.execute("UPDATE pipeline_jobs SET status='running',current_step='voice' WHERE id=?", (job,))
            db.commit()
        status = order_status(ProductionOrderStore(store.settings).get(job))
        assert psutil.pid_exists(identity['pid'])
        assert status['worker_live'] is False and status['can_resume'] is True
        with store.lease(job):
            assert store.get(job)['worker_live'] is True
        # Acquiring a new lease must not stop the unrelated, reused-PID fixture process.
        assert psutil.pid_exists(identity['pid'])


def test_dispatched_importing_process_blocks_resume_until_its_actual_exit(restart_store):
    store = restart_store
    job = _new_job(store)['id']
    with _fixture_worker(store, job, 'idle') as (_, identity):
        store.record_dispatched_worker(job, identity['pid'])
        status = order_status(ProductionOrderStore(store.settings).get(job))
        assert status['status'] == 'queued'
        assert status['worker_live'] is True and status['can_resume'] is False
    status = order_status(ProductionOrderStore(store.settings).get(job))
    assert status['worker_live'] is False and status['can_resume'] is True


def test_legacy_recovery_does_not_requeue_or_clear_modern_order_checkpoints(restart_store):
    store = restart_store
    job = _new_job(store)['id']
    with _fixture_worker(store, job, 'hold'):
        before = store.get(job)
        result = ProductionJobRecoveryService(store.settings).recover_stale_jobs(recover_all_active=True)
        assert result['recovered_count'] == 0
        assert store.get(job)['worker_live'] is True
    result = ProductionJobRecoveryService(store.settings).recover_stale_jobs(recover_all_active=True)
    assert result['recovered_count'] == 0
    after = store.get(job)
    assert after['worker_live'] is False and after['can_resume'] is True
    assert after['checkpoints'] == before['checkpoints']
    with connect(store.settings) as db:
        assert db.execute('SELECT status FROM topic_queue WHERE id=?', (after['topic_id'],)).fetchone()['status'] == 'in_production'


def test_simultaneous_dispatch_creates_only_one_process(restart_store, monkeypatch):
    store = restart_store
    job = _new_job(store)['id']
    real_popen = subprocess.Popen
    both_spawned = threading.Barrier(2)
    owned_contexts = []
    launched_pids = []
    append_lock = threading.Lock()

    def delayed_popen(*args, **kwargs):
        # Replace provider worker entrypoint only; both OS processes and the
        # dispatcher's before/after observation are real.
        context = _fixture_worker(store, job, 'idle', popen=real_popen)
        process, identity = context.__enter__()
        with append_lock:
            owned_contexts.append(context)
            launched_pids.append(identity['pid'])
        time.sleep(.3)  # widen Popen-to-record race without requiring a duplicate
        return process

    monkeypatch.setattr('atnos.production.order_dispatch.subprocess.Popen', delayed_popen)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            def dispatch(_):
                both_spawned.wait(timeout=30)
                return ProductionOrderDispatcher(store.settings).dispatch(job)
            results = list(pool.map(dispatch, range(2)))
        assert sum(result['worker_requested'] for result in results) == 1
        assert len(set(launched_pids)) == 1
    finally:
        for context in owned_contexts:
            context.__exit__(None, None, None)


def test_dispatch_lock_survives_contention_and_releases_after_dispatcher_crash(restart_store):
    from atnos.production.dispatch_lock import dispatch_lock
    store = restart_store
    job = _new_job(store)['id']
    path = store.context(job).artifact_dir / 'worker_logs/dispatch.lock'
    with _fixture_worker(store, job, 'dispatch_hold'):
        with pytest.raises(TimeoutError):
            with dispatch_lock(path, timeout=.2):
                pytest.fail('Concurrent dispatcher acquired held lock')
    with dispatch_lock(path, timeout=1):
        pass  # An OS-released lock does not leave a stale permanent reservation.


def test_dispatch_reattaches_child_from_spawn_record_crash_window(restart_store, monkeypatch):
    from types import SimpleNamespace
    store = restart_store
    job = _new_job(store)['id']
    with _fixture_worker(store, job, 'idle') as (_, identity):
        worker = Path(__file__).parents[1] / 'scripts/run_production_order.py'
        candidate = SimpleNamespace(pid=identity['pid'], cmdline=lambda: [sys.executable, str(worker), 'run', job],
            environ=lambda: {'DATA_DIR': str(store.settings.data_dir)})
        monkeypatch.setattr('atnos.production.order_dispatch.psutil.process_iter', lambda: [candidate])
        monkeypatch.setattr('atnos.production.order_dispatch.subprocess.Popen', lambda *a, **kw: pytest.fail('Duplicate launch'))
        result = ProductionOrderDispatcher(store.settings).dispatch(job)
        assert result['worker_live'] is True
        assert result['worker_requested'] is False
        assert result['reattached_pid'] == identity['pid']
