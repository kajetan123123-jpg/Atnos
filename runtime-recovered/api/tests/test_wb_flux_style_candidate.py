"""Bounded Minote style-candidate compiler and provenance contract."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import shutil
from uuid import uuid4
from pathlib import Path

import pytest

from atnos.production import wb_flux_style_candidate as style_candidate
from atnos.production import wb_local_flux_trial as flux
from atnos.production.order_store import ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from test_wb_flux_refinement import (_generate_real_refinement_candidate,
                                    _mock_flux_candidate_io, rejected_flux_parent)
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts
from test_wb_local_flux_trial import isolated_brain, local_trial, trial


@pytest.fixture
def tmp_path():
    """Keep all test artifacts on the configured E: data drive."""
    root = Path('E:/Atnos/tmp/flux-style-candidate').resolve()
    path = root / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        if path.resolve().parent == root:
            shutil.rmtree(path)


NEED = {
    'asset_subject': 'One person places one horizontal unprinted teal paper card beside one small unbranded shopping bag.',
    'asset_composition': 'Keep both requested props fully visible with a clear gap. Place the pair on a wooden desk. Avoid: extra bags, banknotes, logos.',
}


def test_static_workflow_binds_model_only_lora_and_subject_first_prompt():
    graph = style_candidate._workflow(NEED, {'id': 'D'}, 429514, 'candidate-test')
    assert graph['14'] == {'class_type': 'LoraLoaderModelOnly', 'inputs': {
        'model': ['1', 0], 'lora_name': style_candidate.LORA_FILENAME, 'strength_model': 1.0}}
    assert graph['8']['inputs']['model'] == ['14', 0]
    assert graph['2']['inputs']['device'] == 'cpu'
    assert graph['10']['inputs']['steps'] == 50
    assert graph['6']['inputs'] == {'width': 768, 'height': 1344, 'batch_size': 1}
    prompt = graph['4']['inputs']['text']
    assert prompt.startswith('One person places one horizontal unprinted teal paper card')
    assert prompt.casefold().count('minote') == 1 and 'hand-drawn editorial illustration' in prompt.casefold()
    assert 'small unbranded shopping bag' in prompt and 'paper slip' not in prompt.casefold()
    assert graph['5']['inputs']['text'].find('photograph') >= 0


def test_lora_graph_tampering_is_rejected_before_any_generation():
    graph = style_candidate._workflow(NEED, {'id': 'D'}, 429514, 'candidate-test')
    receipt = {'workflow': graph, 'workflow_sha256': content_hash(graph)}
    style_candidate._verify_lora_graph(receipt)
    changed = deepcopy(receipt)
    changed['workflow']['8']['inputs']['model'] = ['1', 0]
    with pytest.raises(ProductionBlocked, match='LoRA graph binding'):
        style_candidate._verify_lora_graph(changed)


def test_lora_manifest_requires_the_exact_pinned_asset(monkeypatch):
    pinned = {'path': 'E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Shared/models/loras/minoteflux_klein.safetensors',
              'bytes': style_candidate.LORA_BYTES, 'sha256': style_candidate.LORA_SHA256,
              'repo': style_candidate.LORA_REPO, 'revision': style_candidate.LORA_REVISION,
              'filename': style_candidate.LORA_FILENAME, 'license': 'Apache-2.0',
              'architecture': 'flux2_klein_4b', 'trigger': 'minote'}
    monkeypatch.setattr(style_candidate, '_lora_manifest', lambda: pinned)
    style_candidate._check_lora_manifest(pinned)
    with pytest.raises(ProductionBlocked, match='integrity'):
        style_candidate._check_lora_manifest({**pinned, 'revision': '0' * 40})


def test_prompt_uses_refinement_subject_compiler_and_keeps_explicit_exclusions():
    expected = {
        'A': 'varied navy graphite contours',
        'B': 'layered cut-paper illustration',
        'C': 'technical ink illustration',
        'D': 'cinematic hand-drawn editorial illustration',
    }
    for style_id, signature in expected.items():
        positive, negative = style_candidate._compile_prompt(NEED, {'id': style_id})
        assert positive.startswith('One person places one horizontal unprinted teal paper card')
        assert positive.casefold().count('minote') == 1 and signature in positive
        assert 'extra bags' in negative and 'banknotes' in negative and 'logos' in negative


@pytest.fixture
def rejected_plain_refinement(rejected_flux_parent, isolated_brain, monkeypatch, tmp_path, request):
    """A real Flux producer result with intact stage and grounded photo-medium rejection."""
    from atnos.production.wb_flux_refinement import verify_flux_refinement_asset
    from atnos.production.wb_image_api import load_image_lane
    from atnos.production.wb_image_review import grounded_checks

    base = rejected_flux_parent
    store, job, original_context, need, original, _, _, _ = base
    parent, lane, generated, _, submitted = _generate_real_refinement_candidate(
        base, isolated_brain, monkeypatch)
    assert len(submitted) == 1
    asset = next(item for item in isolated_brain if item.id == generated.payload['asset_id'])
    verify_flux_refinement_asset(asset, parent)
    stage = f"assets/image_local/flux2-trial/s01/{parent['digest'][:16]}"
    with store.lease(job) as token:
        store.begin(job, token, stage, {'refinement_digest': parent['digest']}, local_model_call=True)
        store.complete(job, token, stage, generated.payload, list(generated.evidence_paths))

    observation = {
        'objects_and_positions': ['one paper card, centered on a wooden desk'],
        'object_inventory_complete': True, 'setting': 'A wooden desk in a quiet room.',
        'materials_and_textures': ['smooth paper', 'wood grain'],
        'palette_and_regions': ['teal card at center', 'brown desk'],
        'lighting_and_depth': 'Soft side light with visible depth.',
        'drawing_medium_and_linework': getattr(request, 'param',
            'The image looks like a photorealistic camera photograph, with no visible ink or pencil linework.'),
        'framing_and_readability': 'The card is centered and fully visible.',
        'caption_areas': ['upper left'], 'visible_text_and_marks': 'No visible text or marks.',
        'visible_defects': [], 'uncertainty': 'No major uncertainty.'}
    fields = {
        'required_objects_and_counts': ['objects_and_positions', 'object_inventory_complete'],
        'composition_and_readability': ['objects_and_positions', 'framing_and_readability', 'caption_areas'],
        'style_palette_and_lighting': ['materials_and_textures', 'palette_and_regions',
                                       'lighting_and_depth', 'drawing_medium_and_linework'],
        'integrity_and_no_embedded_text': ['visible_text_and_marks', 'visible_defects'],
    }
    raw_checks = {name: {'status': 'failed' if name == 'style_palette_and_lighting' else 'passed',
                         'evidence': 'Pixel evidence supports this assessment clearly.',
                         'observation_fields': refs}
                  for name, refs in fields.items()}
    checks = grounded_checks(raw_checks, observation)
    _, context, _ = flux._scene_state(store, job, 's01')
    key_data = {'image_sha256': flux.file_hash(Path(asset.file_path)), 'scene': 's01', 'review_version': 'fixture', 'contract': 2}
    key = content_hash(key_data)
    review_dir = context.artifact_dir / 'assets/automatic-review' / key
    review_dir.mkdir(parents=True)
    observation_path = review_dir / 'observation-validated.json'
    comparison_path = review_dir / 'comparison-evidence-validated.json'
    observation_path.write_text(__import__('json').dumps(observation), encoding='utf-8')
    comparison_path.write_text(__import__('json').dumps(raw_checks), encoding='utf-8')
    result = {'identity': key_data, 'asset_id': asset.id, 'checks': checks, 'user_approved': False,
              'grounding_sources': {'observation': observation_path.name, 'comparison': comparison_path.name},
              'evidence_files': {observation_path.name: flux.file_hash(observation_path),
                                 comparison_path.name: flux.file_hash(comparison_path)}}
    (review_dir / 'review.json').write_text(__import__('json').dumps(result), encoding='utf-8')
    asset.quality_status = 'REJECTED'
    asset.reviewed_by = 'atnos_automatic_visual_qc:' + key
    monkeypatch.setattr(style_candidate, '_lora_manifest', lambda: {
        'path': str((tmp_path / 'minote.safetensors').resolve()), 'bytes': 1, 'sha256': sha256(b'x').hexdigest(),
        'repo': style_candidate.LORA_REPO, 'revision': style_candidate.LORA_REVISION,
        'filename': style_candidate.LORA_FILENAME, 'license': 'Apache-2.0',
        'architecture': 'flux2_klein_4b', 'trigger': 'minote'})
    (tmp_path / 'minote.safetensors').write_bytes(b'x')
    # Bind the fake fixture bytes to the receipt manifest checks while production
    # continues to use the real pinned E: path and digest.
    return store, job, context, need, original, parent, asset, generated, lane


def test_registration_is_single_append_only_candidate_and_never_dispatches(rejected_plain_refinement, monkeypatch):
    store, job, context, need, original, parent, asset, generated, lane = rejected_plain_refinement
    from atnos.production.order_autonomy import enable
    from atnos.production.wb_image_api import load_image_lane
    from atnos.production import wb_flux_refinement as refinement

    enable(store.settings, job, reason='Test fixture authorizes bounded autonomous local image recovery.')
    calls = []
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda *_: pytest.fail('receipt registration launched GPU'), raising=False)
    monkeypatch.setattr(flux, 'generate_flux_candidate', lambda *_: calls.append('generated'))
    with store.lease(job) as token:
        receipt = style_candidate.register_flux_style_candidate(
            store.settings, job, 's01', parent['digest'], worker_token=token)
    with store.lease(job) as token:
        replay = style_candidate.register_flux_style_candidate(
            store.settings, job, 's01', parent['digest'], worker_token=token)
    assert receipt['digest'] == replay['digest']
    assert receipt['parent_digest'] == parent['digest']
    assert receipt['original_digest'] == original['digest']
    assert receipt['compiler_version'] == style_candidate.COMPILER_VERSION
    assert receipt['models']['minote_lora'] == receipt['lora']
    assert receipt['max_attempts'] == 1 and receipt['user_approved'] is False
    assert receipt['image_attempts_at_registration'] == 2
    assert receipt['workflow_sha256'] == content_hash(receipt['workflow'])
    assert receipt['workflow']['14']['inputs']['model'] == ['1', 0]
    assert receipt['workflow']['8']['inputs']['model'] == ['14', 0]
    assert calls == [] and store.get(job)['provider_calls'] == 2
    assert asset.quality_status == 'REJECTED' and asset.approved is False
    assert (context.artifact_dir / 'flux_style_candidates/s01' / receipt['digest'][:16] /
            'authorization.json').is_file()


def test_real_flux_producer_candidate_carries_and_verifies_lora_provenance(rejected_plain_refinement, isolated_brain, monkeypatch):
    store, job, context, need, _original, parent, _asset, _generated, lane = rejected_plain_refinement
    from atnos.production.order_autonomy import enable
    from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    enable(store.settings, job, reason='Test fixture authorizes bounded autonomous local image recovery.')
    with store.lease(job) as token:
        receipt = style_candidate.register_flux_style_candidate(
            store.settings, job, 's01', parent['digest'], worker_token=token)
    candidate_context = store.context(job)
    style = candidate_context.artifacts['project_profile']['visual_style']
    tags = style_asset_tags(style, job, 's01')
    submitted = _mock_flux_candidate_io(
        monkeypatch, context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16])
    result = flux.generate_flux_candidate(
        store.settings, candidate_context, need, style, tags, lane, 'c' * 64, receipt)
    assert len(submitted) == 1 and result.payload['trial_digest'] == receipt['digest']
    brain = WealthBlueprintAssetBrain(store.settings)
    candidate = next(item for item in brain.catalog.assets if item.id == result.payload['asset_id'])
    evidence = __import__('json').loads(Path(candidate.generation.evidence_path).read_text('utf-8'))
    assert evidence['models']['minote_lora'] == receipt['lora']
    style_candidate.verify_flux_style_asset(candidate, receipt)
    candidate.generation.fingerprint = 'd' * 64
    with pytest.raises(ProductionBlocked, match='provenance is invalid'):
        style_candidate.verify_flux_style_asset(candidate, receipt)


def test_loader_rejects_tampered_receipt_without_reposting(rejected_plain_refinement):
    store, job, context, need, _original, parent, _asset, _generated, _lane = rejected_plain_refinement
    from atnos.production.order_autonomy import enable
    from atnos.production.wb_image_api import load_image_lane
    enable(store.settings, job, reason='Test fixture authorizes bounded autonomous local image recovery.')
    with store.lease(job) as token:
        receipt = style_candidate.register_flux_style_candidate(
            store.settings, job, 's01', parent['digest'], worker_token=token)
    path = context.artifact_dir / 'flux_style_candidates/s01' / receipt['digest'][:16] / 'authorization.json'
    data = __import__('json').loads(path.read_text('utf-8'))
    data['lora']['revision'] = '0' * 40
    path.write_text(__import__('json').dumps(data), encoding='utf-8')
    with pytest.raises(ProductionBlocked, match='integrity'):
        style_candidate.load_flux_style_candidate(
            store.settings, store.context(job), 's01', need,
            load_image_lane(store.settings, job), parent['digest'])
    assert store.get(job)['provider_calls'] == 2


@pytest.mark.parametrize('rejected_plain_refinement', [
    'The image looks like a photorealistic camera photograph, with no visible ink or pencil linework.',
    'This is not a drawing; it is a photograph.',
    'The image is a camera photograph with no visible ink or pencil linework.',
], indirect=True)
def test_intact_photographic_observation_is_eligible(rejected_plain_refinement):
    store, job, context, need, _original, parent, asset, _generated, lane = rejected_plain_refinement
    proof = style_candidate.eligible_flux_style_candidate(
        store.settings, store, store.get(job), context, 's01', need, lane, parent['digest'])
    assert proof['digest'] == parent['digest'] and proof['asset_id'] == asset.id
    assert not (context.artifact_dir / 'flux_style_candidates').exists()
    assert store.get(job)['provider_calls'] == 2


@pytest.mark.parametrize('rejected_plain_refinement', [
    'The image is a drawing, not a photograph.',
    'The image is not photorealistic and uses visible ink linework.',
    'No photographic medium is visible; the image is drawn.',
    'The image is non-photographic hand-drawn artwork.',
    'The image does not look like a photograph.',
    'The image does not look like a photo.',
    'This is not a camera photo; it uses ink contours.',
    'This is a painting rather than a photograph.',
    "The image doesn't look like a photo.",
    'The image might be a photograph, but the medium is uncertain.',
    'The image could be photorealistic, but this is only a possibility.',
    'The image is possibly photographic.',
    'It is hard to tell if this is a photograph or a drawing.',
    'The medium may be photographic.',
    'I cannot tell whether this is a photograph.',
], indirect=True)
def test_intact_negated_or_uncertain_medium_is_ineligible(rejected_plain_refinement):
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.wb_image_review import _verify_grounded_result, proven_rejection_count
    from atnos.production.order_autonomy import enable

    store, job, context, need, _original, parent, asset, _generated, lane = rejected_plain_refinement
    review_dir = context.artifact_dir / 'assets/automatic-review' / asset.reviewed_by.rsplit(':', 1)[1]
    review = __import__('json').loads((review_dir / 'review.json').read_text('utf-8'))
    # These observations were frozen before hashes and grounded checks were made.
    # Prove the real gate reaches medium semantics, rather than failing integrity.
    assert review['checks']['style_palette_and_lighting']['status'] == 'failed'
    _verify_grounded_result(review_dir, review)
    assert proven_rejection_count(context, WealthBlueprintAssetBrain(store.settings), [asset]) == 1
    assert style_candidate.eligible_flux_style_candidate(
        store.settings, store, store.get(job), context, 's01', need, lane, parent['digest']) is None
    enable(store.settings, job, reason='Test fixture authorizes bounded autonomous local image recovery.')
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='observed photographic medium evidence'):
        style_candidate.register_flux_style_candidate(
            store.settings, job, 's01', parent['digest'], worker_token=token)
    assert not (context.artifact_dir / 'flux_style_candidates').exists()
    assert store.get(job)['provider_calls'] == 2


def test_registration_rejects_tampered_observation_hash(rejected_plain_refinement):
    store, job, context, need, _original, parent, asset, _generated, _lane = rejected_plain_refinement
    from atnos.production.order_autonomy import enable
    enable(store.settings, job, reason='Test fixture authorizes bounded autonomous local image recovery.')
    review_path = context.artifact_dir / 'assets/automatic-review' / asset.reviewed_by.rsplit(':', 1)[1] / 'observation-validated.json'
    observation = __import__('json').loads(review_path.read_text('utf-8'))
    observation['drawing_medium_and_linework'] = 'The image is a drawing, not a photograph.'
    write_json(review_path, observation)
    # The altered observation also invalidates its immutable hash, so the review
    # cannot qualify as evidence for another candidate.
    with pytest.raises(ProductionBlocked, match='intact negative pixel-QC evidence'):
        style_candidate.eligible_flux_style_candidate(
            store.settings, store, store.get(job), context, 's01', need, _lane, parent['digest'])
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='intact negative pixel-QC evidence'):
        style_candidate.register_flux_style_candidate(
            store.settings, job, 's01', parent['digest'], worker_token=token)
    assert not (context.artifact_dir / 'flux_style_candidates').exists()
    assert store.get(job)['provider_calls'] == 2
