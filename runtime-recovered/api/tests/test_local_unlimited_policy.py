from __future__ import annotations

import asyncio
import json

import pytest

from atnos.production.order_store import ProductionBlocked
from test_production_order_store import store, create, _write_test_budget
from test_scoped_script_revision import four_thoughts
from test_wb_image_api_production import trial, isolated_brain


@pytest.fixture
def unlimited(store, monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    store.settings.atnos_local_work_unlimited = True
    _write_test_budget(store, max_attempts_per_element=1, max_provider_calls=1, max_cost_usd=0.01)
    return store


def test_local_calls_and_attempts_have_no_lifetime_quota_but_keep_history(unlimited):
    job = create(unlimited)['id']
    with unlimited.lease(job) as token:
        for i in range(6):
            unlimited.begin(job, token, 'script_review/0', {'revision': i}, local_model_call=True)
            unlimited.fail(job, token, 'script_review/0', category='invalid_output', message='Rejected fixture')
    state = unlimited.get(job)
    assert state['provider_calls'] == 6
    assert state['limited_provider_calls'] == 0
    assert state['reserved_cost_units'] == 0
    assert state['checkpoints'][0]['attempts'] == 6
    assert state['local_work_unlimited'] is True


@pytest.mark.parametrize('kwargs', [
    {'provider_call': True, 'estimated_cost_usd': .005},
    {'external_tool_call': True},
])
def test_paid_or_unknown_billing_retains_call_and_attempt_caps(unlimited, kwargs):
    job = create(unlimited)['id']
    with unlimited.lease(job) as token:
        unlimited.begin(job, token, 'model', {'revision': 1}, **kwargs)
        unlimited.fail(job, token, 'model', category='dependency_failure', message='fixture')
        with pytest.raises(ProductionBlocked, match='attempt_limit'):
            unlimited.begin(job, token, 'model', {'revision': 2}, **kwargs)
        with pytest.raises(ProductionBlocked, match='provider_call_limit'):
            unlimited.begin(job, token, 'model-other', {'revision': 1}, **kwargs)
        unlimited.begin(job, token, 'script_draft', {'revision': 1}, local_model_call=True)
    assert unlimited.get(job)['limited_provider_calls'] == 1


def test_free_work_does_not_exhaust_paid_quota_and_paid_cost_guard_stays(unlimited):
    job = create(unlimited)['id']
    with unlimited.lease(job) as token:
        for i in range(3):
            unlimited.begin(job, token, f'local/{i}', {}, local_model_call=True)
        with pytest.raises(ProductionBlocked, match='cost_limit'):
            unlimited.begin(job, token, 'paid-over-budget', {}, provider_call=True, estimated_cost_usd=.02)
        unlimited.begin(job, token, 'paid', {}, provider_call=True, estimated_cost_usd=.01)
        unlimited.begin(job, token, 'local-after-paid', {}, local_model_call=True)
    state = unlimited.get(job)
    assert state['provider_calls'] == 5 and state['limited_provider_calls'] == 1
    assert state['reserved_cost_units'] == 10_000


def test_local_retry_stops_after_three_failures_without_lifetime_block(unlimited):
    from atnos.production.order_execution import StageExecutor
    job = create(unlimited)['id']
    calls = []
    async def invalid():
        calls.append(1)
        raise ValueError('Malformed model fixture')
    with unlimited.lease(job) as token:
        executor = StageExecutor(unlimited, job, token)
        with pytest.raises(ProductionBlocked, match='local_retry_paused'):
            asyncio.run(asyncio.wait_for(executor.run('script_draft', {}, invalid, local_model_call=True), timeout=5))
    assert len(calls) == 3
    assert unlimited.get(job)['can_resume'] is True


def test_unlimited_policy_does_not_advertise_paid_stage_resume(unlimited):
    job = create(unlimited)['id']
    with unlimited.lease(job) as token:
        unlimited.begin(job, token, 'paid', {}, provider_call=True, estimated_cost_usd=.005)
        unlimited.fail(job, token, 'paid', category='dependency_failure', message='fixture')
        with pytest.raises(ProductionBlocked):
            unlimited.begin(job, token, 'paid', {}, provider_call=True, estimated_cost_usd=.005)
    assert unlimited.get(job)['can_resume'] is False


def test_old_local_script_count_block_resumable_without_counter_reset(unlimited):
    unlimited.settings.atnos_local_work_unlimited = False
    job = create(unlimited)['id']
    with unlimited.lease(job) as token:
        unlimited.begin(job, token, 'script_review/0', {}, local_model_call=True)
        unlimited.fail(job, token, 'script_review/0', category='invalid_output', message='fixture')
        with pytest.raises(ProductionBlocked, match='attempt_limit'):
            unlimited.begin(job, token, 'script_review/0', {}, local_model_call=True)
    before = unlimited.get(job)
    unlimited.settings.atnos_local_work_unlimited = True
    after = unlimited.get(job)
    assert after['can_resume'] and after['can_prepare_script']
    for key in ('error', 'status', 'checkpoints', 'history', 'provider_calls', 'reserved_cost_units'):
        assert after[key] == before[key]


def test_registered_studio_action_keeps_counting_after_old_three_operation_cap(store, four_thoughts, monkeypatch):
    from test_studio_script_recovery import rejected_job, action
    from atnos.production.order_execution import StageExecutor
    from atnos.production.studio_script_recovery import register_script_repair, prepare_recovery_context, consume_operation, TABLE
    from atnos.intelligence.storage import connect
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    job, _ = rejected_job(store, four_thoughts)
    register_script_repair(store, job, action())
    store.settings.atnos_local_work_unlimited = True
    calls_before = store.get(job)['provider_calls']
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        prepare_recovery_context(executor)
        for i in range(5):
            consume_operation(executor, 'script_review/0', {'revision': i})
    with connect(store.settings) as db:
        row = db.execute(f'SELECT reviews_used FROM {TABLE} WHERE job_id=?', (job,)).fetchone()
    assert row['reviews_used'] == 5
    assert store.get(job)['provider_calls'] == calls_before  # No inference.


def test_unlimited_local_image_allowance_validates_ledger_and_preserves_receipts(monkeypatch):
    from test_local_image_allowance import _isolated_local_lane, _temp_root, TABLE
    from atnos.production.local_image_allowance import effective_local_image_allowance, register_local_image_allowance
    from atnos.intelligence.storage import connect
    with _temp_root() as temp:
        settings, store, job, context, lane, _ = _isolated_local_lane(temp, monkeypatch)
        receipt = context.artifact_dir/'image_local_authorization.json'
        before = receipt.read_bytes()
        register_local_image_allowance(settings, job, 42, authorized=True, reason='User allows a finite extension before unlimited mode is enabled.')
        settings.atnos_local_work_unlimited = True
        assert effective_local_image_allowance(settings, job, lane) is None
        assert receipt.read_bytes() == before and store.get(job)['provider_calls'] == 17
        from atnos.production import local_image_allowance as allowance
        import sqlite3
        with connect(settings) as db:
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                db.execute(f"UPDATE {TABLE} SET digest='tampered' WHERE job_id=?", (job,))
            rows = allowance._load_rows(db, job)
        rows[0]['digest'] = 'tampered'
        monkeypatch.setattr(allowance, '_load_rows', lambda *_: rows)
        with pytest.raises(ProductionBlocked, match='integrity'):
            effective_local_image_allowance(settings, job, lane)


def test_autonomous_local_recovery_has_no_lifetime_cap_but_deduplicates_failure(store, monkeypatch):
    from test_order_autonomy import create_local
    from atnos.production.order_autonomy import enable, policy, recover_due
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    job = create_local(store)
    store.settings.atnos_local_work_unlimited = True
    enable(store.settings, job, reason='User permits unlimited lifetime local recovery without resetting history.', max_recoveries=1)
    dispatched = []
    # Autonomy holds the persisted opt-in/dispatch lock and calls this entry
    # point. Intercept it so this isolated test never launches a real worker.
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked', lambda self, jid: dispatched.append(jid) or {'id': jid})
    for i in range(2):
        with store.lease(job) as token:
            store.block(job, token, 'remotion_render', category='worker_interrupted', message=f'Safe local interruption {i}')
        recover_due(store.settings)
        recover_due(store.settings)
    assert dispatched == [job, job]
    assert policy(store.settings, job)['recoveries_used'] == 2
    assert store.get(job)['autonomous_recovery']['maximum'] is None


def test_local_image_rejections_pause_per_run_without_a_lifetime_quota(trial, isolated_brain, monkeypatch):
    from PIL import Image
    from atnos.production import wb_image_api as lane
    from atnos.production.order_autonomy import enable
    from atnos.production.asset_brain.models import AssetRecord
    from atnos.production.steps.common import write_json
    from test_wb_image_api_production import prepared_executor
    store, job, reference = trial
    lane.register_local_image_lane(store.settings, job, reference_path=reference, authorized=True)
    store.settings.atnos_local_work_unlimited = True
    enable(store.settings, job, reason='User enables unlimited local work with real QC and bounded retry bursts.')
    monkeypatch.setattr('atnos.production.wb_image_review.proven_rejection_count', lambda *_: 0)
    generated = []
    def generate(settings, context, need, style, tags, authorization, fingerprint, previous_reference):
        image = context.artifact_dir/(fingerprint+'.png')
        Image.new('RGB', (1024, 1536), '#163142').save(image)
        asset = AssetRecord(id='candidate-'+fingerprint, type='background', file_path=str(image), channel='wealth_blueprint',
            quality_status='AWAITING_REVIEW', production_role='background', tags=tags)
        isolated_brain.append(asset)
        generated.append(asset.id)
        return write_json(context.artifact_dir/(fingerprint+'.json'), {'asset_id': asset.id, 'output_path': str(image), 'quality_status': asset.quality_status})
    async def review(executor, asset_id, *args):
        index = next(i for i, asset in enumerate(isolated_brain) if asset.id == asset_id)
        isolated_brain[index] = isolated_brain[index].model_copy(update={'quality_status': 'REJECTED', 'approved': False})
        return {'passed': False, 'user_approved': False}
    monkeypatch.setattr(lane, '_generate_local_candidate', generate)
    monkeypatch.setattr('atnos.production.wb_image_review.review_candidate', review)
    for _ in range(2):
        with store.lease(job) as token:
            executor = prepared_executor(store, job, token)
            with pytest.raises(ProductionBlocked, match='local_image_retry_paused'):
                asyncio.run(lane.produce_image_candidates(executor))
            assert '_local_image_retry_burst' not in executor.context.artifacts
    assert len(generated) == store.get(job)['provider_calls'] == 6
    assert all(asset.approved is False for asset in isolated_brain)
