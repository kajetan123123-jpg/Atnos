"""Frozen registered upstream reuse; fixtures are not live pixel observations."""
import asyncio
import copy

import pytest

from atnos.production import asset_observation, asset_observation_cache, remotion_quality_repair
from atnos.production.channels.wealth_blueprint import remotion_order_edit
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_profile import ProductionOrder, content_hash, VisualQAProfile
from test_production_order_store import store
from test_renderer_refresh_followup import complete


@pytest.fixture
def frozen(store, monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    monkeypatch.setattr(remotion_order_edit, 'renderer_source_fingerprint', lambda: 'c'*64)
    job = store.create(ProductionOrder(topic='Frozen observation scope fixture', delivery_mode='local_only'))['id']
    root = store.context(job).artifact_dir
    source = store.settings.data_dir / 'licensed-fixture.mp4'
    source.write_bytes(b'isolated source binding fixture, not a real video')
    sheet = root / 'observed-frames.png'
    sheet.write_bytes(b'isolated observation sample binding fixture')
    asset = {'id': 'licensed-fixture', 'path': source.relative_to(store.settings.data_dir).as_posix(),
             'sha256': file_hash(source), 'description': 'Original provider title'}
    library = {'assets': [asset], 'manifests': [], 'rejected': []}
    model = VisualQAProfile.model_validate(store.get(job)['profile']['production'].get('visual_qa', {})).model
    observed = {'source_sha256': asset['sha256'], 'model': model, 'status': 'observed',
        'observation': {'summary': 'A fixture scene shows a notebook on a desk.',
            'visible_objects': ['notebook', 'desk'], 'visible_actions': [], 'readable_text': [],
            'limitations': 'Isolated fixture; no actual visual inference was performed.'}}
    with store.lease(job) as token:
        complete(store, job, token, 'asset_library', {'fixture': 'frozen library'}, root/'library.json', library)
        complete(store, job, token, 'asset_observation/licensed-fixture', {'cache_identity': 'previous real identity fixture'},
                 root/'observation.json', observed, [sheet])
        complete(store, job, token, 'asset_planning', {'fixture': 'completed frozen plan'}, root/'plan.json',
                 {'scenes': [{'segment_id': 's01', 'kind': 'fixture only'}]})
        state = store.get(job)
        body = {'job_id': job, 'before_package_sha256': 'b'*64,
            'request_hash': content_hash(state['request']), 'profile_hash': state['profile_hash'],
            'renderer_source_fingerprint': 'c'*64, 'reason': 'Explicit renderer-only fixture, not a new observation',
            'scope': 'renderer_and_downstream_only', 'attempts_reset': False, 'budget_reset': False,
            'user_appearance_approved': False}
        digest = content_hash(body)
        path = root/'renderer_refresh'/digest[:12]/'request.json'
        path.parent.mkdir(parents=True)
        complete(store, job, token, 'remotion_refresh/'+digest[:12], body, path,
                 {**body, 'digest': digest, 'archived_files': [], 'before_checkpoints': state['checkpoints']})
        store.block(job, token, 'rendering', category='renderer_quality_refresh_requested', message='Fixture registered')
    return store, job, library, source, sheet


def run_observation(frozen):
    store, job, library, *_ = frozen
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts['_renderer_refresh'] = remotion_quality_repair.load_renderer_refresh(ex)
        return asyncio.run(asset_observation.observe_library(ex, library))


def test_registered_refresh_reuses_original_observation_without_cache_model_or_new_stage(frozen, monkeypatch):
    store, job, library, *_ = frozen
    before = copy.deepcopy(store.get(job))
    for name in ('model_revision', 'load', 'save'):
        monkeypatch.setattr(asset_observation_cache, name, lambda *_a, **_k: pytest.fail('No re-observation or global cache lookup'))
    monkeypatch.setattr(asset_observation, 'describe_frames', lambda *_a, **_k: pytest.fail('No model inference'))
    result = run_observation(frozen)
    assert result['assets'][0]['provider_description'] == 'Original provider title'
    assert result['assets'][0]['description_source'] == 'sampled_frame_observation'
    assert result['assets'][0]['visual_review'] == 'observed_not_approved_for_scene'
    assert result['assets'][0]['observation']['source_sha256'] == library['assets'][0]['sha256']
    assert library['assets'][0]['description'] == 'Original provider title'
    after = store.get(job)
    assert after['checkpoints'] == before['checkpoints']
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']


@pytest.mark.parametrize('changed', ['source', 'sample', 'library', 'checkpoint', 'missing_observation'])
def test_registered_refresh_rejects_changed_upstream_without_inference(frozen, monkeypatch, changed):
    store, job, library, source, sheet = frozen
    if changed == 'source': source.write_bytes(b'changed source')
    if changed == 'sample': sheet.write_bytes(b'changed observation samples')
    if changed == 'library': library['assets'][0]['description'] = 'Unregistered replacement'
    if changed in {'checkpoint', 'missing_observation'}:
        original_get = store.get
        def changed_get(identity):
            state = copy.deepcopy(original_get(identity))
            if changed == 'missing_observation':
                state['checkpoints'] = [row for row in state['checkpoints'] if not row['stage'].startswith('asset_observation/')]
            else:
                next(row for row in state['checkpoints'] if row['stage'].startswith('asset_observation/'))['attempts'] += 1
            return state
        monkeypatch.setattr(store, 'get', changed_get)
    monkeypatch.setattr(asset_observation_cache, 'model_revision', lambda *_a, **_k: pytest.fail('No model query on rejection'))
    with pytest.raises(ProductionBlocked): run_observation(frozen)


def test_an_unregistered_scope_flag_cannot_reuse_any_observation(store):
    job = store.create(ProductionOrder(topic='No real registered scope fixture', delivery_mode='local_only'))['id']
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts['_renderer_refresh'] = {'scope': 'renderer_and_downstream_only', 'digest': 'a'*64}
        with pytest.raises(ProductionBlocked, match='registered upstream'):
            asyncio.run(asset_observation.observe_library(ex, {'assets': []}))


def test_existing_maintenance_restores_only_the_proven_outer_phase_overwrite(frozen):
    store, job, *_ = frozen
    original = store.get(job)
    original_plan = next(row for row in original['checkpoints'] if row['stage'] == 'asset_planning')
    with store.lease(job) as token:
        store.fail(job, token, 'asset_planning', category='execution_failure',
            message='Renderer-only correction requires intact cached upstream stage: asset_observation/licensed-fixture')
    before = store.get(job)
    result = store.restore_overwritten_asset_plan(job, reason='Proven renderer dependency guard overwritten an intact completed plan')
    assert result['restored'] is True
    after = store.get(job)
    restored = next(row for row in after['checkpoints'] if row['stage'] == 'asset_planning')
    for key in ('status', 'input_hash', 'output_json', 'files_json', 'attempts'):
        assert restored[key] == original_plan[key]
    assert after['error'] == before['error'] and after['status'] == 'blocked'
    assert after['provider_calls'] == before['provider_calls']
    assert run_observation(frozen)['assets'][0]['visual_review'] == 'observed_not_approved_for_scene'


def test_future_outer_phase_guard_preserves_the_completed_plan(frozen, monkeypatch):
    from atnos.production import order_execution
    from atnos.production.steps import order_assets
    from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
    from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
    store, job, *_ = frozen
    before = store.get(job)
    plan = next(row for row in before['checkpoints'] if row['stage'] == 'asset_planning')
    async def noop(*_a, **_k): return None
    async def blocked(*_a, **_k):
        raise ProductionBlocked('Renderer-only correction requires intact cached upstream stage: asset_observation/licensed-fixture')
    monkeypatch.setattr(order_execution, 'preflight_order', noop)
    monkeypatch.setattr(order_execution, 'produce_script', noop)
    monkeypatch.setattr(order_assets, 'produce_assets', blocked)
    monkeypatch.setattr(GpuRuntimeLockService, 'acquire', lambda *_a, **_k: 'fixture-lease')
    monkeypatch.setattr(GpuRuntimeLockService, 'release', lambda *_a, **_k: None)
    monkeypatch.setattr(ProductionGpuResidencyService, 'prepare_for_llm', lambda *_a, **_k: {})
    asyncio.run(order_execution.run_order(store.settings, job))
    after = store.get(job)
    assert after['status'] == 'blocked' and after['current_step'] == 'asset_planning'
    assert next(row for row in after['checkpoints'] if row['stage'] == 'asset_planning') == plan
    assert after['provider_calls'] == before['provider_calls']
