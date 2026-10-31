"""Isolated contract tests: no inference, media, network or production DB writes."""
import asyncio
import copy
import os
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from atnos.production.order_store import OrderConflict, ProductionBlocked, file_hash
from atnos.production.order_execution import StageExecutor
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production.hypothetical_claim_evidence import editorial_script_payload
from atnos.production.studio_script_recovery import (
    StudioScriptRepair, StudioCandidateAssessment, register_script_repair, load_recovery_request,
    repair_execution_scope, prepare_recovery_context, hold_script_candidate,
    load_script_candidate, register_candidate_assessment, load_candidate_assessment,
    require_candidate_acceptance, _records)
from test_studio_user_editorial_feedback import canonical, action
from test_production_order_script import script
from test_production_order_store import store


def controlled_action(store, job, **changes):
    root = Path(store.get(job)['artifact_dir'])
    if not (root / 'scene_plan.json').exists():
        write_json(root / 'scene_plan.json', {'fixture': 'frozen plan'})
    raw = action(request_key='controlled-repair-v4').model_dump(mode='json')
    raw.update(execution_scope='script_only', operation_budget={'writes': 3, 'reviews': 3},
        acceptance_criteria=[{'id': 'K1', 'segment_ids': ['s06'],
            'fields': ['text', 'on_screen'], 'detail': 'Preserve the channel CTA without changing its meaning.'},
            {'id': 'K2', 'segment_ids': ['s01', 's03'],
             'detail': 'Retain the locked scenes and financial assumptions unchanged.'}],
        expected_canonical_sha256=file_hash(root / 'script.json'),
        expected_plan_sha256=file_hash(root / 'scene_plan.json'))
    raw.update(changes)
    return StudioScriptRepair.model_validate(raw)


@pytest.fixture
def registered(store, canonical):
    job, source = canonical
    registration = register_script_repair(store, job, controlled_action(store, job))
    return job, editorial_script_payload(source), registration['request']


def held(store, registered):
    job, source, request = registered
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        prepare_recovery_context(executor)
        identity = next(event['detail'] for event in store.get(job)['history'] if event['event'] == 'studio_script_repair_runtime_loaded')
        assert identity['pid'] == os.getpid() and identity['registration_contract_version'] == 2
        assert identity['loaded_function_hashes']['run_order'] != identity['disk_code_identity']
        assert len(identity['loaded_function_hashes']) == 4
        review = {'status': 'passed', 'issues': [], 'revision': 0,
                  'script_hash': content_hash(source), 'studio_recovery_digest': content_hash(request)}
        with pytest.raises(ProductionBlocked, match='independent_semantic_review_required'):
            asyncio.run(hold_script_candidate(executor, source, review, {'writer': 'fixture-writer', 'reviewer': 'fixture-reviewer'}))
    return load_script_candidate(store, job)


def assessment(candidate):
    segments = {s['id']: s for s in candidate['script']['segments']}
    return {'request_digest': candidate['request_digest'], 'candidate_hash': candidate['candidate_hash'],
            'reviewer': 'isolated-test-operator', 'evidence_source': 'independent_operator',
            'confirmation': 'I reviewed the entire current candidate independently', 'decision': 'accept',
            'checks': [{'criterion_id': c['id'], 'segment_id': sid, 'field': field,
                       'quote': segments[sid][field], 'satisfied': True,
                       'reason': 'Isolated contract fixture only; this is not live quality acceptance.'}
                      for c in candidate['acceptance_criteria'] for sid in c['segment_ids'] for field in c.get('fields', ['text'])]}


def test_contract_requires_scope_budget_hashes_and_unique_criteria(store, canonical):
    job, _ = canonical
    raw = controlled_action(store, job).model_dump(mode='json')
    for key in ['operation_budget', 'expected_plan_sha256', 'expected_canonical_sha256', 'acceptance_criteria']:
        value = copy.deepcopy(raw)
        value.pop(key)
        with pytest.raises(ValueError):
            StudioScriptRepair.model_validate(value)
    value = copy.deepcopy(raw)
    value['acceptance_criteria'].append(value['acceptance_criteria'][0])
    with pytest.raises(ValueError):
        StudioScriptRepair.model_validate(value)
    value = copy.deepcopy(raw)
    value['operation_budget']['writes'] = True
    with pytest.raises(ValueError):
        StudioScriptRepair.model_validate(value)


def test_stale_base_rejected_without_registration(store, canonical):
    job, _ = canonical
    request = controlled_action(store, job, expected_canonical_sha256='0' * 64)
    before = store.get(job)
    with pytest.raises(OrderConflict, match='stale'):
        register_script_repair(store, job, request)
    assert not _records(store.settings, job)
    assert store.get(job)['history'] == before['history']


def test_scope_and_budget_immutable_under_idempotency(store, registered):
    job, _, request = registered
    assert repair_execution_scope(store, job) == 'script_only'
    assert register_script_repair(store, job, controlled_action(store, job))['already_registered']
    with pytest.raises(OrderConflict, match='execution contract'):
        register_script_repair(store, job, controlled_action(store, job, operation_budget={'writes': 4, 'reviews': 3}))
    assert load_recovery_request(store, job) == request


def test_direct_legacy_lease_and_begin_cannot_promote_scope(store, registered):
    job, source, _ = registered
    before = file_hash(Path(store.get(job)['artifact_dir']) / 'script.json')
    with store.lease(job) as token:
        assert token.startswith('script-only:')
        for stage in ['voice/narration', 'assets', 'remotion_render', 'package', 'image_generation']:
            with pytest.raises(ProductionBlocked, match='Script-only admission'):
                store.begin(job, token, stage, {})
        with pytest.raises(ProductionBlocked, match='Independent semantic'):
            store.begin(job, token, 'script', {'script': source})
    assert file_hash(Path(store.get(job)['artifact_dir']) / 'script.json') == before


def test_hold_model_pass_preserves_canonical_plan_and_requires_operator(store, registered, monkeypatch):
    job, _, _ = registered
    before = store.get(job)
    root = Path(before['artifact_dir'])
    hashes = [file_hash(root / name) for name in ['script.json', 'scene_plan.json']]
    candidate = held(store, registered)
    assert candidate['candidate_hash'] == content_hash(candidate['script'])
    assert not store.get(job)['can_resume'] and not store.get(job)['worker_live']
    assert [file_hash(root / name) for name in ['script.json', 'scene_plan.json']] == hashes
    assert store.get(job)['provider_calls'] == before['provider_calls']
    from atnos.production.order_autonomy import enable, recover_due
    enable(store.settings, job, reason='Isolated recovery test of a held script-only candidate.')
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *_: pytest.fail('Held candidate was automatically dispatched'))
    assert recover_due(store.settings) == []


@pytest.mark.parametrize('fault', ['digest', 'hash', 'quote', 'criterion', 'heading', 'duplicate', 'false_pass', 'model', 'strict_bool', 'model_origin'])
def test_bad_assessments_are_rejected_without_mutation(store, registered, fault):
    candidate = held(store, registered)
    job = registered[0]
    value = assessment(candidate)
    if fault == 'digest': value['request_digest'] = '0' * 64
    elif fault == 'hash': value['candidate_hash'] = '0' * 64
    elif fault == 'quote': value['checks'][0]['quote'] = 'This quotation does not exist in the current script.'
    elif fault in {'criterion', 'heading'}: value['checks'].pop(0 if fault == 'criterion' else 1)
    elif fault == 'duplicate': value['checks'].append(value['checks'][0])
    elif fault == 'false_pass': value['checks'][0]['satisfied'] = False
    elif fault == 'model': value['reviewer'] = 'fixture-writer'
    elif fault == 'strict_bool': value['checks'][0]['satisfied'] = 'true'
    elif fault == 'model_origin': value['evidence_source'] = 'model_review'
    before = store.get(job)
    with pytest.raises((ValueError, OrderConflict)):
        register_candidate_assessment(store, job, value)
    assert store.get(job)['history'] == before['history']
    assert load_candidate_assessment(store, job) is None


def test_loose_offline_json_is_not_an_assessment_receipt(store, registered):
    candidate = held(store, registered)
    job = registered[0]
    write_json(Path(store.get(job)['artifact_dir']) / 'studio_script_recovery' / 'assessments' / 'offline-pass.json', assessment(candidate))
    assert load_candidate_assessment(store, job) is None
    with pytest.raises(ProductionBlocked):
        require_candidate_acceptance(store, job, script=candidate['script'])


@pytest.mark.parametrize('fault', ['locked_0', 'locked_2', 'claims', 'title', 'roles'])
def test_candidate_cannot_change_locked_data(store, registered, fault):
    job, source, request = registered
    bad = copy.deepcopy(source)
    if fault.startswith('locked'): bad['segments'][0 if fault == 'locked_0' else 2]['text'] += ' Changed locked scene.'
    elif fault == 'claims': bad['claims'][0]['result'] += 1
    elif fault == 'title': bad['title'] += ' Changed.'
    elif fault == 'roles': bad['segments'][5]['type'] = 'practical'
    before = store.get(job)
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        prepare_recovery_context(executor)
        with pytest.raises(ProductionBlocked, match='frozen|locked'):
            asyncio.run(hold_script_candidate(executor, bad,
                {'status': 'passed', 'issues': [], 'script_hash': content_hash(bad), 'studio_recovery_digest': content_hash(request)}, {}))
    assert store.get(job)['provider_calls'] == before['provider_calls']
    assert load_script_candidate(store, job) is None


def test_retry_budget_survives_worker_restart_under_unlimited_local_policy(store, registered):
    job, _, _ = registered
    store.settings = store.settings.model_copy(update={'atnos_local_work_unlimited': True})
    calls = []
    async def failing_fixture():
        calls.append('no actual inference')
        raise ValueError('Isolated invalid response to exercise retries')
    for _ in range(2):
        with store.lease(job) as token:
            executor = StageExecutor(store, job, token)
            prepare_recovery_context(executor)
            with pytest.raises(ProductionBlocked):
                asyncio.run(executor.run('script_repair/1', {'fixture': 'same action'}, failing_fixture, local_model_call=True))
    assert len(calls) == 3
    assert _records(store.settings, job)[-1]['writes_used'] == 3
    assert store.get(job)['provider_calls'] == 3
    assert 'studio_script_operation_budget' in store.get(job)['error']
    assert not store.get(job)['can_resume']


def test_accepted_candidate_finalizes_through_existing_stage_and_cannot_run_av(store, registered):
    job, source, _ = registered
    candidate = held(store, registered)
    receipt = register_candidate_assessment(store, job, assessment(candidate))
    assert not receipt['worker_requested'] and store.get(job)['can_resume']
    history = store.get(job)['history']
    assert register_candidate_assessment(store, job, assessment(candidate))['already_registered']
    assert store.get(job)['history'] == history
    before_calls = store.get(job)['provider_calls']
    root = Path(store.get(job)['artifact_dir'])
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        prepare_recovery_context(executor)
        verdict = asyncio.run(hold_script_candidate(executor, source, candidate['review'], candidate['model_identities']))
        assert verdict['independent_semantic_review'] == 'operator_assessed'
        assert verdict['human_review'] == 'not_performed'
        store.begin(job, token, 'script', {'script': source, 'review': verdict})
        payload = {**source, 'qa': verdict, 'provenance': {'studio_recovery': {'request_digest': candidate['request_digest']}}}
        path = write_json(root / 'script.json', payload).artifact_path
        store.complete(job, token, 'script', payload, [path])
        with pytest.raises(ProductionBlocked, match='Script-only admission'):
            store.begin(job, token, 'voice/narration', {})
    assert store.get(job)['provider_calls'] == before_calls
    assert load_script_candidate(store, job)['candidate_hash'] == content_hash(source)


def test_dispatch_and_autonomy_race_launches_one_script_worker(store, registered, monkeypatch):
    job = registered[0]
    from atnos.production.order_autonomy import enable, recover_due
    from atnos.production.order_dispatch import ProductionOrderDispatcher
    enable(store.settings, job, reason='Isolated script-only registration and autonomous dispatch race.')
    launches = []
    monkeypatch.setattr('atnos.production.order_dispatch.psutil.process_iter', lambda: [])
    def popen(argv, **kwargs):
        launches.append(argv)
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr('atnos.production.order_dispatch.subprocess.Popen', popen)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(ProductionOrderDispatcher(store.settings).dispatch, job), pool.submit(recover_due, store.settings)]
        for future in futures: future.result()
    assert len(launches) == 1 and launches[0][-2:] == ['prepare-script', job]


def test_script_worker_startup_adoption_and_legacy_run_are_script_only(store, registered, monkeypatch):
    job = registered[0]
    from atnos.production import order_execution
    store.record_dispatched_worker(job, os.getpid())
    seen = []
    async def stop(executor, **kwargs):
        seen.append((executor.token, kwargs))
        raise ProductionBlocked('Isolated preflight stop before any inference or GPU operation')
    monkeypatch.setattr(order_execution, 'preflight_order', stop)
    result = asyncio.run(order_execution.run_order(store.settings, job))
    assert seen[0][0].startswith('script-only:') and seen[0][1] == {'script_only': True}
    assert not store.get(job)['worker_live'] and result['provider_calls'] == 0


def test_previous_v3_receipt_counters_and_bytes_preserved(store, canonical):
    job, _ = canonical
    v3 = register_script_repair(store, job, action(request_key='preserved-v3'))['request']
    with store.lease(job) as token:
        store.block(job, token, 'script_review/0', category='execution_failure', message='Isolated v3 stop')
    before = _records(store.settings, job)[0]
    register_script_repair(store, job, controlled_action(store, job))
    assert _records(store.settings, job)[0] == before
    path = Path(store.get(job)['artifact_dir']) / before['receipt_path']
    import json
    assert json.loads(path.read_text('utf-8')) == v3


def test_new_default_request_cannot_silently_release_controlled_scope(store, registered):
    job = registered[0]
    held(store, registered)
    with pytest.raises(OrderConflict, match='cannot be promoted'):
        register_script_repair(store, job, action(request_key='attempted-default-release'))
    assert repair_execution_scope(store, job) == 'script_only'


def test_dispatch_recovers_prepare_script_startup_crash_window(store, registered, monkeypatch):
    from atnos.production.order_dispatch import ProductionOrderDispatcher
    import atnos.production.order_dispatch as dispatch_module
    job = registered[0]
    worker = Path(dispatch_module.__file__).resolve().parents[3] / 'scripts' / 'run_production_order.py'
    child = SimpleNamespace(pid=os.getpid(), cmdline=lambda: ['python', str(worker), 'prepare-script', job],
                            environ=lambda: {'DATA_DIR': str(store.settings.data_dir)})
    monkeypatch.setattr(dispatch_module.psutil, 'process_iter', lambda: [child])
    monkeypatch.setattr(dispatch_module.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('Duplicate startup worker'))
    result = ProductionOrderDispatcher(store.settings).dispatch(job)
    assert result['reattached_pid'] == os.getpid() and not result['worker_requested']


def test_normal_producer_holds_before_finalize_and_resume_uses_cached_model_results(store, registered, monkeypatch):
    from atnos.production import order_execution
    from atnos.production.steps import order_script
    job, source, _ = registered
    monkeypatch.setattr('atnos.production.script_model_lane.load_script_model_lane', lambda *_a, **_k: None)
    monkeypatch.setattr('atnos.production.script_review_model_lane.load_script_review_model_lane', lambda *_a, **_k: None)
    monkeypatch.setattr(order_script, 'gather_script_context', lambda *_: {'reference_source': 'isolated fixture', 'usage': 'contract only'})
    calls = []
    async def repair_fixture(*_a, **_k):
        calls.append('fixture write; no inference')
        return order_script.EditorialScript.model_validate(source)
    async def review_fixture(*_a, **_k):
        calls.append('fixture review; no inference')
        return {'status': 'passed', 'issues': []}
    monkeypatch.setattr(order_script, 'repair', repair_fixture)
    monkeypatch.setattr(order_script, 'review', review_fixture)
    monkeypatch.setattr(order_script, 'script_issues', lambda *_a, **_k: [])
    monkeypatch.setattr(order_script, 'reviewed_source_facts', lambda *_: {})
    root = Path(store.get(job)['artifact_dir'])
    before = file_hash(root / 'script.json')
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='independent_semantic_review_required'):
            asyncio.run(order_execution.produce_script(StageExecutor(store, job, token)))
    assert file_hash(root / 'script.json') == before
    candidate = load_script_candidate(store, job)
    register_candidate_assessment(store, job, assessment(candidate))
    with store.lease(job) as token:
        result = asyncio.run(order_execution.produce_script(StageExecutor(store, job, token)))
    assert calls == ['fixture write; no inference', 'fixture review; no inference']
    assert result['qa']['independent_semantic_review'] == 'operator_assessed'
    assert result['qa']['human_review'] == 'not_performed'
    assert result['provenance']['studio_recovery']['request_digest'] == candidate['request_digest']
    assert content_hash(editorial_script_payload(result)) == candidate['candidate_hash']


def test_rejected_candidate_is_not_canonical_and_not_recovered(store, registered, monkeypatch):
    from atnos.production.order_autonomy import enable, recover_due
    job = registered[0]
    candidate = held(store, registered)
    value = assessment(candidate)
    value['checks'][0]['satisfied'] = False
    value['decision'] = 'reject'
    register_candidate_assessment(store, job, value)
    with pytest.raises(ProductionBlocked): require_candidate_acceptance(store, job, script=candidate['script'])
    enable(store.settings, job, reason='Isolated recovery test of a rejected independent candidate.')
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *_: pytest.fail('Rejected candidate was dispatched'))
    assert recover_due(store.settings) == []


def test_candidate_assessment_api_uses_registered_evidence_without_dispatch(store, registered, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from atnos.api.routes.production import router
    app = FastAPI()
    app.state.settings = store.settings
    app.include_router(router)
    job = registered[0]
    candidate = held(store, registered)
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *_: pytest.fail('Assessment endpoint dispatched a worker'))
    with TestClient(app) as client:
        got = client.get(f'/api/production/orders/{job}/script-candidate')
        assert got.status_code == 200 and got.headers['cache-control'] == 'no-store'
        assert got.json()['candidate']['candidate_hash'] == candidate['candidate_hash']
        sent = client.post(f'/api/production/orders/{job}/script-candidate/assessment', json=assessment(candidate))
        assert sent.status_code == 200 and not sent.json()['worker_requested']
        assert client.get('/api/production/orders/missing/script-candidate').status_code == 404


def test_legacy_run_stops_after_script_pass_without_any_av_call(store, registered, monkeypatch):
    from atnos.production import order_execution
    from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
    from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
    job = registered[0]
    called = []
    async def preflight(executor, **kwargs):
        assert kwargs == {'script_only': True}
    async def passed_fixture(executor):
        assert executor.token.startswith('script-only:')
        called.append('isolated script pass')
        return {'qa': {'status': 'passed'}}
    async def forbidden(*_a, **_k): pytest.fail('Script-only PASS entered media')
    monkeypatch.setattr(order_execution, 'preflight_order', preflight)
    monkeypatch.setattr(order_execution, 'produce_script', passed_fixture)
    monkeypatch.setattr(GpuRuntimeLockService, 'acquire', lambda *_a, **_k: 'isolated-lock')
    monkeypatch.setattr(GpuRuntimeLockService, 'release', lambda *_a, **_k: None)
    monkeypatch.setattr(ProductionGpuResidencyService, 'prepare_for_llm', lambda *_a, **_k: None)
    for module, names in [('order_assets', ['produce_assets']), ('order_voice', ['produce_voice']),
                          ('order_media', ['produce_captions', 'produce_edit']),
                          ('order_delivery', ['produce_covers', 'produce_package']),
                          ('order_repair', ['produce_final_quality'])]:
        for name in names:
            monkeypatch.setattr('atnos.production.steps.' + module + '.' + name, forbidden)
    result = asyncio.run(order_execution.run_order(store.settings, job, script_only=False))
    assert called == ['isolated script pass']
    assert result['status'] == 'blocked' and 'controlled_script_complete' in result['error']
    assert not store.get(job)['can_resume'] and not store.get(job)['worker_live']


def test_tampered_candidate_and_stale_plan_fail_closed(store, registered):
    job = registered[0]
    candidate = held(store, registered)
    state = store.get(job)
    row = next(r for r in state['checkpoints'] if r['stage'].startswith('script_candidate/'))
    path = Path(state['artifact_dir']) / row['files'][0]['path']
    saved = path.read_bytes()
    path.write_text('{}', encoding='utf-8')
    with pytest.raises(ProductionBlocked, match='evidence'): load_script_candidate(store, job)
    path.write_bytes(saved)  # Isolated fixture restoration only.
    write_json(Path(state['artifact_dir']) / 'scene_plan.json', {'fixture': 'changed frozen plan'})
    with pytest.raises(ProductionBlocked, match='plan changed'):
        register_candidate_assessment(store, job, assessment(candidate))


def test_public_hash_survives_secret_redactor_without_disabling_it():
    from atnos.core.logging import redact_secrets
    from atnos.production.studio_script_recovery import _audit_hash, _read_audit_hash
    digest = '92edc8651044538793a78d14e2b5dc8ec78f171ec0800a88dd75909b10e36571'
    assert redact_secrets(digest) != digest
    assert _read_audit_hash(redact_secrets(_audit_hash(digest))) == digest
    assert redact_secrets({'password': 'fixture-private'})['password'] == '[REDACTED]'
