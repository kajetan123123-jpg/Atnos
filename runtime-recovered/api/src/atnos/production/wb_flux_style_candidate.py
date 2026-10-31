"""One bounded Minote LoRA candidate after a pixel-proven photo-medium miss.

This module only registers and verifies immutable authorization. The ordinary
WB image stage owns the lease, allowance, cache, dispatch and Asset Brain flow.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production import wb_local_flux_trial as flux
from atnos.production import wb_flux_refinement as refinement

VERSION = 'wb-flux2-klein-minote-style-candidate-v1'
COMPILER_VERSION = 'wb-flux2-klein-minote-subject-first-v1'
TABLE = 'production_wb_flux_style_candidates'
LORA_REPO = 'noema-art/minoteflux-klein'
LORA_REVISION = '3ae382c373698c3b9718f586e76b984defd1ebce'
LORA_FILENAME = 'minoteflux_klein.safetensors'
LORA_BYTES = 92_426_592
LORA_SHA256 = '716b5bd2f049142a4ee187563e1103035850a56285e95d2d2a2b78011e71c36b'
LORA_PATH = Path('E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Shared/models/loras') / LORA_FILENAME
LORA_TRIGGER = 'minote'
LORA_STRENGTH = 1.0
_MINOTE_STYLE_ADAPTATION = {
    'A': 'Ink-and-watercolor finish with varied navy graphite contours, tactile materials and restrained antique-gold accents.',
    'B': 'Pigment and ink finish while preserving the requested layered cut-paper medium, visible cut edges and soft layer shadows.',
    'C': 'Pigment accents while preserving precise technical ink contours, blueprint-blue details and material hatching.',
    'D': 'Ink-and-watercolor finish with tactile materials, navy contours, cream highlights and restrained antique gold.',
}
SOURCE = 'Atnos autonomous single Minote style candidate after intact pixel-proven photo-medium rejection; parent retained.'


def _on_e(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.drive.casefold() != 'e:':
        raise ProductionBlocked('Flux style candidate paths must remain on E:')
    return resolved


def _lora_manifest() -> dict:
    path = _on_e(LORA_PATH)
    if not path.is_file() or path.stat().st_size != LORA_BYTES or file_hash(path) != LORA_SHA256:
        raise ProductionBlocked('Pinned Minote LoRA is missing or its pinned bytes do not match')
    return {'path': str(path), 'bytes': LORA_BYTES, 'sha256': LORA_SHA256,
            'repo': LORA_REPO, 'revision': LORA_REVISION, 'filename': LORA_FILENAME,
            'license': 'Apache-2.0', 'architecture': 'flux2_klein_4b', 'trigger': LORA_TRIGGER}


def _check_lora_manifest(item: dict) -> None:
    expected = _lora_manifest()
    if item != expected:
        raise ProductionBlocked('Pinned Minote LoRA authorization integrity failed')


def _compile_prompt(need: dict, style: dict) -> tuple[str, str]:
    style_id = str(style.get('id') or '')
    if style_id not in _MINOTE_STYLE_ADAPTATION:
        raise ProductionBlocked('Minote candidate requires the frozen WB style A, B, C or D')
    positive, negative = refinement._compile_prompt(need, style)
    subject = refinement._normalize_paper_slip(str(need.get('asset_subject') or '').strip())
    if not subject or not positive.startswith(subject):
        raise ProductionBlocked('Minote candidate requires the unchanged literal scene subject')
    # Preserve the frozen A-D medium, adding only a compatible Minote treatment.
    positive = positive.replace(subject, subject + ' minote. ' + _MINOTE_STYLE_ADAPTATION[style_id], 1)
    return positive, negative


def _workflow(need: dict, style: dict, seed: int, prefix: str) -> dict:
    prompt, negative = _compile_prompt(need, style)
    builder = flux.ComfyUiImageGenerationService.__new__(flux.ComfyUiImageGenerationService)
    graph = builder._build_flux2_klein_workflow(
        prompt=prompt, width=768, height=1344, seed=seed,
        unet_name=flux.MODEL_NAMES['unet'][1], clip_name=flux.MODEL_NAMES['clip'][1],
        vae_name=flux.MODEL_NAMES['vae'][1], negative_prompt=negative,
        steps_override=50, cfg_scale_override=4.0)
    graph['2']['inputs']['device'] = 'cpu'
    graph['13']['inputs']['filename_prefix'] = prefix
    graph['14'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {
        'model': ['1', 0], 'lora_name': LORA_FILENAME, 'strength_model': LORA_STRENGTH}}
    graph['8']['inputs']['model'] = ['14', 0]
    return graph


def _affirmative_photographic_medium(medium: str) -> bool:
    """Conservative English observation guard, not a general language classifier.

    Scope qualifiers to each marker's preceding clause. Negating drawing marks
    after an affirmative photo description must not negate that description.
    """
    for clause in re.split(r'[.;!?\n]', medium.casefold().replace('’', "'")):
        for marker in re.finditer(r'\b(?:photoreal\w*|photograph\w*|camera photo|looks like a photo)\b', clause):
            prefix = clause[:marker.start()]
            uncertain = re.search(
                r"\b(?:may|might|could|possibly|perhaps|uncertain|unclear)\b|"
                r"\b(?:cannot|can't|hard to) tell\b", prefix)
            negated = re.search(
                r"\b(?:not|no|non|never|without|isn't|aren't|doesn't|isnt|doesnt)"
                r"(?:[\s-]+[a-z]+){0,5}[\s-]*$|\brather than\s+(?:a\s+|an\s+)?$", prefix)
            if not uncertain and not negated:
                return True
    return False


def _latest_plain_refinement(settings, store, state, context, scene_id, need, lane, parent_digest):
    """Validate the exact plain refinement and require its grounded photo-medium rejection."""
    if not re.fullmatch(r'[0-9a-f]{64}', str(parent_digest)):
        raise OrderConflict('Minote candidate requires the exact plain Flux refinement digest')
    original = flux.load_flux_trial(settings, context, scene_id, need, lane)
    if original is None:
        raise OrderConflict('Minote candidate requires the original scoped Flux parent')
    parent = refinement.load_flux_refinement(settings, context, scene_id, need, lane, original['digest'])
    return _plain_refinement_evidence(settings, store, state, context, scene_id, original, parent, parent_digest)


def _plain_refinement_evidence(settings, store, state, context, scene_id, original, parent, parent_digest):
    """Same grounded parent proof after current-only loaders or archived-envelope verification."""
    if (parent is None or parent['digest'] != parent_digest or parent.get('version') != refinement.VERSION
            or parent.get('parent_digest') != original['digest']
            or not str(parent.get('source', '')).startswith('Atnos autonomous')):
        raise OrderConflict('Minote candidate requires the exact completed autonomous plain refinement')
    stage = f'assets/image_local/flux2-trial/{scene_id}/{parent_digest[:16]}'
    checkpoint = next((row for row in state['checkpoints'] if row['stage'] == stage), None)
    if (not checkpoint or checkpoint.get('status') != 'done' or checkpoint.get('attempts') != 1
            or checkpoint.get('output', {}).get('trial_digest') != parent_digest
            or not store._valid_files(context.artifact_dir, checkpoint.get('files_json'))):
        raise ProductionBlocked('Minote parent must have one intact completed normal image-stage checkpoint')
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.wb_image_review import proven_rejection_count
    brain = WealthBlueprintAssetBrain(settings)
    output = checkpoint.get('output') or {}
    asset = next((item for item in brain.catalog.assets if item.id == output.get('asset_id')), None)
    if (asset is None or asset.source != 'comfyui-flux-trial' or asset.quality_status != 'REJECTED'
            or Path(asset.file_path).resolve() != Path(output.get('output_path', '')).resolve()):
        raise ProductionBlocked('Minote candidate requires the exact rejected plain Flux parent asset')
    refinement.verify_flux_refinement_asset(asset, parent)
    if not proven_rejection_count(context, brain, [asset]):
        raise ProductionBlocked('Minote candidate requires intact negative pixel-QC evidence')
    review_key = str(asset.reviewed_by or '').removeprefix('atnos_automatic_visual_qc:')
    review_dir = context.artifact_dir / 'assets' / 'automatic-review' / review_key
    review = json.loads((review_dir / 'review.json').read_text('utf-8'))
    if review.get('identity', {}).get('contract') != 2:
        raise ProductionBlocked('Minote candidate requires contract-2 grounded pixel observations')
    if review.get('checks', {}).get('style_palette_and_lighting', {}).get('status') != 'failed':
        raise ProductionBlocked('Minote candidate requires a specific failed drawing-medium pixel check')
    from atnos.production.wb_image_review import _verify_grounded_result
    _verify_grounded_result(review_dir, review)
    observation_file = review.get('grounding_sources', {}).get('observation')
    if observation_file not in review.get('evidence_files', {}):
        raise ProductionBlocked('Minote candidate medium rejection has no intact observation source')
    observation = json.loads((review_dir / observation_file).read_text('utf-8'))
    medium = str(observation.get('drawing_medium_and_linework', '')).casefold()
    if not _affirmative_photographic_medium(medium):
        raise ProductionBlocked('Minote candidate requires observed photographic medium evidence')
    image_path = _on_e(Path(asset.file_path))
    evidence_path = _on_e(Path(asset.generation.evidence_path))
    return original, parent, checkpoint, asset, image_path, evidence_path


def eligible_flux_style_candidate(settings, store, state, context, scene_id, need, lane,
                                  parent_digest) -> dict | None:
    """Return proof for a photographically rejected refinement, or None if it is ineligible.

    Missing receipts and a different/uncertain review are normal no-choice states.
    Integrity and runtime failures propagate so they cannot silently select another renderer.
    """
    original = flux.load_flux_trial(settings, context, scene_id, need, lane)
    if original is None:
        return None
    parent = refinement.load_flux_refinement(settings, context, scene_id, need, lane, original['digest'])
    if parent is None or parent.get('digest') != parent_digest:
        return None
    try:
        evidence = _latest_plain_refinement(
            settings, store, state, context, scene_id, need, lane, parent_digest)
    except ProductionBlocked as exc:
        message = str(exc)
        if message in {
            'Minote candidate requires a specific failed drawing-medium pixel check',
            'Minote candidate requires observed photographic medium evidence',
        }:
            return None
        raise
    # Fail early if the actual pinned file is not installed, before routing selection.
    _lora_manifest()
    _, receipt, checkpoint, asset, image_path, evidence_path = evidence
    return {'digest': receipt['digest'], 'asset_id': asset.id, 'stage': checkpoint['stage'],
            'parent_image_sha256': file_hash(image_path),
            'parent_evidence_sha256': file_hash(evidence_path)}


def _effective_image_cap(settings, job_id, lane):
    try:
        from atnos.production.local_image_allowance import effective_local_image_allowance
    except ModuleNotFoundError as exc:
        if exc.name != 'atnos.production.local_image_allowance':
            raise
        return int(lane['max_requests'])
    return effective_local_image_allowance(settings, job_id, lane)


def _image_attempts(state):
    return sum(int(row['attempts']) for row in state['checkpoints']
               if row['stage'].startswith('assets/image_local/'))


def register_flux_style_candidate(settings, job_id: str, scene_id: str, parent_digest: str,
                                  *, worker_token: str) -> dict:
    """Freeze one Minote style candidate under an active authorized local worker lease."""
    from atnos.production.order_autonomy import policy
    from atnos.production.wb_image_api import load_image_lane
    store = ProductionOrderStore(settings)
    state, context, need = flux._scene_state(store, job_id, scene_id)
    if (state['request'].get('project') != 'wealth_blueprint'
            or state['request'].get('delivery_mode') != 'local_only'):
        raise OrderConflict('Minote candidate is restricted to local Wealth Blueprint orders')
    autonomy = policy(settings, job_id)
    if not autonomy or not autonomy.get('enabled'):
        raise OrderConflict('Minote candidate requires enabled local autonomy')
    with connect(settings) as db:
        store._owner(db, job_id, worker_token)
    lane = load_image_lane(settings, job_id)
    if not lane or lane.get('provider') != 'comfyui-local':
        raise OrderConflict('Minote candidate requires the frozen local ComfyUI lane')
    original, parent, checkpoint, asset, image_path, evidence_path = _latest_plain_refinement(
        settings, store, state, context, scene_id, need, lane, parent_digest)
    with connect(settings) as db:
        db.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE}(
            job_id TEXT NOT NULL, scene_id TEXT NOT NULL, parent_digest TEXT NOT NULL,
            compiler_version TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(job_id,scene_id,parent_digest,compiler_version))''')
        previous = db.execute(f'SELECT payload_json,digest,parent_digest FROM {TABLE} WHERE job_id=? AND scene_id=? AND compiler_version=?',
                              (job_id, scene_id, COMPILER_VERSION)).fetchone()
    if previous:
        if previous['parent_digest'] != parent_digest:
            raise OrderConflict('Minote style candidate epoch is already consumed for this scene')
        return load_flux_style_candidate(settings, context, scene_id, need, lane, parent_digest)
    consumed, cap = _image_attempts(state), _effective_image_cap(settings, job_id, lane)
    if cap is not None and consumed >= cap:
        raise ProductionBlocked('Authorized local image request limit reached; no Minote receipt created')
    lora = _lora_manifest()
    models = flux._manifest(flux._model_files())
    models['minote_lora'] = lora
    style = context.artifacts['project_profile']['visual_style']
    graph = _workflow(need, style, 429514, f'wb_flux_minote_{job_id}_{scene_id}_{COMPILER_VERSION}')
    payload = {
        'version': VERSION, 'compiler_version': COMPILER_VERSION, 'job_id': job_id,
        'scene_id': scene_id, 'parent_digest': parent_digest, 'parent_version': parent['version'],
        'parent_asset_id': asset.id, 'parent_image_sha256': file_hash(image_path),
        'parent_evidence_sha256': file_hash(evidence_path), 'parent_stage': checkpoint['stage'],
        'parent_stage_files': json.loads(checkpoint['files_json']), 'original_digest': original['digest'],
        'local_lane_digest': lane['digest'], 'request_hash': content_hash(state['request']),
        'profile_hash': content_hash(state['profile']), 'script_hash': content_hash(context.artifacts['script']),
        'need_hash': content_hash(need), 'models': models,
        'style_id': style['id'], 'style_definition_hash': style['definition_hash'],
        'runtime_files': flux._manifest(flux._runtime_files(settings)), 'lora': lora,
        'workflow': graph, 'workflow_sha256': content_hash(graph), 'seed': 429514,
        'width': 768, 'height': 1344, 'steps': 50, 'cfg': 4.0, 'max_attempts': 1,
        'unet_dtype': 'fp16', 'clip_device': 'cpu', 'vae_device': 'cpu',
        'lora_strength_model': LORA_STRENGTH, 'image_attempts_at_registration': consumed,
        'effective_image_request_cap': cap, 'source': SOURCE, 'user_approved': False,
        'appearance_approval': 'pending'}
    digest = content_hash(payload)
    folder = _on_e(context.artifact_dir / 'flux_style_candidates' / scene_id / digest[:16])
    receipt_path = folder / 'authorization.json'
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute(f'SELECT payload_json,digest,parent_digest FROM {TABLE} WHERE job_id=? AND scene_id=? AND compiler_version=?',
                              (job_id, scene_id, COMPILER_VERSION)).fetchone()
        if previous:
            if previous['parent_digest'] != parent_digest or previous['digest'] != digest:
                raise OrderConflict('Minote style candidate epoch is immutable')
            db.rollback()
        else:
            if receipt_path.exists():
                raise ProductionBlocked('Unregistered Minote receipt path already exists; inspect evidence')
            write_json(receipt_path, {**payload, 'digest': digest})
            db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?)',
                       (job_id, scene_id, parent_digest, COMPILER_VERSION, json_dumps(payload), digest, utc_now_iso()))
            db.commit()
    return load_flux_style_candidate(settings, context, scene_id, need, lane, parent_digest)


def load_flux_style_candidate(settings, context, scene_id, need, lane, parent_digest) -> dict | None:
    """Read and revalidate one style receipt; perform no dispatch or state writes."""
    with connect(settings) as db:
        try:
            row = db.execute(f'SELECT payload_json,digest FROM {TABLE} WHERE job_id=? AND scene_id=? AND parent_digest=? AND compiler_version=?',
                             (context.job_id, scene_id, parent_digest, COMPILER_VERSION)).fetchone()
        except sqlite3.OperationalError as exc:
            if f'no such table: {TABLE}' in str(exc):
                return None
            raise
    if row is None:
        return None
    store = ProductionOrderStore(settings)
    state, canonical_context, canonical_need = flux._scene_state(store, context.job_id, scene_id)
    if content_hash(canonical_need) != content_hash(need):
        raise ProductionBlocked('Minote scene need no longer matches the canonical order')
    canonical_context.artifacts['production_order'] = context.artifacts['production_order']
    parent_values = _latest_plain_refinement(settings, store, state, canonical_context, scene_id, need, lane, parent_digest)
    verified = _verify_style_record(state, context, scene_id, need, lane, parent_digest, row, parent_values)
    flux._check_manifest(verified['models'])
    flux._check_manifest(verified['runtime_files'])
    _check_lora_manifest(verified['lora'])
    return verified


def _verify_style_record(state, context, scene_id, need, lane, parent_digest, row, parent_values):
    """Shared immutable envelope checks; does not choose a canonical or execute a provider."""
    payload, digest = json.loads(row['payload_json']), row['digest']
    receipt_path = _on_e(context.artifact_dir / 'flux_style_candidates' / scene_id / digest[:16] / 'authorization.json')
    original, parent, checkpoint, asset, image_path, evidence_path = parent_values
    frozen, effective = state['profile'], context.artifacts['project_profile']
    profile_matches = (payload.get('profile_hash') == content_hash(frozen)
                       and content_hash({k:v for k,v in effective.items() if k != 'feedback_rules'})
                       == content_hash({k:v for k,v in frozen.items() if k != 'feedback_rules'}))
    if (not receipt_path.is_file() or json.loads(receipt_path.read_text('utf-8')) != {**payload, 'digest': digest}
            or content_hash(payload) != digest or payload.get('version') != VERSION
            or payload.get('compiler_version') != COMPILER_VERSION or payload.get('job_id') != context.job_id
            or payload.get('scene_id') != scene_id or payload.get('parent_digest') != parent_digest
            or payload.get('parent_version') != parent['version'] or payload.get('original_digest') != original['digest']
            or payload.get('parent_asset_id') != asset.id or payload.get('parent_stage') != checkpoint['stage']
            or payload.get('parent_stage_files') != json.loads(checkpoint['files_json'])
            or payload.get('parent_image_sha256') != file_hash(image_path)
            or payload.get('parent_evidence_sha256') != file_hash(evidence_path)
            or payload.get('local_lane_digest') != lane.get('digest') or lane.get('provider') != 'comfyui-local'
            or payload.get('request_hash') != content_hash(context.artifacts['production_order'])
            or not profile_matches or payload.get('script_hash') != content_hash(context.artifacts['script'])
            or payload.get('need_hash') != content_hash(need)
            or payload.get('style_id') != context.artifacts['project_profile']['visual_style']['id']
            or payload.get('style_definition_hash') != context.artifacts['project_profile']['visual_style']['definition_hash']
            or payload.get('workflow_sha256') != content_hash(payload.get('workflow'))
            or payload.get('max_attempts') != 1 or payload.get('user_approved') is not False
            or payload.get('source') != SOURCE):
        raise ProductionBlocked('Minote style candidate authorization integrity failed')
    _verify_lora_graph(payload)
    return {**payload, 'digest': digest}


def _verify_lora_graph(receipt: dict) -> None:
    graph = receipt.get('workflow')
    lora_node = graph.get('14') if isinstance(graph, dict) else None
    guider = graph.get('8') if isinstance(graph, dict) else None
    if (not isinstance(lora_node, dict) or lora_node.get('class_type') != 'LoraLoaderModelOnly'
            or lora_node.get('inputs') != {'model': ['1', 0], 'lora_name': LORA_FILENAME,
                                          'strength_model': LORA_STRENGTH}
            or not isinstance(guider, dict) or guider.get('inputs', {}).get('model') != ['14', 0]
            or receipt.get('workflow_sha256') != content_hash(graph)):
        raise ProductionBlocked('Minote style candidate LoRA graph binding is invalid')


def verify_flux_style_asset(asset, receipt):
    """Prove an asset came from the actual scoped Flux producer using pinned LoRA graph."""
    _verify_lora_graph(receipt)
    refinement.verify_flux_refinement_asset(asset, receipt)
    evidence = json.loads(Path(asset.generation.evidence_path).read_text('utf-8'))
    if (evidence.get('trial_digest') != receipt.get('digest')
            or evidence.get('models', {}).get('minote_lora') != receipt.get('lora')
            or evidence.get('workflow_sha256') != receipt.get('workflow_sha256')
            or asset.generation.backend != 'flux2_klein_scoped_trial'
            or asset.generation.prompt != receipt['workflow']['4']['inputs']['text']):
        raise ProductionBlocked('Flux Minote candidate provenance is invalid')
