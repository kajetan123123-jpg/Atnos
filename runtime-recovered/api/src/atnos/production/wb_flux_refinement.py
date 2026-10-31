"""Append-only, single-epoch Flux refinement for a proven rejected WB image.

Registration only freezes an auditable receipt. Dispatch remains the normal
image stage's responsibility and must use the ordinary worker lease/budget.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.models import StepResult
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json

from atnos.production import wb_local_flux_trial as flux

VERSION = 'wb-flux4b-refinement-v1'
COMPILER_VERSION = 'wb-flux4b-subject-first-v1'
DEFAULT_SEED = 429513
TABLE = 'production_wb_flux_refinements'

_STYLE_DIRECTION = {
    'A': 'Mature hand-drawn editorial illustration, tactile real materials, varied navy graphite contours, cream highlights, restrained antique gold, soft side light and clear depth.',
    'B': 'Mature layered cut-paper illustration, warm paper texture, precise navy ink contours, subtle cut edges and soft layer shadows, restrained antique gold and tactile depth.',
    'C': 'Mature technical ink illustration, precise navy and blueprint-blue contours, dimensional graphite shading, restrained antique gold, fine material hatching and clear object volume; keep real objects intact.',
    'D': 'Mature cinematic hand-drawn editorial illustration, tactile materials, warm practical light against deep navy shadows, cream highlights, restrained antique gold, clear foreground-background depth and steady eye-level framing.',
}


def _on_e(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.drive.casefold() != 'e:':
        raise ProductionBlocked('Flux refinement paths must remain on E:')
    return resolved


def _normalize_paper_slip(text: str) -> str:
    """Normalize only the ambiguous paper noun; preserve quantity and color."""
    text = re.sub(r'(?i)\bblank-faced\s+teal paper slip\b',
                  'horizontal unprinted teal paper card', text)
    text = re.sub(r'(?i)\bblank-faced\s+paper slip\b',
                  'horizontal unprinted paper card', text)
    text = re.sub(r'(?i)\bteal paper slip\b', 'horizontal unprinted teal paper card', text)
    text = re.sub(r'(?i)\bpaper slip\b', 'horizontal unprinted paper card', text)
    text = re.sub(r'(?i)\bleave the paper face blank for a later overlay\b',
                  'keep the paper card surface unprinted for the later overlay', text)
    return text


def _compile_prompt(need: dict, style: dict) -> tuple[str, str]:
    style_id = str(style.get('id') or '')
    if style_id not in _STYLE_DIRECTION:
        raise ProductionBlocked('Flux refinement requires the frozen WB style A, B, C or D')
    subject = _normalize_paper_slip(str(need.get('asset_subject') or '').strip())
    if not subject:
        raise ProductionBlocked('Flux refinement requires the unchanged literal scene subject')

    composition = _normalize_paper_slip(str(need.get('asset_composition') or '').strip())
    positive_composition, separator, exclusions = composition.partition('Avoid:')
    positive_composition = re.sub(r'\s+', ' ', positive_composition).strip()
    # Keep the scene's explicitly requested framing/location, but omit any
    # duplicated generic art-direction prose; canvas geometry comes from graph.
    positive = ' '.join(part.strip() for part in (
        subject,
        positive_composition,
        _STYLE_DIRECTION[style_id],
        'No written text, letters, numbers, labels, logos, or photorealism.',
    ) if part.strip())
    negative = flux.NEGATIVE
    explicit_exclusions = _normalize_paper_slip(exclusions.strip(' .')) if separator else ''
    if explicit_exclusions:
        negative = negative + ', ' + explicit_exclusions
    return positive, negative


def _workflow(need, style, seed, prefix):
    prompt, negative = _compile_prompt(need, style)
    builder = flux.ComfyUiImageGenerationService.__new__(flux.ComfyUiImageGenerationService)
    graph = builder._build_flux2_klein_workflow(
        prompt=prompt, width=768, height=1344, seed=seed,
        unet_name=flux.MODEL_NAMES['unet'][1], clip_name=flux.MODEL_NAMES['clip'][1],
        vae_name=flux.MODEL_NAMES['vae'][1], negative_prompt=negative,
        steps_override=50, cfg_scale_override=4.0)
    graph['2']['inputs']['device'] = 'cpu'
    graph['13']['inputs']['filename_prefix'] = prefix
    return graph


def _parent_evidence(settings, state, context, scene_id, need, lane, parent_digest):
    """Return the original receipt/asset only after one intact pixel rejection."""
    parent = flux.load_flux_trial(settings, context, scene_id, need, lane)
    return _verified_parent_evidence(settings, state, context, scene_id, parent, parent_digest)


def _verified_parent_evidence(settings, state, context, scene_id, parent, parent_digest):
    """Check result/rejection for an already envelope-verified parent receipt."""
    if (not parent or parent['digest'] != parent_digest
            or parent.get('version') != flux.VERSION
            or not str(parent.get('source', '')).startswith('Atnos autonomous')):
        raise OrderConflict('Refinement requires the exact completed autonomous one-shot parent')
    stage = f"assets/image_local/flux2-trial/{scene_id}/{parent_digest[:16]}"
    checkpoint = next((row for row in state['checkpoints'] if row['stage'] == stage), None)
    if (not checkpoint or checkpoint['status'] != 'done' or checkpoint['attempts'] != 1
            or not ProductionOrderStore(settings)._valid_files(context.artifact_dir, checkpoint['files_json'])):
        raise ProductionBlocked('Flux refinement parent must have one intact completed checkpoint')

    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.wb_image_review import proven_rejection_count
    brain = WealthBlueprintAssetBrain(settings)
    asset_id = checkpoint['output'].get('asset_id')
    asset = next((item for item in brain.catalog.assets if item.id == asset_id), None)
    if (not asset or asset.source != 'comfyui-flux-trial' or asset.quality_status != 'REJECTED'
            or not asset.generation or asset.generation.workflow_id != flux.VERSION
            or Path(asset.file_path).resolve() != Path(checkpoint['output'].get('output_path', '')).resolve()):
        raise ProductionBlocked('Flux refinement requires the exact rejected parent asset')
    flux.verify_flux_asset(asset, parent)
    if not proven_rejection_count(context, brain, [asset]):
        raise ProductionBlocked('Flux refinement requires intact negative pixel-QC evidence')
    evidence_path = _on_e(Path(asset.generation.evidence_path))
    image_path = _on_e(Path(asset.file_path))
    return parent, checkpoint, asset, image_path, evidence_path


def _effective_image_cap(settings, job_id, lane):
    # The companion allowance module is append-only and does not change the
    # frozen lane digest. Keep compatibility with deployments before it lands.
    try:
        from atnos.production.local_image_allowance import effective_local_image_allowance
    except ModuleNotFoundError as exc:
        if exc.name != 'atnos.production.local_image_allowance':
            raise
        return int(lane['max_requests'])
    return effective_local_image_allowance(settings, job_id, lane)


def _image_attempts(state) -> int:
    return sum(int(row['attempts']) for row in state['checkpoints']
               if row['stage'].startswith('assets/image_local/'))


def register_flux_refinement(settings, job_id: str, scene_id: str, parent_digest: str,
                             *, worker_token: str, seed: int = DEFAULT_SEED) -> dict:
    """Freeze one new refinement epoch under the live normal worker lease."""
    from atnos.production.order_autonomy import policy
    from atnos.production.wb_image_api import load_image_lane

    store = ProductionOrderStore(settings)
    state, context, need = flux._scene_state(store, job_id, scene_id)
    if state['request'].get('project') != 'wealth_blueprint' or state['request'].get('delivery_mode') != 'local_only':
        raise OrderConflict('Flux refinement is restricted to local Wealth Blueprint orders')
    autonomy = policy(settings, job_id)
    if not autonomy or not autonomy['enabled']:
        raise OrderConflict('Flux refinement requires enabled local autonomy')
    with connect(settings) as db:
        store._owner(db, job_id, worker_token)
    lane = load_image_lane(settings, job_id)
    if not lane or lane.get('provider') != 'comfyui-local':
        raise OrderConflict('Flux refinement requires the frozen local ComfyUI lane')
    if type(seed) is not int or not 0 <= seed < 2 ** 63:
        raise ValueError('Flux refinement seed is invalid')
    parent, checkpoint, asset, image_path, evidence_path = _parent_evidence(
        settings, state, context, scene_id, need, lane, parent_digest)

    with connect(settings) as db:
        db.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE}(
            job_id TEXT NOT NULL, scene_id TEXT NOT NULL, parent_digest TEXT NOT NULL,
            compiler_version TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(job_id,scene_id,parent_digest,compiler_version))''')
        previous = db.execute(f'SELECT payload_json,digest,parent_digest FROM {TABLE} WHERE job_id=? AND scene_id=? AND compiler_version=?',
                              (job_id, scene_id, COMPILER_VERSION)).fetchone()
    if previous:
        previous_payload = json.loads(previous['payload_json'])
        if previous['parent_digest'] != parent_digest:
            raise OrderConflict('Flux refinement compiler epoch is already consumed for this scene')
        if previous_payload.get('seed') != seed:
            raise OrderConflict('Flux refinement epoch is immutable')
        return load_flux_refinement(settings, context, scene_id, need, lane, parent_digest)

    consumed = _image_attempts(state)
    cap = _effective_image_cap(settings, job_id, lane)
    if cap is not None and consumed >= cap:
        raise ProductionBlocked('Authorized local image request limit reached; no refinement receipt created')
    style = context.artifacts['project_profile']['visual_style']
    graph = _workflow(need, style, seed, f'wb_flux_refinement_{job_id}_{scene_id}_{COMPILER_VERSION}')
    parent_stage_files = json.loads(checkpoint['files_json'])
    payload = {
        'version': VERSION, 'compiler_version': COMPILER_VERSION, 'job_id': job_id,
        'scene_id': scene_id, 'parent_digest': parent_digest, 'parent_version': parent['version'],
        'parent_asset_id': asset.id, 'parent_image_sha256': file_hash(image_path),
        'parent_evidence_sha256': file_hash(evidence_path),
        'parent_stage': checkpoint['stage'], 'parent_stage_files': parent_stage_files,
        'local_lane_digest': lane['digest'], 'request_hash': content_hash(state['request']),
        'profile_hash': content_hash(state['profile']),
        'script_hash': content_hash(context.artifacts['script']), 'need_hash': content_hash(need),
        'models': flux._manifest(flux._model_files()),
        'runtime_files': flux._manifest(flux._runtime_files(settings)),
        'workflow': graph, 'workflow_sha256': content_hash(graph), 'seed': seed,
        'width': 768, 'height': 1344, 'steps': 50, 'cfg': 4.0, 'max_attempts': 1,
        'unet_dtype': 'fp16', 'clip_device': 'cpu', 'vae_device': 'cpu',
        'image_attempts_at_registration': consumed, 'effective_image_request_cap': cap,
        'source': 'Atnos autonomous single refinement after intact pixel-proven rejection; parent retained.',
        'user_approved': False, 'appearance_approval': 'pending',
    }
    digest = content_hash(payload)
    folder = _on_e(context.artifact_dir / 'flux_refinements' / scene_id / digest[:16])
    receipt_path = folder / 'authorization.json'
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute(f'SELECT payload_json,digest FROM {TABLE} WHERE job_id=? AND scene_id=? AND parent_digest=? AND compiler_version=?',
                              (job_id, scene_id, parent_digest, COMPILER_VERSION)).fetchone()
        if previous:
            if previous['digest'] != digest:
                raise OrderConflict('Flux refinement epoch is immutable')
            db.rollback()
        else:
            if receipt_path.exists():
                raise ProductionBlocked('Unregistered refinement receipt path already exists; inspect evidence')
            write_json(receipt_path, {**payload, 'digest': digest})
            db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?)',
                       (job_id, scene_id, parent_digest, COMPILER_VERSION, json_dumps(payload), digest, utc_now_iso()))
            db.commit()
    return load_flux_refinement(settings, context, scene_id, need, lane, parent_digest)


def load_flux_refinement(settings, context, scene_id, need, lane, parent_digest) -> dict | None:
    """Read/verify one refinement receipt without dispatch or state writes."""
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
    state = ProductionOrderStore(settings).get(context.job_id)
    _, canonical_context, canonical_need = flux._scene_state(
        ProductionOrderStore(settings), context.job_id, scene_id)
    if content_hash(canonical_need) != content_hash(need):
        raise ProductionBlocked('Flux refinement scene need no longer matches the canonical order')
    context.artifacts.setdefault('script', canonical_context.artifacts['script'])
    if 'script_calculations' in canonical_context.artifacts:
        context.artifacts.setdefault('script_calculations', canonical_context.artifacts['script_calculations'])
    parent_values = _parent_evidence(settings, state, context, scene_id, need, lane, parent_digest)
    verified = _verify_refinement_record(state, context, scene_id, need, lane, parent_digest, row, parent_values)
    flux._check_manifest(verified['models'])
    flux._check_manifest(verified['runtime_files'])
    return verified


def _verify_refinement_record(state, context, scene_id, need, lane, parent_digest, row, parent_values):
    """Immutable envelope checks only; callers establish and verify the parent."""
    payload = json.loads(row['payload_json'])
    digest = row['digest']
    receipt_path = _on_e(context.artifact_dir / 'flux_refinements' / scene_id / digest[:16] / 'authorization.json')
    parent, parent_checkpoint, asset, image_path, evidence_path = parent_values
    frozen = state['profile']
    effective = context.artifacts['project_profile']
    profile_matches = (payload.get('profile_hash') == content_hash(frozen)
                       and content_hash({k:v for k,v in effective.items() if k != 'feedback_rules'})
                       == content_hash({k:v for k,v in frozen.items() if k != 'feedback_rules'}))
    if (not receipt_path.is_file() or json.loads(receipt_path.read_text('utf-8')) != {**payload, 'digest': digest}
            or content_hash(payload) != digest or payload.get('version') != VERSION
            or payload.get('compiler_version') != COMPILER_VERSION or payload.get('job_id') != context.job_id
            or payload.get('scene_id') != scene_id or payload.get('parent_digest') != parent_digest
            or payload.get('parent_version') != parent['version'] or payload.get('parent_asset_id') != asset.id
            or payload.get('parent_stage') != parent_checkpoint['stage']
            or payload.get('parent_stage_files') != json.loads(parent_checkpoint['files_json'])
            or payload.get('parent_image_sha256') != file_hash(image_path)
            or payload.get('parent_evidence_sha256') != file_hash(evidence_path)
            or payload.get('local_lane_digest') != lane.get('digest') or lane.get('provider') != 'comfyui-local'
            or payload.get('request_hash') != content_hash(context.artifacts['production_order'])
            or not profile_matches or payload.get('script_hash') != content_hash(context.artifacts['script'])
            or payload.get('need_hash') != content_hash(need)
            or payload.get('workflow_sha256') != content_hash(payload.get('workflow'))
            or payload.get('max_attempts') != 1 or payload.get('user_approved') is not False
            or payload.get('source') != 'Atnos autonomous single refinement after intact pixel-proven rejection; parent retained.'):
        raise ProductionBlocked('Flux refinement authorization integrity failed')
    return {**payload, 'digest': digest}


def verify_flux_refinement_asset(asset, receipt):
    """Apply the original Flux evidence checks to this distinct refinement id."""
    flux.verify_flux_asset(asset, receipt)
    evidence = json.loads(Path(asset.generation.evidence_path).read_text('utf-8'))
    fingerprint = evidence.get('request_fingerprint')
    if (asset.generation is None or asset.generation.workflow_id != flux.VERSION
            or asset.generation.source_workflow != flux.VERSION
            or asset.generation.model != flux.MODEL_NAMES['unet'][1]
            or not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint)
            or asset.generation.fingerprint != fingerprint
            or asset.request_fingerprint != fingerprint
            or asset.generation.prompt != receipt.get('workflow', {}).get('4', {}).get('inputs', {}).get('text')
            or receipt.get('user_approved') is not False):
        raise ProductionBlocked('Flux refinement candidate provenance is invalid')


def completed_refinement_candidate_evidence(store, state) -> dict | None:
    """Prove an already completed local refinement is safe for normal resume.

    This is deliberately read-only: it validates the blocked execution record,
    completed checkpoint, cached asset and both immutable receipts. It never
    reserves a stage or dispatches a worker/provider request.
    """
    try:
        raw_error = state.get('error')
        error = json.loads(raw_error) if isinstance(raw_error, str) else raw_error
        if (state.get('status') != 'blocked' or state.get('current_step') != 'image_generation'
                or state.get('worker_live') is not False or state.get('can_resume') is not True
                or not isinstance(error, dict) or error.get('category') != 'execution_failure'
                or error.get('message') != 'Flux refinement candidate provenance is invalid'):
            return None

        checkpoints = state.get('checkpoints')
        if not isinstance(checkpoints, list):
            return None
        context = store.context(state['id'])
        matches = []
        for row in checkpoints:
            if not isinstance(row, dict):
                continue
            parts = str(row.get('stage', '')).split('/')
            if len(parts) != 5 or parts[:3] != ['assets', 'image_local', 'flux2-trial']:
                continue
            receipt_path = context.artifact_dir / 'flux_refinements' / parts[3] / parts[4] / 'authorization.json'
            if receipt_path.is_file():
                matches.append(row)
        if len(matches) != 1:
            return None
        checkpoint = matches[0]
        if checkpoint.get('status') != 'done' or checkpoint.get('attempts') != 1:
            return None

        stage_parts = checkpoint['stage'].split('/')
        if len(stage_parts) != 5 or stage_parts[0:3] != ['assets', 'image_local', 'flux2-trial']:
            return None
        scene_id, stage_digest = stage_parts[3:]
        if not re.fullmatch(r'[A-Za-z0-9_-]+', scene_id) or not re.fullmatch(r'[0-9a-f]{16}', stage_digest):
            return None

        output = checkpoint.get('output')
        if (not isinstance(output, dict) or output.get('trial_digest') is None
                or not re.fullmatch(r'[0-9a-f]{64}', str(output.get('trial_digest')))
                or output['trial_digest'][:16] != stage_digest
                or not re.fullmatch(r'[0-9a-f]{64}', str(output.get('request_fingerprint', '')))
                or not output.get('asset_id') or not output.get('output_path')):
            return None
        if not store._valid_files(context.artifact_dir, json_dumps(checkpoint.get('files', []))):
            return None
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
        from atnos.production.wb_image_api import load_image_lane
        brain = WealthBlueprintAssetBrain(store.settings)
        asset = next((item for item in brain.catalog.assets if item.id == output['asset_id']), None)
        if (asset is None or asset.source != 'comfyui-flux-trial'
                or Path(asset.file_path).resolve() != Path(output['output_path']).resolve()
                or asset.request_fingerprint != output['request_fingerprint'] or asset.generation is None):
            return None
        evidence_path = Path(asset.generation.evidence_path or '')
        if not evidence_path.is_file():
            return None
        evidence = json.loads(evidence_path.read_text('utf-8'))
        if (evidence.get('trial_digest') != output['trial_digest']
                or evidence.get('request_fingerprint') != asset.request_fingerprint
                or asset.generation.fingerprint != asset.request_fingerprint):
            return None
        lane = load_image_lane(store.settings, state['id'])
        if not lane or lane.get('provider') != 'comfyui-local':
            return None
        state, context, need = flux._scene_state(store, state['id'], scene_id)
        parent = flux.load_flux_trial(store.settings, context, scene_id, need, lane)
        if parent is None:
            return None
        receipt = load_flux_refinement(store.settings, context, scene_id, need, lane, parent['digest'])
        if (receipt is None or receipt['digest'] != output['trial_digest']
                or receipt.get('user_approved') is not False):
            return None
        verify_flux_refinement_asset(asset, receipt)
        return {'job_id': state['id'], 'scene_id': scene_id, 'stage': checkpoint['stage'],
                'trial_digest': receipt['digest'], 'asset_id': asset.id,
                'request_fingerprint': asset.request_fingerprint,
                'checkpoint_updated_at': checkpoint.get('updated_at')}
    except (KeyError, TypeError, ValueError, OSError, ProductionBlocked, OrderConflict, json.JSONDecodeError):
        return None
