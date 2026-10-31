"""Registered voice correction on isolated fixtures; no model, render or real approval."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import subprocess
import wave
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from atnos.intelligence.storage import connect
from atnos.production import local_voice_lane as lane, remotion_quality_repair as repair, voice_revision as revision
from atnos.production.order_execution import StageExecutor
from atnos.production.order_feedback import ProductionFeedback, ProductionFeedbackStore
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_manifest import save_project_manifest
from atnos.production.project_profile import ProductionOrder, content_hash
from atnos.production.steps.common import write_json
from atnos.production.steps.order_media import produce_captions
from atnos.production.steps import order_voice
from atnos.production.narration_revision import NarrationRevisionRequired
from atnos.production.steps.order_script import SCRIPT_VALIDATOR_VERSION
from atnos.services import qwen_tts_local_runtime_service as qwen
from test_production_order_script import script
from test_production_order_store import store
from test_renderer_refresh_followup import complete
from test_semantic_qa_remotion_scene_gate import _minimal_manifest


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


@pytest.fixture
def trial(store, script, monkeypatch, request):
    job = store.create(ProductionOrder(topic='Fixture voice revision', delivery_mode='local_only'))['id']
    root = store.context(job).artifact_dir
    base_body = {'job_id': job, 'request_hash': content_hash(store.get(job)['request']),
                 'frozen_profile_hash': store.get(job)['profile_hash'], 'sample_sha256': 'a' * 64,
                 'model_id': lane.MODEL_ID, 'model_revision': lane.MODEL_REVISION, 'preset': lane.CLEAR_PRESET,
                 'instruction_sha256': hashlib.sha256(lane.CLEAR_INSTRUCTION.encode()).hexdigest(),
                 'approval_sha256': 'd' * 64, 'public_authorized': True, 'local_only': True}
    base = lane.LocalVoiceLane(**base_body, digest=content_hash(base_body))
    monkeypatch.setattr(lane, '_load_base_local_voice_lane', lambda *a, **k: base)
    registration = put(root / 'voice' / 'local_voice_lane.json', {'FIXTURE_ORIGINAL_IMMUTABLE': True})
    canonical = {**script.model_dump(mode='json'), 'qa': {'status': 'passed',
        'review_contract_version': None if getattr(request, 'param', False) else SCRIPT_VALIDATOR_VERSION}}
    raw = root / 'audio' / 'old.wav'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b'ISOLATED OLD AUDIO FIXTURE; NOT PLAYABLE MEDIA')
    manifest = _minimal_manifest(job, [(f's{i+1:02}', i*5, (i+1)*5) for i in range(6)])
    manifest.brand = manifest.brand.model_copy(update={'visualVariant': 'editorial_motion'})
    save_project_manifest(root / 'project_manifest.json', manifest)
    final = root / 'assets' / 'final_short.mp4'
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b'ISOLATED OLD VIDEO FIXTURE; NOT REAL MP4')
    with store.lease(job) as token:
        complete(store, job, token, 'script', {'fixture': 'script'}, root/'script.json', canonical)
        complete(store, job, token, 'asset_planning', {'fixture': 'assets'}, root/'visuals.json', {'fixture': 'same assets'})
        for stage in ('voice/local_ryan/narration', 'voice/local_ryan_qa/narration', 'voice/local_ryan/alignment', 'voice'):
            complete(store, job, token, stage, {'fixture': 'old '+stage}, root/(stage.replace('/', '_')+'.json'),
                     {'fixture': stage, 'sha256': file_hash(raw), 'qa': {'status': 'passed'}}, [raw])
        complete(store, job, token, 'captions', {'fixture': 'old words'}, root/'captions.json', {'fixture': 'old captions'})
        complete(store, job, token, 'remotion_render', {'fixture': 'old render'}, root/'render.json', {'fixture': 'old render'}, [root/'project_manifest.json'])
        complete(store, job, token, 'remotion_mux', {'fixture': 'old mux'}, root/'mux.json', {'fixture': 'old mux'}, [final])
        complete(store, job, token, 'package', {'fixture': 'old package'}, root/'package.json', {'fixture': 'old package'}, [final])
    with connect(store.settings) as db:
        db.execute("UPDATE pipeline_jobs SET status='ready',current_step='ready',error=NULL WHERE id=?", (job,))
        db.commit()
    output = store.settings.data_dir / 'audition_fixture'
    output.mkdir()
    candidate = output / 'candidate.wav'; candidate.write_bytes(b'ISOLATED NEW RAW AUDIO FIXTURE')
    mastered = output / 'mastered.wav'; mastered.write_bytes(b'ISOLATED NEW MASTERED AUDIO FIXTURE')
    text = '\n\n'.join(s['text'] for s in canonical['segments'][:2])
    request = {'schema_version': 2, 'scope': 'voice_audition_only', 'source_job_id': job,
               'source_script_sha256': file_hash(root/'script.json'), 'text': text,
               'segment_ids': ['s01', 's02'],
               'model_id': lane.MODEL_ID, 'model_revision': lane.MODEL_REVISION,
               'user_approved': False, 'production_enabled': False, 'max_new_synthesis_requests': 1,
               'preserve_current_mastering': True, 'execution_authorization': None}
    request['execution_authorization'] = {'status': 'authorized', 'user_response': 'ISOLATED FIXTURE ONLY',
                                         'scope_sha256': qwen.audition_scope_sha256(request)}
    request_path = put(output/'request.json', request)
    sample = {'schema_version': 2, 'status': 'candidate_unapproved', 'user_approved': False,
              'production_enabled': False, 'source_job_id': job, 'source_script_sha256': file_hash(root/'script.json'),
              'provider': 'qwen_local', 'model_id': lane.MODEL_ID, 'revision': lane.MODEL_REVISION,
              'device': 'cpu', 'speaker': 'Ryan', 'preset': lane.NEUTRAL_PRESET,
              'instruction': lane.NEUTRAL_INSTRUCTION,
              'instruction_sha256': hashlib.sha256(lane.NEUTRAL_INSTRUCTION.encode()).hexdigest(),
              'provider_calls': 1, 'cost_usd': 0, 'request_path': str(request_path),
              'request_sha256': file_hash(request_path), 'text': text,
              'output_path': str(candidate), 'output_sha256': file_hash(candidate)}
    sample_path = put(output/'candidate.json', sample)
    audition = {'source_job_id': job, 'source_script_sha256': file_hash(root/'script.json'),
                'raw_receipt_path': str(sample_path),
                'request_sha256': file_hash(request_path), 'raw_sha256': file_hash(candidate), 'text': text,
                'user_approved': False, 'production_enabled': False, 'time_stretch': False,
                'normalization': {'loudness_target_applied': True}, 'output_file': str(mastered), 'sha256': file_hash(mastered)}
    audition_path = put(output/'audition.json', audition)
    feedback = ProductionFeedbackStore(store.settings).add(job, ProductionFeedback(
        note='ISOLATED TEST SELECTION ONLY, candidate '+file_hash(mastered), category='voice', scope='film'))
    selection_path = put(output/'selection.json', {'status': 'USER_SELECTED_FOR_FURTHER_REVIEW',
        'source_job_id': job, 'feedback_id': feedback['id'], 'preset': lane.NEUTRAL_PRESET,
        'quality_approved': False, 'channel_default_changed': False,
        'selected_raw_receipt': str(sample_path),
        'selected_sample_path': str(mastered), 'selected_sample_sha256': file_hash(mastered)})
    binding = revision.LocalVoiceRevision(expected_canonical_sha256=file_hash(root/'script.json'),
        expected_original_lane_digest=base.digest, candidate_receipt_path=sample_path,
        candidate_receipt_sha256=file_hash(sample_path), audition_path=audition_path,
        audition_sha256=file_hash(audition_path), selection_path=selection_path,
        selection_sha256=file_hash(selection_path), feedback_id=feedback['id'], feedback_sha256=content_hash(feedback))
    monkeypatch.setattr('atnos.production.publication_coordinator.verified_package',
                        lambda *a, **k: {'state': store.get(job), 'hash': 'b'*64})
    monkeypatch.setattr('atnos.production.channels.wealth_blueprint.remotion_order_edit.renderer_source_fingerprint', lambda: 'c'*64)
    return SimpleNamespace(store=store, job=job, root=root, base=base, binding=binding, script=canonical,
                           registration=registration, candidate=candidate, mastered=mastered, output=output)


def register(t, **kwargs):
    return asyncio.run(repair.register_renderer_refresh(t.store, t.job, expected_package_sha256='b'*64,
        reason='ISOLATED FIXTURE: selected interpretation, one full take without script or asset changes',
        local_voice_revision=t.binding, authorized=True, **kwargs))


def executor(t, token):
    ex = StageExecutor(t.store, t.job, token)
    ex.context.artifacts['_renderer_refresh'] = repair.load_renderer_refresh(ex)
    return ex


def test_registration_archives_audio_and_video_without_reset_or_replacing_original_lane(trial):
    t = trial; before = copy.deepcopy(t.store.get(t.job)); old_lane = t.registration.read_bytes()
    result = register(t)
    after = t.store.get(t.job)
    assert result['scope'] == 'local_voice_and_downstream_only'
    assert after['status'] == 'blocked' and after['current_step'] == 'voice_generation'
    assert after['can_resume'] is True
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['history'][:len(before['history'])] == before['history']
    for row in before['checkpoints']:
        assert next(r for r in after['checkpoints'] if r['stage'] == row['stage']) == row
    with t.store.lease(t.job) as token:
        ex = executor(t, token)
        assert ex.context.artifacts['_renderer_refresh']['visual_variant'] == 'editorial_motion'
        assert ex.context.artifacts['_renderer_refresh']['inherited_visual_variant'] is True
        archived = ex.context.artifacts['_renderer_refresh']['archived_files']
        assert {'audio/old.wav', 'assets/final_short.mp4', 'captions.json'} <= {r['original_path'] for r in archived}
        selected = lane.load_local_voice_lane(t.store.settings, t.job)
        assert selected.preset == lane.NEUTRAL_PRESET and selected.digest != t.base.digest
        assert selected.sample_sha256 == file_hash(t.mastered)
    assert t.registration.read_bytes() == old_lane


def test_one_fresh_narration_then_cache_reuse_and_no_second_inference(trial):
    t = trial; register(t); calls = []
    with t.store.lease(t.job) as token:
        ex = executor(t, token)
        async def operation():
            calls.append(1)
            return write_json(t.root/'new-source.json', {'fixture': 'new take'})
        inputs = {'lane': lane.load_local_voice_lane(t.store.settings, t.job).as_dict(), 'text': 'fixture current full text'}
        first = asyncio.run(ex.run('voice/local_ryan/narration', inputs, operation, local_model_call=True))
        assert asyncio.run(ex.run('voice/local_ryan/narration', inputs, operation, local_model_call=True)) == first
        with pytest.raises(ProductionBlocked, match='budget exhausted'):
            asyncio.run(ex.run('voice/local_ryan/narration', {**inputs, 'text': 'different'}, operation, local_model_call=True))
    assert len(calls) == 1
    assert next(r for r in t.store.get(t.job)['checkpoints'] if r['stage']=='voice/local_ryan/narration')['attempts'] == 2


def test_atomic_begin_blocks_two_simultaneous_narration_reservations(trial):
    t = trial; register(t); barrier = Barrier(2)
    with t.store.lease(t.job) as token:
        def reserve():
            barrier.wait(timeout=10)
            try:
                return t.store.begin(t.job, token, 'voice/local_ryan/narration', {'new': 'same'}, local_model_call=True)
            except ProductionBlocked as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve(), range(2)))
    assert results.count(None) == 1
    assert sum(isinstance(r, str) and 'budget exhausted' in r for r in results) == 1
    assert next(r for r in t.store.get(t.job)['checkpoints'] if r['stage']=='voice/local_ryan/narration')['attempts'] == 2


@pytest.mark.parametrize('stage,flags', [
    ('script_repair/1', {'local_model_call': True}), ('asset_ranking', {'local_model_call': True}),
    ('voice/local_ryan/recovery/s01', {'local_model_call': True}), ('image_generation/s01', {}),
    ('covers', {'provider_call': True, 'estimated_cost_usd': 1}),
    ('semantic/s01', {'external_tool_call': True}),
])
def test_recovery_never_expands_into_authoring_images_segments_or_paid_calls(trial, stage, flags):
    t = trial; register(t)
    with t.store.lease(t.job) as token:
        ex = executor(t, token)
        async def never():pytest.fail('Forbidden operation dispatched')
        with pytest.raises(ProductionBlocked):
            asyncio.run(ex.run(stage, {'changed': True}, never, **flags))


def test_normal_captions_are_rebuilt_from_new_voice_words(trial):
    t = trial; register(t)
    words = []
    for s in t.script['segments']:
        for text in s['text'].split():
            start = .2 + len(words)*.2
            words.append({'text': text, 'start_sec': start, 'end_sec': start+.15, 'segment_id': s['id']})
    with t.store.lease(t.job) as token:
        ex = executor(t, token)
        ex.context.artifacts.update(script=t.script, voice={'sha256': 'f'*64, 'word_timestamps': words,
            'duration_seconds': words[-1]['end_sec']+.1, 'timing_source': 'fixture_new_alignment'})
        captions = asyncio.run(produce_captions(ex))
    assert captions['qa']['timing_source'] == 'fixture_new_alignment'
    assert Path(captions['output_file']).read_text(encoding='utf-8').startswith('1\n00:00:00,200')
    row = next(r for r in t.store.get(t.job)['checkpoints'] if r['stage']=='captions')
    assert row['attempts'] == 2 and row['output'] != {'fixture': 'old captions'}


@pytest.mark.parametrize('mutation', ['canonical', 'sample', 'feedback', 'selection', 'lane'])
def test_changed_provenance_blocks_registration_before_mutation(trial, mutation):
    t = trial
    if mutation == 'canonical':(t.root/'script.json').write_text('{}', encoding='utf-8')
    if mutation == 'sample':t.candidate.write_bytes(b'changed sample')
    if mutation == 'feedback':ProductionFeedbackStore(t.store.settings).disable(t.job, t.binding.feedback_id)
    if mutation == 'selection':t.binding.selection_path.write_text('{}', encoding='utf-8')
    if mutation == 'lane':t.binding = t.binding.model_copy(update={'expected_original_lane_digest': '0'*64})
    before = t.store.get(t.job)
    with pytest.raises(ProductionBlocked):register(t)
    after = t.store.get(t.job)
    assert after['checkpoints'] == before['checkpoints'] and after['provider_calls'] == before['provider_calls']


def test_rejects_scope_combination_and_requires_explicit_authorization(trial):
    with pytest.raises(ValueError, match='cannot combine'):register(trial, visual_variant='professional')
    with pytest.raises(ValueError, match='authorized'):
        asyncio.run(repair.register_renderer_refresh(trial.store, trial.job, expected_package_sha256='b'*64,
            reason='An unauthorized fixture must not create a correction', local_voice_revision=trial.binding))


def test_original_upstream_is_protected_after_restart(trial):
    t = trial; register(t)
    (t.root/'visuals.json').write_text('{}', encoding='utf-8')
    restored = ProductionOrderStore(t.store.settings)
    with restored.lease(t.job) as token:
        with pytest.raises(ProductionBlocked, match='immutable upstream'):
            repair.load_renderer_refresh(StageExecutor(restored, t.job, token))


def test_preparation_only_returns_binding_without_registering_or_consuming_budget(trial):
    t = trial; before = t.store.get(t.job)
    binding = revision.prepare_local_voice_revision(t.store, t.job,
        candidate_receipt_path=t.binding.candidate_receipt_path, audition_path=t.binding.audition_path,
        selection_path=t.binding.selection_path, feedback_id=t.binding.feedback_id)
    assert binding == t.binding
    after = t.store.get(t.job)
    assert after['checkpoints'] == before['checkpoints'] and after['history'] == before['history']


def test_existing_qwen_production_adapter_resolves_selected_instruction(trial, monkeypatch):
    t = trial; register(t)
    python = t.output/'fixture-python.exe'; python.touch()
    monkeypatch.setattr(qwen, 'RUNTIME_PYTHON', python)
    monkeypatch.setattr(qwen, '_verified_model_files', lambda: True)
    calls = []
    def child(command, input_text, environment, *_):
        request = json.loads(input_text); calls.append(request)
        assert request['instruction'] == lane.NEUTRAL_INSTRUCTION
        assert request['speaker'] == 'Ryan' and environment['HF_HUB_OFFLINE'] == '1'
        output = Path(request['output_path'])
        with wave.open(str(output), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\x00\x00'*240)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            'model_id': qwen.MODEL_ID, 'revision': qwen.MODEL_REVISION, 'device': 'cpu',
            'output_path': str(output), 'instruction': request['instruction']}), '')
    monkeypatch.setattr(qwen, '_run_qwen_child', child)
    current = lane.load_local_voice_lane(t.store.settings, t.job).as_dict()
    result = asyncio.run(qwen.QwenTTSLocalRuntimeService().synthesize_production(
        text='\n\n'.join(s['text'] for s in t.script['segments']), output_path=t.root/'audio/new-fixture.wav',
        lane=current, settings=t.store.settings))
    assert result['preset'] == lane.NEUTRAL_PRESET and result['lane_digest'] == current['digest']
    assert len(calls) == 1  # Stubbed child; no model is loaded.


def test_failed_narration_does_not_retry_even_with_unlimited_local_work(trial):
    t = trial; register(t); calls = []
    t.store.settings.atnos_local_work_unlimited = True
    with t.store.lease(t.job) as token:
        ex = executor(t, token)
        async def broken():
            calls.append(1)
            raise ValueError('Fixture: invalid provider output')
        with pytest.raises(ProductionBlocked, match='budget exhausted'):
            asyncio.run(ex.run('voice/local_ryan/narration', {'new': 'take'}, broken, local_model_call=True))
    assert len(calls) == 1
    assert t.store.get(t.job)['status'] == 'blocked'


def test_changed_voice_timing_cannot_author_a_different_script(trial, monkeypatch):
    t = trial; register(t)
    async def measured(*args):
        raise NarrationRevisionRequired({'reason': 'fixture measured duration differs'})
    monkeypatch.setattr(order_voice, '_produce_voice_once', measured)
    monkeypatch.setattr('atnos.production.steps.order_script.repair',
                        lambda *a, **k: pytest.fail('Accepted script must not be rewritten'))
    before = (t.root/'script.json').read_bytes()
    with t.store.lease(t.job) as token:
        ex = executor(t, token); ex.context.artifacts['script'] = t.script
        with pytest.raises(ProductionBlocked, match='cannot rewrite'):
            asyncio.run(order_voice.produce_voice(ex))
    assert (t.root/'script.json').read_bytes() == before


def test_speech_rejection_stops_before_segment_recovery(trial, monkeypatch):
    t = trial; register(t); stages = []
    monkeypatch.setattr(order_voice, 'WhisperCppSubtitleService', lambda *_: SimpleNamespace(
        runtime_availability=lambda: SimpleNamespace(available=True)))
    monkeypatch.setattr(order_voice, 'resolve_ffmpeg', lambda: 'fixture-ffmpeg')
    monkeypatch.setattr(qwen, 'runtime_availability', lambda: {'available': True})
    monkeypatch.setattr(order_voice, 'recover_local_ryan_segments',
                        lambda *a: pytest.fail('One full take cannot expand to segment resynthesis'))
    async def run(stage, *args, **kwargs):
        stages.append(stage)
        return {'status': 'failed'} if '_qa/' in stage else {'output_file': str(t.candidate), 'sha256': file_hash(t.candidate)}
    ex = SimpleNamespace(context=SimpleNamespace(artifact_dir=t.root, artifacts={
        '_renderer_refresh': {'local_voice_revision': t.binding.model_dump(mode='json')}}),
        store=t.store, run=run)
    with pytest.raises(ProductionBlocked, match='no automatic segment'):
        asyncio.run(order_voice.produce_local_single_pass_voice(ex, t.base.as_dict(), {}, t.script['segments']))
    assert stages == ['voice/local_ryan/narration', 'voice/local_ryan_qa/narration']


def test_inherited_editorial_variant_still_uses_its_real_qa_contract(trial):
    from test_editorial_planning_story import manifest, context
    from atnos.production.presenter_variant import bind_editorial_scene_directions
    from atnos.production.steps.order_delivery import _planning_desk_audit_contract
    ctx = context()
    ctx.artifacts['_renderer_refresh'].update(scope='local_voice_and_downstream_only',
        local_voice_revision=trial.binding.model_dump(mode='json'))
    bound = bind_editorial_scene_directions(manifest())
    bound.brand.illustrationSystem = 'wb_planning_desk_painted_v1'
    target = _planning_desk_audit_contract(ctx, bound, bound.scenes[0])
    assert target['presentation_variant'] == 'editorial_motion'
    assert 'painted wall' not in target['materials']


def test_tampered_registration_blocks_direct_begin_before_any_attempt(trial):
    t = trial; result = register(t)
    request = t.root/'renderer_refresh'/result['request_digest'][:12]/'request.json'
    request.write_text('{}', encoding='utf-8')
    before = t.store.get(t.job)
    with t.store.lease(t.job) as token:
        with pytest.raises(ProductionBlocked, match='registration changed'):
            t.store.begin(t.job, token, 'voice/local_ryan/narration', {'changed': True}, local_model_call=True)
    after = t.store.get(t.job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['checkpoints'] == before['checkpoints']


def test_existing_run_order_routes_new_voice_and_skips_all_image_generation(trial, monkeypatch):
    from atnos.production import order_execution, wb_image_api
    from atnos.production.steps import order_assets, order_delivery, order_media, order_repair
    t = trial; register(t); calls = []
    async def preflight(executor, *, allow_model_start):
        assert allow_model_start is False
        calls.append('preflight')
    monkeypatch.setattr(order_execution, 'preflight_order', preflight)
    monkeypatch.setattr('atnos.services.gpu_runtime_lock_service.GpuRuntimeLockService',
        lambda *_: SimpleNamespace(acquire=lambda **_: 'fixture-lease', release=lambda *_: None))
    monkeypatch.setattr('atnos.services.production_gpu_residency_service.ProductionGpuResidencyService',
        lambda *_: SimpleNamespace(prepare_for_llm=lambda: None))
    monkeypatch.setattr(wb_image_api, 'load_image_lane', lambda *_: object())
    async def never_images(*_):pytest.fail('A voice correction must not enter the image lane')
    monkeypatch.setattr(wb_image_api, 'produce_image_candidates', never_images)
    def stage(name):
        async def operation(ex):
            calls.append(name)
            if name == 'script':ex.context.artifacts['script'] = t.script
            if name == 'voice':assert lane.load_local_voice_lane(t.store.settings, t.job).preset == lane.NEUTRAL_PRESET
            if name == 'package':ex.context.artifacts['package'] = {'output_file': str(t.root/'package.json')}
        return operation
    for module, name, label in [(order_execution, 'produce_script', 'script'),
        (order_assets, 'produce_assets', 'assets'), (order_voice, 'produce_voice', 'voice'),
        (order_media, 'produce_captions', 'captions'), (order_media, 'produce_edit', 'edit'),
        (order_delivery, 'produce_covers', 'covers'), (order_repair, 'produce_final_quality', 'qa'),
        (order_delivery, 'produce_package', 'package')]:
        monkeypatch.setattr(module, name, stage(label))
    monkeypatch.setattr(ProductionOrderStore, 'finish', lambda self, job, *a: self.get(job))
    asyncio.run(order_execution.run_order(t.store.settings, t.job))
    assert calls == ['preflight', 'script', 'assets', 'voice', 'captions', 'edit', 'covers', 'qa', 'package']
    assert t.store.get(t.job)['status'] != 'ready'  # Routing fixture never pretends to produce AV/QC.


def test_registered_voice_revision_rejects_different_worker_code_before_execution(trial, monkeypatch):
    t = trial; register(t)
    monkeypatch.setattr('atnos.production.order_execution._runtime_code_identity', lambda: 'f'*64)
    with t.store.lease(t.job) as token:
        with pytest.raises(ProductionBlocked, match='worker source changed'):
            executor(t, token)


def test_fixing_local_caption_assembly_does_not_spend_another_model_allowance(trial):
    t = trial; register(t)
    with t.store.lease(t.job) as token:
        ex = executor(t, token)
        async def local():return write_json(t.root/'caption-fixture.json', {'fixture': 'local only'})
        asyncio.run(ex.run('captions', {'revision': 1}, local))
        asyncio.run(ex.run('captions', {'revision': 2}, local))
        async def never_model():pytest.fail('Caption assembly cannot become model generation')
        with pytest.raises(ProductionBlocked, match='another model stage'):
            asyncio.run(ex.run('captions', {'revision': 3}, never_model, local_model_call=True))
    assert next(r for r in t.store.get(t.job)['checkpoints'] if r['stage']=='captions')['attempts'] == 3
