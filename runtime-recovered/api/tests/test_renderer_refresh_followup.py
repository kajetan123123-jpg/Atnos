"""Offline lifecycle fixtures: no actual video/model/provider is dispatched."""
import asyncio
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from atnos.production import remotion_quality_repair as repair
from atnos.production.channels.wealth_blueprint import remotion_order_edit as edit
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_profile import ProductionOrder, content_hash
from atnos.production.steps.order_media import _result
from test_production_order_store import store


def complete(store, job, token, stage, inputs, path, payload, files=()):
    assert store.begin(job, token, stage, inputs) is None
    result = _result(path, payload, files)
    store.complete(job, token, stage, result.payload, [result.artifact_path, *result.evidence_paths])


@pytest.fixture
def rejected_refresh(store, monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    job = store.create(ProductionOrder(topic='A genuinely rejected local render fixture', delivery_mode='local_only'))['id']
    root = store.context(job).artifact_dir
    old_source, new_source = 'a' * 64, 'c' * 64
    monkeypatch.setattr(edit, 'renderer_source_fingerprint', lambda: new_source)
    manifest = root / 'project_manifest.json'
    manifest.write_text('{"test_fixture":"manifest binding, not a real production manifest"}', encoding='utf8')
    raw = root / 'assets' / 'renderer_silent.mp4'
    final = root / 'assets' / 'final_short.mp4'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b'old render fixture only')
    final.write_bytes(b'old final fixture only')
    with store.lease(job) as token:
        for stage in ('script', 'voice', 'captions'):
            complete(store, job, token, stage, {'frozen': stage}, root / (stage + '.json'),
                     {'test_fixture': stage, 'qa': {'status': 'passed'}})
        # Preserve an actual stale package dependency for the follow-up archive.
        complete(store, job, token, 'package', {'fixture': 'old'}, root / 'package.json',
                 {'test_fixture': 'old package, never used as acceptance'}, [final])
        state = store.get(job)
        body = {'job_id': job, 'before_package_sha256': 'b' * 64,
            'request_hash': content_hash(state['request']), 'profile_hash': state['profile_hash'],
            'renderer_source_fingerprint': old_source, 'reason': 'First explicitly authorized renderer fixture correction',
            'scope': 'renderer_and_downstream_only', 'attempts_reset': False, 'budget_reset': False,
            'user_appearance_approved': False}
        digest = content_hash(body)
        previous_path = root / 'renderer_refresh' / digest[:12] / 'request.json'
        previous_path.parent.mkdir(parents=True, exist_ok=True)
        complete(store, job, token, 'remotion_refresh/' + digest[:12], body, previous_path,
                 {**body, 'digest': digest, 'archived_files': [], 'before_checkpoints': state['checkpoints']})
        raw.write_bytes(b'actual successor render fixture only, not real MP4')
        final.write_bytes(b'actual rejected successor AV fixture only, not real MP4')
        render_inputs = {'manifest_sha256': file_hash(manifest), 'renderer_source_fingerprint': old_source,
            'mode': edit.REMOTION_REQUIRED, 'renderer_boundary': edit.RENDERER_BOUNDARY}
        complete(store, job, token, 'remotion_render', render_inputs, root / 'remotion_result.json',
                 {'output_path': str(raw), 'output_sha256': file_hash(raw)}, [raw])
        complete(store, job, token, 'remotion_mux', {'raw': file_hash(raw)}, root / 'remotion_mux.json',
                 {'raw_video_sha256': file_hash(raw), 'final_output_sha256': file_hash(final)}, [final])
        review = {'status': 'failed', 'source': 'remotion_manifest_mp4', 'scene_id': 's01',
            'manifest_sha256': file_hash(manifest), 'video_sha256': file_hash(final), 'clip_sha256': file_hash(final),
            'verdict': {'status': 'fail', 'violations': ['Fixture: the planned pencil movement is absent']}}
        complete(store, job, token, 'semantic/s01', {'video': file_hash(final)}, root / 'qa' / 's01.json', review)
        store.block(job, token, 'qa', category='execution_failure', message='Fixture: scene needs renderer-specific correction')
    state = store.get(job)
    binding = repair.RendererRefreshFollowup(previous_action_digest=digest,
        expected_request_hash=content_hash(state['request']), expected_profile_hash=state['profile_hash'],
        expected_upstream_hash=repair.renderer_refresh_upstream_hash(state['checkpoints']),
        expected_manifest_sha256=file_hash(manifest), expected_render_sha256=file_hash(raw),
        expected_video_sha256=file_hash(final), expected_renderer_source_fingerprint=new_source,
        expected_failed_reviews={'semantic/s01': content_hash(review)})
    return store, job, binding


def register(trial, **kwargs):
    store, job, binding = trial
    return asyncio.run(repair.register_renderer_refresh(store, job, expected_package_sha256='b' * 64,
        reason='Repair the observed renderer occlusion and annotation movement defect',
        authorized=True, followup=binding, **kwargs))


def test_followup_uses_existing_registry_preserves_history_and_recovers(rejected_refresh, monkeypatch):
    store, job, binding = rejected_refresh
    before = copy.deepcopy(store.get(job))
    monkeypatch.setattr('atnos.production.publication_coordinator.verified_package',
                        lambda *_: pytest.fail('A stale predecessor package must not be treated as verified'))
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='source binding changed'):
            repair.load_renderer_refresh(StageExecutor(store, job, token))
    result = register(rejected_refresh)
    after = store.get(job)
    assert after['status'] == 'blocked' and after['current_step'] == 'rendering' and not after['worker_live']
    assert result['scope'] == 'renderer_and_downstream_only'
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    for old in before['checkpoints']:
        assert next(row for row in after['checkpoints'] if row['stage'] == old['stage']) == old
    assert after['history'][:len(before['history'])] == before['history']
    restored = ProductionOrderStore(store.settings)
    with restored.lease(job) as token:
        payload = repair.load_renderer_refresh(StageExecutor(restored, job, token))
    assert payload['followup'] == binding.model_dump(mode='json')
    assert payload['user_appearance_approved'] is False
    assert payload['before_package_sha256'] == 'b' * 64
    assert any(item['path'] == 'assets/final_short.mp4' for item in payload['stale_downstream_entries'])
    archive = next(item for item in payload['archived_files'] if item['original_path'] == 'assets/final_short.mp4')
    assert archive['sha256'] == binding.expected_video_sha256
    assert Path(archive['archive_path']).name == binding.expected_video_sha256+'.mp4'
    assert file_hash(restored.context(job).artifact_dir / archive['archive_path']) == binding.expected_video_sha256
    assert len(repair._refresh_rows(after)) == 2


@pytest.mark.parametrize('field', ['previous_action_digest', 'expected_request_hash', 'expected_profile_hash',
    'expected_upstream_hash', 'expected_manifest_sha256', 'expected_render_sha256', 'expected_video_sha256',
    'expected_renderer_source_fingerprint'])
def test_followup_rejects_any_stale_expected_binding_without_recording(rejected_refresh, field):
    store, job, binding = rejected_refresh
    before = store.get(job)
    broken = binding.model_copy(update={field: 'e' * 64})
    with pytest.raises(ProductionBlocked):register((store, job, broken))
    after = store.get(job)
    assert after['checkpoints'] == before['checkpoints'] and after['history'] == before['history']
    assert after['provider_calls'] == before['provider_calls']


def test_followup_requires_explicit_authorization_and_rejects_arbitrary_blocked(rejected_refresh):
    store, job, binding = rejected_refresh
    with pytest.raises(ValueError, match='authorized'):
        asyncio.run(repair.register_renderer_refresh(store, job, expected_package_sha256='b' * 64,
            reason='The actual renderer bug is understood and ready for a correction', followup=binding))
    other = store.create(ProductionOrder(topic='An arbitrary blocked fixture', delivery_mode='local_only'))['id']
    with store.lease(other) as token:store.block(other, token, 'qa', category='execution_failure', message='Unrelated blockage')
    with pytest.raises(ProductionBlocked, match='existing registered'):
        asyncio.run(repair.register_renderer_refresh(store, other, expected_package_sha256='b' * 64,
            reason='Attempt without a real predecessor correction registry', authorized=True, followup=binding))


def test_followup_requires_a_changed_renderer_not_just_another_reason(rejected_refresh, monkeypatch):
    store, job, binding = rejected_refresh
    before = store.get(job)
    monkeypatch.setattr(edit, 'renderer_source_fingerprint', lambda: 'a' * 64)
    binding = binding.model_copy(update={'expected_renderer_source_fingerprint': 'a' * 64})
    with pytest.raises(ProductionBlocked):register((store, job, binding))
    assert store.get(job)['checkpoints'] == before['checkpoints']


@pytest.mark.parametrize('mutation', ['scope', 'request', 'profile', 'predecessor_digest', 'upstream', 'qa_pass', 'qa_old_video', 'no_render_after_registry'])
def test_followup_rejects_nonlocal_or_changed_actual_evidence(rejected_refresh, monkeypatch, mutation):
    store, job, binding = rejected_refresh
    original = store.get
    before = original(job)
    def altered_get(job_id):
        state = copy.deepcopy(original(job_id))
        if job_id != job:return state
        if mutation == 'scope':state['request']['delivery_mode'] = 'scheduled'
        if mutation == 'request':state['request']['topic'] = 'Changed unapproved topic'
        if mutation == 'profile':state['profile_hash'] = 'e' * 64
        if mutation == 'predecessor_digest':repair._latest_refresh(state)['output']['digest'] = 'e' * 64
        if mutation == 'upstream':next(r for r in state['checkpoints'] if r['stage'] == 'voice')['attempts'] += 1
        if mutation.startswith('qa_'):
            output = next(r for r in state['checkpoints'] if r['stage'] == 'semantic/s01')['output']
            if mutation == 'qa_pass':output['status'] = 'passed'; output['verdict']['status'] = 'pass'
            else:output['video_sha256'] = 'e' * 64
        if mutation == 'no_render_after_registry':
            state['history'] = [e for e in state['history'] if e['stage'] != 'remotion_render']
        return state
    monkeypatch.setattr(store, 'get', altered_get)
    with pytest.raises(ProductionBlocked):register((store, job, binding))
    assert original(job)['checkpoints'] == before['checkpoints']
    assert original(job)['history'] == before['history']


def test_followup_rejects_missing_or_fake_failure_receipt(rejected_refresh):
    store, job, binding = rejected_refresh
    bad = binding.model_copy(update={'expected_failed_reviews': {'semantic/s01': 'e' * 64}})
    with pytest.raises(ProductionBlocked, match='genuine failed'):register((store, job, bad))
    bad = binding.model_copy(update={'expected_failed_reviews': {'package': 'e' * 64}})
    with pytest.raises(ProductionBlocked, match='genuine failed'):register((store, job, bad))


def test_followup_is_one_bounded_action_and_never_loads_different_source(rejected_refresh, monkeypatch):
    store, job, binding = rejected_refresh
    register(rejected_refresh)
    state = store.get(job)
    latest = repair._latest_refresh(state)['output']
    next_binding = binding.model_copy(update={'previous_action_digest': latest['digest'],
        'expected_renderer_source_fingerprint': 'd' * 64})
    monkeypatch.setattr(edit, 'renderer_source_fingerprint', lambda: 'd' * 64)
    with pytest.raises(ProductionBlocked, match='source binding changed'):
        with store.lease(job) as token:repair.load_renderer_refresh(StageExecutor(store, job, token))
    with pytest.raises(ProductionBlocked):register((store, job, next_binding))
    assert store.get(job)['checkpoints'] == state['checkpoints']
    with pytest.raises(ValidationError):repair.RendererRefreshFollowup.model_validate({**binding.model_dump(), 'followup_number': 2})


def test_load_does_not_ignore_a_malformed_existing_registry_stage(rejected_refresh, monkeypatch):
    store, job, binding = rejected_refresh
    original = store.get
    def malformed_get(job_id):
        state = copy.deepcopy(original(job_id))
        if job_id == job:repair._latest_refresh(state)['stage'] = 'remotion_refresh/unexpected'
        return state
    monkeypatch.setattr(store, 'get', malformed_get)
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='source binding changed'):
            repair.load_renderer_refresh(StageExecutor(store, job, token))


def test_followup_does_not_steal_live_worker_or_modify_upstream(rejected_refresh):
    store, job, binding = rejected_refresh
    before = store.get(job)
    with store.lease(job):
        with pytest.raises(ProductionBlocked):register(rejected_refresh)
    root = store.context(job).artifact_dir
    (root / 'voice.json').write_text('{"changed":"upstream fixture"}', encoding='utf8')
    with pytest.raises(ProductionBlocked, match='intact cached upstream'):register(rejected_refresh)
    assert store.get(job)['checkpoints'] == before['checkpoints']


def test_followup_rechecks_expected_evidence_after_acquiring_lease(rejected_refresh, monkeypatch):
    store, job, binding = rejected_refresh
    before = store.get(job)
    original = store.get
    reads = 0
    def changing_get(job_id):
        nonlocal reads
        state = original(job_id)
        if job_id == job:
            reads += 1
            if state['worker_live']:
                state = copy.deepcopy(state)
                state['profile_hash'] = 'e' * 64
        return state
    monkeypatch.setattr(store, 'get', changing_get)
    with pytest.raises(ProductionBlocked):register(rejected_refresh)
    assert reads >= 2
    assert original(job)['checkpoints'] == before['checkpoints']


def test_followup_preserves_cache_only_and_paid_guards_on_resume(rejected_refresh):
    store, job, binding = rejected_refresh
    register(rejected_refresh)
    async def never():pytest.fail('No inference, provider or external relay may run in this test')
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        executor.context.artifacts['_renderer_refresh'] = repair.load_renderer_refresh(executor)
        with pytest.raises(ProductionBlocked, match='intact cached upstream'):
            asyncio.run(executor.run('voice/new', {}, never, local_model_call=True))
        with pytest.raises(ProductionBlocked, match='paid or external'):
            asyncio.run(executor.run('semantic/s01', {}, never, provider_call=True, estimated_cost_usd=1))
    assert store.get(job)['provider_calls'] == 0
