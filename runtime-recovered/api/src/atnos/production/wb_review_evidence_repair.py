"""Bounded critic correction when known pixels were omitted from PASS citations.

Never regenerates the image, converts uncertainty into approval, or repairs an
unknown observation. The original review and model answers remain inspectable.
"""
import asyncio
import json
from pathlib import Path

from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production import wb_image_review as review

VERSION = 'wb-critic-evidence-repair-v1'
CONSUMED_MESSAGE = 'One-shot Flux trial already consumed; inspect its evidence, no second POST'


def candidate_evidence(context, brain, asset, need, style):
    """Pure proof of incomplete citations, rather than missing pixel evidence."""
    prefix = 'atnos_automatic_visual_qc:'
    try:
        if asset.quality_status != 'REJECTED' or not str(asset.reviewed_by or '').startswith(prefix):
            return None
        key = asset.reviewed_by[len(prefix):]
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            return None
        directory = context.artifact_dir / 'assets' / 'automatic-review' / key
        result = json.loads((directory / 'review.json').read_text('utf-8'))
        identity = result['identity']
        if (result['asset_id'] != asset.id or identity.get('contract') != 2
                or content_hash(identity) != key or identity['need'] != need or identity['style'] != style
                or identity['image_sha256'] != file_hash(brain.resolve_file(asset))
                or result['user_approved'] is not False or not result['evidence_files']):
            return None
        for name, digest in result['evidence_files'].items():
            if Path(name).name != name or file_hash(directory / name) != digest:
                return None
        review._verify_grounded_result(directory, result)
        sources = result['grounding_sources']
        if not sources['comparison'].startswith('comparison-') or sources['comparison'].startswith('comparison-evidence'):
            return None  # One correction epoch, with bounded distinct format attempts.
        observation = json.loads((directory / sources['observation']).read_text('utf-8'))
        raw = review.GroundedWBIllustrationChecks.model_validate_json(
            (directory / sources['comparison']).read_text('utf-8')).model_dump(mode='json')
        if any(item['status'] != 'passed' for item in raw.values()):
            return None  # Truthful negative and genuinely uncertain judgments are retained.
        missing = {}
        for category, item in raw.items():
            fields = review._PASS_FIELDS[category] - set(item['observation_fields'])
            if fields:
                if any(not review._field_is_established(field, observation[field]) for field in review._PASS_FIELDS[category]):
                    return None
                missing[category] = sorted(fields)
        if not missing or review.verdict(result['checks'])['passed']:
            return None
        trace_name = sources['comparison'].removesuffix('-validated.json')
        trace = json.loads((directory / (trace_name + '.json')).read_text('utf-8'))
        checks = review.revalidate_cached_comparison(trace, observation, need=need, style=style)
        if checks != result['checks'] or trace['inputs']['model_revision'] != identity['model_revision']:
            return None
        if json.loads(trace['response']['message']['content']) != raw:
            return None
        return {'version': VERSION, 'review_identity': key, 'asset_id': asset.id,
                'review_sha256': file_hash(directory / 'review.json'), 'missing_citations': missing,
                'image_sha256': identity['image_sha256'], 'new_image_requests': 0,
                'approval_written': False}
    except (OSError, ValueError, KeyError, TypeError, ProductionBlocked):
        return None


def _complete_citations(payload, observation):
    review.validate_grounding_format(payload, observation)
    for category, item in payload.items():
        missing = review._PASS_FIELDS[category] - set(item['observation_fields'])
        if item['status'] == 'passed' and missing:
            raise ValueError(category + ': PASS omits required observation_fields: ' + ', '.join(sorted(missing)))


async def repair_incomplete_review(executor, asset_id, need, style, lane):
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.asset_brain.approval import AssetApprovalService
    from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
    from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
    from atnos.production.asset_observation_cache import model_revision
    settings, context = executor.store.settings, executor.context
    brain = WealthBlueprintAssetBrain(settings)
    asset = next((item for item in brain.catalog.assets if item.id == asset_id), None)
    proof = candidate_evidence(context, brain, asset, need, style) if asset else None
    if not proof:
        raise ProductionBlocked('Incomplete critic correction has no intact eligible evidence')
    directory = context.artifact_dir / 'assets' / 'automatic-review' / proof['review_identity']
    original = json.loads((directory / 'review.json').read_text('utf-8'))
    identity = original['identity']
    if (file_hash(Path(lane['reference_path'])) != identity['reference_sha256']
            or lane['reference_sha256'] != identity['reference_sha256']):
        raise ProductionBlocked('WB reference changed before critic evidence correction')
    observation = json.loads((directory / original['grounding_sources']['observation']).read_text('utf-8'))
    comparison = original['grounding_sources']['comparison'].removesuffix('-validated.json')
    trace = json.loads((directory / (comparison + '.json')).read_text('utf-8'))
    prompt = (trace['inputs']['prompt'] + '\nComplete the assessment of these same observed pixels. '
        'A previous PASS omitted required evidence dimensions; that answer cannot qualify the image. '
        'For every PASS, cite ALL required observation_fields below and assess each dimension. '
        'Preserve negative facts. If the observation explicitly describes photorealistic rendering while '
        'the chosen style requires hand-drawn or painted illustration and forbids photorealism, mark '
        'style_palette_and_lighting failed and cite drawing_medium_and_linework. Unknown medium remains '
        'uncertain. Never infer favorable linework from the requested style. Do not change the observation '
        'or require new objects. Required PASS citations: ' + json.dumps(
            {name: sorted(fields) for name, fields in review._PASS_FIELDS.items()}))
    lock = GpuRuntimeLockService(settings)
    lease = await asyncio.to_thread(lock.acquire, owner=f'production-order:{context.job_id}', timeout_s=600)
    try:
        await asyncio.to_thread(ProductionGpuResidencyService(settings).prepare_for_llm)
        revision = await asyncio.to_thread(model_revision, settings, identity['model_revision']['name'])
        if revision != identity['model_revision']:
            raise ProductionBlocked('Cached critic evidence model identity changed')
        raw = await review.infer_review(executor, directory, proof['review_identity'], identity,
            revision, 'comparison-evidence', prompt, review.GroundedWBIllustrationChecks,
            payload_validator=lambda value: _complete_citations(value, observation))
        archive = directory / 'review-before-evidence-repair.json'
        if archive.is_file() and json.loads(archive.read_text('utf-8')) != original:
            raise ProductionBlocked('Original critic review archive changed')
        if not archive.is_file():
            write_json(archive, original)
        if file_hash(brain.resolve_file(asset)) != proof['image_sha256']:
            raise ProductionBlocked('Image changed during critic evidence correction')
        result = {**review.verdict(review.grounded_checks(raw, observation)), 'identity': identity,
            'asset_id': asset_id, 'grounding_sources': {
                'observation': original['grounding_sources']['observation'],
                'comparison': review._validated_source(directory, 'comparison-evidence', review.GroundedWBIllustrationChecks)},
            'evidence_repair': {**proof, 'original_review_file': archive.name},
            'evidence_files': {p.name: file_hash(p) for p in directory.glob('*.json') if p.name != 'review.json'},
            'scope': original['scope']}
        write_json(directory / 'review.json', result)
        approval = AssetApprovalService(brain)
        if result['passed']:
            approval.approve(asset_id, reviewed_by='atnos_automatic_visual_qc:' + proof['review_identity'])
        else:
            approval.reject(asset_id, 'LOW_QUALITY', notes=json.dumps(result['checks'])[:500],
                            reviewed_by='atnos_automatic_visual_qc:' + proof['review_identity'])
        return result
    finally:
        await asyncio.to_thread(lock.release, lease)


def incomplete_review_recovery_evidence(store, state):
    """Authorize normal monitor dispatch only for this resolved, known QC gap."""
    try:
        error = json.loads(state.get('error') or '{}')
        if (state.get('status') != 'blocked' or state.get('worker_live') or not state.get('can_resume')
                or state.get('current_step') != 'image_generation'
                or error != {'category': 'execution_failure', 'message': CONSUMED_MESSAGE}):
            return None
        from atnos.production.wb_image_api import load_image_lane, image_selection_plan, _preflight_image_plan
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
        from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
        context = store.context(state['id'])
        required = ['script', 'script_calculations'] if (state['request'].get('intake') or {}).get('kind') == 'user_hypothetical' else ['script']
        for name in required:
            row = next((row for row in state['checkpoints'] if row['stage'] == name and row['status'] == 'done'), None)
            if not row or not store._valid_files(context.artifact_dir, row['files_json']):
                return None
            context.artifacts[name] = row['output']
        lane = load_image_lane(store.settings, state['id'])
        if not lane or lane['provider'] != 'comfyui-local':
            return None
        style = context.artifacts['project_profile']['visual_style']
        plan = image_selection_plan(context)
        brain = WealthBlueprintAssetBrain(store.settings)
        for scene, (native, need) in zip(plan['scenes'], _preflight_image_plan(plan)):
            if native:
                continue
            tags = set(style_asset_tags(style, state['id'], scene['scene_id']))
            for asset in brain.catalog.assets:
                if asset.type != 'background' or not tags.issubset(asset.tags):
                    continue
                proof = candidate_evidence(context, brain, asset, need, style)
                if not proof:
                    continue
                checkpoints = [row for row in state['checkpoints'] if isinstance(row.get('output'), dict)
                    and row['output'].get('asset_id') == asset.id and row['stage'].startswith('assets/image_local/')]
                if (len(checkpoints) != 1 or checkpoints[0]['status'] != 'done'
                        or not store._valid_files(context.artifact_dir, checkpoints[0]['files_json'])):
                    return None
                prefix = 'assets/image_review/' + proof['review_identity'][:16] + '/'
                for name in ('observation', 'reference', 'comparison'):
                    row = next((row for row in state['checkpoints'] if row['stage'] == prefix + name and row['status'] == 'done'), None)
                    if not row or not store._valid_files(context.artifact_dir, row['files_json']):
                        return None
                return {**proof, 'job_id': state['id'], 'scene_id': scene['scene_id'],
                        'image_checkpoint': checkpoints[0]['stage']}
        return None
    except (OSError, ValueError, KeyError, TypeError, ProductionBlocked):
        return None
