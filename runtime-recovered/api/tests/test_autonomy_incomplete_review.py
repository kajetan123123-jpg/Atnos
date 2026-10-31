"""Bounded recheck of missing QC citations against completed blind evidence."""
import asyncio
import json

from PIL import Image
import pytest

from atnos.production import wb_image_api as lane
from atnos.production import wb_image_review as review
from atnos.production import wb_review_evidence_repair as repair
from atnos.production.asset_brain.models import AssetRecord
from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
from atnos.production.order_autonomy import enable, policy, recover_due
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import file_hash, ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production.steps.order_script import EditorialScript
from test_autonomy_review_recovery import record
from test_production_order_store import store
from test_wb_image_api_production import trial, isolated_brain
from test_wb_image_review_grounding import observation, grounded_answer


@pytest.fixture
def four_thoughts():
    rows = [
        ('hook', 'A raise can change your income while leaving your savings unchanged.'),
        ('practical', 'Write down a new monthly commitment before adding it to your budget.'),
        ('practical', 'Choose an affordable amount for a separate savings goal.'),
        ('cta', 'On your next payday, make that allocation before deciding what else to spend.'),
    ]
    return EditorialScript.model_validate({'title': 'Where the extra income goes',
        'description': 'A qualitative example of income allocation and recurring expenses.', 'claims': [],
        'segments': [{'id': f's{i:02}', 'type': kind, 'text': text, 'on_screen': 'A deliberate allocation',
                      'visual_intent': 'Show the spoken allocation with physical objects'}
                     for i, (kind, text) in enumerate(rows, 1)]})


@pytest.fixture
def incomplete_review(trial, isolated_brain):
    store, job, reference = trial
    lane.register_local_image_lane(store.settings, job, reference_path=reference, authorized=True)
    context = store.context(job)
    state = store.get(job)
    context.artifacts['script'] = next(row['output'] for row in state['checkpoints'] if row['stage'] == 'script')
    plan = lane.image_selection_plan(context)
    scene, (native, need) = plan['scenes'][0], lane._preflight_image_plan(plan)[0]
    assert not native
    style = context.artifacts['project_profile']['visual_style']

    candidate = context.artifact_dir / 'review-candidate.png'
    Image.new('RGB', (768, 1344), '#132938').save(candidate)
    ref_observation = observation()
    candidate_observation = observation()
    candidate_observation['drawing_medium_and_linework'] = (
        'Rendered as a photorealistic digital illustration, with no visible brushstrokes or linework.')
    revision = {'name': 'qwen3-vl:8b-instruct', 'digest': 'isolated-image-review', 'ollama_version': '0.32.0'}
    identity = {'image_sha256': file_hash(candidate), 'reference_sha256': file_hash(reference),
                'need': need, 'style': style, 'model_revision': revision, 'contract': 2,
                'observation_contract': review.OBSERVATION_CONTRACT}
    key = content_hash(identity)
    directory = context.artifact_dir / 'assets' / 'automatic-review' / key
    directory.mkdir(parents=True)

    raw = grounded_answer(candidate_observation, 'passed')
    # Raw judgements are all PASS, but the comparison omitted citations for
    # dimensions that the completed blind observation explicitly establishes.
    raw['required_objects_and_counts']['observation_fields'] = ['objects_and_positions']
    raw['composition_and_readability']['observation_fields'] = ['objects_and_positions', 'framing_and_readability']
    raw['style_palette_and_lighting']['observation_fields'] = ['materials_and_textures', 'palette_and_regions', 'lighting_and_depth']
    raw['integrity_and_no_embedded_text']['observation_fields'] = ['visible_text_and_marks']
    prompt = 'Compare pixels. ' + json.dumps({'candidate_observation': candidate_observation,
        'reference_observation': ref_observation, 'required_scene': need, 'style_direction': style})
    traces = {
        'observation': record(review.OBSERVATION_PROMPT, review.IllustrationObservation,
                              candidate_observation, revision, candidate),
        'reference': record(review.OBSERVATION_PROMPT, review.IllustrationObservation,
                            ref_observation, revision, reference),
        'comparison': record(prompt, review.GroundedWBIllustrationChecks, raw, revision),
    }
    checks = review.grounded_checks(raw, candidate_observation)
    assert all(item['status'] == 'uncertain' for item in checks.values())
    source_names = {name: name + '-validated.json' for name in traces}
    evidence_files = {}
    with store.lease(job) as token:
        for name, trace in traces.items():
            stage = f'assets/image_review/{key[:16]}/{name}'
            store.begin(job, token, stage, {'identity': identity, 'prompt': trace['inputs']['prompt'],
                       'validation_contract': 2}, local_model_call=True)
            trace_path = write_json(directory / (name + '.json'), trace).artifact_path
            payload = candidate_observation if name == 'observation' else ref_observation if name == 'reference' else raw
            validated_path = write_json(directory / source_names[name], payload).artifact_path
            store.complete(job, token, stage, payload, [trace_path, validated_path])
            evidence_files[trace_path.name] = file_hash(trace_path)
            evidence_files[validated_path.name] = file_hash(validated_path)

        asset = AssetRecord(id='candidate-incomplete-critic-evidence', type='background',
            channel='wealth_blueprint', production_role='background', source='comfyui-flux-trial',
            quality_status='REJECTED', file_path=str(candidate),
            tags=style_asset_tags(style, job, scene['scene_id']),
            reviewed_by='atnos_automatic_visual_qc:' + key)
        report = {'identity': identity, 'asset_id': asset.id, 'checks': checks,
            **review.verdict(checks), 'grounding_sources': source_names,
            'evidence_files': evidence_files, 'scope': 'This job scene only; no channel-wide user appearance approval.'}
        write_json(directory / 'review.json', report)
        isolated_brain.append(asset)
        image_stage = f"assets/image_local/flux2-trial/{scene['scene_id']}/{key[:16]}"
        image_record = write_json(context.artifact_dir / 'image-stage.json',
                                  {'asset_id': asset.id, 'output_path': str(candidate)})
        store.begin(job, token, image_stage, {'review_identity': key}, local_model_call=True)
        store.complete(job, token, image_stage, {'asset_id': asset.id, 'output_path': str(candidate)},
                       [image_record.artifact_path, candidate])
        store.block(job, token, 'image_generation', category='execution_failure',
            message=repair.CONSUMED_MESSAGE)
    enable(store.settings, job, reason='User authorizes bounded correction of omitted citations against completed pixel evidence.', max_recoveries=1)
    return store, job, context, asset, directory, key, need, style, candidate


def test_incomplete_citation_proof_requires_intact_complete_blind_evidence(incomplete_review):
    store, job, context, asset, directory, key, need, style, candidate = incomplete_review
    state = store.get(job)
    brain = __import__('atnos.production.asset_brain.service', fromlist=['WealthBlueprintAssetBrain']).WealthBlueprintAssetBrain(store.settings)
    assert repair.candidate_evidence(context, brain, asset, need, style)
    assert repair.incomplete_review_recovery_evidence(store, state)

    # A true negative, incomplete observation, or changed pixels is not a
    # citation omission and cannot authorize recovery.
    report_path = directory / 'comparison-validated.json'
    raw = json.loads(report_path.read_text('utf-8'))
    raw['style_palette_and_lighting']['status'] = 'failed'
    report_path.write_text(json.dumps(raw), encoding='utf-8')
    assert repair.candidate_evidence(context, brain, asset, need, style) is None


@pytest.mark.parametrize('source,mutation', [
    ('comparison-validated.json', 'genuine_failure'),
    ('observation-validated.json', 'unknown_medium'),
])
def test_monitor_rejects_real_negative_or_unknown_evidence_even_with_updated_file_hashes(
        incomplete_review, source, mutation):
    store, job, context, asset, directory, key, need, style, _ = incomplete_review
    path = directory / source
    payload = json.loads(path.read_text('utf-8'))
    if mutation == 'genuine_failure':
        payload['style_palette_and_lighting']['status'] = 'failed'
    else:
        payload['drawing_medium_and_linework'] = 'Not established by the pixel observation; remains uncertain.'
    path.write_text(json.dumps(payload), encoding='utf-8')
    report_path = directory / 'review.json'
    report = json.loads(report_path.read_text('utf-8'))
    report['evidence_files'][source] = file_hash(path)
    report_path.write_text(json.dumps(report), encoding='utf-8')
    brain = __import__('atnos.production.asset_brain.service', fromlist=['WealthBlueprintAssetBrain']).WealthBlueprintAssetBrain(store.settings)
    state = store.get(job)
    assert repair.candidate_evidence(context, brain, asset, need, style) is None
    assert repair.incomplete_review_recovery_evidence(store, state) is None


def test_cached_review_recovery_dispatches_once_without_image_generation(incomplete_review, monkeypatch):
    store, job, context, asset, directory, key, need, style, candidate = incomplete_review
    before = store.get(job)
    dispatches = []
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda self, job_id: dispatches.append(job_id) or {'id': job_id})
    monkeypatch.setattr(lane, '_generate_candidate', lambda *args, **kwargs: pytest.fail('Image POST repeated'))
    monkeypatch.setattr(review, '_infer', lambda *args, **kwargs: pytest.fail('Ordinary review inference repeated'))
    assert recover_due(store.settings) == [{'id': job}]
    assert recover_due(store.settings) == []
    after = store.get(job)
    assert dispatches == [job]
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['profile_hash'] == before['profile_hash']
    assert [(row['stage'], row['attempts']) for row in after['checkpoints']] == [(row['stage'], row['attempts']) for row in before['checkpoints']]
    event = next(row for row in after['history'] if row['event'] == 'autonomous_recovery_reserved')
    assert event['detail']['kind'] == 'incomplete_critic_evidence_recovery'
    assert event['detail']['evidence']['new_image_requests'] == 0
    assert asset.approved is False


def _install_repair_mocks(monkeypatch, asset, observed, mode='direct_failure'):
    from atnos.production.asset_brain.approval import AssetApprovalService
    calls = []

    def infer(settings, directory, name, *, prompt, schema, revision, image_bytes=None):
        calls.append(name)
        raw = grounded_answer(observed, 'passed')
        if mode == 'format_retry' and name == 'comparison-evidence':
            # First corrected answer still misses citations; normal infer_review
            # must use its bounded format-2 stage before preserving the failure.
            raw['style_palette_and_lighting']['observation_fields'].remove('drawing_medium_and_linework')
        else:
            raw['style_palette_and_lighting']['status'] = 'failed'
            raw['style_palette_and_lighting']['evidence'] = (
                'The observed photorealistic rendering has no visible brushstrokes or linework.')
        trace = record(prompt, schema, raw, revision)
        write_json(directory / (name + '.json'), trace)
        return schema.model_validate(raw)

    monkeypatch.setattr(review, '_infer', infer)
    monkeypatch.setattr('atnos.services.gpu_runtime_lock_service.GpuRuntimeLockService.acquire',
                        lambda self, **kwargs: object())
    monkeypatch.setattr('atnos.services.gpu_runtime_lock_service.GpuRuntimeLockService.release',
                        lambda self, lease: None)
    monkeypatch.setattr('atnos.services.production_gpu_residency_service.ProductionGpuResidencyService.prepare_for_llm',
                        lambda self: None)
    monkeypatch.setattr('atnos.production.asset_observation_cache.model_revision',
                        lambda settings, name: {'name': name, 'digest': 'isolated-image-review', 'ollama_version': '0.32.0'})

    class Approval:
        def __init__(self, brain):
            self.brain = brain
        def approve(self, *_args, **_kwargs):
            pytest.fail('An incomplete review repair must not auto-approve this photorealistic result')
        def reject(self, asset_id, code, *, notes, reviewed_by):
            assert code == 'LOW_QUALITY'
            target = next(item for item in self.brain.catalog.assets if item.id == asset_id)
            target.quality_status = 'REJECTED'
            target.approved = False
            target.reviewed_by = reviewed_by

    monkeypatch.setattr('atnos.production.asset_brain.approval.AssetApprovalService', Approval)
    return calls


@pytest.mark.parametrize('mode,expected_calls,expected_sources', [
    ('direct_failure', ['comparison-evidence'], ['comparison-evidence.json', 'comparison-evidence-validated.json']),
    ('format_retry', ['comparison-evidence', 'comparison-evidence-format-1'],
     ['comparison-evidence.json', 'comparison-evidence-format-1.json',
      'comparison-evidence-format-1-validated.json']),
])
def test_real_repair_flow_preserves_original_review_and_records_negative_medium(
        incomplete_review, monkeypatch, mode, expected_calls, expected_sources):
    store, job, context, asset, directory, key, need, style, candidate = incomplete_review
    original_trace = (directory / 'comparison.json').read_bytes()
    original_review = json.loads((directory / 'review.json').read_text('utf-8'))
    observed = json.loads((directory / 'observation-validated.json').read_text('utf-8'))
    calls = _install_repair_mocks(monkeypatch, asset, observed, mode)
    monkeypatch.setattr(lane, '_generate_candidate',
                        lambda *args, **kwargs: pytest.fail('Critic evidence repair must not submit an image request'))
    before = store.get(job)
    lane_receipt = lane.load_image_lane(store.settings, job)

    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        result = asyncio.run(repair.repair_incomplete_review(executor, asset.id, need, style, lane_receipt))
    after_first = store.get(job)

    assert calls == expected_calls
    assert result['checks']['style_palette_and_lighting']['status'] == 'failed'
    assert result['user_approved'] is False
    assert (directory / 'comparison.json').read_bytes() == original_trace
    assert json.loads((directory / 'review-before-evidence-repair.json').read_text('utf-8')) == original_review
    assert all((directory / name).is_file() for name in expected_sources)
    assert after_first['provider_calls'] == before['provider_calls'] + len(expected_calls)
    assert after_first['reserved_cost_units'] == before['reserved_cost_units']
    image_checkpoint = next(row for row in after_first['checkpoints'] if row['stage'].startswith('assets/image_local/'))
    prior_checkpoint = next(row for row in before['checkpoints'] if row['stage'] == image_checkpoint['stage'])
    assert image_checkpoint['attempts'] == prior_checkpoint['attempts'] == 1
    assert asset.quality_status == 'REJECTED' and asset.approved is False

    brain = __import__('atnos.production.asset_brain.service', fromlist=['WealthBlueprintAssetBrain']).WealthBlueprintAssetBrain(store.settings)
    assert review.proven_rejection_count(context, brain, [asset]) == 1

    # The new comparison source closes the one permitted evidence-repair epoch.
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        with pytest.raises(ProductionBlocked, match='no intact eligible evidence'):
            asyncio.run(repair.repair_incomplete_review(executor, asset.id, need, style, lane_receipt))
    after_repeat = store.get(job)
    assert calls == expected_calls
    assert after_repeat['provider_calls'] == after_first['provider_calls']
    assert after_repeat['reserved_cost_units'] == after_first['reserved_cost_units']
    assert asset.approved is False
