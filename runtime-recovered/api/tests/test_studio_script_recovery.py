"""Studio action tests use controlled model responses, never live production."""
import asyncio
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atnos.api.routes import production as routes
from atnos.intelligence.storage import connect
from atnos.production.order_dispatch import order_status
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.order_store import _event
from atnos.production.project_profile import ProductionOrder, content_hash
from atnos.production.scoped_script_revision import load_feedback, register_feedback
from atnos.production.steps import order_script
from atnos.production.steps.common import write_json
from atnos.production.studio_script_recovery import (
    StudioScriptRepair, apply_recovery_guidance, load_recovery_request,
    prepare_recovery_context, recovery_snapshot, register_script_repair,
)
from test_production_order_api import seed_account
from test_production_order_script import execution
from test_production_order_store import store
from test_scoped_script_revision import feedback, four_thoughts, original_order, script
from test_support import create_test_app
from test_wb_script_quality_recovery import _accept_first, _followup


def rejected_job(store, four_thoughts, *, issues=None, key='draft-source'):
    job = store.create(ProductionOrder(topic='Follow additional income', delivery_mode='local_only'), request_key=key)['id']
    raw = copy.deepcopy(four_thoughts.model_dump(mode='json'))
    for segment in raw['segments']:
        if segment['type'] == 'practical':
            segment['type'] = 'explanation'
    defects = issues or [{'code': 'practical_share', 'segment_ids': ['s02', 's03'],
        'detail': 'No spoken words deliver a concrete practical action; minimum share is not met.'}]
    with store.lease(job) as token:
        for stage, value in [('script_draft', raw), ('script_review/0', {'status': 'failed', 'issues': defects, 'script_hash': content_hash(raw), 'revision': 0})]:
            store.begin(job, token, stage, {'test_source': stage}, local_model_call=True)
            path = write_json(store.context(job).artifact_dir / 'script' / (stage.replace('/', '-') + '.json'), value).artifact_path
            store.complete(job, token, stage, value, [path])
        store.begin(job, token, 'script_repair/1', {'test_source': 'rejected repair'}, local_model_call=True)
        store.fail(job, token, 'script_repair/1', category='invalid_output', message='Repair still has practical_share: concrete steps missing')
    return job, raw


def action(key='studio-request-001', note='Use distinct concrete actions.'):
    return StudioScriptRepair(request_key=key, note=note)


def test_review_contract_hold_does_not_resurface_old_length_findings(store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    _accept_first(store, job, feedback, four_thoughts, monkeypatch)
    with store.lease(job) as token:
        store.block(job, token, 'script_feedback', category='script_review_contract_changed',
            message='Old accepted review requires the current review contract.')
    before = store.get(job)
    monkeypatch.setattr('atnos.production.studio_script_recovery._latest_issues', lambda state: [
        {'code': 'word_budget', 'segment_ids': ['s02'], 'detail': '63 words; expected 66-81'}])
    snapshot = recovery_snapshot(store, job)
    assert snapshot['kind'] == 'script'
    assert snapshot['issues'] == []
    assert snapshot['can_repair_script'] is False
    assert 'kontrakt' in snapshot['repair_blocker']
    assert '63' not in str(snapshot)
    assert store.get(job) == before


def test_get_recovers_real_issues_and_registration_preserves_bytes_history_counters(store, four_thoughts):
    job, raw = rejected_job(store, four_thoughts)
    before = store.get(job)
    snapshot = recovery_snapshot(store, job)
    assert snapshot['kind'] == 'script' and snapshot['can_repair_script']
    assert snapshot['issues'][0]['code'] == 'practical_share'
    assert snapshot['issues'][0]['segment_ids'] == ['s02', 's03']
    assert snapshot['issues'][0]['message'].startswith('Za mało')
    registration = register_script_repair(store, job, action())
    assert registration['request']['mode'] == 'normal_review'
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['request'] == before['request'] and after['profile_hash'] == before['profile_hash']
    assert [(row['stage'], row['attempts']) for row in after['checkpoints']] == [(row['stage'], row['attempts']) for row in before['checkpoints']]
    assert after['history'][:len(before['history'])] == before['history']
    receipt = load_recovery_request(store, job)
    for entry in receipt['archive']:
        saved = Path(after['artifact_dir']) / entry['archive_path']
        assert file_hash(saved) == entry['sha256']
        if entry.get('original_path'):
            source = Path(after['artifact_dir']) / entry['original_path']
            assert saved.read_bytes() == source.read_bytes()
    assert not any(row['stage'] == 'script' for row in after['checkpoints'])
    assert raw['segments'][1]['type'] == 'explanation'  # No automatic practical relabel.


def test_idempotency_same_note_is_durable_but_changed_note_conflicts_even_live(store, four_thoughts):
    job, _ = rejected_job(store, four_thoughts)
    first = register_script_repair(store, job, action())
    with store.lease(job):
        again = register_script_repair(store, job, action())
        assert again['already_registered'] and again['request'] == first['request']
        with pytest.raises(OrderConflict, match='inną uwagą'):
            register_script_repair(store, job, action(note='Another instruction.'))
        with pytest.raises(OrderConflict, match='aktywnego'):
            register_script_repair(store, job, action(key='new-key-live'))


@pytest.mark.parametrize('fault', ['draft_bytes', 'receipt_bytes', 'archive_bytes'])
def test_integrity_fault_blocks_registration_and_never_fakes_resume(store, four_thoughts, fault):
    job, _ = rejected_job(store, four_thoughts)
    if fault != 'draft_bytes':
        register_script_repair(store, job, action())
        with store.lease(job) as token:
            store.block(job, token, 'script_repair/1', category='invalid_output', message='A new explicit assessment is needed')
    state = store.get(job)
    root = Path(state['artifact_dir'])
    if fault == 'draft_bytes':
        row = next(row for row in state['checkpoints'] if row['stage'] == 'script_draft')
        target = root / row['files'][0]['path']
    elif fault == 'receipt_bytes':
        target = next((root / 'studio_script_recovery/requests').glob('*.json'))
    else:
        target = next((root / 'studio_script_recovery/bytes').iterdir())
    target.write_bytes(b'tampered')
    snapshot = recovery_snapshot(store, job)
    assert snapshot['kind'] == 'integrity' and not snapshot['can_repair_script']
    with pytest.raises(OrderConflict, match='Dowody'):
        register_script_repair(store, job, action(key='new-key-integrity'))
    assert store.get(job)['provider_calls'] == state['provider_calls']


@pytest.mark.parametrize('stage,category,kind', [
    ('assets/image_local/flux2-trial/s01', 'dependency_failure', 'resource'),
    ('assets/image_local/s01', 'resource_unavailable', 'resource'),
    ('asset_planning', 'asset_candidates_waiting', 'assets'),
    ('waiting_for_runtime', 'resource_unavailable', 'resource'),
    ('rendering', 'invalid_input', 'other'),
])
def test_non_script_blockers_have_no_script_repair_button(store, four_thoughts, stage, category, kind):
    job, _ = rejected_job(store, four_thoughts)
    with store.lease(job) as token:
        store.block(job, token, stage, category=category, message='Controlled tool dependency failure')
    snapshot = recovery_snapshot(store, job)
    assert snapshot['kind'] == kind and not snapshot['can_repair_script']
    if stage.startswith('assets/image'):
        assert 'Kandydat nie powstał' in snapshot['message']


@pytest.mark.parametrize('fault,expected', [('audio', 'głos'), ('arithmetic', 'obliczenia'), ('calls', 'limit wywołań'), ('attempts', 'limit 20')])
def test_scope_and_budgets_remain_real(store, four_thoughts, fault, expected, monkeypatch):
    job, _ = rejected_job(store, four_thoughts)
    if fault == 'audio':
        monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
        with store.lease(job) as token:
            store.begin(job, token, 'voice/s01', {})
            path = write_json(store.context(job).artifact_dir / 'voice-evidence.json', {'audio_path': 'recorded.wav'}).artifact_path
            store.complete(job, token, 'voice/s01', {'audio_path': 'recorded.wav'}, [path])
            store.block(job, token, 'script_repair/1', category='invalid_output', message='Earlier script issue')
    else:
        if fault == 'arithmetic':
            with store.lease(job) as token:
                store.begin(job, token, 'script_calculations', {})
                value = {'claims': {'c1': {'result': 100}}}
                path = write_json(store.context(job).artifact_dir / 'calculations.json', value).artifact_path
                store.complete(job, token, 'script_calculations', value, [path])
                store.block(job, token, 'script_repair/1', category='invalid_output', message='Calculation narrative needs review')
        with connect(store.settings) as db:
            if fault == 'calls':
                maximum = store.get(job)['max_provider_calls']
                db.execute('UPDATE pipeline_job_orders SET provider_calls=? WHERE job_id=?', (maximum, job))
            elif fault == 'attempts':
                db.execute("UPDATE pipeline_stage_checkpoints SET attempts=20 WHERE job_id=? AND stage='script_repair/1'", (job,))
            db.commit()
    snapshot = recovery_snapshot(store, job)
    assert not snapshot['can_repair_script'] and expected in snapshot['repair_blocker']
    with pytest.raises(OrderConflict):
        register_script_repair(store, job, action())


def test_overlay_retains_original_scoped_feedback_hash_without_mutating_draft_inputs(store, four_thoughts, feedback):
    job = store.create(ProductionOrder(topic='A guide then a rejected draft', delivery_mode='local_only'))['id']
    feedback.issues[0].segment_ids = []
    register_feedback(store.settings, job, feedback, approved=True)
    with store.lease(job) as token:
        store.begin(job, token, 'script_draft', {'contract_version': order_script.SCRIPT_PROMPT_VERSION})
        path = write_json(store.context(job).artifact_dir / 'script-source.json', four_thoughts.model_dump(mode='json')).artifact_path
        store.complete(job, token, 'script_draft', four_thoughts.model_dump(mode='json'), [path])
        store.block(job, token, 'script_repair/1', category='script_quality_limit', message=json.dumps([
            {'code': 'word_budget', 'segment_ids': ['s02'], 'detail': 'The actual sentence needs a shorter complete thought.'}]))
    prior = store.get(job)
    registration = register_script_repair(store, job, action(note='Keep the cause and consequence clear.'))
    scoped = load_feedback(store, job)
    context = store.context(job)
    context.artifacts['studio_script_recovery'] = {**registration['request'], 'digest': content_hash(registration['request'])}
    context.artifacts['scoped_script_guidance'] = scoped['feedback']
    before_config = copy.deepcopy(context.artifacts['project_profile']['production'])
    apply_recovery_guidance(context)
    assert context.artifacts['project_profile']['production'] == before_config
    assert store.get(job)['profile_hash'] == prior['profile_hash']
    assert context.artifacts['scoped_script_guidance']['issues'][0]['code'] == 'temporal_logic'
    assert context.artifacts['scoped_script_guidance']['issues'][-1]['code'] == 'word_budget'
    assert 'Keep the cause and consequence clear' in order_script.scoped_guidance_contract(context)
    assert registration['request']['base_feedback_hash'] == scoped['feedback_hash']


def test_actual_operations_bounded_across_restarts_and_cache_does_not_consume_more(store, four_thoughts):
    job, _ = rejected_job(store, four_thoughts)
    register_script_repair(store, job, action())
    calls = []
    async def fail_schema():
        calls.append('actual attempted response')
        raise ValueError('Controlled schema mismatch')
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        prepare_recovery_context(executor)
        with pytest.raises(ProductionBlocked, match='three-operation'):
            asyncio.run(executor.run('script_repair/1', {'new_digest': 'fixture'}, fail_schema, local_model_call=True))
    assert len(calls) == 3
    assert next(row for row in store.get(job)['checkpoints'] if row['stage'] == 'script_repair/1')['attempts'] == 4
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        prepare_recovery_context(executor)
        with pytest.raises(ProductionBlocked, match='three-operation'):
            asyncio.run(executor.run('script_repair/1', {'new_digest': 'fixture'}, fail_schema, local_model_call=True))
    assert len(calls) == 3


def test_verified_cache_reuse_does_not_increment_studio_operation_count(store, four_thoughts):
    job, _ = rejected_job(store, four_thoughts)
    register_script_repair(store, job, action())
    async def actual():
        return write_json(store.context(job).artifact_dir / 'repaired-source.json', four_thoughts.model_dump(mode='json'))
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        prepare_recovery_context(executor)
        first = asyncio.run(executor.run('script_repair/1', {'same': True}, actual, local_model_call=True))
        cached = asyncio.run(executor.run('script_repair/1', {'same': True}, actual, local_model_call=True))
    assert cached == first
    with connect(store.settings) as db:
        row = db.execute('SELECT writes_used FROM production_studio_script_recovery WHERE job_id=?', (job,)).fetchone()
    assert row['writes_used'] == 1


def test_stage_attempt_refusal_does_not_consume_a_studio_review(store, four_thoughts):
    job,_=rejected_job(store,four_thoughts,key='bounded-refusal-fixture')
    register_script_repair(store,job,action('bounded-refusal-action'))
    calls=[]
    async def never_called():
        calls.append(True)
        raise AssertionError('An exhausted stage must not call a model')
    with store.lease(job) as token:
        maximum=store.get(job)['profile']['production']['max_attempts_per_element']
        for index in range(1,maximum):
            store.begin(job,token,'script_review/0',{'fixture':index},local_model_call=True)
            store.fail(job,token,'script_review/0',category='invalid_output',message='Fixture refusal')
        before=store.get(job)
        executor=StageExecutor(store,job,token)
        prepare_recovery_context(executor)
        with pytest.raises(ProductionBlocked,match='attempt_limit'):
            asyncio.run(executor.run('script_review/0',{'fixture':'exhausted'},never_called,local_model_call=True))
        after=store.get(job)
    assert calls==[]
    assert before['provider_calls']==after['provider_calls']
    assert next(r for r in after['checkpoints'] if r['stage']=='script_review/0')['attempts']==maximum
    with connect(store.settings) as db:
        row=db.execute('SELECT reviews_used FROM production_studio_script_recovery WHERE job_id=?',(job,)).fetchone()
    assert row['reviews_used']==0


def test_api_get_post_are_concrete_and_only_normal_dispatcher_launches(tmp_path, monkeypatch, four_thoughts):
    app = create_test_app(tmp_path)
    launched = []
    def dispatch(self, job_id):
        launched.append(job_id)
        with connect(self.settings) as db:
            _event(db, job_id, None, 'worker_launched', {'controlled_dispatch': True})
            db.commit()
        return {**order_status(self.store.get(job_id)), 'worker_requested': True}
    monkeypatch.setattr(routes.ProductionOrderDispatcher, 'dispatch', dispatch)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        store = ProductionOrderStore(app.state.settings)
        job, _ = rejected_job(store, four_thoughts)
        before = store.get(job)
        response = client.get(f'/api/production/orders/{job}/recovery')
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert response.json()['can_repair_script']
        assert store.get(job)['history'] == before['history']
        body = {'note': 'Use a concrete choice instead of vague motivation.', 'request_key': 'api-studio-action-001'}
        first = client.post(f'/api/production/orders/{job}/script-repair', json=body)
        second = client.post(f'/api/production/orders/{job}/script-repair', json=body)
        assert first.status_code == second.status_code == 202
        assert first.json()['id'] == second.json()['id'] == job
        assert first.json()['worker_requested'] is True and second.json()['worker_requested'] is False
        assert launched == [job]
        conflict = client.post(f'/api/production/orders/{job}/script-repair', json={**body, 'note': 'A changed action'})
        assert conflict.status_code == 409
        assert client.post(f'/api/production/orders/{job}/script-repair', json={**body, 'note': 'x' * 2001}).status_code == 422
        assert client.get('/api/production/orders/not-found/recovery').status_code == 404
        assert client.post('/api/production/orders/not-found/script-repair', json=body).status_code == 404
    assert store.get(job)['provider_calls'] == before['provider_calls']


def test_api_rejects_live_worker_and_wrong_recovery_kind(tmp_path, monkeypatch, four_thoughts):
    app = create_test_app(tmp_path)
    monkeypatch.setattr(routes.ProductionOrderDispatcher, 'dispatch', lambda *_: pytest.fail('Unsafe dispatch'))
    with TestClient(app) as client:
        seed_account(app.state.settings)
        store = ProductionOrderStore(app.state.settings)
        job, _ = rejected_job(store, four_thoughts)
        endpoint = f'/api/production/orders/{job}/script-repair'
        with store.lease(job):
            assert client.post(endpoint, json=action().model_dump()).status_code == 409
        with store.lease(job) as token:
            store.block(job, token, 'assets/image_local/s01', category='dependency_failure', message='Generator crashed')
        assert client.post(endpoint, json=action().model_dump()).status_code == 409


def test_studio_normal_pipeline_reuses_source_reviews_real_127_words_and_keeps_feedback_hash(
        execution, four_thoughts, feedback, monkeypatch):
    store = execution[0]
    job = store.create(ProductionOrder(topic='Where additional income goes', delivery_mode='local_only'))['id']
    feedback.issues[0].segment_ids = []
    register_feedback(store.settings, job, feedback, approved=True)
    frozen_feedback_hash = load_feedback(store, job)['feedback_hash']
    source = copy.deepcopy(four_thoughts.model_dump(mode='json'))
    for segment in source['segments']:
        if segment['type'] == 'practical':
            segment['type'] = 'explanation'
    oversized = copy.deepcopy(four_thoughts.model_dump(mode='json'))
    for index, segment in enumerate(oversized['segments']):
        words = segment['text'].rstrip('.').split()
        segment['text'] = ' '.join(words + ['carefully'] * ((32 if index < 3 else 31) - len(words))) + '.'
    phase = ['source']
    repairs = []
    draft_calls = []
    review_counts = []
    async def writer(settings, context, prompt, schema, **kwargs):
        if schema.__name__ == 'CalculationPlan':
            return {'rationale': 'No numerical example in this qualitative trial.', 'examples': []}
        if schema.__name__ in {'GuidedWBScript', 'AdaptedScript', 'EditorialScript'}:
            draft_calls.append(prompt)
            return {key: value for key, value in source.items() if key != 'claims'}
        if schema.__name__ in {'AdaptedScriptRepair', 'ScriptRepair', 'EditorialFieldRepair'}:
            if phase[0] == 'source':
                raise ValueError('script_repair deterministic: controlled earlier repair interruption')
            repairs.append(prompt)
            assert 'Clarify the allocation before new spending' in prompt
            result = oversized if len(repairs) == 1 else four_thoughts.model_dump(mode='json')
            return {'replacements': result['segments']}
        raise AssertionError(schema.__name__)
    async def reviewer(settings, context, candidate):
        issues = order_script.script_issues(candidate, context.artifacts['project_profile'])
        if issues:
            # This additional controlled wording issue scopes the general
            # writer to all sentences; QA still retains the real length/share.
            issues.append({'code': 'naturalness', 'segment_ids': [s.id for s in candidate.segments],
                           'detail': 'Replace repetitive wording with complete distinct instructions.'})
        if phase[0] == 'repair':
            review_counts.append(sum(len(s.text.split()) for s in candidate.segments))
        return {'status': 'failed' if issues else 'passed', 'issues': issues, 'semantic_review': 'controlled_honest_review'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    monkeypatch.setattr(order_script, 'try_deterministic_repair', lambda *args: (None, {}))
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    before = store.get(job)
    assert recovery_snapshot(store, job)['can_repair_script']
    request = register_script_repair(store, job, action(note='Clarify the allocation before new spending.'))['request']
    phase[0] = 'repair'
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
        cached = asyncio.run(produce_script(StageExecutor(store, job, token)))
    after = store.get(job)
    rows = {row['stage']: row for row in after['checkpoints']}
    assert len(draft_calls) == 1 and rows['script_draft']['attempts'] == 1
    assert review_counts == [sum(len(s['text'].split()) for s in source['segments']), 127,
                             sum(len(s.text.split()) for s in four_thoughts.segments)]
    assert len(repairs) == 2 and rows['script_repair/1']['attempts'] == 2
    assert rows['script_review/1']['output']['status'] == 'failed'
    assert 'word_budget' in {issue['code'] for issue in rows['script_review/1']['output']['issues']}
    assert result == cached and result['qa']['status'] == 'passed'
    assert result['qa']['feedback_hash'] == frozen_feedback_hash
    assert result['qa']['studio_recovery_digest'] == content_hash(request)
    assert result['provenance']['studio_recovery']['user_appearance_approved'] is False
    assert after['provider_calls'] == before['provider_calls'] + 5
    assert after['profile_hash'] == before['profile_hash'] and after['reserved_cost_units'] == before['reserved_cost_units']
    assert load_feedback(store, job)['feedback_hash'] == frozen_feedback_hash


def test_studio_never_accepts_third_failed_review_or_repeated_text(execution, four_thoughts, monkeypatch):
    store = execution[0]
    job, rejected = rejected_job(store, four_thoughts, key='three-review-source')
    register_script_repair(store, job, action())
    calls = []
    async def writer(settings, context, prompt, schema, **kwargs):
        if schema.__name__ == 'CalculationPlan':
            return {'rationale': 'No numerical example in this qualitative trial.', 'examples': []}
        if schema.__name__ in {'GuidedWBScript', 'AdaptedScript', 'EditorialScript'}:
            return {key: value for key, value in rejected.items() if key != 'claims'}
        if schema.__name__ in {'AdaptedScriptRepair', 'ScriptRepair', 'EditorialFieldRepair'}:
            calls.append(prompt)
            updated = copy.deepcopy(rejected)
            for segment in updated['segments']:
                segment['text'] = segment['text'].rstrip('.') + (' Clearly.' if len(calls) == 1 else ' Deliberately.')
            if schema.__name__ == 'EditorialFieldRepair':
                return {'patches': [{key: segment[key] for key in ('id','text','on_screen')} for segment in updated['segments']]}
            return {'replacements': updated['segments']}
        raise AssertionError(schema.__name__)
    async def rejected_review(settings, context, candidate):
        return {'status': 'failed', 'issues': [{'code': 'naturalness', 'segment_ids': [s.id for s in candidate.segments],
            'detail': 'Actual controlled reviewer still rejects this wording.'}], 'semantic_review': 'controlled_rejected'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', rejected_review)
    monkeypatch.setattr(order_script, 'try_deterministic_repair', lambda *args: (None, {}))
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='bounded selective repair'):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    state = store.get(job)
    assert len(calls) == 2 and not any(row['stage'] == 'script' for row in state['checkpoints'])
    assert state['status'] == 'blocked' and 'script_quality_limit' in state['error']
    assert all(row['output']['status'] == 'failed' for row in state['checkpoints'] if row['stage'].startswith('script_review/'))


def _legacy_three_rounds(store, job, feedback, four_thoughts, monkeypatch):
    _accept_first(store, job, feedback, four_thoughts, monkeypatch)
    root = store.context(job).artifact_dir
    canonical = next(row for row in store.get(job)['checkpoints'] if row['stage'] == 'script')
    old_hash = file_hash(root / 'script_feedback/request.json')
    files = [*canonical['files'], {'path': 'script_feedback/request.json', 'sha256': old_hash}]
    with connect(store.settings) as db:
        db.execute("UPDATE pipeline_stage_checkpoints SET files_json=? WHERE job_id=? AND stage='script'", (json.dumps(files), job))
        db.commit()
    for number in (2, 3):
        following = _followup(feedback) if number == 2 else feedback.model_copy(update={
            'reason': 'Correct the distinct recorded remaining defect after another independent rejected review.'})
        registered = register_feedback(store.settings, job, following, approved=True, followup=True)
        prefix = 'script_scoped/' + registered['feedback_hash'][:12]
        raw = four_thoughts.model_dump(mode='json')
        issues = [{'code': 'naturalness', 'segment_ids': ['s03'], 'detail': 'The actual latest reviewer rejects unnatural phrasing in the scene.'}]
        review = {'status': 'failed', 'issues': issues, 'feedback_hash': registered['feedback_hash'], 'script_hash': content_hash(raw)}
        with store.lease(job) as token:
            for stage, value in [(prefix + '/draft/1', raw), (prefix + '/review/1', review)]:
                store.begin(job, token, stage, {'controlled_evidence': number})
                path = write_json(root / (stage + '.json'), value).artifact_path
                store.complete(job, token, stage, value, [path])
            store.block(job, token, 'script_feedback', category='script_feedback_rejected', message=json.dumps(issues))
    return old_hash


def test_legacy_pointer_byte_archive_allows_real_b_followup_but_never_approves_it(
        store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    old_hash = _legacy_three_rounds(store, job, feedback, four_thoughts, monkeypatch)
    root = store.context(job).artifact_dir
    assert file_hash(root / 'script_feedback/request.json') != old_hash
    before = store.get(job)
    canonical = next(row for row in before['checkpoints'] if row['stage'] == 'script')
    assert recovery_snapshot(store, job)['can_repair_script']
    registered = register_script_repair(store, job, action(note='Correct the actual remaining unnatural phrase.'))
    assert registered['request']['mode'] == 'scoped_followup'
    current = load_feedback(store, job)
    assert current['round'] == 4 and current['feedback']['max_drafts'] == 3
    assert current['rejected_continuation']['status'] == 'rejected_not_accepted'
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls'] and after['reserved_cost_units'] == before['reserved_cost_units']
    assert next(row for row in after['checkpoints'] if row['stage'] == 'script')['output'] == canonical['output']
    assert any(event['event'] == 'editorial_rounds_authorized' and event['detail']['approved_maximum'] == 4 for event in after['history'])
    copies = [entry for entry in registered['request']['archive'] if entry.get('original_path') == 'script_feedback/request.json']
    assert copies and copies[0]['sha256'] == old_hash
    assert file_hash(root / copies[0]['archive_path']) == old_hash
    assert not any(row['stage'] == 'script_scoped/' + current['feedback_hash'][:12] + '/accepted' for row in after['checkpoints'])


@pytest.mark.parametrize('damage', ['known_pointer_archive', 'other_script_file'])
def test_legacy_pointer_exception_never_ignores_tampering(store, original_order, feedback, four_thoughts, monkeypatch, damage):
    job, _ = original_order
    old_hash = _legacy_three_rounds(store, job, feedback, four_thoughts, monkeypatch)
    root = store.context(job).artifact_dir
    scoped = load_feedback(store, job)
    if damage == 'known_pointer_archive':
        saved = next(entry for entry in scoped['original_evidence'] if entry['sha256'] == old_hash)
        (root / saved['archive_path']).write_text('{}')
    else:
        (root / 'script.json').write_text('{}')
    before = store.get(job)
    recovery = recovery_snapshot(store, job)
    assert recovery['kind'] == 'integrity' and not recovery['can_repair_script']
    with pytest.raises(OrderConflict, match='Dowody'):
        register_script_repair(store, job, action())
    assert store.get(job)['provider_calls'] == before['provider_calls']


def test_latest_rejected_repair_length_wins_over_old_84_word_review(store, four_thoughts):
    job, _ = rejected_job(store, four_thoughts, issues=[{'code': 'word_budget', 'segment_ids': ['s01', 's02'],
        'detail': '84 words; expected 90-125'}])
    with store.lease(job) as token:
        store.block(job, token, 'script_repair/1', category='invalid_output',
            message="Repair still has 127 spoken words; required 90-125. Use the provided per-segment word targets: {'s01': 20, 's02': 22, 's03': 23, 's04': 24, 's05': 18}")
    snapshot = recovery_snapshot(store, job)
    assert snapshot['can_repair_script']
    assert snapshot['issues'][0]['detail'].startswith('127 spoken words in the latest rejected repair')
    assert '127 słów' in snapshot['issues'][0]['message'] and '90–125' in snapshot['issues'][0]['message']
    assert snapshot['issues'][0]['segment_ids'] == ['s01', 's02', 's03', 's04', 's05']
    assert all('84 words' not in issue['detail'] for issue in snapshot['issues'])


def test_same_key_can_recover_dispatch_crash_without_new_registration_or_extra_allowance(tmp_path, monkeypatch, four_thoughts):
    app = create_test_app(tmp_path)
    launches = []
    def dispatch(self, job_id):
        launches.append(job_id)
        if len(launches) == 1:
            raise ProductionBlocked('Controlled failure before a worker was started')
        with connect(self.settings) as db:
            _event(db, job_id, None, 'worker_launched', {'controlled_dispatch': True})
            db.commit()
        return {**order_status(self.store.get(job_id)), 'worker_requested': True}
    monkeypatch.setattr(routes.ProductionOrderDispatcher, 'dispatch', dispatch)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        store = ProductionOrderStore(app.state.settings)
        job, _ = rejected_job(store, four_thoughts)
        before = store.get(job)
        endpoint = f'/api/production/orders/{job}/script-repair'
        body = action().model_dump()
        assert client.post(endpoint, json=body).status_code == 409
        receipt_before = load_recovery_request(store, job)
        recovered = client.post(endpoint, json=body)
        assert recovered.status_code == 202 and recovered.json()['worker_requested'] is True
        assert client.post(endpoint, json=body).json()['worker_requested'] is False
        assert load_recovery_request(store, job) == receipt_before
        assert launches == [job, job]
        with connect(store.settings) as db:
            row = db.execute('SELECT count(*) AS n, sum(writes_used) AS writes FROM production_studio_script_recovery WHERE job_id=?', (job,)).fetchone()
        assert row['n'] == 1 and row['writes'] == 0
        assert store.get(job)['provider_calls'] == before['provider_calls']


def test_changed_database_output_is_not_approved_just_because_file_hash_exists(store, four_thoughts):
    job, _ = rejected_job(store, four_thoughts)
    with connect(store.settings) as db:
        db.execute("UPDATE pipeline_stage_checkpoints SET output_json=? WHERE job_id=? AND stage='script_draft'", (json.dumps({'qa': {'status': 'passed'}}), job))
        db.commit()
    snapshot = recovery_snapshot(store, job)
    assert snapshot['kind'] == 'integrity' and not snapshot['can_repair_script']


def test_outside_production_storage_never_creates_repair_files(store, four_thoughts, tmp_path):
    job, _ = rejected_job(store, four_thoughts)
    forbidden = tmp_path / 'must-not-create-studio-recovery'
    with connect(store.settings) as db:
        db.execute('UPDATE pipeline_jobs SET artifact_dir=? WHERE id=?', (str(forbidden), job))
        db.commit()
    assert recovery_snapshot(store, job)['kind'] == 'integrity'
    with pytest.raises(ProductionBlocked, match='storage'):
        register_script_repair(store, job, action())
    assert not forbidden.exists()


def test_studio_identical_fingerprint_stops_even_for_legacy_narrative_exception(execution, four_thoughts, monkeypatch):
    store = execution[0]
    job, raw = rejected_job(store, four_thoughts, key='same-narrative-source')
    register_script_repair(store, job, action())
    async def writer(settings, context, prompt, schema, **kwargs):
        if schema.__name__ == 'CalculationPlan':
            return {'rationale': 'No numerical example in this qualitative trial.', 'examples': []}
        if schema.__name__ in {'GuidedWBScript', 'AdaptedScript', 'EditorialScript'}:
            return {key: value for key, value in raw.items() if key != 'claims'}
        pytest.fail('Identical narrative repair should be detected without another model call')
    async def rejected_review(settings, context, candidate):
        return {'status': 'failed', 'issues': [{'code': 'narrative_rules_violation', 'segment_ids': [s.id for s in candidate.segments],
            'detail': 'A real narrative defect remains and the writer must change its wording.'}], 'semantic_review': 'controlled_rejected'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', rejected_review)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='fingerprint matches'):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert not any(row['stage'] == 'script' for row in store.get(job)['checkpoints'])
