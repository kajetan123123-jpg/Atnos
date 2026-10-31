import json

import pytest

from atnos.intelligence.storage import connect
from atnos.production.order_autonomy import _recover_job, enable, policy, recover_due, suspend
from atnos.production.project_profile import ProductionOrder
from test_production_order_store import store


REASON = 'User paused autonomous recovery while reviewing the visual quality of this order.'


def _local_order(store, topic):
    return store.create(ProductionOrder(topic=topic, delivery_mode='local_only'))['id']


def _order_rows(settings, job_id):
    with connect(settings) as db:
        return {
            'job': dict(db.execute('SELECT * FROM pipeline_jobs WHERE id=?', (job_id,)).fetchone()),
            'order': dict(db.execute('SELECT * FROM pipeline_job_orders WHERE job_id=?', (job_id,)).fetchone()),
            'checkpoints': [dict(row) for row in db.execute(
                'SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? ORDER BY rowid', (job_id,))],
        }


def _suspension_events(settings, job_id):
    with connect(settings) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM pipeline_job_events WHERE job_id=? AND event='autonomous_recovery_suspended' ORDER BY id",
            (job_id,))]


def test_suspend_preserves_existing_policy_job_and_live_lease(store):
    job = _local_order(store, 'A clear annual savings decision')
    other_job = _local_order(store, 'A separate long-term savings decision')
    original_reason = 'User authorized bounded automatic local recovery for this production order.'
    enable(store.settings, job, reason=original_reason, max_recoveries=4)
    enable(store.settings, other_job, reason=original_reason)
    with connect(store.settings) as db:
        db.execute('UPDATE production_order_autonomy SET recoveries_used=2,last_failure_hash=? WHERE job_id=?',
                   ('prior-failure', job))
        db.execute('UPDATE pipeline_job_orders SET provider_calls=3,reserved_cost_units=1234 WHERE job_id=?',
                   (job,))
        db.commit()

    with store.lease(job):
        before_policy = policy(store.settings, job)
        before_rows = _order_rows(store.settings, job)
        before_history = store.get(job)['history']
        assert store.get(job)['worker_live']

        suspended = suspend(store.settings, job, reason=REASON)

        assert suspended == policy(store.settings, job)
        assert suspended['enabled'] == 0
        assert {key: value for key, value in suspended.items() if key not in {'enabled', 'updated_at'}} == {
            key: value for key, value in before_policy.items() if key not in {'enabled', 'updated_at'}}
        assert _order_rows(store.settings, job) == before_rows
        assert store.get(job)['worker_live']
        assert policy(store.settings, other_job)['enabled'] == 1
        events = _suspension_events(store.settings, job)
        assert len(events) == 1
        assert json.loads(events[0]['detail_json'])['reason'] == REASON
        assert len(store.get(job)['history']) == len(before_history) + 1

        assert suspend(store.settings, job, reason=REASON) == suspended
        assert _suspension_events(store.settings, job) == events
        assert _order_rows(store.settings, job) == before_rows


def test_suspended_queued_order_is_not_dispatched(store, monkeypatch):
    job = _local_order(store, 'A simple scheduled bill decision')
    enable(store.settings, job, reason='User authorized bounded automatic recovery of this local order.')
    suspend(store.settings, job, reason=REASON)
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda *args: pytest.fail('Suspended order was dispatched'))
    assert recover_due(store.settings) == []


@pytest.mark.parametrize(('branch', 'status', 'step', 'error'), [
    ('queued', 'queued', 'pending', None),
    ('script_only', 'queued', 'script_feedback', None),
    ('intake_review', 'blocked', 'intake_review', '{"category":"supplied_script_contract_pending"}'),
    ('ordinary_recovery', 'blocked', 'remotion_render', '{"category":"worker_interrupted"}'),
])
def test_suspend_wins_against_stale_recovery_snapshot(store, monkeypatch, branch, status, step, error):
    job = _local_order(store, f'A savings decision for {branch}')
    with connect(store.settings) as db:
        db.execute('UPDATE pipeline_jobs SET status=?,current_step=?,error=? WHERE id=?',
                   (status, step, error, job))
        db.commit()
    enable(store.settings, job, reason='User authorized bounded automatic recovery of this local order.')
    stale_snapshot = policy(store.settings, job)
    suspend(store.settings, job, reason=REASON)
    before_rows = _order_rows(store.settings, job)
    before_policy = policy(store.settings, job)
    before_history = store.get(job)['history']
    monkeypatch.setattr('atnos.production.order_recovery.recover_interrupted_orders',
                        lambda *args, **kwargs: pytest.fail('Suspended snapshot reconciled order state'))
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda *args: pytest.fail('Suspended snapshot dispatched a worker'))

    assert _recover_job(store.settings, store, stale_snapshot) == []
    assert _order_rows(store.settings, job) == before_rows
    assert policy(store.settings, job) == before_policy
    assert store.get(job)['history'] == before_history


def test_suspend_requires_reason_local_order_and_existing_policy(store):
    job = _local_order(store, 'A simple emergency fund decision')
    enable(store.settings, job, reason='User authorized bounded automatic recovery of this local order.')
    original = policy(store.settings, job)
    for invalid in ('', 'too short', None):
        with pytest.raises(ValueError, match='explicit reason'):
            suspend(store.settings, job, reason=invalid)
    assert policy(store.settings, job) == original
    assert _suspension_events(store.settings, job) == []

    unregistered = _local_order(store, 'A separate bill planning decision')
    with pytest.raises(ValueError, match='policy not found'):
        suspend(store.settings, unregistered, reason=REASON)
    assert policy(store.settings, unregistered) is None

    published = store.create(ProductionOrder(topic='A normal project policy order'))['id']
    with pytest.raises(ValueError, match='local production'):
        suspend(store.settings, published, reason=REASON)
    assert policy(store.settings, published) is None
