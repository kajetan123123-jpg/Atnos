from contextlib import contextmanager
from copy import deepcopy
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production import wb_image_api as lane
from atnos.production import wb_local_flux_trial as flux
from atnos.production.asset_brain.models import AssetRecord, AssetGenerationProvenance
from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
from test_wb_image_api_production import trial, isolated_brain, prepared_executor, authorize
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts

runtime_files_original = flux._runtime_files


@pytest.fixture
def local_trial(trial, tmp_path, monkeypatch):
    store, job, _ = trial
    authorize(trial)
    lane.register_local_image_lane(store.settings, job, authorized=True)
    paths = {}
    for key in ('unet', 'clip', 'vae', 'python', 'main', 'model_paths', 'builder'):
        path = tmp_path / key
        path.write_bytes(('isolated fixture ' + key).encode())
        paths[key] = path
    monkeypatch.setattr(flux, '_model_files', lambda: {key: paths[key] for key in ('unet', 'clip', 'vae')})
    monkeypatch.setattr(flux, '_runtime_files', lambda settings: {key: paths[key] for key in ('python', 'main', 'model_paths', 'builder')})
    _, context, need = flux._scene_state(store, job, 's01')
    return store, job, context, need, paths


def register(local_trial):
    store, job, _, _, _ = local_trial
    return flux.register_flux_trial(store.settings, job, 's01', authorized=True)


def test_registration_is_explicit_immutable_and_activates_no_gpu(local_trial, monkeypatch):
    store, job, context, need, _ = local_trial
    before = store.get(job)
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda _: pytest.fail('registration must not launch'))
    with pytest.raises(ValueError, match='Explicit'):
        flux.register_flux_trial(store.settings, job, 's01')
    receipt = register(local_trial)
    assert receipt['max_attempts'] == 1 and receipt['user_approved'] is False
    assert receipt['workflow']['2']['inputs']['device'] == 'cpu'
    assert receipt['workflow']['8']['inputs']['cfg'] == 4
    assert receipt['workflow']['10']['inputs']['steps'] == 50
    assert register(local_trial)['digest'] == receipt['digest']
    with pytest.raises(OrderConflict, match='immutable'):
        flux.register_flux_trial(store.settings, job, 's01', authorized=True, seed=991)
    after = store.get(job)
    assert (before['provider_calls'], before['reserved_cost_units'], before['profile_hash']) == (
        after['provider_calls'], after['reserved_cost_units'], after['profile_hash'])
    with store.lease(job), pytest.raises(OrderConflict, match='stopped'):
        register(local_trial)


def test_automatic_choice_requires_policy_and_exact_current_worker_lease(local_trial,monkeypatch):
    from atnos.production.order_autonomy import enable
    store,job,context,need,_=local_trial
    monkeypatch.setattr(flux.ColdFluxSession,'_launch',lambda _:pytest.fail('Registration launched a model'))
    with store.lease(job) as token:
        with pytest.raises(OrderConflict,match='autonomy'):
            flux.register_flux_trial(store.settings,job,'s01',authorized=True,worker_token=token)
        enable(store.settings,job,reason='User authorizes autonomous local production with verified backend selection.')
        with pytest.raises(OrderConflict,match='lease'):
            flux.register_flux_trial(store.settings,job,'s01',authorized=True,worker_token='foreign-token')
        before=store.get(job)
        receipt=flux.register_flux_trial(store.settings,job,'s01',authorized=True,worker_token=token)
        assert receipt['max_attempts']==1 and receipt['user_approved'] is False
        assert 'Atnos autonomous' in receipt['source']
        assert store.get(job)['provider_calls']==before['provider_calls']
        assert store.get(job)['profile_hash']==before['profile_hash']


@pytest.mark.parametrize('tamper', ['receipt', 'model', 'graph', 'script'])
def test_tampered_authorization_or_inputs_abort_before_dispatch(local_trial, monkeypatch, tamper):
    store, job, context, need, paths = local_trial
    receipt = register(local_trial)
    if tamper in {'receipt', 'graph'}:
        path = context.artifact_dir / 'flux_trial/s01/authorization.json'
        data = json.loads(path.read_text())
        if tamper == 'graph':
            data['workflow']['10']['inputs']['steps'] = 4
        else:
            data['local_lane_digest'] = 'unapproved'
        path.write_text(json.dumps(data))
    elif tamper == 'model':
        paths['unet'].write_bytes(b'different weights')
    else:
        context.artifacts['script'] = {'different': 'script'}
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda _: pytest.fail('tampered authorization must not launch'))
    with pytest.raises(ProductionBlocked, match='integrity'):
        flux.load_flux_trial(store.settings, context, 's01', need, lane.load_image_lane(store.settings, job))
    assert store.get(job)['provider_calls'] == 0


def test_transient_writer_guidance_does_not_change_frozen_flux_authorization(local_trial):
    store, job, context, need, _ = local_trial
    receipt = register(local_trial)
    frozen = deepcopy(store.get(job)['profile'])
    context.artifacts['project_profile'] = deepcopy(frozen)
    context.artifacts['project_profile'].setdefault('feedback_rules', []).append(
        'Recovery guidance: correct the flagged practical instruction.')
    restored = flux.load_flux_trial(store.settings, context, 's01', need, lane.load_image_lane(store.settings, job))
    assert restored['digest'] == receipt['digest']
    assert store.get(job)['profile'] == frozen
    assert store.get(job)['provider_calls'] == 0


def test_changed_flux_runtime_manifest_can_only_recover_to_standard_lane_after_no_post(local_trial, monkeypatch):
    store, job, context, need, paths = local_trial
    receipt = register(local_trial)
    folder = context.artifact_dir / 'assets' / 'image_local' / 'flux_trial' / receipt['digest'][:16]
    folder.mkdir(parents=True)
    (folder / 'workflow.json').write_text(json.dumps(receipt['workflow']), encoding='utf-8')
    stage = f"assets/image_local/flux2-trial/s01/{receipt['digest'][:16]}"
    with store.lease(job) as token:
        store.begin(job, token, stage, {'trial': receipt['digest']}, local_model_call=True)
        store.fail(job, token, stage, category='dependency_failure',
                   message='Flux trial requires at least 16 GiB available RAM; no POST made')
    # Simulate that the normal Comfy service changed after this failed trial's
    # immutable receipt was recorded. The failed path must not execute Flux.
    paths['builder'].write_bytes(b'updated current Comfy builder')
    monkeypatch.setattr(flux.psutil, 'net_connections', lambda **_: [])
    before = store.get(job)
    restored = flux.load_flux_trial(store.settings, context, 's01', need,
                                   lane.load_image_lane(store.settings, job), allow_failed_fallback=True)
    checkpoint = next(row for row in store.get(job)['checkpoints'] if row['stage'] == stage)
    fallback = flux.verify_failed_flux_allows_local_comfy_fallback(
        context, 's01', restored, checkpoint, read_only=True)
    assert restored['digest'] == receipt['digest']
    assert fallback['no_post_made'] is True and fallback['flux_retry_permitted'] is False
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['profile_hash'] == before['profile_hash']
    assert next(row for row in after['checkpoints'] if row['stage'] == stage)['attempts'] == 1


@pytest.mark.parametrize('field', ['visual_style', 'production', 'channel_name'])
def test_transient_guidance_cannot_hide_effective_profile_changes(local_trial, field):
    store, job, context, need, _ = local_trial
    register(local_trial)
    context.artifacts['project_profile'] = deepcopy(store.get(job)['profile'])
    context.artifacts['project_profile'].setdefault('feedback_rules', []).append('Transient writer hint')
    context.artifacts['project_profile'][field] = {'changed': 'unauthorized'}
    with pytest.raises(ProductionBlocked, match='integrity'):
        flux.load_flux_trial(store.settings, context, 's01', need, lane.load_image_lane(store.settings, job))
    assert store.get(job)['provider_calls'] == 0


def test_low_memory_aborts_cold_preflight_without_launch_or_post(local_trial, monkeypatch):
    store, _, context, _, _ = local_trial
    receipt = register(local_trial)
    released = []
    class Lock:
        def __init__(self, _): pass
        def acquire(self, **_): return 'owned'
        def release(self, lease): released.append(lease)
    monkeypatch.setattr('atnos.services.gpu_runtime_lock_service.GpuRuntimeLockService', Lock)
    monkeypatch.setattr('atnos.services.hardware_guard_service.HardwareGuardService.require_gpu', lambda *_: None)
    monkeypatch.setattr('atnos.services.production_gpu_residency_service.ProductionGpuResidencyService.prepare_for_comfy', lambda _: {})
    monkeypatch.setattr('atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.stop_server', lambda *a, **k: {'stopped': True})
    monkeypatch.setattr('atnos.services.comfyui_runtime_service.ComfyUiRuntimeService._matching_install_processes', lambda _: [])
    monkeypatch.setattr(flux.psutil, 'virtual_memory', lambda: SimpleNamespace(available=flux.MIN_RAM - 1))
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda _: pytest.fail('not enough RAM'))
    with pytest.raises(flux.FluxTrialStopped, match='16 GiB'):
        with flux.ColdFluxSession(store.settings, receipt, context.artifact_dir / 'cold').open():
            pytest.fail('preflight must not yield')
    assert released == ['owned']


def test_existing_busy_or_unknown_comfy_is_never_stopped_twice(local_trial, monkeypatch):
    store, _, context, _, _ = local_trial
    receipt = register(local_trial)
    class Lock:
        def __init__(self, _): pass
        def acquire(self, **_): return 'owned'
        def release(self, _): pass
    monkeypatch.setattr('atnos.services.gpu_runtime_lock_service.GpuRuntimeLockService', Lock)
    monkeypatch.setattr('atnos.services.hardware_guard_service.HardwareGuardService.require_gpu', lambda *_: None)
    monkeypatch.setattr('atnos.services.production_gpu_residency_service.ProductionGpuResidencyService.prepare_for_comfy', lambda _: {})
    calls = []
    monkeypatch.setattr('atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.stop_server',
                        lambda *a, **k: (calls.append(k) or {'stopped': False}))
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda _: pytest.fail('busy queue'))
    with pytest.raises(flux.FluxTrialStopped, match='busy'):
        with flux.ColdFluxSession(store.settings, receipt, context.artifact_dir / 'cold').open():
            pass
    assert calls == [{'require_idle': True}]


@pytest.mark.parametrize('failure', ['timeout', 'native_exit'])
def test_one_post_intent_survives_failure_and_forbids_resubmit(local_trial, tmp_path, failure):
    receipt = register(local_trial)
    write_json(tmp_path / 'runtime.json', {'identity':{'pid':111,'created':1}})
    calls = []
    class Service:
        def _queue_prompt(self, graph):
            calls.append(graph)
            if failure == 'timeout':
                raise TimeoutError('uncertain queue outcome')
            return 'actual-prompt'
        def _wait_for_image(self, id, **kwargs):
            raise RuntimeError('native runtime exited')
    for _ in range(2):
        with pytest.raises(flux.FluxTrialStopped):
            flux._submit_and_wait_once(Service(), receipt, tmp_path)
    assert len(calls) == 1
    intent = json.loads((tmp_path / 'dispatch.json').read_text())
    assert intent['status'] == ('dispatch_intent' if failure == 'timeout' else 'queued')
    assert (tmp_path / 'failure.json').is_file()


def _failed_flux_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(flux, '_on_e', lambda path: Path(path).resolve())
    context = SimpleNamespace(job_id='prod-test', artifact_dir=tmp_path / 'order')
    scene_id = 's01'
    digest = 'a' * 64
    receipt = {'digest': digest, 'job_id': context.job_id, 'scene_id': scene_id,
        'version': 'wb-flux2-klein-minote-style-candidate-v1', 'max_attempts': 1,
        'workflow_sha256': 'workflow-hash'}
    folder = context.artifact_dir / 'assets' / 'image_local' / 'flux_trial' / digest[:16]
    (folder / 'output').mkdir(parents=True)
    (folder / 'dispatch.json').write_text(json.dumps({'prompt_id': 'prompt-123', 'status': 'queued',
        'trial_digest': digest, 'workflow_sha256': receipt['workflow_sha256']}), encoding='utf-8')
    (folder / 'failure.json').write_text(json.dumps({'automatic_retry': False, 'dispatch_preserved': True,
        'error': 'ComfyUI runtime stopped while processing prompt prompt-123: [WinError 10061] connection refused',
        'error_type': 'ComfyUiImageGenerationError', 'status': 'stopped_nonretryable', 'trial_digest': digest}),
        encoding='utf-8')
    instance = {'argv': ['E:\\Atnos\\Tools\\Comfy\\python.exe', '-s', 'E:\\Atnos\\Tools\\Comfy\\main.py',
        '--listen', '127.0.0.1', '--port', '8195', '--base-directory', str(folder),
        '--output-directory', str(folder / 'output')],
        'created': 1000.0, 'cwd': 'E:\\Atnos\\Tools\\Comfy', 'exe': 'E:\\Atnos\\Tools\\Comfy\\python.exe',
        'marker': 'wb-flux2-klein-minote-style-candidate-v1', 'pid': 654321, 'port': 8195, 'trial_digest': digest}
    (folder / 'instance.json').write_text(json.dumps(instance), encoding='utf-8')
    checkpoint = {'stage': f'assets/image_local/flux2-trial/{scene_id}/{digest[:16]}', 'status': 'failed',
        'attempts': 1, 'error_json': json.dumps({'category': 'dependency_failure',
        'message': 'Flux trial stopped; outcome evidence preserved and no second POST permitted'})}
    return context, scene_id, receipt, checkpoint


def test_exact_stopped_flux_failure_records_comfy_fallback_without_retry(tmp_path, monkeypatch):
    context, scene_id, receipt, checkpoint = _failed_flux_evidence(tmp_path, monkeypatch)
    def absent(pid):
        raise flux.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(flux.psutil, 'Process', absent)

    fallback = flux.verify_failed_flux_allows_local_comfy_fallback(context, scene_id, receipt, checkpoint)
    assert fallback['fallback_provider'] == 'comfyui-local'
    assert fallback['flux_retry_permitted'] is False
    assert fallback['appearance_approval'] == 'pending'
    evidence = Path(fallback['evidence_path'])
    assert evidence.is_file()
    assert json.loads(evidence.read_text(encoding='utf-8'))['digest'] == fallback['digest']
    assert flux.verify_failed_flux_allows_local_comfy_fallback(context, scene_id, receipt, checkpoint) == fallback


@pytest.mark.parametrize('tamper', ['retry_flag', 'output_file', 'wrong_prompt'])
def test_flux_fallback_fails_closed_on_uncertain_or_existing_result(tmp_path, monkeypatch, tamper):
    context, scene_id, receipt, checkpoint = _failed_flux_evidence(tmp_path, monkeypatch)
    folder = context.artifact_dir / 'assets' / 'image_local' / 'flux_trial' / receipt['digest'][:16]
    if tamper == 'retry_flag':
        failure = json.loads((folder / 'failure.json').read_text(encoding='utf-8'))
        failure['automatic_retry'] = True
        (folder / 'failure.json').write_text(json.dumps(failure), encoding='utf-8')
    elif tamper == 'output_file':
        (folder / 'output' / 'unexpected.png').write_bytes(b'candidate')
    else:
        failure = json.loads((folder / 'failure.json').read_text(encoding='utf-8'))
        failure['error'] = 'connection refused for another prompt'
        (folder / 'failure.json').write_text(json.dumps(failure), encoding='utf-8')
    def absent(pid):
        raise flux.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(flux.psutil, 'Process', absent)
    with pytest.raises(ProductionBlocked, match='fallback|Flux'):
        flux.verify_failed_flux_allows_local_comfy_fallback(context, scene_id, receipt, checkpoint)


def test_flux_fallback_blocks_if_original_process_is_still_present(tmp_path, monkeypatch):
    context, scene_id, receipt, checkpoint = _failed_flux_evidence(tmp_path, monkeypatch)
    folder = context.artifact_dir / 'assets' / 'image_local' / 'flux_trial' / receipt['digest'][:16]
    instance = json.loads((folder / 'instance.json').read_text(encoding='utf-8'))
    class LiveProcess:
        def __init__(self, pid): assert pid == instance['pid']
        def create_time(self): return instance['created']
        def exe(self): return instance['exe']
        def cwd(self): return instance['cwd']
        def cmdline(self): return instance['argv']
    monkeypatch.setattr(flux.psutil, 'Process', LiveProcess)
    with pytest.raises(ProductionBlocked, match='still present'):
        flux.verify_failed_flux_allows_local_comfy_fallback(context, scene_id, receipt, checkpoint)


def _failed_pre_dispatch_flux_evidence(tmp_path, monkeypatch, message='Flux trial requires at least 16 GiB available RAM; no POST made'):
    monkeypatch.setattr(flux, '_on_e', lambda path: Path(path).resolve())
    monkeypatch.setattr(flux.psutil, 'net_connections', lambda **_: [])
    context = SimpleNamespace(job_id='prod-test', artifact_dir=tmp_path / 'order')
    scene_id = 's01'
    workflow = {'1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'trial.safetensors'}}}
    digest = 'b' * 64
    receipt = {'digest': digest, 'job_id': context.job_id, 'scene_id': scene_id,
        'version': 'wb-flux2-klein-minote-style-candidate-v1', 'max_attempts': 1,
        'workflow': workflow, 'workflow_sha256': flux.content_hash(workflow)}
    folder = context.artifact_dir / 'assets' / 'image_local' / 'flux_trial' / digest[:16]
    folder.mkdir(parents=True)
    (folder / 'workflow.json').write_text(json.dumps(workflow), encoding='utf-8')
    checkpoint = {'stage': f'assets/image_local/flux2-trial/{scene_id}/{digest[:16]}',
        'status': 'failed', 'attempts': 1, 'input_hash': 'c' * 64,
        'error_json': json.dumps({'category': 'dependency_failure', 'message': message})}
    return context, scene_id, receipt, checkpoint, folder


def test_exact_pre_dispatch_resource_gate_records_local_fallback_without_flux_retry(tmp_path, monkeypatch):
    context, scene_id, receipt, checkpoint, folder = _failed_pre_dispatch_flux_evidence(tmp_path, monkeypatch)
    fallback = flux.verify_failed_flux_allows_local_comfy_fallback(context, scene_id, receipt, checkpoint)
    assert fallback['outcome'] == 'abandoned_without_product'
    assert fallback['no_post_made'] is True and fallback['prompt_id'] is None
    assert fallback['flux_retry_permitted'] is False
    assert fallback['appearance_approval'] == 'pending' and fallback['user_approved'] is False
    assert Path(fallback['evidence_path']).is_file()
    assert flux.verify_failed_flux_allows_local_comfy_fallback(context, scene_id, receipt, checkpoint) == fallback
    assert flux.verify_failed_flux_allows_local_comfy_fallback(
        context, scene_id, receipt, checkpoint, read_only=True) == fallback


@pytest.mark.parametrize('tamper', ['dispatch', 'extra_file', 'listener', 'wrong_workflow', 'wrong_message'])
def test_pre_dispatch_fallback_fails_closed_on_any_possible_request_or_changed_evidence(tmp_path, monkeypatch, tamper):
    context, scene_id, receipt, checkpoint, folder = _failed_pre_dispatch_flux_evidence(tmp_path, monkeypatch)
    if tamper == 'dispatch':
        (folder / 'dispatch.json').write_text('{}', encoding='utf-8')
    elif tamper == 'extra_file':
        (folder / 'mystery.json').write_text('{}', encoding='utf-8')
    elif tamper == 'listener':
        monkeypatch.setattr(flux.psutil, 'net_connections', lambda **_: [SimpleNamespace(
            status=flux.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=8195))])
    elif tamper == 'wrong_workflow':
        (folder / 'workflow.json').write_text('{}', encoding='utf-8')
    else:
        checkpoint['error_json'] = json.dumps({'category': 'dependency_failure', 'message': 'not a known preflight gate'})
    with pytest.raises(ProductionBlocked):
        flux.verify_failed_flux_allows_local_comfy_fallback(context, scene_id, receipt, checkpoint)
    assert not (context.artifact_dir / 'assets' / 'image_local' / 'flux_recovery').exists()


def test_normal_candidate_stage_counts_once_and_never_uses_cloud_or_global_backend_route(local_trial, isolated_brain, monkeypatch):
    store, job, _, _, _ = local_trial
    receipt = register(local_trial)
    guard = ComfyUiImageGenerationService._runtime_incompatible_backends
    calls = []
    def fail(*args):
        calls.append(args)
        raise flux.FluxTrialStopped('native runtime exit')
    monkeypatch.setattr(flux, 'generate_flux_candidate', fail)
    monkeypatch.setattr(lane, '_generate_candidate', lambda *_: pytest.fail('cloud is prohibited'))
    monkeypatch.setattr(lane, '_generate_local_candidate', lambda *_: pytest.fail('no SDXL automatic fallback'))
    for _ in range(2):
        with store.lease(job) as token, pytest.raises(ProductionBlocked):
            asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    state = store.get(job)
    checkpoint = next(s for s in state['checkpoints'] if '/flux2-trial/' in s['stage'])
    assert checkpoint['attempts'] == 1 and len(calls) == 1
    assert state['provider_calls'] == 1 and state['reserved_cost_units'] == 0
    assert ComfyUiImageGenerationService._runtime_incompatible_backends is guard


def test_pending_flux_cache_tamper_blocks_without_second_generation(local_trial, isolated_brain, monkeypatch, tmp_path):
    store, job, _, _, _ = local_trial
    receipt = register(local_trial)
    folder = tmp_path / 'candidate'
    folder.mkdir()
    image = folder / 'image.png'
    image.write_bytes(b'original image bytes')
    graph = write_json(folder / 'workflow.json', receipt['workflow']).artifact_path
    history = write_json(folder / 'history.json', {'actual-id': {'status': {'completed': True}}}).artifact_path
    evidence = write_json(folder / 'metadata.json', {'trial_digest': receipt['digest'],
        'workflow_sha256': receipt['workflow_sha256'], 'image_sha256': flux.file_hash(image),
        'history_sha256': flux.file_hash(history), 'user_approved': False}).artifact_path
    calls = []
    def generate(settings, context, need, style, tags, lane_data, fingerprint, trial_receipt):
        calls.append(1)
        asset = AssetRecord(id='scoped-flux', type='background', channel='wealth_blueprint', production_role='background',
            source='comfyui-flux-trial', quality_status='AWAITING_REVIEW', tags=tags, file_path=str(image),
            generation=AssetGenerationProvenance(prompt='plain envelope', workflow_id=flux.VERSION, model='test',
                generated_at='2026-10-01', source_workflow=flux.VERSION, channel='wealth_blueprint',
                fingerprint=fingerprint, evidence_path=str(evidence)))
        isolated_brain.append(asset)
        return write_json(context.artifact_dir / 'mock-candidate.json',
                          {'quality_status': asset.quality_status, 'asset_id': asset.id, 'output_path': str(image)})
    monkeypatch.setattr(flux, 'generate_flux_candidate', generate)
    for _ in range(2):
        with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
            asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert calls == [1] and store.get(job)['provider_calls'] == 1
    image.write_bytes(b'changed image')
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='cache integrity'):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert calls == [1] and isolated_brain[0].approved is False


def test_success_uses_one_post_then_reads_and_validates_exact_prompt(local_trial, monkeypatch, tmp_path):
    receipt = register(local_trial)
    write_json(tmp_path / 'runtime.json', {'identity':{'pid':111,'created':1}})
    calls = []
    history = {'p-one': {'status': {'completed': True, 'status_str': 'success'}, 'outputs': {'13': {'images': []}}}}
    class Client:
        def __init__(self, **_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, url):
            calls.append(('get', url))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: history)
    class Service:
        runtime = SimpleNamespace(base_url='http://127.0.0.1:8195')
        def _queue_prompt(self, graph): calls.append(('post', content_hash(graph))); return 'p-one'
        def _wait_for_image(self, id, **kwargs):
            calls.append(('wait', id, kwargs)); return {'filename': 'actual.png'}
        def _download_image(self, meta): calls.append(('download', meta)); return b'validated PNG'
        def _validate_image_bytes(self, data, **kwargs):
            calls.append(('validate', data, kwargs))
    monkeypatch.setattr(flux.httpx, 'Client', Client)
    result = flux._submit_and_wait_once(Service(), receipt, tmp_path)
    assert result == (b'validated PNG', 'p-one', history)
    assert len([item for item in calls if item[0] == 'post']) == 1
    assert calls[-1][2] == {'expected_width': 768, 'expected_height': 1344}
    assert json.loads((tmp_path / 'dispatch.json').read_text())['prompt_id'] == 'p-one'
    with pytest.raises(flux.FluxTrialStopped, match='already dispatched'):
        flux._submit_and_wait_once(Service(), receipt, tmp_path)
    assert len([item for item in calls if item[0] == 'post']) == 1


@pytest.mark.parametrize('failure', ['missing_native_node', 'low_vram', 'foreign_queue'])
def test_native_cold_schema_memory_queue_preflight_fails_before_submission(local_trial, monkeypatch, tmp_path, failure):
    store, _, _, _, _ = local_trial
    receipt = register(local_trial)
    session = flux.ColdFluxSession(store.settings, receipt, tmp_path)
    session.process = SimpleNamespace(poll=lambda: None)
    session.identity = {'pid': 321, 'port': 8195}
    (tmp_path / 'model-paths.yaml').write_text('isolated models')
    monkeypatch.setattr(session, '_owns_listener', lambda: True)
    nodes = {row['class_type']: {} for row in receipt['workflow'].values()}
    for cls, key, expected in [('UNETLoader', 'unet_name', flux.MODEL_NAMES['unet'][1]),
                               ('CLIPLoader', 'clip_name', flux.MODEL_NAMES['clip'][1]),
                               ('VAELoader', 'vae_name', flux.MODEL_NAMES['vae'][1])]:
        nodes[cls] = {'input': {'required': {key: [[expected]]}}}
    nodes['CLIPLoader']['input']['required']['type'] = [['flux2']]
    nodes['CLIPLoader']['input']['optional'] = {'device': [['cpu', 'default']]}
    if failure == 'missing_native_node':
        del nodes['Flux2Scheduler']
    payload = {'/object_info': nodes,
        '/system_stats': {'devices': [{'vram_free': flux.MIN_VRAM - 1 if failure == 'low_vram' else flux.MIN_VRAM}]},
        '/queue': {'queue_running': [1] if failure == 'foreign_queue' else [], 'queue_pending': []}}
    monkeypatch.setattr(session, '_request', lambda path: payload[path])
    with pytest.raises(flux.FluxTrialStopped, match='no POST'):
        session._preflight()
    assert not (tmp_path / 'dispatch.json').exists()


def test_stop_cannot_terminate_reused_or_foreign_pid(local_trial, monkeypatch, tmp_path):
    store, _, _, _, paths = local_trial
    receipt = register(local_trial)
    session = flux.ColdFluxSession(store.settings, receipt, tmp_path)
    session.process = SimpleNamespace(pid=321, poll=lambda: None)
    session.identity = {'pid': 321, 'created': 1000, 'argv': [str(paths['python'])],
                        'cwd': str(tmp_path), 'port': 8195}
    terminated = []
    monkeypatch.setattr(flux.psutil, 'Process', lambda _: SimpleNamespace(
        create_time=lambda: 1001, terminate=lambda: terminated.append(321)))
    with pytest.raises(flux.FluxTrialStopped, match='ownership changed'):
        session._stop_own()
    assert terminated == []


def test_stop_preserves_foreign_request_even_in_our_cold_process(local_trial, monkeypatch, tmp_path):
    store, _, _, _, paths = local_trial
    receipt = register(local_trial)
    session = flux.ColdFluxSession(store.settings, receipt, tmp_path)
    session.process = SimpleNamespace(pid=321, poll=lambda: None)
    session.identity = {'pid': 321, 'created': 1000, 'argv': [str(paths['python'])],
                        'cwd': str(tmp_path), 'port': 8195}
    terminated = []
    monkeypatch.setattr(flux.psutil, 'Process', lambda _: SimpleNamespace(
        create_time=lambda: 1000, cmdline=lambda: [str(paths['python'])], exe=lambda: str(paths['python']),
        cwd=lambda: str(tmp_path), terminate=lambda: terminated.append(321)))
    monkeypatch.setattr(session, '_request', lambda path: {'queue_running': [
        [1, 'foreign-prompt', receipt['workflow'], {'client_id': 'another-application'}]], 'queue_pending': []})
    with pytest.raises(flux.FluxTrialStopped, match='Another request'):
        session._stop_own()
    assert terminated == []


@pytest.mark.parametrize('foreign_parent', [False, True])
def test_listener_reconciliation_requires_our_exact_venv_child(local_trial, monkeypatch, tmp_path, foreign_parent):
    store, _, _, _, paths = local_trial
    receipt = register(local_trial)
    base = tmp_path / 'base_python'
    base.write_bytes(b'known base executable')
    receipt['runtime_files']['base_python'] = {'path': str(base)}
    session = flux.ColdFluxSession(store.settings, receipt, tmp_path)
    argv = [str(paths['python']), '-s', str(paths['main']), '--port', '8195']
    session.process = SimpleNamespace(pid=321, poll=lambda: None)
    session.identity = {'pid': 321, 'created': 1000, 'argv': argv, 'cwd': str(tmp_path), 'port': 8195}
    session.launcher_identity = dict(session.identity)
    child = SimpleNamespace(pid=322, create_time=lambda: 1000.1,
        cmdline=lambda: [str(base), *argv[1:]], exe=lambda: str(base), cwd=lambda: str(tmp_path))
    launcher = SimpleNamespace(pid=321, create_time=lambda: 1000, cmdline=lambda: argv,
        exe=lambda: str(paths['python']), cwd=lambda: str(tmp_path),
        children=lambda **_: [] if foreign_parent else [child])
    monkeypatch.setattr(flux.psutil, 'Process', lambda pid: launcher if pid == 321 else child)
    monkeypatch.setattr(flux.psutil, 'net_connections', lambda **_: [
        SimpleNamespace(pid=322, status=flux.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=8195))])
    assert session._owns_listener() is (not foreign_parent)
    if not foreign_parent:
        assert session.identity['pid'] == 322 and session.identity['launcher_pid'] == 321
        assert session._owns_listener() is True
    else:
        assert session.identity['pid'] == 321 and not session.marker.exists()


def test_runtime_selection_preserves_venv_with_torch_and_records_base(local_trial, monkeypatch, tmp_path):
    store, _, _, _, paths = local_trial
    # Call the original module implementation, not the registration fixture stub.
    install = tmp_path / 'install'
    base = install / 'standalone-env/python.exe'
    base.parent.mkdir(parents=True)
    base.write_bytes(b'base, no site packages')
    monkeypatch.setattr('atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.describe_install',
        lambda _: SimpleNamespace(install_dir=install, python_executable=paths['python'], main_script=paths['main']))
    monkeypatch.setattr(flux, '_on_e', lambda p: p.resolve())
    monkeypatch.setattr(flux.inspect, 'getfile', lambda _: str(paths['builder']))
    settings = SimpleNamespace(comfyui_shared_model_paths=paths['model_paths'], comfyui_base_url='http://127.0.0.1:8188')
    selected = runtime_files_original(settings)
    assert selected['python'] == paths['python']
    assert selected['base_python'] == base


def test_gone_listener_is_not_ready_and_reports_runtime_exit(local_trial, monkeypatch, tmp_path):
    store, _, _, _, _ = local_trial
    receipt = register(local_trial)
    session = flux.ColdFluxSession(store.settings, receipt, tmp_path)
    session.process = SimpleNamespace(poll=lambda: 0xC0000005)
    session.identity = {'pid': 321, 'port': 8195}
    monkeypatch.setattr(flux.psutil, 'net_connections', lambda **_: [])
    def gone(_):
        raise flux.psutil.NoSuchProcess(321)
    monkeypatch.setattr(flux.psutil, 'Process', gone)
    monkeypatch.setattr(flux.httpx, 'get', lambda *a, **k: pytest.fail('gone listener must not be queried'))
    assert session._owns_listener() is False
    endpoint = flux._FixedEndpoint(session)
    assert endpoint._server_ready() is False
    with pytest.raises(flux.FluxTrialStopped, match='native runtime exit.*3221225477'):
        _ = endpoint.base_url
    assert not (tmp_path / 'dispatch.json').exists()


def test_unowned_listener_readiness_does_not_claim_native_exit(monkeypatch):
    session = SimpleNamespace(_owns_listener=lambda: False, process=SimpleNamespace(poll=lambda: None))
    endpoint = flux._FixedEndpoint(session)
    monkeypatch.setattr(flux.httpx, 'get', lambda *a, **k: pytest.fail('unowned listener must not be queried'))
    assert endpoint._server_ready() is False
    with pytest.raises(flux.FluxTrialStopped, match='identity is no longer owned'):
        _ = endpoint.base_url
