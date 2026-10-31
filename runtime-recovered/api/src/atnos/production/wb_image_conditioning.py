"""Append-only conditioning of an existing local lane, never permission to POST.

The base lane, previous Flux receipts and budgets remain immutable. Only a
separately authorized bounded dispatch action can acquire a new conditioned image.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from atnos.core.logging import redact_secrets
from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.dispatch_lock import dispatch_lock
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, _event, _same_process, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json

VERSION = 'wb-local-conditioning-amendment-v1'
TABLE = 'production_image_conditioning_amendments'
RECEIPT = 'image_conditioning_amendment.json'
MEDIA = {'voice','audio','captions','edit','render','remotion_render','mux','package'}


def _row(db, job_id):
    try:
        return db.execute(f'SELECT payload_json,digest FROM {TABLE} WHERE job_id=?',(job_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc): raise
        return None


def _history(value, root):
    if value is None: return None
    allowed={'script_path','script_sha256','calculations_path','calculations_sha256'}
    if (not isinstance(value,dict) or set(value)-allowed
            or not {'script_path','script_sha256'} <= set(value)
            or ('calculations_path' in value) != ('calculations_sha256' in value)):
        raise ValueError('Conditioning history requires explicit archived file/hash pairs')
    result={}
    for name in ('script','calculations'):
        if name+'_path' not in value: continue
        path=Path(value[name+'_path']).resolve()
        digest=value[name+'_sha256']
        if (path.drive.casefold()!='e:' or not path.is_relative_to(root.resolve()) or not path.is_file()
                or not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest)
                or file_hash(path)!=digest):
            raise ValueError('Conditioning history must be intact archived files under this order on E:')
        result[name+'_path']=str(path)
        result[name+'_sha256']=digest
    return result


def _checkpoint(store,state,name,filename):
    row=next((r for r in state['checkpoints'] if r['stage']==name and r['status']=='done'),None)
    root=Path(state['artifact_dir'])
    path=root/filename
    if (not row or not store._valid_files(root,row['files_json']) or not path.is_file()
            or not any((root/entry['path']).resolve()==path.resolve() for entry in row['files'])
            or json.loads(path.read_text('utf8'))!=row['output']):
        raise ProductionBlocked('Conditioning requires intact normal canonical/plan checkpoints')
    return row


def _inputs(settings,job_id,*,scene_id,binding,seed,history):
    from atnos.production.wb_image_api import (load_image_lane, _composition_bindings,
        _local_generator_identity, image_selection_plan, _preflight_image_plan)
    from atnos.production.studio_script_recovery import verify_finalized_script_acceptance
    from atnos.production.wb_local_flux_trial import verify_conditioning_replacement_history
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',str(scene_id)):
        raise ValueError('Conditioning scene ID is invalid')
    # Both OrderAssetRequest and Comfy GenerationRequest use this seed range.
    if type(seed) is not int or not 0<=seed<=2_147_483_647:
        raise ValueError('Conditioning seed must be an explicit supported integer')
    store=ProductionOrderStore(settings)
    state=store.get(job_id)
    root=Path(state['artifact_dir']).resolve()
    base=load_image_lane(settings,job_id)
    if (root.drive.casefold()!='e:' or state['request'].get('project')!='wealth_blueprint'
            or state['request'].get('delivery_mode')!='local_only' or not base
            or base.get('provider')!='comfyui-local' or base.get('reservation_usd_per_request')!=0
            or base.get('version')!='wb-image-local-v1' or 'composition_references' in base):
        raise OrderConflict('Conditioning is restricted to an existing zero-cost local WB lane on E:')
    script_row=_checkpoint(store,state,'script','script.json')
    plan_row=_checkpoint(store,state,'asset_planning','scene_plan.json')
    script,film_plan=script_row['output'],plan_row['output']
    acceptance=verify_finalized_script_acceptance(store,job_id,script=script)
    segments=script['segments']
    scenes=film_plan.get('scenes',[])
    if (len(scenes)!=len(segments) or film_plan.get('claims')!=script.get('claims',[])
            or any(scene.get('segment_id')!=segment['id'] or scene.get('narration')!=segment['text']
                   or scene.get('headline')!=segment['on_screen'] for scene,segment in zip(scenes,segments))):
        raise ProductionBlocked('Conditioning plan does not describe the accepted current canonical')
    context=store.context(job_id)
    context.artifacts['script']=script
    calculations=next((r for r in state['checkpoints'] if r['stage']=='script_calculations' and r['status']=='done'),None)
    if calculations:
        if not store._valid_files(root,calculations['files_json']):
            raise ProductionBlocked('Conditioning calculation evidence changed')
        context.artifacts['script_calculations']=calculations['output']
    normalized=_composition_bindings([binding])[0]
    plan=image_selection_plan(context)
    needs=_preflight_image_plan(plan)
    target=next(((scene,needs[index]) for index,scene in enumerate(plan['scenes']) if scene['scene_id']==scene_id),None)
    if not target or target[1][0] or target[1][1] is None:
        raise OrderConflict('Conditioning target is not a current physical image scene')
    need=target[1][1]
    if (need.get('type')!='background' or need.get('asset_subject')!=normalized['asset_subject']
            or need.get('illustration_function')!=normalized['illustration_function']):
        raise OrderConflict('Conditioning reference does not match the current semantic subject/function')
    archived=_history(history,root)
    if archived: context.artifacts['conditioning_replacement_history']=archived
    prior=verify_conditioning_replacement_history(settings,context,scene_id,need,base)
    values={'base_lane_digest':base['digest'],'request_hash':content_hash(state['request']),
        'profile_hash':state['profile_hash'],'canonical_sha256':file_hash(root/'script.json'),
        'plan_sha256':file_hash(root/'scene_plan.json'),'semantic_plan_hash':content_hash(plan),
        'semantic_need_hash':content_hash(need),'scene_id':scene_id,'binding':normalized,'seed':seed,
        'history':archived,'prior_flux_proof':prior,'generator_identity':_local_generator_identity(settings),
        'script_acceptance':acceptance,
        'checkpoint_bindings':{r['stage']:{'input_hash':r['input_hash'], 'output_hash':content_hash(r['output']),
                                        'files_json':r['files_json']} for r in (script_row,plan_row)}}
    return store,state,values


def _stopped(db,job_id,values):
    current=db.execute('SELECT j.status,o.lease_pid,o.lease_process_started,o.request_json,o.profile_json '
        'FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?',(job_id,)).fetchone()
    if (not current or current['status'] not in {'queued','blocked'}
            or _same_process(current['lease_pid'],current['lease_process_started'])):
        raise OrderConflict('Conditioning registration requires a stopped order; active work raced admission')
    if (content_hash(json.loads(current['request_json']))!=values['request_hash']
            or content_hash(json.loads(current['profile_json']))!=values['profile_hash']):
        raise OrderConflict('Conditioning registration raced a changed order')
    rows=db.execute("SELECT stage,status FROM pipeline_stage_checkpoints WHERE job_id=? AND status IN ('running','done')",
                    (job_id,)).fetchall()
    if any(row['stage'].split('/')[0] in MEDIA for row in rows):
        raise OrderConflict('Conditioning registration must precede active/completed audiovisual media')
    for stage,expected in values['checkpoint_bindings'].items():
        row=db.execute('SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?',(job_id,stage)).fetchone()
        if (not row or row['status']!='done' or row['input_hash']!=expected['input_hash']
                or row['files_json']!=expected['files_json'] or content_hash(json.loads(row['output_json']))!=expected['output_hash']):
            raise OrderConflict('Conditioning registration raced updated canonical/plan checkpoints')
    try:
        action=db.execute('SELECT digest FROM production_local_image_actions WHERE job_id=?',(job_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):raise
        action=None
    return action


def register_conditioning_amendment(settings,job_id,*,scene_id,binding,seed,
        expected_canonical_sha256,expected_plan_sha256,reason,authorized=False,history=None):
    if not authorized:
        raise ValueError('Explicit layout-conditioning registration choice is required; it is not generation consent')
    if not isinstance(reason,str) or len(reason.strip())<10 or len(reason)>800:
        raise ValueError('Conditioning registration requires a concrete bounded reason')
    store=ProductionOrderStore(settings)
    with dispatch_lock(store.context(job_id).artifact_dir/'worker_logs/dispatch.lock'):
        store,state,values=_inputs(settings,job_id,scene_id=scene_id,binding=binding,seed=seed,history=history)
        if (values['canonical_sha256']!=expected_canonical_sha256 or values['plan_sha256']!=expected_plan_sha256):
            raise OrderConflict('Conditioning expected canonical/plan hashes changed')
        payload={'version':VERSION,'job_id':job_id,**values,'reason':redact_secrets(reason.strip()),
                 'generation_authorized':False,'appearance_approval':'pending'}
        digest=content_hash(payload)
        root=Path(state['artifact_dir'])
        receipt=root/RECEIPT
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            action=_stopped(db,job_id,values)
            existing=_row(db,job_id)
            if existing:
                if existing['digest']!=digest:
                    raise OrderConflict('Conditioning amendment is immutable; history and action budget cannot reset')
                return load_conditioning_amendment(settings,store.context(job_id))
            if action:
                raise OrderConflict('Conditioning cannot replace an already registered dispatch action')
            if receipt.exists():
                raise ProductionBlocked('Unregistered conditioning receipt exists; inspect before proceeding')
            # Recheck physical inputs at commit, after the immutable proof was built.
            if (file_hash(root/'script.json')!=values['canonical_sha256'] or file_hash(root/'scene_plan.json')!=values['plan_sha256']
                    or file_hash(Path(values['binding']['reference_path']))!=values['binding']['reference_sha256']
                    or file_hash(Path(values['binding']['approval_receipt_path']))!=values['binding']['approval_receipt_sha256']):
                raise OrderConflict('Conditioning files changed during registration')
            db.execute(f'CREATE TABLE IF NOT EXISTS {TABLE}(job_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,digest TEXT NOT NULL,created_at TEXT NOT NULL)')
            write_json(receipt,{**payload,'digest':digest})
            db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?)',(job_id,json_dumps(payload),digest,utc_now_iso()))
            _event(db,job_id,None,'local_image_conditioning_registered',{'digest':digest,'scene_id':scene_id,
                    'base_lane_digest':values['base_lane_digest'],'generation_authorized':False})
            db.commit()
        return {**payload,'digest':digest}


def load_conditioning_amendment(settings,context):
    with connect(settings) as db:row=_row(db,context.job_id)
    receipt=context.artifact_dir/RECEIPT
    if row is None:
        if receipt.exists():raise ProductionBlocked('Unregistered conditioning receipt cannot be ignored')
        return None
    try:
        payload=json.loads(row['payload_json'])
        if (not receipt.is_file() or content_hash(payload)!=row['digest']
                or json.loads(receipt.read_text('utf8'))!={**payload,'digest':row['digest']}
                or payload['version']!=VERSION or payload['job_id']!=context.job_id
                or payload['generation_authorized'] is not False or payload['appearance_approval']!='pending'):
            raise ValueError('Immutable conditioning receipt changed')
        _,_,values=_inputs(settings,context.job_id,scene_id=payload['scene_id'],binding=payload['binding'],
                          seed=payload['seed'],history=payload['history'])
        expected={'version':VERSION,'job_id':context.job_id,**values,'reason':payload['reason'],
                  'generation_authorized':False,'appearance_approval':'pending'}
        if expected!=payload:raise ValueError('Conditioning inputs/provenance no longer match')
        return {**payload,'digest':row['digest']}
    except (OSError,ValueError,TypeError,KeyError,OrderConflict,ProductionBlocked) as exc:
        raise ProductionBlocked('Conditioning amendment integrity failed: '+str(exc)) from exc


def apply_conditioning_amendment(plan,scene_needs,amendment):
    if amendment is None:return scene_needs
    resolved=list(scene_needs)
    indexes=[index for index,scene in enumerate(plan['scenes']) if scene['scene_id']==amendment['scene_id']]
    if len(indexes)!=1:raise ProductionBlocked('Conditioning target scene is absent or duplicated')
    index=indexes[0]
    native,need=resolved[index]
    if native or need is None or content_hash(need)!=amendment['semantic_need_hash']:
        raise ProductionBlocked('Conditioning target need changed after planning')
    binding=amendment['binding']
    if (need.get('composition_reference_path') is not None
            and Path(need['composition_reference_path']).resolve()!=Path(binding['reference_path'])):
        raise ProductionBlocked('Conditioning conflicts with the existing explicit reference')
    if need.get('seed') is not None and need['seed']!=amendment['seed']:
        raise ProductionBlocked('Conditioning conflicts with the existing explicit seed')
    resolved[index]=(False,{**need,'seed':amendment['seed'],
        'composition_reference_path':binding['reference_path'],'composition_reference_sha256':binding['reference_sha256'],
        'composition_reference_purpose':'layout_only','composition_approval_sha256':binding['approval_receipt_sha256'],
        'conditioning_amendment_digest':amendment['digest']})
    return resolved


def require_conditioning_dispatch(action,amendment,scene_id):
    if (not action or action.get('conditioning_amendment_digest')!=amendment['digest']
            or action.get('conditioning_scene_id')!=scene_id or action.get('max_new_requests')!=1):
        raise ProductionBlocked('Conditioning requires separate bounded dispatch consent for one new request')


def verify_conditioning_dispatch_workflow(amendment,workflow,reference_upload):
    """Check the real upload and pixel route immediately before reserving POST."""
    try:
        binding=amendment['binding']
        graph=workflow
        sampler=graph['7']['inputs']
        if (not isinstance(reference_upload,dict)
                or Path(reference_upload['source_path']).resolve()!=Path(binding['reference_path'])
                or reference_upload['source_sha256']!=binding['reference_sha256']
                or file_hash(Path(binding['reference_path']))!=binding['reference_sha256']
                or not reference_upload['resolved_image_name']
                or graph['4']['class_type']!='LoadImage'
                or graph['4']['inputs']['image']!=reference_upload['resolved_image_name']
                or graph['5']['class_type']!='ImageScale' or graph['5']['inputs']['image']!=['4',0]
                or graph['6']['class_type']!='VAEEncode' or graph['6']['inputs']['pixels']!=['5',0]
                or graph['7']['class_type']!='KSampler' or sampler['latent_image']!=['6',0]
                or sampler['seed']!=amendment['seed']
                or not 0<float(sampler['denoise'])<1):
            raise ValueError('Reference upload, seed or pixel-conditioning route differs')
        expected_denoise=(amendment['generator_identity'].get('model_profile') or {}).get('reference_denoise')
        if expected_denoise is not None and abs(float(sampler['denoise'])-float(expected_denoise))>1e-9:
            raise ValueError('Conditioning denoise differs from the bound generator configuration')
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise ProductionBlocked('Conditioning POST cannot prove the bound pixel input: '+str(exc)) from exc
