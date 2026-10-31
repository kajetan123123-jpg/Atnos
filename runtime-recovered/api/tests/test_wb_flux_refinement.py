"""Append-only Flux refinement receipts; tests never launch Comfy or dispatch."""
from __future__ import annotations

import asyncio
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from atnos.production import wb_local_flux_trial as flux
from atnos.production import wb_flux_refinement as refinement
from atnos.production.asset_brain.models import AssetGenerationProvenance, AssetRecord
from atnos.production.order_autonomy import enable, policy, recover_due
from atnos.production.order_store import OrderConflict, ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts
from test_wb_image_api_production import trial, isolated_brain
from test_wb_local_flux_trial import local_trial


@pytest.fixture(autouse=True)
def no_real_worker_launch(monkeypatch):
    # Recovery now calls _dispatch_locked under its own dispatch lock. An old
    # mock of dispatch must never let this offline suite start a real child.
    monkeypatch.setattr('atnos.production.order_dispatch.subprocess.Popen',
                        lambda *_a,**_k:pytest.fail('Offline refinement test attempted a real worker launch'))


@pytest.fixture
def rejected_flux_parent(local_trial, isolated_brain, monkeypatch):
    store, job, context, need, _ = local_trial
    enable(store.settings, job, reason='User authorizes bounded autonomous local image recovery for this test.')
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda *_: pytest.fail('registration launched GPU'))
    with store.lease(job) as token:
        receipt = flux.register_flux_trial(store.settings, job, 's01', authorized=True, worker_token=token)
        old_receipt_bytes = (context.artifact_dir / 'flux_trial/s01/authorization.json').read_bytes()
        folder = context.artifact_dir / 'parent-flux-fixture'
        folder.mkdir(parents=True)
        image = folder / 'image.png'
        Image.new('RGB', (768, 1344), '#132938').save(image)
        graph = write_json(folder / 'workflow.json', receipt['workflow']).artifact_path
        history = write_json(folder / 'history.json', {
            'parent-prompt': {'status': {'completed': True, 'status_str': 'success'}}}).artifact_path
        evidence = write_json(folder / 'metadata.json', {
            'trial_digest': receipt['digest'], 'workflow_sha256': receipt['workflow_sha256'],
            'image_sha256': file_hash(image), 'history_sha256': file_hash(history),
            'user_approved': False}).artifact_path
        asset = AssetRecord(id=f"flux_trial_wb_d_{receipt['digest'][:16]}", type='background',
            channel='wealth_blueprint', production_role='background', source='comfyui-flux-trial',
            quality_status='REJECTED', file_path=str(image), width=768, height=1344,
            generation=AssetGenerationProvenance(prompt=receipt['workflow']['4']['inputs']['text'],
                workflow_id=flux.VERSION, model=flux.MODEL_NAMES['unet'][1], generated_at='2026-10-08',
                source_workflow=flux.VERSION, channel='wealth_blueprint', fingerprint=receipt['digest'],
                evidence_path=str(evidence)))
        isolated_brain.append(asset)
        stage = f"assets/image_local/flux2-trial/s01/{receipt['digest'][:16]}"
        output = {'asset_id': asset.id, 'output_path': str(image), 'trial_digest': receipt['digest']}
        report = write_json(folder / 'candidate.json', output).artifact_path
        store.begin(job, token, stage, {'receipt': receipt['digest']}, local_model_call=True)
        store.complete(job, token, stage, output, [report, image, graph, history, evidence])
        store.block(job, token, 'image_generation', category='quality_rejection', message='Parent image rejected by pixel review')

    # Contract-1 immutable review fixture exercises the same current
    # proven_rejection_count gate used by the live pipeline.
    identity = {'image_sha256': file_hash(image), 'scene': 's01', 'review_version': 'fixture'}
    key = content_hash(identity)
    review_dir = context.artifact_dir / 'assets/automatic-review' / key
    review_dir.mkdir(parents=True)
    checks = {name: {'status': 'failed' if name == 'required_objects_and_counts' else 'passed',
                     'evidence': 'Observed required object mismatch in the rendered pixels.'}
              for name in ('required_objects_and_counts', 'composition_and_readability',
                           'style_palette_and_lighting', 'integrity_and_no_embedded_text')}
    observation_path = review_dir / 'observation.json'
    write_json(observation_path, {'objects': ['incorrect paper shape']})
    write_json(review_dir / 'review.json', {'identity': identity, 'asset_id': asset.id, 'checks': checks,
        'evidence_files': {observation_path.name: file_hash(observation_path)}, 'user_approved': False})
    asset.reviewed_by = 'atnos_automatic_visual_qc:' + key
    return store, job, context, need, receipt, asset, image, old_receipt_bytes


def _authorize_refinement(parent, *, seed=refinement.DEFAULT_SEED):
    store, job, context, need, receipt, asset, image, _ = parent
    with store.lease(job) as token:
        return refinement.register_flux_refinement(store.settings, job, 's01', receipt['digest'],
                                                   worker_token=token, seed=seed)


def test_compiler_preserves_objects_and_uses_horizontal_card_and_compact_exact_style():
    need = {'asset_subject': 'one blank-faced teal paper slip beside one small unbranded shopping bag.',
        'asset_composition': 'Keep both requested props fully visible with a clear gap; leave the paper face blank for a later overlay. Place the pair on a desk. Avoid: extra bags, banknotes, logos.'}
    prompt, negative = refinement._compile_prompt(need, {'id': 'D'})
    assert prompt.startswith('one horizontal unprinted teal paper card beside one small unbranded shopping bag.')
    assert 'blank-faced' not in prompt and 'paper slip' not in prompt and 'vertical' not in prompt
    assert prompt.count('one ') >= 1 and 'small unbranded shopping bag' in prompt
    assert 'Mature cinematic hand-drawn editorial illustration' in prompt
    assert 'extra bags' in negative and 'banknotes' in negative and 'logos' in negative
    assert 'portrait' in negative
    for style_id, keyword in (('A', 'varied navy graphite contours'),
                              ('B', 'layered cut-paper illustration'),
                              ('C', 'technical ink illustration'),
                              ('D', 'cinematic hand-drawn editorial illustration')):
        style_prompt, _ = refinement._compile_prompt(need, {'id': style_id})
        assert keyword in style_prompt
        assert 'paper slip' not in style_prompt and 'one small unbranded shopping bag' in style_prompt


def test_new_refinement_receipt_is_stable_append_only_and_never_dispatches(rejected_flux_parent, monkeypatch):
    parent = rejected_flux_parent
    store, job, context, need, old, asset, image, original_receipt = parent
    old_path = context.artifact_dir / 'flux_trial/s01/authorization.json'
    old_bytes = original_receipt
    assert old_path.read_bytes() == old_bytes
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda *_: pytest.fail('receipt registration launched GPU'))
    monkeypatch.setattr(flux, 'generate_flux_candidate', lambda *_: pytest.fail('receipt registration dispatched'))
    receipt = _authorize_refinement(parent)
    receipt2 = _authorize_refinement(parent)
    assert receipt['digest'] == receipt2['digest']
    assert receipt['parent_digest'] == old['digest']
    assert receipt['compiler_version'] == refinement.COMPILER_VERSION
    assert receipt['user_approved'] is False and receipt['max_attempts'] == 1
    assert receipt['image_attempts_at_registration'] == 1
    assert receipt['effective_image_request_cap'] == 20
    assert receipt['workflow_sha256'] == content_hash(receipt['workflow'])
    assert receipt['workflow']['2']['inputs']['device'] == 'cpu'
    assert receipt['workflow']['4']['inputs']['text'] != old['workflow']['4']['inputs']['text']
    prompt = receipt['workflow']['4']['inputs']['text']
    assert 'horizontal unprinted teal paper card' in prompt and 'small unbranded shopping bag' in prompt
    assert 'blank-faced' not in prompt and 'paper slip' not in prompt and 'one complete vertical' not in prompt
    assert old_path.read_bytes() == old_bytes
    assert store.get(job)['provider_calls'] == 1
    assert asset.quality_status == 'REJECTED' and asset.approved is False
    assert (context.artifact_dir / 'flux_refinements/s01' / receipt['digest'][:16] / 'authorization.json').is_file()


@pytest.mark.parametrize('damage', ['pending', 'missing-checkpoint', 'tampered-image', 'no-proof', 'uncertain'])
def test_refinement_requires_intact_completed_proven_rejection(rejected_flux_parent, damage):
    parent = rejected_flux_parent
    store, job, context, need, receipt, asset, image, _ = parent
    if damage == 'pending':
        asset.quality_status = 'AWAITING_REVIEW'
    elif damage == 'missing-checkpoint':
        from atnos.intelligence.storage import connect
        stage = f"assets/image_local/flux2-trial/s01/{receipt['digest'][:16]}"
        with connect(store.settings) as db:
            db.execute("UPDATE pipeline_stage_checkpoints SET status='failed' WHERE job_id=? AND stage=?", (job, stage))
            db.commit()
    elif damage == 'tampered-image':
        Image.new('RGB', (768, 1344), '#ffffff').save(image)
    else:
        if damage == 'uncertain':
            review = json.loads((context.artifact_dir / 'assets/automatic-review' /
                asset.reviewed_by.rsplit(':', 1)[1] / 'review.json').read_text('utf-8'))
            review['checks']['required_objects_and_counts']['status'] = 'uncertain'
            (context.artifact_dir / 'assets/automatic-review' /
                asset.reviewed_by.rsplit(':', 1)[1] / 'review.json').write_text(json.dumps(review), encoding='utf-8')
        else:
            asset.reviewed_by = 'atnos_automatic_visual_qc:' + ('0' * 64)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        refinement.register_flux_refinement(store.settings, job, 's01', receipt['digest'], worker_token=token)
    root = context.artifact_dir / 'flux_refinements'
    assert not list(root.glob('**/authorization.json')) if root.exists() else True
    assert store.get(job)['provider_calls'] == 1


def test_refinement_respects_exhausted_existing_image_allowance(rejected_flux_parent, monkeypatch):
    parent = rejected_flux_parent
    store, job, context, _, receipt, _, _, _ = parent
    monkeypatch.setattr(refinement, '_effective_image_cap', lambda *_: 1)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='request limit'):
        refinement.register_flux_refinement(store.settings, job, 's01', receipt['digest'], worker_token=token)
    assert not (context.artifact_dir / 'flux_refinements').exists()
    assert store.get(job)['provider_calls'] == 1


def test_seed_change_cannot_create_a_second_epoch(rejected_flux_parent):
    parent = rejected_flux_parent
    first = _authorize_refinement(parent)
    store, job, _, _, old, _, _, _ = parent
    with store.lease(job) as token, pytest.raises(OrderConflict, match='immutable'):
        refinement.register_flux_refinement(store.settings, job, 's01', old['digest'],
                                            worker_token=token, seed=first['seed'] + 1)


def test_tampered_new_receipt_fails_read_only_loader(rejected_flux_parent):
    parent = rejected_flux_parent
    store, job, context, need, old, _, _, _ = parent
    receipt = _authorize_refinement(parent)
    path = context.artifact_dir / 'flux_refinements/s01' / receipt['digest'][:16] / 'authorization.json'
    saved = json.loads(path.read_text('utf-8'))
    saved['workflow_sha256'] = '0' * 64
    path.write_text(json.dumps(saved), encoding='utf-8')
    current_context = store.context(job)
    from atnos.production.wb_image_api import load_image_lane
    lane = load_image_lane(store.settings, job)
    with pytest.raises(ProductionBlocked, match='integrity'):
        refinement.load_flux_refinement(store.settings, current_context, 's01', need, lane, old['digest'])
    assert store.get(job)['provider_calls'] == 1


def _mock_flux_candidate_io(monkeypatch, folder):
    """Replace only the runtime session and one-shot transport boundary."""
    class MockColdFluxSession:
        def __init__(self, _settings, _receipt, output_folder):
            self.folder = output_folder

        def open(self):
            return self

        def __enter__(self):
            self.folder.mkdir(parents=True, exist_ok=True)
            for name, value in {
                'dispatch.json': {'status': 'queued', 'prompt_id': 'mock-prompt'},
                'runtime.json': {'status': 'mock-runtime'},
                'instance.json': {'status': 'mock-instance'},
                'model-paths.yaml': 'mock: true\n',
            }.items():
                path = self.folder / name
                path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
            return object()

        def __exit__(self, *_exc):
            return False

    image = BytesIO()
    Image.new('RGB', (768, 1344), '#132938').save(image, format='PNG')
    monkeypatch.setattr(flux, 'ColdFluxSession', MockColdFluxSession)
    submitted = []
    def submit(*_args):
        submitted.append(True)
        return image.getvalue(), 'mock-prompt', {'mock-prompt': {'status': {'completed': True, 'status_str': 'success'}}}
    monkeypatch.setattr(flux, '_submit_and_wait_once', submit)
    return submitted


def _generate_real_refinement_candidate(parent, isolated_brain, monkeypatch):
    store, job, context, need, parent_receipt, _asset, _image, _old_bytes = parent
    receipt = _authorize_refinement(parent)
    from atnos.production.wb_image_api import load_image_lane
    from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
    lane = load_image_lane(store.settings, job)
    style = context.artifacts['project_profile']['visual_style']
    tags = style_asset_tags(style, job, 's01')
    submitted = _mock_flux_candidate_io(monkeypatch,
        context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16])
    # The existing test fixture replaces the catalog with an in-memory list;
    # keep the production ingestion method and persist only through that fixture.
    from atnos.production.asset_brain.approval import AssetApprovalService
    def append_in_fixture(_service, record):
        isolated_brain.append(record)
        return record
    monkeypatch.setattr(AssetApprovalService, '_replace', append_in_fixture)
    fingerprint = 'b' * 64
    result = flux.generate_flux_candidate(store.settings, context, need, style, tags, lane, fingerprint, receipt)
    return receipt, lane, result, fingerprint, submitted


def test_real_refinement_generator_provenance_matches_and_tampered_fingerprint_is_rejected(
        rejected_flux_parent, isolated_brain, monkeypatch):
    parent = rejected_flux_parent
    store, job, _context, _need, _parent_receipt, _asset, _image, _ = parent
    receipt, _lane, result, fingerprint, _submitted = _generate_real_refinement_candidate(parent, isolated_brain, monkeypatch)
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    brain = WealthBlueprintAssetBrain(store.settings)
    asset = next(item for item in brain.catalog.assets if item.id == result.payload['asset_id'])
    assert result.payload['trial_digest'] == receipt['digest']
    assert asset.generation.fingerprint == fingerprint
    assert asset.request_fingerprint == fingerprint
    evidence = json.loads(Path(asset.generation.evidence_path).read_text('utf-8'))
    assert evidence['request_fingerprint'] == fingerprint
    expected_negative = receipt['workflow']['5']['inputs']['text']
    assert evidence['negative_prompt'] == expected_negative
    assert asset.generation.negative_prompt == expected_negative
    refinement.verify_flux_refinement_asset(asset, receipt)

    asset.generation.fingerprint = 'c' * 64
    with pytest.raises(ProductionBlocked, match='provenance is invalid'):
        refinement.verify_flux_refinement_asset(asset, receipt)
    assert store.get(job)['provider_calls'] == 1


def test_completed_refinement_candidate_is_read_only_resume_evidence(rejected_flux_parent, isolated_brain, monkeypatch):
    parent = rejected_flux_parent
    store, job, _context, _need, _parent_receipt, _asset, _image, _ = parent
    receipt, _lane, result, _fingerprint, _submitted = _generate_real_refinement_candidate(parent, isolated_brain, monkeypatch)
    stage = f"assets/image_local/flux2-trial/s01/{receipt['digest'][:16]}"
    with store.lease(job) as token:
        store.begin(job, token, stage, {'refinement_digest': receipt['digest']}, local_model_call=True)
        store.complete(job, token, stage, result.payload, result.evidence_paths)
    with store.lease(job) as token:
        store.block(job, token, 'image_generation', category='execution_failure',
                    message='Flux refinement candidate provenance is invalid')

    state = store.get(job)
    assert state['status'] == 'blocked' and state['current_step'] == 'image_generation'
    assert state['worker_live'] is False and state['can_resume'] is True
    calls_before_evidence = state['provider_calls']
    proof = refinement.completed_refinement_candidate_evidence(store, state)
    assert proof == {'job_id': job, 'scene_id': 's01', 'stage': stage,
        'trial_digest': receipt['digest'], 'asset_id': result.payload['asset_id'],
        'request_fingerprint': result.payload['request_fingerprint'],
        'checkpoint_updated_at': next(row['updated_at'] for row in state['checkpoints'] if row['stage'] == stage)}
    assert store.get(job)['provider_calls'] == calls_before_evidence


@pytest.mark.parametrize('damage', ['wrong-message', 'wrong-category', 'worker-live', 'missing-checkpoint',
                                    'tampered-request', 'tampered-output', 'tampered-digest'])
def test_completed_refinement_evidence_fails_closed(rejected_flux_parent, isolated_brain, monkeypatch, damage):
    parent = rejected_flux_parent
    store, job, context, _need, _parent_receipt, _asset, _image, _ = parent
    receipt, _lane, result, _fingerprint, _submitted = _generate_real_refinement_candidate(parent, isolated_brain, monkeypatch)
    stage = f"assets/image_local/flux2-trial/s01/{receipt['digest'][:16]}"
    with store.lease(job) as token:
        store.begin(job, token, stage, {'refinement_digest': receipt['digest']}, local_model_call=True)
        store.complete(job, token, stage, result.payload, result.evidence_paths)
    with store.lease(job) as token:
        store.block(job, token, 'image_generation', category='execution_failure',
                    message='Flux refinement candidate provenance is invalid')
    if damage == 'tampered-request':
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
        asset = next(item for item in WealthBlueprintAssetBrain(store.settings).catalog.assets
                     if item.id == result.payload['asset_id'])
        asset.request_fingerprint = 'd' * 64
    elif damage == 'tampered-output':
        output = Path(result.payload['output_path'])
        Image.new('RGB', (768, 1344), '#ffffff').save(output)

    state = store.get(job)
    calls_before_evidence = state['provider_calls']
    if damage == 'wrong-message':
        state['error'] = json.dumps({'category': 'execution_failure', 'message': 'different failure'})
    elif damage == 'wrong-category':
        state['error'] = json.dumps({'category': 'quality_rejection',
                                     'message': 'Flux refinement candidate provenance is invalid'})
    elif damage == 'worker-live':
        state['worker_live'] = True
    elif damage == 'missing-checkpoint':
        state['checkpoints'] = [row for row in state['checkpoints'] if row['stage'] != stage]
    elif damage == 'tampered-digest':
        checkpoint = next(row for row in state['checkpoints'] if row['stage'] == stage)
        checkpoint['output']['trial_digest'] = 'e' * 64
    assert refinement.completed_refinement_candidate_evidence(store, state) is None
    assert store.get(job)['provider_calls'] == calls_before_evidence


@pytest.fixture
def completed_refinement_recovery(rejected_flux_parent, isolated_brain, monkeypatch):
    parent = rejected_flux_parent
    store, job, context, _need, parent_receipt, _parent_asset, _parent_image, old_receipt_bytes = parent
    receipt, _lane, result, _fingerprint, submitted = _generate_real_refinement_candidate(
        parent, isolated_brain, monkeypatch)
    stage = f"assets/image_local/flux2-trial/s01/{receipt['digest'][:16]}"
    with store.lease(job) as token:
        store.begin(job, token, stage, {'refinement_digest': receipt['digest']}, local_model_call=True)
        store.complete(job, token, stage, result.payload, result.evidence_paths)
    with store.lease(job) as token:
        store.block(job, token, 'image_generation', category='execution_failure',
                    message='Flux refinement candidate provenance is invalid')
    new_receipt_path = context.artifact_dir / 'flux_refinements/s01' / receipt['digest'][:16] / 'authorization.json'
    before = store.get(job)
    return (store, job, context, parent_receipt, receipt, stage, result, submitted,
            old_receipt_bytes, new_receipt_path.read_bytes(), before)


def test_monitor_reserves_and_dispatches_completed_refinement_once(completed_refinement_recovery, monkeypatch):
    (store, job, context, parent_receipt, receipt, stage, result, submitted,
     old_receipt_bytes, new_receipt_bytes, before) = completed_refinement_recovery
    dispatched = []
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda self, job_id: dispatched.append(job_id) or {'id': job_id})

    assert recover_due(store.settings) == [{'id': job}]
    assert recover_due(store.settings) == []
    after = store.get(job)
    assert dispatched == [job] and len(submitted) == 1
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['profile_hash'] == before['profile_hash']
    assert next(row for row in after['checkpoints'] if row['stage'] == stage)['attempts'] == 1
    assert policy(store.settings, job)['recoveries_used'] == 1
    assert (context.artifact_dir / 'flux_trial/s01/authorization.json').read_bytes() == old_receipt_bytes
    assert (context.artifact_dir / 'flux_refinements/s01' / receipt['digest'][:16] /
            'authorization.json').read_bytes() == new_receipt_bytes
    assert next(row for row in after['history'] if row['event'] == 'autonomous_recovery_reserved')[
        'detail']['kind'] == 'completed_refinement_provenance_recovery'
    assert next(row for row in after['history'] if row['event'] == 'autonomous_recovery_reserved')[
        'detail']['evidence']['request_fingerprint'] == result.payload['request_fingerprint']
    assert result.payload['asset_id']


def test_monitor_does_not_reserve_for_tampered_completed_refinement(completed_refinement_recovery,
                                                                    isolated_brain, monkeypatch):
    (store, job, _context, _parent_receipt, _receipt, _stage, result, submitted,
     _old_receipt_bytes, _new_receipt_bytes, before) = completed_refinement_recovery
    asset = next(item for item in isolated_brain if item.id == result.payload['asset_id'])
    asset.generation.fingerprint = 'f' * 64
    dispatched = []
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda self, job_id: dispatched.append(job_id) or {'id': job_id})
    assert recover_due(store.settings) == []
    after = store.get(job)
    assert dispatched == [] and len(submitted) == 1
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['profile_hash'] == before['profile_hash']
    assert policy(store.settings, job)['recoveries_used'] == 0
