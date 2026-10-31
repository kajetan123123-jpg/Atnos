"""Explicit one-shot Flux candidate inside normal WB orders, never a global backend.

Registration is a separate operator action; importing this module activates
nothing. The ordinary candidate stage owns the budget and Asset Brain review.
The cold process and exactly one POST are bound to an immutable scene receipt.
"""
from __future__ import annotations

from contextlib import contextmanager
import inspect
import json
import os
import re
from pathlib import Path
import socket
import sqlite3
import subprocess
from time import monotonic, sleep

import httpx
import psutil

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.core.logging import redact_secrets
from atnos.production.models import StepResult
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService

VERSION = 'wb-flux-cold-one-shot-v1'
MODEL_ROOT = Path('E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Shared/models')
MODEL_NAMES = {
    'unet': ('diffusion_models', 'flux-2-klein-base-4b.safetensors'),
    'clip': ('text_encoders', 'qwen_3_4b.safetensors'),
    'vae': ('vae', 'flux2-vae.safetensors'),
}
MIN_RAM = 16 * 1024 ** 3
MIN_VRAM = 12 * 1024 ** 3
NEGATIVE = 'photograph, plastic 3d, collage, duplicate objects, sailboat, flowers, wax seal, text, letters, numbers, currency portraits, watermark, logo'


class FluxTrialStopped(RuntimeError):
    """Nonretryable: never turn a runtime error into a second image request."""


def _observe_abandoned_flux(folder: Path, receipt: dict) -> dict:
    """Read-only local outcome proof. Never substitutes a connection failure."""
    folder = _on_e(folder)
    sources = {name: folder / (name + '.json') for name in ('dispatch', 'instance', 'workflow', 'runtime')}
    if any(not path.is_file() for path in sources.values()):
        raise ProductionBlocked('Incomplete local dispatch evidence; outcome remains unknown')
    dispatch, instance, graph, runtime = (json.loads(sources[name].read_text('utf8')) for name in sources)
    if (dispatch.get('status') != 'queued' or not dispatch.get('prompt_id')
            or dispatch.get('trial_digest') != receipt['digest']
            or dispatch.get('workflow_sha256') != receipt['workflow_sha256']
            or content_hash(graph) != receipt['workflow_sha256']
            or instance.get('trial_digest') != receipt['digest']
            or instance.get('marker') != VERSION):
        raise ProductionBlocked('Local dispatch identity mismatch; outcome remains unknown')
    argv = instance.get('argv') or []
    def arg(name):
        try:
            return argv[argv.index(name) + 1]
        except (ValueError, IndexError):
            raise ProductionBlocked('Incomplete local runtime arguments')
    output = _on_e(folder / 'output')
    allowed_python = {_on_e(Path(v['path'])) for k,v in receipt['runtime_files'].items() if k in {'python','base_python'}}
    saved_identity = runtime.get('identity') or {}
    identity_keys = ('pid','created','port','argv','cwd','exe','launcher_pid','launcher_created')
    if (int(instance.get('port') or 0) != 8195 or str(arg('--port')) != '8195' or arg('--listen') != '127.0.0.1'
            or len(argv) < 3 or _on_e(Path(argv[0])) not in allowed_python
            or _on_e(Path(argv[2])) != _on_e(Path(receipt['runtime_files']['main']['path']))
            or any(instance.get(k) != saved_identity.get(k) for k in identity_keys)
            or runtime.get('trial_digest') != receipt['digest']
            or (dispatch.get('instance_sha256') and dispatch['instance_sha256'] != content_hash(saved_identity))
            or _on_e(Path(arg('--base-directory'))) != folder
            or _on_e(Path(arg('--output-directory'))) != output
            or _on_e(Path(instance['cwd'])) != _on_e(Path(receipt['runtime_files']['main']['path'])).parent
            or _on_e(Path(instance['exe'])) not in allowed_python):
        raise ProductionBlocked('Local runtime is not bound to this isolated output directory')
    for pid_key, created_key in (('pid', 'created'), ('launcher_pid', 'launcher_created')):
        if pid_key == 'launcher_pid' and not instance.get(pid_key) and not instance.get(created_key):
            continue  # No venv wrapper: the single saved serving process is the launcher.
        pid, created = int(instance.get(pid_key) or 0), float(instance.get(created_key) or 0)
        if pid <= 0 or created <= 0:
            raise ProductionBlocked('Incomplete local process identity')
        try:
            process = psutil.Process(pid)
            actual_created = process.create_time()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise ProductionBlocked('Cannot inspect local process; outcome remains unknown') from exc
        if abs(actual_created - created) > .01:
            raise ProductionBlocked('Local process PID was reused; outcome remains unknown')
        raise ProductionBlocked('Local generator or launcher remains active; do not repeat dispatch')
    try:
        listeners = [c for c in psutil.net_connections(kind='tcp')
                     if c.status == psutil.CONN_LISTEN and c.laddr.port == int(instance['port'])]
    except psutil.AccessDenied as exc:
        raise ProductionBlocked('Cannot inspect local runtime port') from exc
    if listeners:
        raise ProductionBlocked('Local runtime port has a listener; outcome remains unknown')
    if (not output.is_dir() or any(output.rglob('*'))
            or any((folder / name).exists() for name in ('image.png','history.json','candidate.json'))):
        raise ProductionBlocked('Result artifacts exist or output inventory is unavailable; reconcile result before fallback')
    return {'outcome': 'abandoned_without_product', 'prompt_id': dispatch['prompt_id'],
            'trial_digest': receipt['digest'], 'process_state': 'recorded_runtime_absent',
            'port_state': 'no_listener', 'output_files': [], 'flux_retry_permitted': False,
            'sources': {name: {'path': str(path), 'sha256': file_hash(path)} for name,path in sources.items()}}


def reconcile_interrupted_flux_trial(settings, job_id: str) -> dict:
    """Registered reconciliation under the ordinary dispatch lock and worker lease."""
    from atnos.production.dispatch_lock import dispatch_lock
    from atnos.production.wb_image_api import load_image_lane
    store = ProductionOrderStore(settings)
    context = store.context(job_id)
    with dispatch_lock(context.artifact_dir / 'worker_logs' / 'dispatch.lock'):
        state = store.get(job_id)
        match = re.fullmatch(r'assets/image_local/flux2-trial/([^/]+)/([0-9a-f]{16})', str(state.get('current_step')))
        if (not match or state['worker_live'] or state['status'] != 'blocked'
                or state['request'].get('delivery_mode') != 'local_only'
                or json.loads(state.get('error') or '{}').get('category') != 'execution_outcome_unknown'):
            raise OrderConflict('Reconciliation requires a stopped local order with unknown dispatched Flux outcome')
        scene_id, prefix = match.groups()
        _, context, need = _scene_state(store, job_id, scene_id)
        lane = load_image_lane(settings, job_id)
        receipt = load_flux_trial(settings, context, scene_id, need, lane)
        if not receipt or receipt['digest'][:16] != prefix:
            raise ProductionBlocked('Current Flux authorization does not match interrupted stage')
        folder = _on_e(context.artifact_dir / 'assets/image_local/flux_trial' / prefix)
        stage = state['current_step']
        checkpoint = next(row for row in state['checkpoints'] if row['stage'] == stage)
        with store.lease(job_id) as token:
            proof = _observe_abandoned_flux(folder, receipt)
            payload = {'version':'wb-flux-outcome-reconciliation-v1', 'job_id':job_id, 'scene_id':scene_id,
                       'stage':stage, 'input_hash':checkpoint['input_hash'], 'attempts':checkpoint['attempts'],
                       **proof}
            path = folder / 'reconciliation.json'
            report = write_json(path, {**payload, 'digest':content_hash(payload)})
            # Reobserve before the transactional terminal transition. A new
            # artifact or process must never be hidden by an earlier snapshot.
            if _observe_abandoned_flux(folder, receipt) != proof:
                raise ProductionBlocked('Local outcome evidence changed before reconciliation')
            store.reconcile_abandoned_local_stage(job_id, token, stage, expected_input_hash=checkpoint['input_hash'],
                                                  evidence_path=path, evidence_sha256=file_hash(path))
        return {**payload, 'digest':content_hash(payload), 'evidence_path':str(report.artifact_path)}


def _reconciled_flux_fallback(context, scene_id, receipt, checkpoint):
    folder = _on_e(context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16])
    path = folder / 'reconciliation.json'
    if not path.is_file():
        raise ProductionBlocked('Missing registered local outcome reconciliation')
    record = json.loads(path.read_text('utf8'))
    payload = {k:v for k,v in record.items() if k != 'digest'}
    if (record.get('digest') != content_hash(payload) or record.get('job_id') != context.job_id
            or record.get('scene_id') != scene_id or record.get('stage') != checkpoint.get('stage')
            or record.get('input_hash') != checkpoint.get('input_hash')
            or record.get('attempts') != checkpoint.get('attempts')):
        raise ProductionBlocked('Registered local outcome reconciliation mismatch')
    current = _observe_abandoned_flux(folder, receipt)
    if any(record.get(k) != v for k,v in current.items()):
        raise ProductionBlocked('Registered local outcome evidence changed')
    # The checkpoint binds this exact receipt; an offline fixture cannot grant fallback.
    error = json.loads(checkpoint.get('error_json') or '{}')
    if error.get('evidence_sha256') != file_hash(path):
        raise ProductionBlocked('Checkpoint does not bind this reconciliation receipt')
    return {**record, 'fallback_provider':'comfyui-local', 'evidence_path':str(path),
            'user_approved':False, 'appearance_approval':'pending'}


def _on_e(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.drive.casefold() != 'e:':
        raise ProductionBlocked('Flux trial paths must remain on E:')
    return resolved


def _model_files() -> dict[str, Path]:
    return {key: _on_e(MODEL_ROOT / folder / name) for key, (folder, name) in MODEL_NAMES.items()}


def _runtime_files(settings) -> dict[str, Path]:
    from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
    install = ComfyUiRuntimeService(settings).describe_install()
    # The venv contains Torch; standalone-env is its base executable only.
    python = install.python_executable
    paths = {'python': python, 'main': install.main_script,
             'model_paths': settings.comfyui_shared_model_paths,
             'builder': Path(inspect.getfile(ComfyUiImageGenerationService))}
    if any(value is None or not value.is_file() for value in paths.values()):
        raise ProductionBlocked('Flux trial requires the existing E interpreter, main.py and shared model config')
    base = install.install_dir / 'standalone-env/python.exe' if install.install_dir else None
    if base and base.is_file():
        paths['base_python'] = base
    return {key: _on_e(value) for key, value in paths.items()}


def _manifest(paths: dict[str, Path]) -> dict:
    return {key: {'path': str(path), 'bytes': path.stat().st_size, 'sha256': file_hash(path)}
            for key, path in paths.items()}


def _check_manifest(manifest: dict) -> None:
    for item in manifest.values():
        path = _on_e(Path(item['path']))
        if not path.is_file() or path.stat().st_size != item['bytes'] or file_hash(path) != item['sha256']:
            raise ProductionBlocked('Flux trial model/runtime integrity failed')


def _scene_state(store, job_id, scene_id):
    from atnos.production.wb_image_api import image_selection_plan
    from atnos.production.channels.wealth_blueprint.scene_production_assets import _needs_for_scene
    state, context = store.get(job_id), store.context(job_id)
    canonical = next((row for row in state['checkpoints'] if row['stage'] == 'script' and row['status'] == 'done'), None)
    if (not canonical or canonical['output'].get('qa', {}).get('status') != 'passed'
            or not store._valid_files(context.artifact_dir, json_dumps(canonical['files']))):
        raise OrderConflict('Flux trial requires the intact canonical reviewed script')
    context.artifacts['script'] = canonical['output']
    if (state['request'].get('intake') or {}).get('kind')=='user_hypothetical':
        calculations=next((row for row in state['checkpoints'] if row['stage']=='script_calculations' and row['status']=='done'),None)
        if not calculations or not store._valid_files(context.artifact_dir,json_dumps(calculations['files'])):
            raise ProductionBlocked('Flux scene planning requires intact validated calculations')
        context.artifacts['script_calculations']=calculations['output']
    scene = next((item for item in image_selection_plan(context)['scenes'] if item['scene_id'] == scene_id), None)
    if not scene:
        raise OrderConflict('Flux trial scene is absent from the actual WB plan')
    need = _needs_for_scene(scene)[0]
    if need.get('type') != 'background' or not need.get('asset_subject'):
        raise OrderConflict('Flux trial requires a literal background illustration')
    return state, context, need


def _prompt(need: dict, style: dict) -> str:
    return (f"{need['asset_subject']}. {need.get('asset_composition', '')} "
            f"Drawing medium and art direction: {style['prompt']}. "
            'One complete vertical hand-drawn illustration, navy contours, cream paper, restrained gold light. '
            'Deliberate materials and realistic object proportions. Plain unprinted paper cards. '
            'A large clear central subject with quiet top and bottom caption space. '
            'No printed text, letters, numbers, logos or currency portraits. No unrelated scenery or objects.')


def _workflow(need, style, seed, prefix):
    # Pure native builder; no server startup, backend route or health write.
    builder = ComfyUiImageGenerationService.__new__(ComfyUiImageGenerationService)
    graph = builder._build_flux2_klein_workflow(
        prompt=_prompt(need, style), width=768, height=1344, seed=seed,
        unet_name=MODEL_NAMES['unet'][1], clip_name=MODEL_NAMES['clip'][1], vae_name=MODEL_NAMES['vae'][1],
        negative_prompt=NEGATIVE, steps_override=50, cfg_scale_override=4.0)
    graph['2']['inputs']['device'] = 'cpu'
    graph['13']['inputs']['filename_prefix'] = prefix
    return graph


def register_flux_trial(settings, job_id: str, scene_id: str, *, authorized=False, seed=429512, worker_token=None) -> dict:
    """Record one explicit stopped-order trial; never dispatch or reserve a call here."""
    from atnos.production.wb_image_api import load_image_lane
    if not authorized:
        raise ValueError('Explicit scoped local Flux trial choice is required')
    store = ProductionOrderStore(settings)
    state, context, need = _scene_state(store, job_id, scene_id)
    lane = load_image_lane(settings, job_id)
    autonomous=False
    if worker_token is not None:
        from atnos.production.order_autonomy import policy
        autonomy=policy(settings,job_id)
        if not autonomy or not autonomy['enabled']:
            raise OrderConflict('Flux automatic choice requires enabled local autonomy')
        with connect(settings) as db:
            store._owner(db,job_id,worker_token)
        autonomous=True
    if ((not autonomous and (state['worker_live'] or state['status'] not in {'blocked', 'queued'}))
            or state['request'].get('project') != 'wealth_blueprint'
            or state['request'].get('delivery_mode') != 'local_only'
            or not lane or lane['provider'] != 'comfyui-local'):
        raise OrderConflict('Flux trial requires a stopped authorized local WB order')
    if any(row['status'] == 'done' and row['stage'].split('/')[0] in {'voice', 'edit', 'remotion_render', 'package'}
           for row in state['checkpoints']):
        raise OrderConflict('Flux trial must precede completed audiovisual media')
    if not isinstance(seed, int) or not 0 <= seed < 2 ** 63:
        raise ValueError('Flux trial seed is invalid')
    style = context.artifacts['project_profile']['visual_style']
    graph = _workflow(need, style, seed, f'wb_flux_trial_{job_id}_{scene_id}')
    payload = {
        'version': VERSION, 'job_id': job_id, 'scene_id': scene_id,
        'local_lane_digest': lane['digest'], 'request_hash': content_hash(state['request']),
        'profile_hash': content_hash(context.artifacts['project_profile']),
        'script_hash': content_hash(context.artifacts['script']), 'need_hash': content_hash(need),
        'models': _manifest(_model_files()), 'runtime_files': _manifest(_runtime_files(settings)),
        'workflow': graph, 'workflow_sha256': content_hash(graph), 'seed': seed,
        'width': 768, 'height': 1344, 'steps': 50, 'cfg': 4.0, 'max_attempts': 1,
        'unet_dtype': 'fp16', 'clip_device': 'cpu', 'vae_device': 'cpu',
        'source': ('Atnos autonomous one-shot local backend failover after pixel-proven rejection; existing local authorization and current worker lease.'
                   if autonomous else 'Explicit operator choice for one scene under the user-authorized local ComfyUI lane.'),
        'user_approved': False, 'appearance_approval': 'pending',
    }
    digest = content_hash(payload)
    receipt = context.artifact_dir / 'flux_trial' / scene_id / 'authorization.json'
    with connect(settings) as db:
        db.execute('CREATE TABLE IF NOT EXISTS production_wb_flux_trials '
                   '(job_id TEXT NOT NULL, scene_id TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, '
                   'created_at TEXT NOT NULL, PRIMARY KEY(job_id,scene_id))')
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT digest FROM production_wb_flux_trials WHERE job_id=? AND scene_id=?',
                              (job_id, scene_id)).fetchone()
        if previous:
            if previous['digest'] != digest:
                raise OrderConflict('Flux trial receipt is immutable')
        else:
            write_json(receipt, {**payload, 'digest': digest})
            db.execute('INSERT INTO production_wb_flux_trials VALUES(?,?,?,?,?)',
                       (job_id, scene_id, json_dumps(payload), digest, utc_now_iso()))
            db.commit()
    return load_flux_trial(settings, context, scene_id, need, lane)


def load_flux_trial(settings, context, scene_id, need, lane, *, allow_failed_fallback: bool = False) -> dict | None:
    with connect(settings) as db:
        try:
            row = db.execute('SELECT payload_json,digest FROM production_wb_flux_trials WHERE job_id=? AND scene_id=?',
                             (context.job_id, scene_id)).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' in str(exc):
                return None
            raise
    if row is None:
        return None
    state = ProductionOrderStore(settings).get(context.job_id)
    verified = _verify_flux_trial_record(state, context, scene_id, need, lane, row)
    # Execution still requires today's installed model/runtime to be identical
    # to the frozen authorization. Historical inspection does not execute it.
    try:
        _check_manifest(verified['models'])
        _check_manifest(verified['runtime_files'])
    except ProductionBlocked:
        if not allow_failed_fallback:
            raise
        expected_stage = f"assets/image_local/flux2-trial/{scene_id}/{verified['digest'][:16]}"
        checkpoint = next((item for item in state['checkpoints'] if item['stage'] == expected_stage), None)
        if checkpoint is None or checkpoint.get('status') != 'failed':
            raise
        fallback = verify_failed_flux_allows_local_comfy_fallback(
            context, scene_id, verified, checkpoint)
        if not fallback or fallback.get('no_post_made') is not True:
            raise ProductionBlocked('Flux integrity changed; only a proven pre-dispatch failure may use standard ComfyUI')
        # Return only as an identity token so the caller can attach the
        # no-dispatch proof and use the normal standard-Comfy lane. It must not
        # be passed to any Flux executor after this point.
    return verified


def _verify_flux_trial_record(state, context, scene_id, need, lane, row):
    """Verify the immutable historical envelope, without admitting execution."""
    payload = json.loads(row['payload_json'])
    receipt = context.artifact_dir / 'flux_trial' / scene_id / 'authorization.json'
    # Writer recovery appends feedback_rules to the execution context. The
    # authorization binds the frozen order profile, not those transient hints.
    # Every other effective profile field must still match that frozen profile.
    frozen_profile = state['profile']
    effective_profile = context.artifacts['project_profile']
    profile_matches = (
        payload['profile_hash'] == content_hash(frozen_profile)
        and content_hash({key: value for key, value in effective_profile.items() if key != 'feedback_rules'})
        == content_hash({key: value for key, value in frozen_profile.items() if key != 'feedback_rules'})
    )
    if (not receipt.is_file() or json.loads(receipt.read_text('utf-8')) != {**payload, 'digest': row['digest']}
            or content_hash(payload) != row['digest'] or payload['version'] != VERSION
            or payload['job_id'] != context.job_id or payload['scene_id'] != scene_id
            or lane['provider'] != 'comfyui-local' or payload['local_lane_digest'] != lane['digest']
            or payload['request_hash'] != content_hash(context.artifacts['production_order'])
            or not profile_matches
            or payload['script_hash'] != content_hash(context.artifacts['script'])
            or payload['need_hash'] != content_hash(need)
            or payload['workflow_sha256'] != content_hash(payload['workflow'])
            or payload['max_attempts'] != 1):
        raise ProductionBlocked('Flux trial authorization integrity failed')
    return {**payload, 'digest': row['digest']}


def verify_failed_flux_allows_local_comfy_fallback(context, scene_id: str, receipt: dict,
                                                    checkpoint: dict | None, *, read_only: bool = False) -> dict | None:
    """Record a narrowly verified ComfyUI recovery; never resubmit the Flux POST.

    A fallback is eligible only after either (a) the immutable one-shot Flux
    attempt failed with the known local-runtime connection-refused error, with
    no image/history result and the exact Flux process confirmed absent, or (b)
    a specifically recognized pre-dispatch resource gate proves that no Flux
    process or POST was reached. Any uncertainty keeps the order blocked.
    """
    if checkpoint is None or int(checkpoint.get('attempts') or 0) < 1:
        return None
    if checkpoint.get('status') != 'failed':
        return None
    if json.loads(checkpoint.get('error_json') or '{}').get('category') == 'local_operation_abandoned':
        return _reconciled_flux_fallback(context, scene_id, receipt, checkpoint)

    digest = str(receipt.get('digest') or '')
    if (len(digest) != 64 or receipt.get('job_id') != context.job_id
            or receipt.get('scene_id') != scene_id or receipt.get('max_attempts') != 1
            or not str(receipt.get('version') or '').startswith('wb-flux')
            or not isinstance(receipt.get('workflow_sha256'), str)):
        raise ProductionBlocked('Flux recovery receipt is incomplete; local fallback is blocked')

    expected_stage = f"assets/image_local/flux2-trial/{scene_id}/{digest[:16]}"
    try:
        checkpoint_error = checkpoint.get('error_json') or '{}'
        if isinstance(checkpoint_error, str):
            checkpoint_error = json.loads(checkpoint_error)
        if checkpoint.get('stage') != expected_stage:
            raise ProductionBlocked('Flux failure checkpoint does not prove a safe local fallback')

        # Resource gates in ColdFluxSession.open run before _launch(), after the
        # immutable workflow has been written but before instance/runtime or
        # dispatch receipts can exist. This is a separate proof from reconciling
        # an accepted/uncertain POST; do not weaken that path or replay Flux.
        pre_dispatch_failures = {
            'Flux trial requires at least 16 GiB available RAM; no POST made',
            'Flux trial requires at least 12 GiB free VRAM; no POST made',
        }
        if (checkpoint_error.get('category') == 'dependency_failure'
                and checkpoint_error.get('message') in pre_dispatch_failures):
            return _record_pre_dispatch_flux_fallback(
                context, scene_id, receipt, checkpoint, checkpoint_error, read_only=read_only)

        if (checkpoint_error.get('category') != 'dependency_failure'
                or 'Flux trial stopped' not in str(checkpoint_error.get('message') or '')
                or 'no second POST permitted' not in str(checkpoint_error.get('message') or '')):
            raise ProductionBlocked('Flux failure checkpoint does not prove a safe local fallback')

        folder = _on_e(context.artifact_dir / 'assets' / 'image_local' / 'flux_trial' / digest[:16])
        paths = {name: _on_e(folder / f'{name}.json') for name in ('dispatch', 'failure', 'instance')}
        if any(not path.is_file() for path in paths.values()):
            raise ProductionBlocked('Flux recovery evidence is incomplete; local fallback is blocked')
        dispatch, failure, instance = (json.loads(paths[name].read_text('utf-8'))
                                       for name in ('dispatch', 'failure', 'instance'))

        output = _on_e(folder / 'output')
        if (not output.is_dir() or any(output.iterdir())
                or (folder / 'image.png').exists() or (folder / 'history.json').exists()):
            raise ProductionBlocked('Flux trial has result artifacts; local fallback is blocked pending review')

        prompt_id = str(dispatch.get('prompt_id') or '')
        error = str(failure.get('error') or '')
        if (dispatch.get('status') != 'queued' or not prompt_id
                or dispatch.get('trial_digest') != digest
                or dispatch.get('workflow_sha256') != receipt.get('workflow_sha256')
                or failure.get('status') != 'stopped_nonretryable'
                or failure.get('trial_digest') != digest
                or failure.get('error_type') != 'ComfyUiImageGenerationError'
                or failure.get('dispatch_preserved') is not True
                or failure.get('automatic_retry') is not False
                or prompt_id not in error or '10061' not in error):
            raise ProductionBlocked('Flux failure does not match the one-time connection-refused recovery')

        pid = int(instance.get('pid') or 0)
        created = float(instance.get('created') or 0)
        argv = [str(value) for value in instance.get('argv') or []]
        def argument_value(name):
            try:
                return argv[argv.index(name) + 1]
            except (ValueError, IndexError):
                return None
        if (pid <= 0 or created <= 0 or instance.get('trial_digest') != digest
                or not str(instance.get('marker') or '').startswith('wb-flux')
                or int(instance.get('port') or 0) != 8195
                or argument_value('--port') != '8195'
                or argument_value('--base-directory') is None
                or Path(argument_value('--base-directory')).resolve() != folder
                or argument_value('--output-directory') is None
                or Path(argument_value('--output-directory')).resolve() != output):
            raise ProductionBlocked('Flux process identity evidence is invalid; local fallback is blocked')
        _on_e(Path(instance['exe']))
        _on_e(Path(instance['cwd']))
        if len(argv) < 3:
            raise ProductionBlocked('Flux process command line is incomplete; local fallback is blocked')
        _on_e(Path(argv[0]))
        _on_e(Path(argv[2]))
        # PID absence is the only accepted proof of termination. A reused PID,
        # a live process or denied inspection all fail closed; we never stop it.
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            process_state = 'pid_absent'
        except psutil.AccessDenied as exc:
            raise ProductionBlocked('Cannot verify Flux process ownership; local fallback is blocked') from exc
        else:
            try:
                actual_created = process.create_time()
                actual_exe = Path(process.exe()).resolve()
                actual_cwd = Path(process.cwd()).resolve()
                actual_cmdline = process.cmdline()
            except psutil.NoSuchProcess:
                process_state = 'pid_absent'
            except psutil.AccessDenied as exc:
                raise ProductionBlocked('Cannot verify Flux process ownership; local fallback is blocked') from exc
            else:
                expected_exe = _on_e(Path(instance['exe']))
                expected_cwd = _on_e(Path(instance['cwd']))
                expected_argv = [str(value) for value in instance.get('argv') or []]
                if (abs(actual_created - created) > 1.0 or actual_exe != expected_exe or actual_cwd != expected_cwd
                        or actual_cmdline != expected_argv):
                    raise ProductionBlocked('Flux PID identity changed; local fallback is blocked')
                raise ProductionBlocked('Flux process is still present; local fallback is blocked')

        evidence = {
            'version': 'wb-local-comfy-fallback-after-flux-runtime-failure-v1',
            'job_id': context.job_id, 'scene_id': scene_id,
            'flux_digest': digest, 'flux_version': receipt['version'],
            'checkpoint_stage': expected_stage, 'checkpoint_status': 'failed',
            'checkpoint_attempts': int(checkpoint['attempts']),
            'dispatch_prompt_id': prompt_id,
            'dispatch_status': 'queued', 'failure_status': 'stopped_nonretryable',
            'failure_code': 'WinError 10061', 'process_state': process_state,
            'process_pid': pid, 'output_files': [],
            'evidence_files': {name: {'path': str(path), 'sha256': file_hash(path)}
                               for name, path in paths.items()},
            'fallback_provider': 'comfyui-local', 'flux_retry_permitted': False,
            'appearance_approval': 'pending', 'user_approved': False,
        }
        evidence_digest = content_hash(evidence)
        destination = _on_e(context.artifact_dir / 'assets' / 'image_local' / 'flux_recovery'
                            / scene_id / evidence_digest[:16] / 'fallback.json')
        if destination.exists():
            if json.loads(destination.read_text('utf-8')) != {**evidence, 'digest': evidence_digest}:
                raise ProductionBlocked('Existing Flux recovery receipt differs; local fallback is blocked')
        elif read_only:
            raise ProductionBlocked('Flux recovery requires its already registered fallback receipt')
        else:
            write_json(destination, {**evidence, 'digest': evidence_digest})
        return {**evidence, 'digest': evidence_digest, 'evidence_path': str(destination)}
    except ProductionBlocked:
        raise
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ProductionBlocked('Flux failure evidence cannot prove a safe local fallback') from exc


def _record_pre_dispatch_flux_fallback(context, scene_id, receipt, checkpoint, checkpoint_error, *, read_only=False):
    """Persist proof for the known pre-launch capacity gates; never dispatch here."""
    digest = str(receipt.get('digest') or '')
    folder = _on_e(context.artifact_dir / 'assets' / 'image_local' / 'flux_trial' / digest[:16])
    workflow_path = _on_e(folder / 'workflow.json')
    if (not workflow_path.is_file() or not isinstance(receipt.get('workflow'), dict)
            or json.loads(workflow_path.read_text('utf-8')) != receipt['workflow']
            or content_hash(receipt['workflow']) != receipt.get('workflow_sha256')):
        raise ProductionBlocked('Pre-dispatch Flux workflow evidence is missing or changed')

    # The preflight gate occurs before ColdFluxSession._launch. Only the
    # already-written immutable workflow may exist; any process, request,
    # result, or unexplained artifact makes the outcome uncertain.
    files = sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*') if path.is_file())
    if files != ['workflow.json']:
        raise ProductionBlocked('Pre-dispatch Flux folder has extra evidence; reconcile it before fallback')
    for name in ('dispatch.json', 'failure.json', 'instance.json', 'runtime.json',
                 'history.json', 'image.png', 'candidate.json', 'reconciliation.json'):
        if (folder / name).exists():
            raise ProductionBlocked('Pre-dispatch Flux evidence contains a possible request or result')
    try:
        listeners = [connection for connection in psutil.net_connections(kind='tcp')
                     if connection.status == psutil.CONN_LISTEN and connection.laddr.port == 8195]
    except (psutil.AccessDenied, psutil.Error) as exc:
        raise ProductionBlocked('Cannot verify the isolated Flux port is unused; fallback is blocked') from exc
    if listeners:
        raise ProductionBlocked('Flux trial port has a listener; pre-dispatch outcome is uncertain')

    payload = {
        'version': 'wb-local-comfy-fallback-after-flux-pre-dispatch-gate-v1',
        'job_id': context.job_id, 'scene_id': scene_id, 'flux_digest': digest,
        'flux_version': receipt['version'], 'checkpoint_stage': checkpoint['stage'],
        'checkpoint_status': checkpoint['status'], 'checkpoint_attempts': int(checkpoint['attempts']),
        'checkpoint_error_sha256': content_hash(checkpoint_error),
        'outcome': 'abandoned_without_product', 'prompt_id': None,
        'no_post_made': True, 'pre_dispatch_failure': checkpoint_error['message'],
        'workflow_sha256': receipt['workflow_sha256'], 'workflow_file_sha256': file_hash(workflow_path),
        'port_state': 'no_listener', 'output_files': [],
        'fallback_provider': 'comfyui-local', 'flux_retry_permitted': False,
        'appearance_approval': 'pending', 'user_approved': False,
    }
    evidence_digest = content_hash(payload)
    destination = _on_e(context.artifact_dir / 'assets' / 'image_local' / 'flux_recovery'
                        / scene_id / evidence_digest[:16] / 'fallback.json')
    if destination.exists():
        if json.loads(destination.read_text('utf-8')) != {**payload, 'digest': evidence_digest}:
            raise ProductionBlocked('Existing pre-dispatch fallback receipt differs; fallback is blocked')
    elif read_only:
        raise ProductionBlocked('Pre-dispatch fallback requires its already registered receipt')
    else:
        write_json(destination, {**payload, 'digest': evidence_digest})
    return {**payload, 'digest': evidence_digest, 'evidence_path': str(destination)}


def _conditioning_history_rows(settings, job_id, scene_id):
    """Read all compiler epochs, so an unexpected descendant cannot disappear."""
    from atnos.production import wb_flux_refinement as refinement, wb_flux_style_candidate as style
    result = {}
    with connect(settings) as db:
        for table in ('production_wb_flux_trials', refinement.TABLE, style.TABLE):
            try:
                result[table] = [dict(row) for row in db.execute(
                    f'SELECT * FROM {table} WHERE job_id=? AND scene_id=? ORDER BY rowid', (job_id, scene_id))]
            except sqlite3.OperationalError as exc:
                if 'no such table:' not in str(exc):
                    raise
                result[table] = []
    return result


def _conditioning_history_process_absence(context, receipt, checkpoint):
    """A completed image is not evidence that its old runtime is absent now."""
    folder = _on_e(context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16])
    instance_file, runtime_file = folder / 'instance.json', folder / 'runtime.json'
    if checkpoint.get('status') == 'done':
        saved = {entry['path']: entry['sha256'] for entry in checkpoint.get('files', [])}
        if any(saved.get(path.relative_to(context.artifact_dir).as_posix()) != file_hash(path)
               for path in (instance_file, runtime_file)):
            raise ProductionBlocked('Historical Flux runtime files are not bound to the completed checkpoint')
    instance = json.loads(instance_file.read_text('utf-8'))
    runtime = json.loads(runtime_file.read_text('utf-8'))
    keys = ('pid', 'created', 'port', 'argv', 'cwd', 'exe', 'launcher_pid', 'launcher_created')
    if (instance.get('trial_digest') != receipt['digest'] or runtime.get('trial_digest') != receipt['digest']
            or not str(instance.get('marker') or '').startswith('wb-flux')
            or int(instance.get('port') or 0) != 8195
            or any(instance.get(key) != runtime.get('identity', {}).get(key) for key in keys)):
        raise ProductionBlocked('Flux history runtime identity is missing or changed')
    for pid_key, created_key in (('pid', 'created'), ('launcher_pid', 'launcher_created')):
        pid, created = instance.get(pid_key), instance.get(created_key)
        if pid_key == 'launcher_pid' and pid is None and created is None:
            continue
        if type(pid) is not int or pid <= 0 or not isinstance(created, (int, float)) or created <= 0:
            raise ProductionBlocked('Flux history process identity is incomplete')
        try:
            process = psutil.Process(pid)
            actual_created = process.create_time()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise ProductionBlocked('Cannot verify historical Flux process absence') from exc
        if abs(actual_created - created) > .01:
            raise ProductionBlocked('Historical Flux PID was reused; process outcome is not proven')
        raise ProductionBlocked('Historical Flux process remains active; no replacement dispatch')
    if any(c.status == psutil.CONN_LISTEN and c.laddr.port == 8195 for c in psutil.net_connections(kind='tcp')):
        raise ProductionBlocked('Historical Flux port has a listener; replacement must wait')
    return {'process_state': 'recorded_runtime_absent', 'port_state': 'no_listener',
            'instance_sha256': file_hash(instance_file), 'runtime_sha256': file_hash(runtime_file)}


def verify_conditioning_replacement_history(settings, context, scene_id, need, lane) -> dict | None:
    """Inspect an immutable historical Flux chain, never authorize or dispatch.

    The caller supplies an actual hashed script archive under artifact_dir in
    artifacts['conditioning_replacement_history']; current text cannot stand in
    for a different historical script. Default execution loaders stay current-only.
    """
    from copy import copy
    from atnos.production import wb_flux_refinement as refinement, wb_flux_style_candidate as style
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.wb_image_api import image_selection_plan
    from atnos.production.channels.wealth_blueprint.scene_production_assets import _needs_for_scene
    from atnos.production.wb_image_review import proven_rejection_count

    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', str(scene_id)):
            raise ProductionBlocked('Invalid conditioning history scene')
        store = ProductionOrderStore(settings)
        state = store.get(context.job_id)
        root = _on_e(context.artifact_dir)
        if (root != Path(state['artifact_dir']).resolve() or lane.get('provider') != 'comfyui-local'
                or state['request'].get('project') != 'wealth_blueprint'
                or state['request'].get('delivery_mode') != 'local_only'):
            raise ProductionBlocked('Conditioning history requires the actual local WB order')
        records = _conditioning_history_rows(settings, context.job_id, scene_id)
        prefix = f'assets/image_local/flux2-trial/{scene_id}/'
        checkpoints = [row for row in state['checkpoints'] if row['stage'].startswith(prefix)]
        if not any(records.values()):
            if checkpoints or (root / 'flux_trial' / scene_id).exists():
                raise ProductionBlocked('Historical Flux checkpoint/receipt has no registered lineage')
            return None
        base_rows = records['production_wb_flux_trials']
        if len(base_rows) != 1 or any(len(rows) > 1 for rows in records.values()):
            raise ProductionBlocked('Historical Flux lineage is orphaned or has unsupported compiler epochs')

        binding = context.artifacts.get('conditioning_replacement_history')
        if not isinstance(binding, dict) or set(binding) - {
                'script_path', 'script_sha256', 'calculations_path', 'calculations_sha256'}:
            raise ProductionBlocked('Hashed historical Flux script binding is required')
        def archived(name):
            path = _on_e(Path(binding.get(name + '_path') or ''))
            digest = binding.get(name + '_sha256')
            if (not path.is_relative_to(root) or not re.fullmatch(r'[0-9a-f]{64}', str(digest))
                    or not path.is_file() or file_hash(path) != digest):
                raise ProductionBlocked('Historical Flux script/calculation archive is missing or changed')
            return path, json.loads(path.read_text('utf-8'))
        script_path, old_script = archived('script')
        historical = copy(context)
        historical.artifacts = {'script': old_script, 'production_order': state['request'],
                                'project_profile': state['profile']}
        calculation_path = None
        if (state['request'].get('intake') or {}).get('kind') == 'user_hypothetical':
            calculation_path, calculations = archived('calculations')
            historical.artifacts['script_calculations'] = calculations
        plan = image_selection_plan(historical)
        scene = next((row for row in plan['scenes'] if row['scene_id'] == scene_id), None)
        needs = _needs_for_scene(scene) if scene else []
        if len(needs) != 1:
            raise ProductionBlocked('Historical Flux physical scene cannot be reconstructed')
        old_need = needs[0]
        if (not old_need.get('asset_subject') or old_need.get('type') != 'background'
                or any(old_need.get(key) != need.get(key) for key in ('type', 'asset_subject', 'illustration_function'))):
            raise ProductionBlocked('Replacement differs from the historical Flux physical function/subject')
        base = _verify_flux_trial_record(state, historical, scene_id, old_need, lane, base_rows[0])
        if base is None or base['digest'] != base_rows[0]['digest']:
            raise ProductionBlocked('Historical Flux parent is missing')
        lineage = [(base, root / 'flux_trial' / scene_id / 'authorization.json')]
        refinement_rows = records[refinement.TABLE]
        style_rows = records[style.TABLE]
        if refinement_rows:
            parent_values = refinement._verified_parent_evidence(
                settings, state, historical, scene_id, base, base['digest'])
            refined = refinement._verify_refinement_record(
                state, historical, scene_id, old_need, lane, base['digest'], refinement_rows[0], parent_values)
            lineage.append((refined, root / 'flux_refinements' / scene_id / refined['digest'][:16] / 'authorization.json'))
        elif style_rows:
            raise ProductionBlocked('Historical Flux style has no registered refinement parent')
        if style_rows:
            parents = style._plain_refinement_evidence(
                settings, store, state, historical, scene_id, base, refined, refined['digest'])
            styled = style._verify_style_record(
                state, historical, scene_id, old_need, lane, refined['digest'], style_rows[0], parents)
            lineage.append((styled, root / 'flux_style_candidates' / scene_id / styled['digest'][:16] / 'authorization.json'))
        expected_stages = {prefix + receipt['digest'][:16] for receipt, _ in lineage}
        if any(row['stage'] not in expected_stages for row in checkpoints):
            raise ProductionBlocked('Unexpected historical Flux dispatch checkpoint requires reconciliation')
        brain = WealthBlueprintAssetBrain(settings)
        proofs = []
        for receipt, receipt_path in lineage:
            stage = prefix + receipt['digest'][:16]
            row = next((entry for entry in checkpoints if entry['stage'] == stage), None)
            folder = root / 'assets/image_local/flux_trial' / receipt['digest'][:16]
            if row is None:
                if folder.exists() and any(folder.iterdir()):
                    raise ProductionBlocked('Historical Flux execution files lack their normal checkpoint')
                terminal = {'status': 'not_dispatched', 'flux_retry_permitted': False}
            else:
                if row.get('attempts') != 1 or row['status'] not in {'done', 'failed'}:
                    raise ProductionBlocked('Historical Flux outcome is pending, running or unknown')
                if row['status'] == 'failed':
                    fallback = verify_failed_flux_allows_local_comfy_fallback(
                        historical, scene_id, receipt, row, read_only=True)
                    if not fallback:
                        raise ProductionBlocked('Historical Flux failure has no existing verified fallback')
                    terminal = {'status': 'verified_existing_fallback', 'fallback_digest': fallback['digest'],
                        'fallback_path': fallback['evidence_path'],
                        'fallback_sha256': file_hash(Path(fallback['evidence_path']))}
                else:
                    if (not row.get('files') or not store._valid_files(root, row['files_json'])
                            or row['output'].get('trial_digest') != receipt['digest']):
                        raise ProductionBlocked('Historical completed Flux checkpoint is missing or changed')
                    asset = next((a for a in brain.catalog.assets if a.id == row['output'].get('asset_id')), None)
                    if (asset is None or asset.source != 'comfyui-flux-trial'
                            or Path(asset.file_path).resolve() != Path(row['output'].get('output_path', '')).resolve()):
                        raise ProductionBlocked('Historical Flux result has no matching source asset')
                    if receipt['version'] == style.VERSION:
                        style.verify_flux_style_asset(asset, receipt)
                    elif receipt['version'] == refinement.VERSION:
                        refinement.verify_flux_refinement_asset(asset, receipt)
                    else:
                        verify_flux_asset(asset, receipt)
                    if proven_rejection_count(historical, brain, [asset]) != 1:
                        raise ProductionBlocked('Historical Flux result lacks its intact pixel rejection')
                    key = str(asset.reviewed_by).split(':', 1)[1]
                    review_path = root / 'assets/automatic-review' / key / 'review.json'
                    terminal = {'status': 'verified_rejected_result', 'asset_id': asset.id,
                        'image_sha256': file_hash(brain.resolve_file(asset)),
                        'review_path': str(review_path), 'review_sha256': file_hash(review_path)}
                terminal['runtime'] = _conditioning_history_process_absence(historical, receipt, row)
            proofs.append({'digest': receipt['digest'], 'version': receipt['version'],
                'receipt_path': str(receipt_path), 'receipt_sha256': file_hash(receipt_path), 'stage': stage,
                'checkpoint_hash': content_hash(row) if row else None, 'terminal_proof': terminal})
        after = store.get(context.job_id)
        if (records != _conditioning_history_rows(settings, context.job_id, scene_id)
                or content_hash(checkpoints) != content_hash([r for r in after['checkpoints'] if r['stage'].startswith(prefix)])
                or file_hash(script_path) != binding['script_sha256']
                or calculation_path and file_hash(calculation_path) != binding['calculations_sha256']):
            raise ProductionBlocked('Historical Flux evidence changed during inspection')
        payload = {'version': 'wb-flux-conditioning-replacement-history-v1', 'job_id': context.job_id,
            'scene_id': scene_id, 'local_lane_digest': lane['digest'], 'current_need_hash': content_hash(need),
            'historical_script_path': str(script_path), 'historical_script_sha256': binding['script_sha256'],
            'historical_script_hash': content_hash(old_script), 'historical_need_hash': content_hash(old_need),
            'lineage_digests': [receipt['digest'] for receipt, _ in lineage], 'lineage': proofs,
            'flux_retry_permitted': False, 'new_generation_authorized': False}
        if calculation_path:
            payload.update(historical_calculations_path=str(calculation_path),
                           historical_calculations_sha256=binding['calculations_sha256'])
        return {**payload, 'digest': content_hash(payload)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, psutil.AccessDenied) as exc:
        raise ProductionBlocked('Historical Flux conditioning replacement proof is incomplete or changed') from exc


def verify_flux_asset(asset, receipt):
    if not asset.generation or not asset.generation.evidence_path:
        raise ProductionBlocked('Flux candidate provenance is missing')
    evidence = _on_e(Path(asset.generation.evidence_path))
    source = _on_e(Path(asset.file_path))
    if not evidence.is_file() or not source.is_file():
        raise ProductionBlocked('Flux candidate evidence is missing')
    data = json.loads(evidence.read_text('utf-8'))
    graph = evidence.parent / 'workflow.json'
    history = evidence.parent / 'history.json'
    if (data.get('trial_digest') != receipt['digest'] or data.get('workflow_sha256') != receipt['workflow_sha256']
            or data.get('image_sha256') != file_hash(source) or not graph.is_file() or not history.is_file()
            or content_hash(json.loads(graph.read_text('utf-8'))) != receipt['workflow_sha256']
            or file_hash(history) != data.get('history_sha256') or data.get('user_approved') is not False):
        raise ProductionBlocked('Flux candidate/cache integrity failed')


class _FixedEndpoint:
    def __init__(self, session):
        self.session = session
    @property
    def base_url(self):
        if not self.session._owns_listener():
            exit_code = self.session.process.poll() if self.session.process is not None else None
            if exit_code is not None:
                raise FluxTrialStopped(f'Cold Flux native runtime exit (code {exit_code}); no second POST permitted')
            raise FluxTrialStopped('Cold Flux listener identity is no longer owned')
        return self.session.base_url
    def _server_ready(self):
        try:
            return httpx.get(self.base_url + '/system_stats', timeout=3).is_success
        except (httpx.HTTPError, FluxTrialStopped):
            return False


class _TrialImageService(ComfyUiImageGenerationService):
    def __init__(self, settings, receipt):
        super().__init__(settings)
        self.receipt = receipt

    def _queue_prompt(self, workflow):
        # Stable ownership evidence in queue.extra_data, without an internal
        # transport retry or invocation of the global backend selector.
        with httpx.Client(timeout=self.settings.comfyui_health_timeout_s * 5) as client:
            response = client.post(self.runtime.base_url + '/prompt', json={
                'prompt': workflow, 'client_id': 'atnos-wb-flux-' + self.receipt['digest']})
            response.raise_for_status()
            prompt_id = str(response.json().get('prompt_id') or '').strip()
        if not prompt_id:
            raise FluxTrialStopped('Cold Flux did not confirm prompt_id')
        return prompt_id


class ColdFluxSession:
    """A unique managed child; no candidate discovery, model switching or retries."""
    def __init__(self, settings, receipt, folder):
        self.settings, self.receipt, self.folder = settings, receipt, _on_e(folder)
        self.process = None
        self.marker = self.folder / 'instance.json'
        self.base_url = ''
        self.snapshot = {}

    def _request(self, path):
        with httpx.Client(timeout=5) as client:
            response = client.get(self.base_url + path)
            response.raise_for_status()
            return response.json()

    @contextmanager
    def open(self):
        from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
        from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
        from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
        from atnos.services.hardware_guard_service import HardwareGuardService
        HardwareGuardService.require_gpu('WB Flux style trial')
        lock = GpuRuntimeLockService(self.settings)
        lease = lock.acquire(owner=f"wb-flux-trial:{self.receipt['job_id']}:{self.receipt['scene_id']}", timeout_s=600)
        try:
            ProductionGpuResidencyService(self.settings).prepare_for_comfy()
            primary = ComfyUiRuntimeService(self.settings)
            stopped = primary.stop_server(require_idle=True)
            if not stopped.get('stopped'):
                raise FluxTrialStopped('Own Comfy session is busy or ownership is unknown; cold trial aborted')
            if primary._matching_install_processes():
                raise FluxTrialStopped('Another Comfy process remains; cold trial aborted without stopping it')
            if psutil.virtual_memory().available < MIN_RAM:
                raise FluxTrialStopped('Flux trial requires at least 16 GiB available RAM; no POST made')
            try:
                self._launch()
                self._preflight()
                yield self
            finally:
                try:
                    self._stop_own()
                except Exception as exc:
                    write_json(self.folder / 'cleanup.json', {'stopped': False,
                        'error_type': type(exc).__name__, 'error': redact_secrets(str(exc))[:1500],
                        'trial_digest': self.receipt['digest'], 'automatic_force_stop': False})
                    raise FluxTrialStopped('Cold Flux cleanup deferred; process/queue evidence requires inspection') from exc
        finally:
            lock.release(lease)

    def _launch(self):
        # Fixed port, exclusive binding check, unique marker. Never discover/reuse
        # another listener or overwrite the normal Comfy marker.
        port = 8195
        with socket.socket() as check:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            check.bind(('127.0.0.1', port))
        self.base_url = f'http://127.0.0.1:{port}'
        files = self.receipt['runtime_files']
        python, main = Path(files['python']['path']), Path(files['main']['path'])
        self.folder.mkdir(parents=True, exist_ok=True)
        for name in ('input', 'output', 'temp', 'user'):
            (self.folder / name).mkdir(exist_ok=True)
        # Empty base model directories + only the receipt's exact model roots
        # avoid an identically named model in another installed mapping.
        scoped_models = self.folder / 'model-paths.yaml'
        lines = ['atnos_flux_trial:', '  is_default: true']
        for key, (category, _) in MODEL_NAMES.items():
            root = _on_e(Path(self.receipt['models'][key]['path']).parent)
            lines.append(f'  {category}: {json.dumps(root.as_posix())}')
        if 'minote_lora' in self.receipt['models']:
            # This optional adapter belongs only to this pinned scene receipt.
            # Do not expose other installations or change global model routing.
            root = _on_e(Path(self.receipt['models']['minote_lora']['path']).parent)
            lines.append(f'  loras: {json.dumps(root.as_posix())}')
        scoped_models.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        argv = [str(python), '-s', str(main), '--listen', '127.0.0.1', '--port', str(port),
                '--fp16-unet', '--cpu-vae', '--lowvram', '--disable-smart-memory',
                '--disable-all-custom-nodes', '--disable-async-offload', '--disable-dynamic-vram',
                '--disable-mmap', '--cache-classic', '--reserve-vram', '2', '--disable-auto-launch',
                '--base-directory', str(self.folder), '--extra-model-paths-config', str(scoped_models)]
        for name in ('input', 'output', 'temp', 'user'):
            argv.extend([f'--{name}-directory', str(self.folder / name)])
        env = os.environ.copy()
        profile = Path('E:/Atnos/Profiles/AtnosSystem')
        env.update(USERPROFILE=str(profile), APPDATA=str(profile / 'AppData/Roaming'),
                   LOCALAPPDATA=str(profile / 'AppData/Local'),
                   TEMP=str(self.folder / 'temp'), TMP=str(self.folder / 'temp'),
                   HF_HOME=str(self.settings.data_dir / 'tool-caches/huggingface'),
                   XDG_CACHE_HOME=str(self.settings.data_dir / 'tool-caches/xdg'),
                   TORCH_HOME=str(self.settings.data_dir / 'tool-caches/torch'))
        with (self.folder / 'stdout.log').open('ab') as out, (self.folder / 'stderr.log').open('ab') as err:
            self.process = subprocess.Popen(argv, cwd=main.parent, env=env, stdout=out, stderr=err,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            process = psutil.Process(self.process.pid)
            started = process.create_time()
        except psutil.Error as exc:
            # Popen holds the exact just-created Windows process handle; this
            # cleanup never enumerates or guesses a PID belonging to others.
            self.process.terminate()
            self.process.wait(timeout=15)
            raise FluxTrialStopped('Cannot persist cold process identity; no POST made') from exc
        self.identity = {'pid': process.pid, 'created': started, 'argv': argv,
                         'cwd': str(main.parent), 'port': port, 'marker': VERSION,
                         'exe': process.exe()}
        self.launcher_identity = dict(self.identity)
        write_json(self.marker, {**self.identity, 'status': 'STARTING', 'trial_digest': self.receipt['digest']})

    @staticmethod
    def _identity_matches(process, identity):
        return (abs(process.create_time() - identity['created']) < .01
                and process.cmdline() == identity['argv']
                and Path(process.exe()).resolve() == Path(identity.get('exe', identity['argv'][0])).resolve()
                and Path(process.cwd()).resolve() == Path(identity['cwd']).resolve())

    def _reconcile_listener(self, listeners):
        """Accept only the exact venv child of the saved Popen launcher."""
        if len(listeners) != 1 or not getattr(self, 'launcher_identity', None):
            return False
        launcher = psutil.Process(self.launcher_identity['pid'])
        if not self._identity_matches(launcher, self.launcher_identity):
            return False
        pid = next(iter(listeners))
        if pid not in {p.pid for p in launcher.children(recursive=True)}:
            return False
        process = psutil.Process(pid)
        argv = process.cmdline()
        allowed = {Path(item['path']).resolve() for key, item in self.receipt['runtime_files'].items()
                   if key in {'python', 'base_python'}}
        if (not argv or Path(process.exe()).resolve() not in allowed
                or Path(argv[0]).resolve() not in allowed
                or argv[1:] != self.launcher_identity['argv'][1:]
                or Path(process.cwd()).resolve() != Path(self.launcher_identity['cwd']).resolve()
                or process.create_time() < self.launcher_identity['created']):
            return False
        self.identity = {**self.launcher_identity, 'pid': pid, 'created': process.create_time(),
                         'argv': argv, 'exe': process.exe(), 'launcher_pid': launcher.pid,
                         'launcher_created': self.launcher_identity['created']}
        write_json(self.marker, {**self.identity, 'status': 'STARTING', 'trial_digest': self.receipt['digest']})
        return True

    def _owns_listener(self):
        if self.process is None:
            return False
        try:
            listeners = {c.pid for c in psutil.net_connections(kind='tcp')
                         if c.status == psutil.CONN_LISTEN and c.laddr.port == self.identity['port']}
            process = psutil.Process(self.identity['pid'])
            if self._identity_matches(process, self.identity) and listeners == {process.pid}:
                return True
            return self._reconcile_listener(listeners)
        except psutil.NoSuchProcess:
            return False

    def _preflight(self):
        deadline = monotonic() + 120
        while monotonic() < deadline:
            if self.process.poll() is not None:
                raise FluxTrialStopped('Flux cold runtime exited before readiness; no POST made')
            try:
                if self._owns_listener():
                    stats = self._request('/system_stats')
                    nodes = self._request('/object_info')
                    queue = self._request('/queue')
                    break
            except (httpx.HTTPError, psutil.Error):
                pass
            sleep(1)
        else:
            raise FluxTrialStopped('Flux cold readiness deadline exceeded; no POST made')
        if queue.get('queue_running') != [] or queue.get('queue_pending') != []:
            raise FluxTrialStopped('Flux trial queue is not empty; no POST made')
        required = {row['class_type'] for row in self.receipt['workflow'].values()}
        if not required.issubset(nodes):
            raise FluxTrialStopped('Flux native nodes are missing; no POST made')
        for cls, key, expected in [('UNETLoader', 'unet_name', MODEL_NAMES['unet'][1]),
                                   ('CLIPLoader', 'clip_name', MODEL_NAMES['clip'][1]),
                                   ('VAELoader', 'vae_name', MODEL_NAMES['vae'][1])]:
            if expected not in nodes[cls]['input']['required'][key][0]:
                raise FluxTrialStopped('Flux exact model is not exposed; no POST made')
        if 'minote_lora' in self.receipt['models']:
            expected = Path(self.receipt['models']['minote_lora']['path']).name
            if expected not in nodes['LoraLoaderModelOnly']['input']['required']['lora_name'][0]:
                raise FluxTrialStopped('Flux exact style adapter is not exposed; no POST made')
        if ('flux2' not in nodes['CLIPLoader']['input']['required']['type'][0]
                or 'cpu' not in nodes['CLIPLoader']['input']['optional']['device'][0]):
            raise FluxTrialStopped('Flux CPU encoder schema is unavailable; no POST made')
        devices = stats.get('devices', [])
        if not any(int(item.get('vram_free', 0)) >= MIN_VRAM for item in devices):
            raise FluxTrialStopped('Flux trial requires at least 12 GiB free VRAM; no POST made')
        self.snapshot = stats
        write_json(self.folder / 'runtime.json', {'identity': self.identity, 'system_stats': stats,
                   'nodes': sorted(required), 'models': self.receipt['models'], 'trial_digest': self.receipt['digest'],
                   'isolated_model_config_sha256': file_hash(self.folder / 'model-paths.yaml')})
        write_json(self.marker, {**self.identity, 'status': 'READY', 'trial_digest': self.receipt['digest']})

    def _stop_own(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            process = psutil.Process(self.identity['pid'])
            if not self._identity_matches(process, self.identity):
                raise FluxTrialStopped('Flux trial process ownership changed; no process stopped')
            queue = self._request('/queue')
            running, pending = queue.get('queue_running'), queue.get('queue_pending')
            if not isinstance(running, list) or not isinstance(pending, list):
                raise FluxTrialStopped('Cold Flux queue ownership is unknown; no process stopped')
            for row in [*running, *pending]:
                if (not isinstance(row, list) or len(row) < 4 or not isinstance(row[3], dict)
                        or row[3].get('client_id') != 'atnos-wb-flux-' + self.receipt['digest']
                        or content_hash(row[2]) != self.receipt['workflow_sha256']):
                    raise FluxTrialStopped('Another request uses the cold Flux session; no process stopped')
            # Only the child launched for this one bounded operation is cancelled;
            # no /free, process-tree kill or native model switch is used.
            process.terminate()
            process.wait(timeout=15)
            write_json(self.marker, {**self.identity, 'status': 'STOPPED', 'trial_digest': self.receipt['digest']})
        except psutil.NoSuchProcess:
            return


def _submit_and_wait_once(service, receipt, folder) -> tuple[bytes, str, dict]:
    """Durable submission intent is written before the only possible POST."""
    dispatch = folder / 'dispatch.json'
    if dispatch.exists():
        raise FluxTrialStopped('Flux trial already dispatched or outcome uncertain; no second POST')
    runtime = folder / 'runtime.json'
    if not runtime.is_file():
        raise FluxTrialStopped('Missing runtime identity snapshot; no POST made')
    identity_sha256 = content_hash(json.loads(runtime.read_text('utf8'))['identity'])
    # Atomic O_EXCL is also a fail-closed second line of protection if callers
    # accidentally bypass the normal order lease. Do not overwrite intent.
    with dispatch.open('x', encoding='utf-8') as handle:
        json.dump({'status': 'dispatch_intent', 'trial_digest': receipt['digest'],
                   'workflow_sha256': receipt['workflow_sha256'], 'instance_sha256':identity_sha256,
                   'created_at': utc_now_iso()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        prompt_id = service._queue_prompt(receipt['workflow'])
        write_json(dispatch, {'status': 'queued', 'prompt_id': prompt_id, 'trial_digest': receipt['digest'],
                             'workflow_sha256': receipt['workflow_sha256'], 'instance_sha256':identity_sha256})
        meta = service._wait_for_image(prompt_id, timeout_s=1200)
        with httpx.Client(timeout=10) as client:
            response = client.get(service.runtime.base_url + '/history/' + prompt_id)
            response.raise_for_status()
            history = response.json()
        record = history.get(prompt_id)
        if not record or not record.get('status', {}).get('completed') or record.get('status', {}).get('status_str') != 'success':
            raise FluxTrialStopped('Flux history did not confirm this prompt completed successfully')
        image = service._download_image(meta)
        service._validate_image_bytes(image, expected_width=768, expected_height=1344)
        return image, prompt_id, history
    except Exception as exc:
        write_json(folder / 'failure.json', {'status': 'stopped_nonretryable', 'error_type': type(exc).__name__,
                   'error': redact_secrets(str(exc))[:1500],
                   'trial_digest': receipt['digest'], 'dispatch_preserved': True, 'automatic_retry': False})
        raise FluxTrialStopped('Flux trial stopped; outcome evidence preserved and no second POST permitted') from exc


def generate_flux_candidate(settings, context, need, style, tags, lane, fingerprint, receipt):
    from atnos.production.asset_brain.approval import AssetApprovalService
    from atnos.production.asset_brain.models import AssetGenerationProvenance, AssetRecord
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    folder = _on_e(context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16])
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'dispatch.json').exists():
        raise FluxTrialStopped('Flux trial was already dispatched; operator must inspect its evidence')
    _check_manifest(receipt['models'])
    _check_manifest(receipt['runtime_files'])
    graph_path = write_json(folder / 'workflow.json', receipt['workflow']).artifact_path
    # No route through generate_background: no optional backend auto-selection,
    # internal retries, fallback or global backend-health mutation.
    with ColdFluxSession(settings, receipt, folder).open() as runtime:
        service = _TrialImageService(settings, receipt)
        service.runtime = _FixedEndpoint(runtime)
        image_bytes, prompt_id, history = _submit_and_wait_once(service, receipt, folder)
    target = folder / 'image.png'
    target.write_bytes(image_bytes)
    history_path = write_json(folder / 'history.json', history).artifact_path
    actual_negative = receipt['workflow']['5']['inputs']['text']
    evidence = {'provider': 'comfyui-local', 'backend': 'flux2_klein_scoped_trial',
        'trial_digest': receipt['digest'], 'authorization_digest': lane['digest'],
        'models': receipt['models'], 'runtime_files': receipt['runtime_files'],
        'workflow_sha256': receipt['workflow_sha256'], 'history_sha256': file_hash(history_path),
        'prompt': receipt['workflow']['4']['inputs']['text'], 'negative_prompt': actual_negative,
        'prompt_id': prompt_id, 'image_sha256': file_hash(target), 'width': 768, 'height': 1344,
        'seed': receipt['seed'], 'unet_dtype_requested': 'fp16', 'clip_device_requested': 'cpu', 'vae_device_requested': 'cpu',
        'dtype_observed': 'not_independently_verified; inspect runtime log',
        'technical_generation': 'passed', 'visual_review': 'pending', 'user_approved': False,
        'visual_style_id': style['id'], 'visual_style_definition_hash': style['definition_hash'],
        'request_fingerprint': fingerprint, 'cost_note': 'Local one-shot; no API reservation',
        'global_backend_verification': 'unchanged'}
    metadata = write_json(folder / 'metadata.json', evidence).artifact_path
    asset = AssetRecord(id=f"flux_trial_wb_{style['id'].lower()}_{receipt['digest'][:16]}",
        type='background', channel='wealth_blueprint', production_role='background', file_path=str(target),
        width=768, height=1344, tags=[*tags, *need.get('tags', [])], source='comfyui-flux-trial',
        description=need['asset_subject'], license_status='generated_pending_review', request_fingerprint=fingerprint,
        generation=AssetGenerationProvenance(prompt=evidence['prompt'], negative_prompt=actual_negative,
            workflow_id=VERSION, model=MODEL_NAMES['unet'][1], generated_at=utc_now_iso(), source_workflow=VERSION,
            channel='wealth_blueprint', fingerprint=fingerprint, seed=receipt['seed'], comfy_prompt_id=prompt_id,
            backend='flux2_klein_scoped_trial', evidence_path=str(metadata)))
    record = AssetApprovalService(WealthBlueprintAssetBrain(settings)).ingest_generated(asset)
    payload = {**evidence, 'asset_id': record.id, 'quality_status': record.quality_status, 'output_path': str(target)}
    report = write_json(folder / 'candidate.json', payload)
    paths = [target, metadata, graph_path, history_path, folder / 'dispatch.json', folder / 'runtime.json',
             folder / 'instance.json', folder / 'model-paths.yaml']
    paths.extend(path for name in ('stdout.log', 'stderr.log') if (path := folder / name).is_file())
    return StepResult(report.artifact_name, report.artifact_path, payload, report.message, tuple(paths))
