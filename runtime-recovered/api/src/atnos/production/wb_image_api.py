"""Explicitly authorized WB image API lane, using normal orders and Asset Brain.

The authorization is separate from the immutable order snapshot. Each HTTP
request has a durable paid stage; candidates never approve themselves.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import shutil

import httpx
from PIL import Image

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.models import StepResult
from atnos.production.local_image_dispatch_budget import LocalImageDispatchBlocked
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json

LANE_VERSION = 'wb-image-api-v1'
MODEL = 'gpt-image-1.5'
# Conservative reservation including prompt and up to two reference images;
# this is NOT an invoice. Provider usage is retained separately.
RESERVATION_USD = 1.0
LOCAL_COMPOSITION_VERSION = 'wb-image-local-v2-composition'

# Ask the existing semantic planner for its literal physical subject. These
# are supported directing intents, not paths or substitute production assets.
_COMPOSITION_INTENTS = {
    'bill_and_bag': 'One plain unprinted bill beside one small shopping bag.',
    'bill_and_account_ui': 'One plain unprinted bill beside a phone account screen.',
    'coffee_reassurance': 'One ordinary coffee cup stays upright and uncrossed; no red X.',
    'balance_scale': 'One plain balance scale with two empty unmarked pans.',
    'car_repair_garage': 'One still parked car inside a quiet garage, without people.',
}


def _composition_subject(function: str) -> str:
    from atnos.production.channels.wealth_blueprint.deterministic import _source_physical_illustration_for
    intent = _COMPOSITION_INTENTS.get(function)
    spec = _source_physical_illustration_for([intent]) if intent else None
    if not spec or spec['function'] != function:
        raise ValueError('Unsupported WB composition illustration function')
    return spec['subject']


def _composition_bindings(raw: list[dict] | None) -> list[dict]:
    """Validate actual layout-only reference/provenance files without generating.

    Pixel layout approval is not approval of finished art. Persist both hashes;
    load/use rechecks them rather than relying on an approved boolean.
    """
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > len(_COMPOSITION_INTENTS):
        raise ValueError('Composition references require a bounded list of physical functions')
    result, seen = [], set()
    allowed = {'illustration_function', 'asset_subject', 'reference_path', 'reference_sha256',
               'approval_receipt_path', 'approval_receipt_sha256', 'purpose'}
    for value in raw:
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError('Invalid composition reference binding fields')
        function = str(value.get('illustration_function') or '')
        expected_subject = _composition_subject(function)
        if function in seen or value.get('asset_subject') != expected_subject:
            raise ValueError('Composition reference function/subject mismatch or duplicate')
        if value.get('purpose', 'layout_only') != 'layout_only':
            raise ValueError('Composition reference is layout_only, never production approval')
        reference = Path(value.get('reference_path') or '').resolve()
        approval = Path(value.get('approval_receipt_path') or '').resolve()
        if (reference.drive.casefold() != 'e:' or approval.drive.casefold() != 'e:'
                or not reference.is_file() or not approval.is_file()):
            raise ValueError('Composition reference and approval receipt must be existing files on E:')
        reference_hash, approval_hash = file_hash(reference), file_hash(approval)
        if ((value.get('reference_sha256') is not None and value['reference_sha256'] != reference_hash)
                or (value.get('approval_receipt_sha256') is not None
                    and value['approval_receipt_sha256'] != approval_hash)):
            raise ValueError('Composition reference or approval receipt hash changed')
        with Image.open(reference) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'}:
                raise ValueError('Unsupported composition reference image')
            image.verify()
        proof = json.loads(approval.read_text('utf-8'))
        if (not isinstance(proof, dict) or proof.get('status') != 'USER_ACCEPTED_COMPOSITION_REFERENCE'
                or Path(str(proof.get('path') or '')).resolve() != reference
                or proof.get('sha256') != reference_hash
                or not str(proof.get('user_answer') or '').strip()
                or 'layout only' not in str(proof.get('approval_scope') or '').casefold()):
            raise ValueError('Composition reference lacks matching user layout approval provenance')
        if proof.get('illustration_function'):
            if proof['illustration_function'] != function or proof.get('asset_subject') != expected_subject:
                raise ValueError('Composition approval provenance function/subject mismatch')
        else:
            # Existing card+bag receipt predates typed subject metadata. Its
            # recorded inspection is narrowly accepted for that layout alone.
            description = str(proof.get('visual_review') or '').casefold()
            if function != 'bill_and_bag' or 'card' not in description or 'bag' not in description:
                raise ValueError('Legacy composition approval only identifies the card-and-bag layout')
        result.append({'illustration_function': function, 'asset_subject': expected_subject,
            'reference_path': str(reference), 'reference_sha256': reference_hash,
            'approval_receipt_path': str(approval), 'approval_receipt_sha256': approval_hash,
            'purpose': 'layout_only'})
        seen.add(function)
    return sorted(result, key=lambda value: value['illustration_function'])


def _apply_local_composition_bindings(scene_needs, lane):
    """Join authorized references after semantic planning, before any acquisition."""
    raw = lane.get('composition_references')
    if raw is None:
        return scene_needs  # Historical lanes retain their exact behavior/digest.
    if lane.get('provider') != 'comfyui-local':
        raise ProductionBlocked('Composition reference binding requires the local ComfyUI lane')
    try:
        bindings = _composition_bindings(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise ProductionBlocked('Composition reference binding integrity failed: ' + str(exc)) from exc
    by_function = {value['illustration_function']: value for value in bindings}
    resolved, used = [], set()
    for native, need in scene_needs:
        if native or not need:
            resolved.append((native, need))
            continue
        binding = by_function.get(str(need.get('illustration_function') or ''))
        if not binding:
            resolved.append((native, need))
            continue
        if need.get('type') != 'background' or need.get('asset_subject') != binding['asset_subject']:
            raise ProductionBlocked('Semantic scene differs from the authorized composition function/subject')
        if (need.get('composition_reference_path') is not None
                and Path(need['composition_reference_path']).resolve() != Path(binding['reference_path'])):
            raise ProductionBlocked('Semantic scene has a conflicting composition reference')
        resolved.append((native, {**need, 'composition_reference_path': binding['reference_path'],
            'composition_reference_sha256': binding['reference_sha256'],
            'composition_reference_purpose': 'layout_only',
            'composition_approval_sha256': binding['approval_receipt_sha256']}))
        used.add(binding['illustration_function'])
    if used != set(by_function):
        raise ProductionBlocked('Registered composition reference has no matching physical scene in the semantic plan')
    return resolved


def register_local_image_dispatch_action(settings, job_id: str, *, action_key: str,
                                          max_new_requests: int = 3, authorized: bool = False,
                                          reason: str = "", conditioning_amendment_digest: str | None = None) -> dict:
    """Register a bounded local action, without starting Comfy or changing limits."""
    from atnos.production.local_image_dispatch_budget import register_local_image_action
    return register_local_image_action(settings, job_id, action_key=action_key,
        max_new_requests=max_new_requests, authorized=authorized, reason=reason,
        conditioning_amendment_digest=conditioning_amendment_digest)


def register_local_composition_amendment(settings,job_id: str,**kwargs) -> dict:
    """Register layout/seed only; a separate bounded action still controls POST."""
    from atnos.production.wb_image_conditioning import register_conditioning_amendment
    return register_conditioning_amendment(settings,job_id,**kwargs)


def register_image_lane(settings, job_id: str, *, reference_path: Path, authorized: bool = False) -> dict:
    if not authorized:
        raise ValueError('Explicit image API authorization is required')
    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    context = store.context(job_id)
    style = context.artifacts['project_profile'].get('visual_style') or {}
    if (state['worker_live'] or state['status'] not in {'queued', 'blocked'}
            or state['request'].get('project') != 'wealth_blueprint'
            or state['request'].get('delivery_mode') != 'local_only'
            or style.get('id') not in {'A', 'B', 'C', 'D'}):
        raise OrderConflict('Image lane requires a stopped local WB style trial')
    # No already-created media can silently change provider underneath it.
    if any(row['status'] == 'done' and row['stage'].split('/')[0] in {'voice', 'edit', 'remotion_render', 'package'}
           for row in state['checkpoints']):
        raise OrderConflict('Image lane must be selected before completed audiovisual media')
    reference_path = reference_path.resolve()
    if reference_path.drive.casefold() != 'e:' or not reference_path.is_file():
        raise ValueError('Reference must be an existing file on E:')
    with Image.open(reference_path) as source:
        if source.format not in {'PNG', 'JPEG', 'WEBP'}:
            raise ValueError('Unsupported image reference')
        source.verify()
    payload = {
        'version': LANE_VERSION, 'job_id': job_id, 'provider': 'openai-cloud', 'model': MODEL,
        'size': '1024x1536', 'quality': 'high', 'reservation_usd_per_request': RESERVATION_USD,
        'max_requests': 20, 'reference_path': str(reference_path), 'reference_sha256': file_hash(reference_path),
        'request_hash': content_hash(state['request']),
        'profile_hash': content_hash(context.artifacts['project_profile']),
        'source': 'User explicitly chose the existing OpenAI key in the Atnos vault.',
        'appearance_approval': 'pending',
    }
    digest = content_hash(payload)
    with connect(settings) as db:
        db.execute('CREATE TABLE IF NOT EXISTS production_image_lane_authorizations '
                   '(job_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL)')
        existing = db.execute('SELECT digest FROM production_image_lane_authorizations WHERE job_id=?', (job_id,)).fetchone()
        if existing:
            if existing['digest'] != digest:
                raise OrderConflict('Image API authorization is immutable; existing lane differs')
            return load_image_lane(settings, job_id)
        receipt = write_json(context.artifact_dir / 'image_api_authorization.json', {**payload, 'digest': digest})
        db.execute('INSERT INTO production_image_lane_authorizations VALUES (?,?,?,?)',
                   (job_id, json_dumps(payload), digest, utc_now_iso()))
        db.commit()
    return {**payload, 'digest': digest, 'receipt': str(receipt.artifact_path)}


def load_image_lane(settings, job_id: str) -> dict | None:
    with connect(settings) as db:
        try:
            row = db.execute('SELECT * FROM production_image_lane_authorizations WHERE job_id=?', (job_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' in str(exc):
                return None
            raise
    if row is None:
        return None
    payload = json.loads(row['payload_json'])
    store = ProductionOrderStore(settings)
    context = store.context(job_id)
    receipt_name = ('image_local_authorization.json'
                    if payload.get('provider') == 'comfyui-local' else 'image_api_authorization.json')
    receipt = context.artifact_dir / receipt_name
    reference = Path(payload['reference_path'])
    if (content_hash(payload) != row['digest'] or not receipt.is_file()
            or json.loads(receipt.read_text('utf-8')) != {**payload, 'digest': row['digest']}
            or payload['request_hash'] != content_hash(store.get(job_id)['request'])
            or payload['profile_hash'] != content_hash(context.artifacts['project_profile'])
            or not reference.is_file() or file_hash(reference) != payload['reference_sha256']):
        raise ProductionBlocked('Image API authorization/reference integrity failed')
    with connect(settings) as db:
        try:
            override = db.execute('SELECT payload_json,digest FROM production_image_lane_overrides WHERE job_id=?', (job_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise
            override = None
    if override:
        if 'composition_references' in payload:
            raise ProductionBlocked('Direct local composition lane cannot have a provider override')
        replacement = json.loads(override['payload_json'])
        replacement_receipt = context.artifact_dir / 'image_local_authorization.json'
        if (content_hash(replacement) != override['digest'] or replacement.get('supersedes_digest') != row['digest']
                or not replacement_receipt.is_file()
                or json.loads(replacement_receipt.read_text('utf-8')) != {**replacement, 'digest': override['digest']}):
            raise ProductionBlocked('Local image authorization integrity failed')
        return {**payload, **replacement, 'digest': override['digest']}
    if 'composition_references' in payload:
        try:
            if (payload.get('provider') != 'comfyui-local'
                    or payload.get('version') != LOCAL_COMPOSITION_VERSION
                    or not payload['composition_references']
                    or _composition_bindings(payload['composition_references']) != payload['composition_references']):
                raise ValueError('Composition payload is not canonical local layout-only binding')
        except (OSError, ValueError, TypeError) as exc:
            raise ProductionBlocked('Local composition reference integrity failed: ' + str(exc)) from exc
    return {**payload, 'digest': row['digest']}


def register_local_image_lane(settings, job_id: str, *, reference_path: Path | None = None,
                              authorized: bool = False,
                              composition_references: list[dict] | None = None) -> dict:
    if not authorized:
        raise ValueError('Explicit local image provider choice is required')
    from atnos.production.dispatch_lock import dispatch_lock
    store = ProductionOrderStore(settings)
    with dispatch_lock(store.context(job_id).artifact_dir / 'worker_logs/dispatch.lock'):
        return _register_local_image_lane(settings, job_id, reference_path=reference_path,
            authorized=authorized, composition_references=composition_references)


def _register_local_image_lane(settings, job_id: str, *, reference_path: Path | None = None,
                               authorized: bool = False,
                               composition_references: list[dict] | None = None) -> dict:
    if not authorized:
        raise ValueError('Explicit local image provider choice is required')
    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    if state['worker_live'] or state['status'] not in {'queued', 'blocked'}:
        raise OrderConflict('Provider selection requires a stopped order')
    if any(row['status'] in {'running', 'done'} and row['stage'].split('/')[0] in
           {'voice', 'audio', 'captions', 'edit', 'render', 'remotion_render', 'mux', 'package'}
           for row in state['checkpoints']):
        raise OrderConflict('Local image lane must be selected before active or completed audiovisual media')
    bindings = _composition_bindings(composition_references)
    lane = load_image_lane(settings, job_id)
    if not lane:
        if reference_path is None:
            raise ValueError('A reference image is required for direct local ComfyUI selection')
        context = store.context(job_id)
        style = context.artifacts['project_profile'].get('visual_style') or {}
        if (state['request'].get('project') != 'wealth_blueprint'
                or state['request'].get('delivery_mode') != 'local_only'
                or style.get('id') not in {'A', 'B', 'C', 'D'}):
            raise OrderConflict('Local image lane requires a stopped local WB style trial')
        if any(row['status'] == 'done' and row['stage'].split('/')[0] in
               {'voice', 'edit', 'render', 'remotion_render', 'package'} for row in state['checkpoints']):
            raise OrderConflict('Local image lane must be selected before completed audiovisual media')
        reference_path = Path(reference_path).resolve()
        if reference_path.drive.casefold() != 'e:' or not reference_path.is_file():
            raise ValueError('Reference must be an existing file on E:')
        with Image.open(reference_path) as source:
            if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                raise ValueError('Unsupported image reference')
            source.verify()
        payload = {
            'version': LOCAL_COMPOSITION_VERSION if bindings else 'wb-image-local-v1',
            'job_id': job_id, 'provider': 'comfyui-local',
            'model': 'configured WB QUALITY workflow', 'reservation_usd_per_request': 0.0,
            'max_requests': 20, 'reference_path': str(reference_path),
            'reference_sha256': file_hash(reference_path),
            'request_hash': content_hash(state['request']),
            'profile_hash': content_hash(context.artifacts['project_profile']),
            'source': 'User explicitly selected local ComfyUI in Atnos with this reference image.',
            'appearance_approval': 'pending',
        }
        if bindings:
            payload['composition_references'] = bindings
        digest = content_hash(payload)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            from atnos.production.order_store import _same_process
            current = db.execute('SELECT j.status,o.lease_pid,o.lease_process_started,o.request_json,o.profile_json '
                'FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?', (job_id,)).fetchone()
            media = db.execute("SELECT stage,status FROM pipeline_stage_checkpoints WHERE job_id=? "
                "AND status IN ('running','done')", (job_id,)).fetchall()
            if (not current or current['status'] not in {'queued','blocked'}
                    or _same_process(current['lease_pid'], current['lease_process_started'])
                    or any(row['stage'].split('/')[0] in
                           {'voice','audio','captions','edit','render','remotion_render','mux','package'} for row in media)
                    or content_hash(json.loads(current['request_json'])) != payload['request_hash']
                    or content_hash(json.loads(current['profile_json'])) != payload['profile_hash']):
                raise OrderConflict('Local image lane registration raced with active work or a changed order')
            db.execute('CREATE TABLE IF NOT EXISTS production_image_lane_authorizations '
                       '(job_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL)')
            existing = db.execute('SELECT digest FROM production_image_lane_authorizations WHERE job_id=?',
                                  (job_id,)).fetchone()
            if existing:
                if existing['digest'] != digest:
                    raise OrderConflict('Image lane authorization is immutable; existing lane differs')
                return load_image_lane(settings, job_id)
            receipt = write_json(context.artifact_dir / 'image_local_authorization.json',
                                 {**payload, 'digest': digest})
            db.execute('INSERT INTO production_image_lane_authorizations VALUES (?,?,?,?)',
                       (job_id, json_dumps(payload), digest, utc_now_iso()))
            db.commit()
        return {**payload, 'digest': digest, 'receipt': str(receipt.artifact_path)}
    if lane['provider'] == 'comfyui-local':
        if composition_references is not None and lane.get('composition_references', []) != bindings:
            raise OrderConflict('Local image lane is immutable; composition references cannot change or reset it')
        return lane
    if bindings:
        raise OrderConflict('Composition references require a new direct local lane, not a cloud provider override')
    payload = {'provider': 'comfyui-local', 'model': 'configured WB QUALITY workflow',
        'reservation_usd_per_request': 0.0, 'max_requests': 20,
        'supersedes_digest': lane['digest'],
        'source': 'User explicitly chose local ComfyUI in Atnos after the existing cloud authorization.',
        'appearance_approval': 'pending', 'version': 'wb-image-local-v1'}
    digest = content_hash(payload)
    with connect(settings) as db:
        db.execute('CREATE TABLE IF NOT EXISTS production_image_lane_overrides '
                   '(job_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL)')
        write_json(store.context(job_id).artifact_dir / 'image_local_authorization.json', {**payload, 'digest': digest})
        db.execute('INSERT INTO production_image_lane_overrides VALUES(?,?,?,?)',
                   (job_id, json_dumps(payload), digest, utc_now_iso()))
        db.commit()
    return load_image_lane(settings, job_id)


def _image_scene_planner_input(context):
    from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
    from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
    from atnos.production.steps.order_script import NumericClaim

    script = context.artifacts['script']
    request = context.artifacts['production_order']
    style = context.artifacts['project_profile']['visual_style']
    if (not isinstance(script.get('qa'), dict) or script['qa'].get('status') != 'passed'):
        raise ProductionBlocked('Image plan requires the validated source script and its claim ledger')
    try:
        claims = [NumericClaim.model_validate(claim).model_dump(mode='json')
                  for claim in script.get('claims') or []]
    except Exception as exc:
        raise ProductionBlocked(f'Image plan source claim ledger is invalid: {exc}') from exc
    claim_ids = [claim['id'] for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ProductionBlocked('Image plan source claim ledger contains duplicate IDs')

    intake = request.get('intake') if isinstance(request.get('intake'), dict) else {}
    if intake.get('kind') == 'user_hypothetical':
        receipt = context.artifacts.get('script_calculations')
        raw_ledger = receipt.get('claims') if isinstance(receipt, dict) else None
        if not isinstance(raw_ledger, dict):
            raise ProductionBlocked('Hypothetical image plan is missing its validated calculation claim ledger')
        try:
            ledger = {str(key): NumericClaim.model_validate(value).model_dump(mode='json')
                      for key, value in raw_ledger.items()}
        except Exception as exc:
            raise ProductionBlocked(f'Hypothetical calculation claim ledger is invalid: {exc}') from exc
        if ledger != {claim['id']: claim for claim in claims}:
            raise ProductionBlocked('Image plan script claims differ from the validated calculation ledger')

    cursor, segments = 0.0, []
    for item in script['segments']:
        duration = max(3.0, len(item['text'].split()) / 2.5)
        segments.append(NarrationSegment(id=item['id'], text=item['text'], on_screen=item.get('on_screen', ''),
                                         visual_intent=item.get('visual_intent', ''),
                                         start=cursor, end=cursor + duration))
        cursor += duration
    return ScenePlannerInput(
        script=' '.join(s.text for s in segments), segments=segments,
        profile={'channel': 'wealth_blueprint', 'topic': request['topic'],
                 'direction': request.get('direction', ''), 'optional_notes': request.get('optional_notes', ''),
                 'disable_visual_story': True},
        design_system={'visual_style_id': style['id']},
        claims=claims,
        segment_claim_map={s['id']: s.get('claim_ids', []) for s in script['segments']},
    ), claims


def image_selection_plan(context) -> dict:
    """Same semantic planner as the film. Times here are only pre-audio estimates."""
    from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic

    planner_input, claims = _image_scene_planner_input(context)
    plan = plan_scenes_deterministic(planner_input).model_dump(mode='json')
    # ScenePlan intentionally serializes only scenes. Native chart validation
    # still needs the exact validated claim ledger used to derive its series.
    plan['claims'] = claims
    return plan


def _preflight_image_plan(plan: dict) -> list[tuple[bool, dict | None]]:
    """Validate every nonnative still before any provider or asset acquisition call."""
    from atnos.production.channels.wealth_blueprint.scene_production_assets import _needs_for_scene

    scene_needs: list[tuple[bool, dict | None]] = []
    unsupported: list[str] = []
    for index, scene in enumerate(plan['scenes']):
        native_role = _native_scene_for_image_trial(
            scene, index=index, total=len(plan['scenes']), claims=plan.get('claims') or [])
        if native_role:
            scene_needs.append((True, None))
            continue
        needs = _needs_for_scene(scene)
        need = needs[0] if needs else {}
        if not need.get('asset_subject') or need.get('type') != 'background':
            scene_id = str(scene.get('scene_id') or scene.get('segment_id') or index)
            need_type = str(need.get('type') or 'missing')
            unsupported.append(f'{scene_id} (asset type: {need_type})')
        scene_needs.append((False, need))
    if unsupported:
        raise ProductionBlocked(
            'IMAGE_PLAN_UNSUPPORTED: Image plan requires a supported physical illustration for every scene; '
            'no image was generated. Unsupported scenes: ' + ', '.join(unsupported)
            + '. Choose an existing native Remotion composition or provide a supported physical scene before asset acquisition.'
        )
    return scene_needs


def preflight_image_plan_for_order(context, lane: dict) -> dict:
    """Validate the deterministic image plan immediately after script QA.

    This runs before library observation, semantic ranking, and scene-plan model
    calls. It only checks the current script and renderer/asset contracts.
    """
    plan = image_selection_plan(context)
    scene_needs = _preflight_image_plan(plan)
    from atnos.production.project_profile import content_hash
    context.artifacts['_image_plan_preflight'] = {
        'script_hash': content_hash(context.artifacts.get('script') or {}),
        'profile_hash': content_hash(context.artifacts.get('project_profile') or {}),
        'lane_digest': lane.get('digest'),
        'plan': plan,
        'scene_needs': scene_needs,
    }
    return {'scene_count': len(plan['scenes']), 'status': 'passed'}


def build_image_brief(need: dict, style: dict) -> str:
    subject = str(need.get('asset_subject') or '').strip()
    if not subject:
        raise ProductionBlocked('Image API lane requires an explicit physical subject, not an invented substitute')
    composition = str(need.get('asset_composition') or '').partition(' Avoid:')[0].strip()
    return (
        f"Draw {subject}. {composition}\n"
        f"Art direction: {style['prompt']}\n"
        "Create one complete vertical illustration for an English personal-finance short. "
        "Use the attached Wealth Blueprint board ONLY as a drawing, palette and lighting reference. "
        "The Art direction above controls this variant's rendering medium; the reference must not override it. "
        "If a second image is attached, it is a reviewed prior shot from this episode: preserve its palette and "
        "the design of shared objects, while drawing the NEW requested subject and state. "
        "Do not copy unrelated objects from a previous shot. "
        "Do not reproduce its collage, panels, titles, Alex or desktop scene unless requested. "
        "Preserve the frozen medium while adding cinematic depth: charcoal-navy contours, cream-paper surfaces, restrained warm-gold practical light, soft painted shadows and subtle paper texture. "
        "Keep the requested object set and count exact; no duplicates, piles or additional paper props. "
        "Objects occupy the large central area. Preserve quiet top and lower caption areas. "
        "Keep essential objects inside the central 80% width for a 9:16 frame crop. "
        "This is one shot, not a sheet of panels. No text, letters, numbers, logos or arrows. "
        "No photographic or plastic 3D finish. "
        "The illustration must explain the requested state without suggesting investment gains."
    )


def _generate_candidate(settings, context, need, style, tags, lane, fingerprint, previous_reference=None):
    from atnos.db.session import DatabaseManager
    from atnos.services.account_credentials_service import AccountCredentialsService
    from atnos.services.runtime_settings_service import RuntimeSettingsService
    from atnos.services.creative_asset_operator_service import CreativeAssetOperatorService, CreativeAssetRequest
    from atnos.production.asset_brain.approval import AssetApprovalService
    from atnos.production.asset_brain.models import AssetGenerationProvenance, AssetRecord
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    prompt = build_image_brief(need, style)
    request = CreativeAssetRequest(raw_content=prompt, brief=prompt, graphic_type='illustration',
        needs_text_overlay=False, prefers_generative_art=True, prefers_photorealism=False,
        prefers_external_image_api=True, prefers_browser_image_tool=False)
    references = [Path(lane['reference_path'])]
    if previous_reference:
        references.append(previous_reference)
    database = DatabaseManager(settings)
    with database.session_factory() as session:
        operator = CreativeAssetOperatorService(settings, account_credentials=AccountCredentialsService(session, settings))
        result = operator.generate_image_api_candidate(request=request, model_name=lane['model'], size=lane['size'],
            quality=lane['quality'], reference_paths=tuple(references), allow_external_fallback=True,
            runtime_controls=RuntimeSettingsService(session, settings).snapshot())
    source = Path(result.descriptor.absolute_path)
    target_dir = context.artifact_dir / 'assets' / 'image_api' / fingerprint[:16]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / 'image.png'
    shutil.copyfile(source, target)
    with Image.open(target) as image:
        width, height = image.size
        image.verify()
    evidence = {
        'provider': 'openai-cloud', 'model': result.model_name, 'settings': result.settings,
        'usage': result.usage, 'request_id': result.request_id, 'endpoint': result.endpoint,
        'prompt': result.prompt, 'revised_prompt': result.revised_prompt,
        'references': [{'path': str(p), 'sha256': file_hash(p)} for p in references],
        'image_sha256': file_hash(target), 'width': width, 'height': height,
        'visual_style_id': style['id'], 'visual_style_definition_hash': style['definition_hash'],
        'authorization_digest': lane['digest'], 'request_fingerprint': fingerprint,
        'reservation_usd': lane['reservation_usd_per_request'], 'cost_note': 'Reservation, not provider invoice',
        'appearance_approval': 'pending', 'user_approved': False,
    }
    evidence_file = write_json(target_dir / 'metadata.json', evidence).artifact_path
    asset = AssetRecord(id=f"openai_wb_{style['id'].lower()}_{fingerprint[:16]}", type='background',
        file_path=str(target), channel='wealth_blueprint', production_role='background', width=width, height=height,
        tags=[*tags, *need.get('tags', [])], source='openai-image-api', description=str(need.get('asset_subject')),
        license_status='generated_pending_review', request_fingerprint=fingerprint,
        generation=AssetGenerationProvenance(prompt=result.prompt, workflow_id=LANE_VERSION,
            model=result.model_name, generated_at=utc_now_iso(), source_workflow=LANE_VERSION,
            channel='wealth_blueprint', fingerprint=fingerprint, reference_files=[str(p) for p in references],
            backend='openai', evidence_path=str(evidence_file)))
    record = AssetApprovalService(WealthBlueprintAssetBrain(settings)).ingest_generated(asset)
    payload = {**evidence, 'asset_id': record.id, 'output_path': str(target), 'quality_status': record.quality_status}
    report = write_json(target_dir / 'candidate.json', payload)
    return StepResult(report.artifact_name, report.artifact_path, payload, report.message, (target, evidence_file))


def _local_generator_identity(settings):
    """Changing the compiled prompt or selected checkpoint cannot reuse old generation."""
    from atnos.production.comfy_channels.prompts import WB_PROMPT_COMPILER_VERSION
    from atnos.production.comfy_channels.model_profiles import model_profiles_path
    from atnos.production.comfy_channels.illustration_checkpoint import TRIAL_ENV, receipt_path
    candidate = os.environ.get(TRIAL_ENV, '').strip()
    profiles = model_profiles_path(settings)
    profile = json.loads(profiles.read_text('utf8')).get('channels', {}).get('wealth_blueprint') if profiles.is_file() else None
    receipt = receipt_path(settings) if candidate else None
    return {'prompt_compiler': WB_PROMPT_COMPILER_VERSION, 'model_profile': profile,
            'illustration_candidate': candidate,
            'candidate_receipt_sha256': file_hash(receipt) if receipt and receipt.is_file() else None}


def _verify_local_composition_conditioning(source: Path, reference: Path, *, seed: int | None = None) -> None:
    """A reused still must prove the requested pixels reached the sampler."""
    try:
        evidence = json.loads(source.with_suffix(source.suffix + '.comfyui.json').read_text('utf-8'))
        upload = evidence['reference_upload']
        graph = evidence['workflow']
        if (Path(upload['source_path']).resolve() != reference.resolve()
                or upload['source_sha256'] != file_hash(reference)
                or graph['4']['class_type'] != 'LoadImage'
                or not upload['resolved_image_name']
                or graph['4']['inputs']['image'] != upload['resolved_image_name']
                or graph['5']['class_type'] != 'ImageScale'
                or graph['5']['inputs']['image'] != ['4', 0]
                or graph['6']['class_type'] != 'VAEEncode'
                or graph['6']['inputs']['pixels'] != ['5', 0]
                or graph['7']['class_type'] != 'KSampler'
                or graph['7']['inputs']['latent_image'] != ['6', 0]
                or (seed is not None and graph['7']['inputs'].get('seed') != seed)):
            raise ValueError('Wrong reference hash or disconnected pixel-conditioning graph')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('Composition reference conditioning is unproven; candidate cannot be reused') from exc


def _matches_local_composition_need(brain, asset, need: dict) -> bool:
    """A style/order match alone cannot replace explicit pixel conditioning."""
    reference = need.get('composition_reference_path')
    if not reference:
        return True
    try:
        _verify_local_composition_conditioning(brain.resolve_file(asset), Path(reference).resolve(),
                                              seed=need.get('seed'))
    except RuntimeError:
        return False
    return True


def _generate_local_candidate(settings, context, need, style, tags, lane, fingerprint, previous_reference=None):
    from atnos.production.channels.wealth_blueprint.scene_production_assets import _try_acquisition
    # A brand board or previously rejected result is not a scene composition.
    # Only an explicit scene reference may activate local pixel conditioning.
    reference = need.get('composition_reference_path')
    options = {}
    if reference:
        path = Path(reference).resolve()
        if path.drive.casefold() != 'e:':
            raise RuntimeError('Explicit composition reference must be on E:; no generation was dispatched')
        if not path.is_file():
            raise RuntimeError('Explicit composition reference is missing; no generation was dispatched')
        options['reference_files'] = [str(path)]
    asset, acquisition = _try_acquisition(settings, need, allow_generate=True, visual_style=style, style_tags=tags, **options)
    acquisition = acquisition or {}
    if not acquisition.get('asset_id') or not acquisition.get('output_path'):
        raise RuntimeError('Local illustration acquisition failed: ' + str(acquisition.get('reason', 'no candidate')))
    source = Path(acquisition['output_path'])
    if not source.is_file():
        raise RuntimeError('Local candidate source is missing')
    if reference:
        _verify_local_composition_conditioning(source, path, seed=need.get('seed'))
    target_dir = context.artifact_dir / 'assets' / 'image_local' / fingerprint[:16]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / 'image.png'
    shutil.copyfile(source, target)
    paths = [target]
    for name in ('metadata.json', 'image.png.comfyui.json'):
        evidence = source.parent / name
        if evidence.is_file():
            copied = target_dir / name
            shutil.copyfile(evidence, copied)
            paths.append(copied)
    payload = {'provider': 'comfyui-local', 'asset_id': acquisition['asset_id'], 'output_path': str(source),
        'preview_path': str(target), 'image_sha256': file_hash(source),
        'quality_status': acquisition.get('quality_status', 'GENERATED'),
        'acquisition': acquisition, 'authorization_digest': lane['digest'],
        'visual_style_id': style['id'], 'visual_style_definition_hash': style['definition_hash'],
        'cost_note': 'Local generation; no API reservation', 'appearance_approval': 'pending', 'user_approved': False,
        **({'conditioning_amendment_digest':need['conditioning_amendment_digest']}
           if need.get('conditioning_amendment_digest') else {})}
    report = write_json(target_dir / 'candidate.json', payload)
    return StepResult(report.artifact_name, report.artifact_path, payload, report.message, tuple(paths))


def _native_scene_for_image_trial(scene: dict, *, index: int, total: int, claims: list[dict]) -> str | None:
    """Return the native render role only when the normal Remotion contract can render it."""
    from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type
    from atnos.production.project_manifest import SceneType

    contract = scene.get('meaning_contract') if isinstance(scene.get('meaning_contract'), dict) else {}
    role = str(contract.get('native_scene_role') or '')
    if not role:
        return None
    chart = scene.get('chart') if isinstance(scene.get('chart'), dict) else {}
    claim_ids = [str(value) for value in (chart.get('claim_ids') or []) if str(value)]
    available = {str(row.get('id')) for row in claims if isinstance(row, dict) and row.get('id')}
    values = chart.get('values') or []
    chart_valid = len(values) >= 2 and bool(claim_ids) and set(claim_ids).issubset(available)
    if chart_valid and chart.get('verified_series') is not None:
        try:
            from atnos.production.chart_series import validate_compound_checkpoints
            validate_compound_checkpoints(chart['verified_series'], claims)
        except Exception:
            chart_valid = False
    scene_type = layout_to_scene_type(
        str(scene.get('layout_id') or 'fullscreen_statement'),
        visual_focus=str(scene.get('visual_focus') or 'statement'),
        index=index,
        total=total,
        has_number=bool(scene.get('number_value')),
        has_chart=chart_valid,
    )
    if role == 'chart' and chart_valid and scene_type == SceneType.CHART:
        return role
    if role == 'reserve_readiness' and scene_type == SceneType.HOOK:
        relation = scene.get('visual_intent', {}).get('relation') or {}
        if (contract.get('relation_kind') == 'reserve_readiness'
                and relation.get('kind') == 'reserve_readiness'
                and relation.get('stage') in {'need', 'identify', 'access', 'separate', 'review'}
                and not relation.get('unsupported_gap')
                and contract.get('object_roles') == {item['role']: item['label'] for item in relation.get('objects', [])}):
            return role
    if role == 'end_card' and index == total - 1 and scene_type == SceneType.CTA:
        source_intents = ' '.join(str(value) for value in (scene.get('source_visual_intents') or []))
        narration = str(scene.get('narration_excerpt') or '')
        from atnos.production.channels.wealth_blueprint.deterministic import is_native_logo_closure_intent
        if ('logo' in source_intents.casefold()
                and (any(word in narration.casefold() for word in ('follow', 'subscribe', 'visit', 'join', 'download'))
                     or is_native_logo_closure_intent(scene.get('source_visual_intents') or []))):
            return role
    return None


async def produce_image_candidates(executor):
    # No lifetime local quota; one invocation must still stop after repeated
    # rejection or a critic that makes no progress. Recursive continuation
    # shares this transient state. A later explicit resume starts a new burst.
    key = '_local_image_retry_burst'
    owner = key not in executor.context.artifacts
    if owner:
        executor.context.artifacts[key] = {'requests': {}, 'reviews': {}}
    try:
        return await _produce_image_candidates(executor)
    finally:
        if owner:
            executor.context.artifacts.pop(key, None)


async def _produce_image_candidates(executor):
    settings, context = executor.store.settings, executor.context
    lane = load_image_lane(settings, context.job_id)
    if lane is None:
        return
    from atnos.production.local_image_dispatch_budget import local_image_action_status
    local_action = local_image_action_status(settings, context.job_id) if lane['provider'] == 'comfyui-local' else None
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
    from atnos.production.project_profile import content_hash
    cached_preflight = context.artifacts.get('_image_plan_preflight')
    current_script_hash = content_hash(context.artifacts.get('script') or {})
    current_profile_hash = content_hash(context.artifacts.get('project_profile') or {})
    if (isinstance(cached_preflight, dict)
            and cached_preflight.get('script_hash') == current_script_hash
            and cached_preflight.get('profile_hash') == current_profile_hash
            and cached_preflight.get('lane_digest') == lane.get('digest')):
        plan = cached_preflight['plan']
        scene_needs = cached_preflight['scene_needs']
    else:
        plan = image_selection_plan(context)
        scene_needs = _preflight_image_plan(plan)
    style = context.artifacts['project_profile']['visual_style']
    # Validate the whole still-image plan before paying for or invoking its
    # first image. Native-rendered roles keep the exact existing exemption.
    from atnos.production.wb_image_conditioning import load_conditioning_amendment, apply_conditioning_amendment
    amendment = load_conditioning_amendment(settings,context)
    scene_needs = _apply_local_composition_bindings(
        apply_conditioning_amendment(plan,scene_needs,amendment),lane)
    waiting, previous_reference = [], None
    qualified_assets = []
    for index, scene in enumerate(plan['scenes']):
        native_role, need = scene_needs[index]
        if native_role:
            continue  # Native chart and closing card use the same renderer path as full production.
        assert need is not None
        from atnos.production.wb_local_flux_trial import load_flux_trial, verify_flux_asset
        conditioned = amendment is not None and need.get('conditioning_amendment_digest') == amendment['digest']
        # The amendment loader revalidates the complete historical Flux lineage
        # and its terminal proof. Old receipts are never passed off as authorizing
        # the newly conditioned need, and remain byte-for-byte intact.
        flux_trial = (load_flux_trial(settings, context, scene['scene_id'], need, lane,
                                      allow_failed_fallback=True)
                      if lane['provider'] == 'comfyui-local' and not conditioned else None)
        if flux_trial and need.get('composition_reference_path'):
            raise ProductionBlocked('Existing Flux trial cannot satisfy explicit composition conditioning; '
                                    'reconcile its scoped authorization before dispatch')
        parent_flux_trial = flux_trial
        if parent_flux_trial:
            from atnos.production.wb_flux_refinement import load_flux_refinement
            refinement = load_flux_refinement(settings, context, scene['scene_id'], need, lane,
                                              parent_flux_trial['digest'])
            if refinement:
                flux_trial = refinement
                from atnos.production.wb_flux_style_candidate import load_flux_style_candidate
                styled = load_flux_style_candidate(settings, context, scene['scene_id'], need, lane,
                                                   refinement['digest'])
                if styled:
                    flux_trial = styled
        tags = style_asset_tags(style, context.job_id, scene['scene_id'])
        brain = WealthBlueprintAssetBrain(settings)
        candidates = [a for a in brain.catalog.assets if set(tags).issubset(a.tags)
                      and a.type == 'background' and a.quality_status not in {'REJECTED', 'SUPERSEDED'}
                      and (lane['provider'] != 'comfyui-local' or _matches_local_composition_need(brain, a, need))]
        for candidate in candidates:
            if candidate.source == 'comfyui-flux-trial':
                if flux_trial is None:
                    raise ProductionBlocked('Flux candidate has no intact scoped authorization')
                _verify_scoped_flux_candidate(candidate, flux_trial)
        approved = next((a for a in candidates if a.is_production_selectable() and brain.resolve_file(a).is_file()), None)
        if approved:
            from atnos.production.wb_image_review import verify_qualification
            verify_qualification(context, approved, brain.resolve_file(approved))
            if approved.source == 'openai-image-api':
                evidence = Path(approved.generation.evidence_path) if approved.generation and approved.generation.evidence_path else None
                if (not evidence or not evidence.is_file()
                        or json.loads(evidence.read_text('utf-8')).get('image_sha256') != file_hash(brain.resolve_file(approved))):
                    raise ProductionBlocked('Reviewed image changed after generation; no silent production reuse')
            # A reviewed prior shot is a continuity reference, never an unreviewed one.
            previous_reference = brain.resolve_file(approved)
            reviewer = str(approved.reviewed_by or '')
            review = (context.artifact_dir / 'assets/automatic-review' / reviewer.split(':',1)[1] / 'review.json'
                      if reviewer.startswith('atnos_automatic_visual_qc:') else None)
            qualified_assets.append({'scene_id':scene['scene_id'], 'asset_id':approved.id,
                'image_sha256':file_hash(previous_reference), 'quality_status':approved.quality_status,
                'reviewed_by':reviewer, 'qualification_sha256':file_hash(review) if review and review.is_file() else None})
            continue
        burst = context.artifacts['_local_image_retry_burst']
        unlimited_local = settings.atnos_local_work_unlimited and lane['provider'] == 'comfyui-local'
        if unlimited_local:
            scene_id = scene['scene_id']
            reviews = burst['reviews'].get(scene_id, 0) + 1
            burst['reviews'][scene_id] = reviews
            if reviews > 6:
                raise ProductionBlocked('local_image_retry_paused: no accepted review progress; inspect the recorded candidates before resuming')
        # Incomplete PASS citations must correct the critic's evidence before
        # choosing another backend. Unknown pixels and truthful failures do not
        # qualify for this bounded correction; no image POST is replayed.
        from atnos.production.order_autonomy import policy
        autonomy = policy(settings, context.job_id)
        if lane['provider'] == 'comfyui-local' and autonomy and autonomy['enabled']:
            from atnos.production.wb_review_evidence_repair import candidate_evidence, repair_incomplete_review
            incomplete = next((a for a in brain.catalog.assets if a.type == 'background'
                and set(tags).issubset(a.tags) and _matches_local_composition_need(brain, a, need)
                and candidate_evidence(context, brain, a, need, style)), None)
            if incomplete:
                if incomplete.source == 'comfyui-flux-trial':
                    if not flux_trial:
                        raise ProductionBlocked('Flux candidate has no intact scoped authorization')
                    _verify_scoped_flux_candidate(incomplete, flux_trial)
                await repair_incomplete_review(executor, incomplete.id, need, style, lane)
                return await produce_image_candidates(executor)
        pending = next(iter(candidates), None)
        if pending:
            from atnos.production.order_autonomy import policy
            autonomy = policy(settings, context.job_id)
            if lane['provider'] == 'comfyui-local' and autonomy and autonomy['enabled']:
                from atnos.production.wb_image_review import review_candidate
                await review_candidate(executor, pending.id, need, style, lane)
                return await produce_image_candidates(executor)
            waiting.append({'scene_id': scene['scene_id'], 'acquisition': {'status': _review_status(pending.quality_status),
                'asset_id': pending.id, 'output_path': str(brain.resolve_file(pending)), 'quality_status': pending.quality_status}})
            break
        if (lane['provider'] == 'comfyui-local' and local_action is not None
                and local_action.get('remaining_new_requests', 0) <= 0):
            # A rejected candidate is a permanent quality outcome, not a
            # transient transport failure. Stop before opening another stage;
            # the durable dispatch guard remains the final no-POST barrier.
            raise ProductionBlocked(
                'Bounded local image action exhausted after its recorded request; '
                'no additional image stage or ComfyUI dispatch was started.'
            )
        if amendment is not None:
            from atnos.production.wb_image_conditioning import require_conditioning_dispatch
            # The separate one-request action is scene-scoped. An ordinary
            # resume/autonomy may inspect/reuse, never spend it on another scene.
            require_conditioning_dispatch(local_action,amendment,scene['scene_id'])
        checkpoints = executor.store.get(context.job_id)['checkpoints']
        local = lane['provider'] == 'comfyui-local'
        stage_prefix = 'assets/image_local/' if local else 'assets/image_api/'
        consumed = sum(int(row['attempts']) for row in checkpoints if row['stage'].startswith(stage_prefix))
        maximum = lane['max_requests']
        if local:
            from atnos.production.local_image_allowance import effective_local_image_allowance
            maximum = effective_local_image_allowance(settings, context.job_id, lane)
        if maximum is not None and consumed >= maximum:
            raise ProductionBlocked('Authorized image request limit reached; history and cost remain preserved')
        automatic_flux_rejected=False
        if flux_trial and not local_action and str(flux_trial.get('source','')).startswith('Atnos autonomous'):
            from atnos.production.wb_image_review import proven_rejection_count
            failed_flux=[a for a in brain.catalog.assets if set(tags).issubset(a.tags)
                         and a.source=='comfyui-flux-trial'
                         and a.id.endswith(flux_trial['digest'][:16])]
            if proven_rejection_count(context,brain,failed_flux):
                automatic_flux_rejected=True
                if flux_trial.get('parent_digest'):
                    from atnos.production.wb_flux_style_candidate import (
                        VERSION as style_version, eligible_flux_style_candidate, register_flux_style_candidate)
                    # A photographic medium miss can select a pinned drawing
                    # adapter once. Every original POST/receipt stays consumed.
                    if flux_trial.get('version') != style_version and eligible_flux_style_candidate(
                            settings, executor.store, executor.store.get(context.job_id), context,
                            scene['scene_id'], need, lane, flux_trial['digest']):
                        flux_trial = await asyncio.to_thread(register_flux_style_candidate, settings,
                            context.job_id, scene['scene_id'], flux_trial['digest'], worker_token=executor.token)
                    else:
                        flux_trial=None
                else:
                    from atnos.production.wb_flux_refinement import register_flux_refinement
                    flux_trial=await asyncio.to_thread(register_flux_refinement,settings,context.job_id,
                        scene['scene_id'],flux_trial['digest'],worker_token=executor.token)
        flux_fallback = None
        if local and flux_trial:
            flux_stage = f"assets/image_local/flux2-trial/{scene['scene_id']}/{flux_trial['digest'][:16]}"
            failed_flux_stage = next((row for row in checkpoints if row['stage'] == flux_stage), None)
            if failed_flux_stage and failed_flux_stage.get('status') == 'failed':
                from atnos.production.wb_local_flux_trial import verify_failed_flux_allows_local_comfy_fallback
                flux_fallback = verify_failed_flux_allows_local_comfy_fallback(
                    context, scene['scene_id'], flux_trial, failed_flux_stage)
                if flux_fallback:
                    # The exact one-shot Flux evidence is retained. This only
                    # resumes the already-authorized standard ComfyUI lane.
                    flux_trial = None
        if (local and local_action is None and flux_trial is None and not automatic_flux_rejected and not flux_fallback
                and not need.get('composition_reference_path')):
            from atnos.production.order_autonomy import policy
            from atnos.production.wb_image_review import proven_rejection_count
            from atnos.production.wb_local_flux_trial import register_flux_trial
            autonomy=policy(settings,context.job_id)
            rejected=[a for a in brain.catalog.assets if set(tags).issubset(a.tags)]
            if autonomy and autonomy['enabled'] and proven_rejection_count(context,brain,rejected)>=2:
                # Escalate the local backend, not the acceptance threshold. One
                # immutable request, same scene/style, normal budget and pixel QC.
                flux_trial=await asyncio.to_thread(register_flux_trial,settings,context.job_id,scene['scene_id'],
                    authorized=True,worker_token=executor.token)
        fingerprint = content_hash({'need': need, 'tags': tags, 'authorization': lane['digest'],
            'composition_reference_sha256': file_hash(Path(need['composition_reference_path'])) if need.get('composition_reference_path') else None,
            **({'flux_trial_digest': flux_trial['digest']} if flux_trial else {}),
            **({'flux_fallback_digest': flux_fallback['digest']} if flux_fallback else {}),
            **({'generator_contract': _local_generator_identity(settings)} if local and not flux_trial else {}),
            'continuity_sha256': file_hash(previous_reference) if previous_reference else None,
            'prior_rejected': sorted(a.id for a in brain.catalog.assets if set(tags).issubset(a.tags) and a.quality_status == 'REJECTED')})
        stage = (f"{stage_prefix}flux2-trial/{scene['scene_id']}/{flux_trial['digest'][:16]}" if flux_trial
                 else f"{stage_prefix}{scene['scene_id']}/{fingerprint[:16]}")
        previous_stage = next((row for row in checkpoints if row['stage'] == stage), None)
        if flux_trial and previous_stage and previous_stage['attempts'] >= 1:
            # A rejected, failed, lost or uncertain trial never becomes a new
            # POST just because prior_rejected/fingerprint changed on resume.
            raise ProductionBlocked('One-shot Flux trial already consumed; inspect its evidence, no second POST')
        if previous_stage and 'outcome uncertain' in str(previous_stage.get('error_json') or ''):
            raise ProductionBlocked('Prior image API request outcome uncertain; no automatic or resume-time second POST')
        if previous_stage and any(code in str(previous_stage.get('error_json') or '') for code in (
                'credit_balance_exhausted', 'insufficient_quota', 'billing_hard_limit_reached')):
            raise ProductionBlocked('Image API billing remains blocked; restore the account before authorizing another request')

        async def generate():
            # A timeout after HTTP dispatch has uncertain billing. Do not hide an
            # automatic second POST inside a retry/fallback to another model.
            try:
                if flux_trial:
                    if local_action:
                        raise ProductionBlocked('Bounded local action allows only standard ComfyUI candidates; '
                                                'reconcile the consumed Flux trial before a new dispatch')
                    from atnos.production.wb_local_flux_trial import generate_flux_candidate
                    return await asyncio.to_thread(generate_flux_candidate, settings, context, need, style, tags,
                                                   lane, fingerprint, flux_trial)
                generator = _generate_local_candidate if local else _generate_candidate
                if local and local_action:
                    from atnos.production.local_image_dispatch_budget import local_image_dispatch_scope
                    with local_image_dispatch_scope(settings, context.job_id, executor.token, stage, fingerprint):
                        generated = await asyncio.to_thread(generator, settings, context, need, style, tags,
                                                          lane, fingerprint, previous_reference)
                else:
                    generated = await asyncio.to_thread(generator, settings, context, need, style, tags,
                                                        lane, fingerprint, previous_reference)
                if flux_fallback and flux_fallback.get('outcome') == 'abandoned_without_product':
                    reconciliation_path = Path(flux_fallback['evidence_path'])
                    proof = {'stage': failed_flux_stage['stage'], 'input_hash': failed_flux_stage['input_hash'],
                             'attempts': failed_flux_stage['attempts'],
                             'reconciliation_sha256': file_hash(reconciliation_path)}
                    if not proof['reconciliation_sha256']:
                        raise ProductionBlocked('Local fallback reconciliation evidence is missing')
                    payload = {**generated.payload, 'replaces_local_operation': proof}
                    updated = write_json(generated.artifact_path, payload)
                    generated = StepResult(updated.artifact_name, updated.artifact_path, payload, updated.message,
                                           tuple(dict.fromkeys([*generated.evidence_paths, reconciliation_path])))
                return generated
            except LocalImageDispatchBlocked as exc:
                await asyncio.to_thread(executor.store.fail, context.job_id, executor.token, stage,
                                        category=exc.category, message=str(exc))
                raise
            except (httpx.TransportError, TimeoutError, OSError) as exc:
                raise RuntimeError('Image API request outcome uncertain; inspect provider evidence before retry') from exc
            except httpx.HTTPStatusError as exc:
                # 429 can mean an empty account balance, not a short rate limit.
                # Images are explicitly retried by the operator after repair.
                raise RuntimeError(str(exc)) from None
            except ValueError as exc:
                # Invalid PNG dimensions or local registration errors cannot be
                # repaired by blindly paying to draw the same illustration again.
                raise RuntimeError(str(exc)) from None

        if unlimited_local:
            requests = burst['requests'].get(scene['scene_id'], 0)
            if requests >= 3:
                raise ProductionBlocked('local_image_retry_paused: three consecutive unaccepted local candidates in this run; inspect their QC before resuming')
            burst['requests'][scene['scene_id']] = requests + 1
        result = await executor.run(stage, {'need': need, 'style': style, 'lane': lane['digest'], 'fingerprint': fingerprint,
            **({'flux_fallback_evidence': flux_fallback} if flux_fallback else {})},
            generate, provider_call=not local, local_model_call=local,
            estimated_cost_usd=lane['reservation_usd_per_request'])
        from atnos.production.order_autonomy import policy
        autonomy = policy(settings, context.job_id)
        if lane['provider'] == 'comfyui-local' and autonomy and autonomy['enabled']:
            from atnos.production.wb_image_review import review_candidate
            await review_candidate(executor, result['asset_id'], need, style, lane)
            return await produce_image_candidates(executor)
        waiting.append({'scene_id': scene['scene_id'], 'acquisition': {'status': _review_status(result['quality_status']),
            'asset_id': result['asset_id'], 'output_path': result['output_path'], 'quality_status': result['quality_status']}})
        break  # Review the first real candidate before spending on the next shot.
    review_payload = {'version': LANE_VERSION, 'timing': 'estimated_before_final_audio', 'waiting': waiting, 'plan': plan}
    if waiting:
        write_json(context.artifact_dir / 'image_api_review.json', review_payload)
        raise SceneAssetsNotReady(waiting)
    async def finalize_images():
        from atnos.production.wb_image_review import verify_qualification
        current_brain = WealthBlueprintAssetBrain(settings)
        files = []
        for selected in qualified_assets:
            asset = next((a for a in current_brain.catalog.assets if a.id==selected['asset_id']),None)
            if not asset or not asset.is_production_selectable():
                raise ProductionBlocked('Selected image qualification changed before finalization')
            image = current_brain.resolve_file(asset)
            if file_hash(image)!=selected['image_sha256']:
                raise ProductionBlocked('Selected image pixels changed before finalization')
            verify_qualification(context,asset,image)
            if selected['qualification_sha256']:
                directory=context.artifact_dir/'assets/automatic-review'/selected['reviewed_by'].split(':',1)[1]
                if file_hash(directory/'review.json')!=selected['qualification_sha256']:
                    raise ProductionBlocked('Selected image review changed before finalization')
                files.extend(directory.glob('*.json'))
        final=write_json(context.artifact_dir/'image_api_review.json',{
            **review_payload,'qualified_assets':qualified_assets,'appearance_approval':'user_review_pending'})
        return StepResult(final.artifact_name,final.artifact_path,final.payload,final.message,tuple(files))
    return await executor.run('image_generation',{'plan':plan,'lane_digest':lane['digest'],
        'qualified_assets':qualified_assets,'contract_version':2},finalize_images)


def _review_status(quality_status: str) -> str:
    return 'awaiting_approval' if quality_status in {'AWAITING_REVIEW', 'TECHNICALLY_VALID'} else quality_status.casefold()


def _verify_scoped_flux_candidate(asset, receipt):
    from atnos.production.wb_flux_style_candidate import VERSION as style_version, verify_flux_style_asset
    from atnos.production.wb_flux_refinement import VERSION as refinement_version, verify_flux_refinement_asset
    from atnos.production.wb_local_flux_trial import VERSION as trial_version, verify_flux_asset
    if receipt.get('version') == style_version:
        verify_flux_style_asset(asset, receipt)
    elif receipt.get('version') == refinement_version:
        verify_flux_refinement_asset(asset, receipt)
    elif receipt.get('version') == trial_version:
        verify_flux_asset(asset, receipt)
    else:
        raise ProductionBlocked('Unknown scoped Flux authorization version')
