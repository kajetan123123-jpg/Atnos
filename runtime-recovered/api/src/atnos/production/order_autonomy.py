"""Persisted opt-in recovery for local orders, using the normal dispatcher.

No counters, quality gates, frozen profiles or provider limits are reset here.
"""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from threading import Lock
from time import monotonic
from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production.dispatch_lock import dispatch_lock
from atnos.production.order_store import ProductionOrderStore, _event, file_hash
from atnos.production.order_dispatch import ProductionOrderDispatcher
from atnos.production.project_profile import content_hash

TABLE = 'production_order_autonomy'
logger = logging.getLogger(__name__)
_dependency_pool = None
_dependency_probes = {}
_dependency_probe_lock = Lock()


def _initialize(db):
    db.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE}(
        job_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, max_recoveries INTEGER NOT NULL,
        recoveries_used INTEGER NOT NULL DEFAULT 0, last_failure_hash TEXT,
        reason TEXT NOT NULL, updated_at TEXT NOT NULL)''')


def enable(settings, job_id, *, reason, max_recoveries=6):
    state = ProductionOrderStore(settings).get(job_id)
    if state['request'].get('delivery_mode') != 'local_only':
        raise ValueError('Autonomous recovery is restricted to local production; no publication')
    if type(max_recoveries) is not int or not 1 <= max_recoveries <= 40 or len(reason.strip()) < 20:
        raise ValueError('Recovery needs an explicit reason and a bounded limit')
    with connect(settings) as db:
        _initialize(db)
        db.execute(f'''INSERT INTO {TABLE}(job_id,enabled,max_recoveries,reason,updated_at)
            VALUES(?,1,?,?,?) ON CONFLICT(job_id) DO UPDATE SET enabled=1,
            max_recoveries=excluded.max_recoveries,reason=excluded.reason,updated_at=excluded.updated_at''',
            (job_id,max_recoveries,reason,utc_now_iso()))
        _event(db,job_id,'autonomy','autonomous_recovery_enabled',
               {'reason':reason,'max_recoveries':max_recoveries,'budgets_reset':False,'publish':False})
        db.commit()


def suspend(settings, job_id, *, reason):
    """Stop future autonomous recovery of one local order without touching its worker."""
    if not isinstance(reason, str) or len(reason.strip()) < 20:
        raise ValueError('Suspension needs an explicit reason')
    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    if state['request'].get('delivery_mode') != 'local_only':
        raise ValueError('Autonomous recovery is restricted to local production; no publication')
    # Recovery may register a repair while holding dispatch.lock itself. The
    # outer per-order lock serializes that whole recovery with suspension.
    with dispatch_lock(_order_lock_path(store, job_id, 'autonomy.lock')):
        with dispatch_lock(_order_lock_path(store, job_id, 'dispatch.lock')):
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
                    raise ValueError('Autonomous recovery policy not found')
                row = db.execute(f'SELECT * FROM {TABLE} WHERE job_id=?', (job_id,)).fetchone()
                if row is None:
                    raise ValueError('Autonomous recovery policy not found')
                if not row['enabled']:
                    return dict(row)
                db.execute(f'UPDATE {TABLE} SET enabled=0,updated_at=? WHERE job_id=?',
                           (utc_now_iso(),job_id))
                _event(db,job_id,'autonomy','autonomous_recovery_suspended',
                       {'reason':reason.strip(),'budgets_reset':False,'worker_terminated':False,'publish':False})
                db.commit()
                return dict(db.execute(f'SELECT * FROM {TABLE} WHERE job_id=?', (job_id,)).fetchone())


def policy(settings, job_id):
    with connect(settings) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)).fetchone():
            return None
        row = db.execute(f'SELECT * FROM {TABLE} WHERE job_id=?',(job_id,)).fetchone()
        return dict(row) if row else None


def _order_lock_path(store, job_id, name):
    return store.context(job_id).artifact_dir / 'worker_logs' / name


def _dispatch_if_enabled(settings, store, job_id):
    """Recheck the persisted opt-in under the same lock as worker launch."""
    with dispatch_lock(_order_lock_path(store, job_id, 'dispatch.lock')):
        current = policy(settings, job_id)
        if not current or not current['enabled']:
            return None
        return ProductionOrderDispatcher(settings)._dispatch_locked(job_id)


def recover_due(settings):
    """Reconcile eligible orders independently; one bad order cannot starve others."""
    store = ProductionOrderStore(settings)
    with connect(settings) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)).fetchone():
            return []
        jobs = [dict(row) for row in db.execute(f'SELECT * FROM {TABLE} WHERE enabled=1')]
    results = []
    for job in jobs:
        try:
            results.extend(_recover_job(settings,store,job))
        except Exception as exc:
            # Retain the failed order and all consumed accounting. Other eligible
            # orders still get their turn, including after a dispatcher failure.
            _record_monitor_failure(settings,job['job_id'],exc)
    return results


def _record_monitor_failure(settings, job_id, exc):
    from atnos.core.logging import redact_secrets
    detail={'error_type':type(exc).__name__,'message':redact_secrets(str(exc))[:600],
            'other_orders_continue':True,'budgets_reset':False}
    logger.warning('Autonomous recovery for %s deferred (%s); other orders continue',job_id,type(exc).__name__)
    try:
        with connect(settings) as db:
            if not db.execute('SELECT 1 FROM pipeline_jobs WHERE id=?',(job_id,)).fetchone():
                return
            previous=db.execute("SELECT detail_json FROM pipeline_job_events WHERE job_id=? AND event='autonomous_recovery_deferred' ORDER BY id DESC LIMIT 1",(job_id,)).fetchone()
            if not previous or json.loads(previous['detail_json'])!=detail:
                _event(db,job_id,'autonomy','autonomous_recovery_deferred',detail)
                db.commit()
    except Exception:
        # If the database itself is unavailable, logging remains enough to keep
        # the monitor alive; a diagnostics write must not abort the next order.
        logger.warning('Cannot persist deferred recovery diagnostic for %s',job_id)


def _completed_flux_evidence(store, state):
    """Permit cache reuse after a code fix, never another one-shot Flux POST.

    Reconstruct the same effective context the worker will use. A frozen receipt
    alone is insufficient: the completed image, workflow and review evidence
    must also remain intact. Every actual trial is checked, including rejected
    images whose truthful pixel verdict must survive this recovery.
    """
    from pathlib import Path
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.studio_script_recovery import load_recovery_request, apply_recovery_guidance
    from atnos.production.wb_image_api import load_image_lane, image_selection_plan, _preflight_image_plan
    from atnos.production.wb_local_flux_trial import load_flux_trial, verify_flux_asset
    from atnos.production.wb_image_review import proven_rejection_count, verify_qualification
    try:
        context=store.context(state['id'])
        required=['script','script_calculations'] if (state['request'].get('intake') or {}).get('kind')=='user_hypothetical' else ['script']
        for stage in required:
            row=next((row for row in state['checkpoints'] if row['stage']==stage and row['status']=='done'),None)
            if not row or not store._valid_files(context.artifact_dir,row['files_json']):
                return None
            context.artifacts[stage]=row['output']
        if context.artifacts['script'].get('qa',{}).get('status')!='passed':
            return None
        recovery=load_recovery_request(store,state['id'])
        if recovery:
            context.artifacts['studio_script_recovery']={**recovery,'digest':content_hash(recovery)}
            apply_recovery_guidance(context)
        lane=load_image_lane(store.settings,state['id'])
        if not lane or lane['provider']!='comfyui-local':
            return None
        plan=image_selection_plan(context)
        scene_needs=_preflight_image_plan(plan)
        brain=WealthBlueprintAssetBrain(store.settings)
        trials=[]
        for scene,(native_role,need) in zip(plan['scenes'],scene_needs):
            if native_role:
                continue
            receipt=load_flux_trial(store.settings,context,scene['scene_id'],need,lane)
            if receipt is None:
                continue
            stage=f"assets/image_local/flux2-trial/{scene['scene_id']}/{receipt['digest'][:16]}"
            checkpoint=next((row for row in state['checkpoints'] if row['stage']==stage),None)
            if (not checkpoint or checkpoint['status']!='done' or checkpoint['attempts']!=1
                    or not store._valid_files(context.artifact_dir,checkpoint['files_json'])):
                return None
            asset=next((asset for asset in brain.catalog.assets if asset.id==checkpoint['output'].get('asset_id')),None)
            if not asset or asset.source!='comfyui-flux-trial':
                return None
            verify_flux_asset(asset,receipt)
            if Path(asset.file_path).resolve()!=Path(checkpoint['output'].get('output_path','')).resolve():
                return None
            if asset.quality_status=='REJECTED':
                if not proven_rejection_count(context,brain,[asset]):
                    return None
            elif asset.is_production_selectable():
                verify_qualification(context,asset,brain.resolve_file(asset))
            elif asset.quality_status not in {'AWAITING_REVIEW','TECHNICALLY_VALID'}:
                return None
            trials.append({'scene_id':scene['scene_id'],'trial_digest':receipt['digest'],
                           'asset_id':asset.id,'quality_status':asset.quality_status,
                           'completed_attempts':checkpoint['attempts']})
        # A missing trial never turns a generic integrity error into a retry.
        return {'trials':trials,'reuse_only':True,'new_flux_requests':0} if trials else None
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):
        return None


def _probe_dependency_preflight(store, state):
    """Run every ordinary availability gate, without starting any dependency."""
    from types import SimpleNamespace
    import httpx
    from atnos.production.order_execution import preflight_order
    try:
        executor=SimpleNamespace(store=store,job_id=state['id'],context=store.context(state['id']))
        asyncio.run(preflight_order(executor,allow_model_start=False))
    except (OSError,ValueError,RuntimeError,httpx.HTTPError):
        return None
    return {'preflight':'passed','read_only':True,'models_started':False,
            'profile_hash':state['profile_hash'],'blocked_at':state['updated_at']}


def _dependencies_ready(store, state):
    """Poll bounded probe handles instead of blocking the rest of the queue.

    HTTP probes have the normal preflight timeout. File/model verification can
    take longer on a cold filesystem, so at most two independent read-only
    probes run in the background; dispatch waits for their actual result.
    """
    global _dependency_pool
    key=(str(store.settings.data_dir.resolve()),state['id'],state['profile_hash'],
         state['updated_at'],state.get('error'))
    with _dependency_probe_lock:
        known=_dependency_probes.get(key)
        if known:
            future,_=known
            if not future.done():
                return None
            del _dependency_probes[key]
        else:
            now=monotonic()
            for old,(old_future,started) in list(_dependency_probes.items()):
                if old_future.done() and now-started>120:
                    del _dependency_probes[old]
            if len(_dependency_probes)>=16:
                return None
            if _dependency_pool is None:
                _dependency_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='atnos-recovery-preflight')
            _dependency_probes[key]=(_dependency_pool.submit(_probe_dependency_preflight,store,state),now)
            return None
    # Unexpected errors are reported by the per-job monitor diagnostic. A
    # missing dependency returns None and never consumes a recovery or AI call.
    return future.result()


def _failure_hash(state, error, issues=None):
    identity={'error':error,'step':state['current_step'],'calls':state['provider_calls'],'issues':issues}
    if state['current_step']=='preflight' and error.get('category')=='dependency_unavailable':
        # A new actual worker failure has a new block timestamp even if no AI
        # call occurred. An unchanged stored failure is reserved only once.
        identity['blocked_at']=state['updated_at']
    return content_hash(identity)


def _completed_review_evidence(store, state):
    """Revalidate this failure's existing grounded critic answers, without LLM calls."""
    from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
    from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
    from atnos.production.wb_image_api import load_image_lane, image_selection_plan, _preflight_image_plan
    from atnos.production.wb_image_review import (
        IllustrationObservation, OBSERVATION_CONTRACT, revalidate_cached_comparison,
    )
    try:
        checkpoints=state['checkpoints']
        latest=max((row for row in checkpoints if row['stage'].startswith('assets/image_review/')
                    and row['stage'].endswith('/comparison-format-2')),key=lambda row:row['updated_at'],default=None)
        if not latest or latest['status']!='done' or '_review_format_error' not in latest['output']:
            return None
        prefix=latest['stage'].split('/')[2]
        context=store.context(state['id'])
        directories=list((context.artifact_dir/'assets/automatic-review').glob(prefix+'*'))
        if len(directories)!=1 or not directories[0].is_dir():
            return None
        directory=directories[0]
        traces={}
        for name in ('observation','reference','comparison','comparison-format-1','comparison-format-2'):
            stage=f'assets/image_review/{prefix}/{name}'
            checkpoint=next((row for row in checkpoints if row['stage']==stage and row['status']=='done'),None)
            if not checkpoint or not store._valid_files(context.artifact_dir,checkpoint['files_json']):
                return None
            path=directory/(name+'.json')
            trace=json.loads(path.read_text('utf-8'))
            body=trace['response']
            if (trace['status']!='response_received' or content_hash(trace['inputs'])!=trace['input_hash']
                    or not body.get('done') or body.get('done_reason')=='length'):
                return None
            traces[name]=trace
        observation=IllustrationObservation.model_validate_json(traces['observation']['response']['message']['content']).model_dump(mode='json')
        reference=IllustrationObservation.model_validate_json(traces['reference']['response']['message']['content']).model_dump(mode='json')
        for name,value in (('observation',observation),('reference',reference)):
            checkpoint=next(row for row in checkpoints if row['stage']==f'assets/image_review/{prefix}/{name}')
            if checkpoint['output']!=value:
                return None
        required=['script','script_calculations'] if (state['request'].get('intake') or {}).get('kind')=='user_hypothetical' else ['script']
        for stage in required:
            checkpoint=next((row for row in checkpoints if row['stage']==stage and row['status']=='done'),None)
            if not checkpoint or not store._valid_files(context.artifact_dir,checkpoint['files_json']):
                return None
            context.artifacts[stage]=checkpoint['output']
        if context.artifacts['script'].get('qa',{}).get('status')!='passed':
            return None
        lane=load_image_lane(store.settings,state['id'])
        if not lane or lane['provider']!='comfyui-local':
            return None
        style=context.artifacts['project_profile']['visual_style']
        plan=image_selection_plan(context)
        needs=_preflight_image_plan(plan)
        brain=WealthBlueprintAssetBrain(store.settings)
        revision=traces['observation']['inputs']['model_revision']
        image_hash=traces['observation']['inputs']['image_sha256']
        if (traces['reference']['inputs']['image_sha256']!=lane['reference_sha256']
                or traces['reference']['inputs']['model_revision']!=revision):
            return None
        for scene,(native,need) in zip(plan['scenes'],needs):
            if native:
                continue
            identity={'image_sha256':image_hash,'reference_sha256':lane['reference_sha256'],
                      'need':need,'style':style,'model_revision':revision,'contract':2,
                      'observation_contract':OBSERVATION_CONTRACT}
            if content_hash(identity)!=directory.name:
                continue
            tags=set(style_asset_tags(style,state['id'],scene['scene_id']))
            asset=next((asset for asset in brain.catalog.assets if asset.type=='background'
                        and tags.issubset(asset.tags) and brain.resolve_file(asset).is_file()
                        and file_hash(brain.resolve_file(asset))==image_hash),None)
            if asset is None:
                return None
            for name in ('comparison','comparison-format-1','comparison-format-2'):
                if traces[name]['inputs']['model_revision']!=revision:
                    return None
                try:
                    checks=revalidate_cached_comparison(traces[name],observation,need=need,style=style)
                except (ValueError,KeyError,TypeError):
                    continue
                return {'review_identity':directory.name,'scene_id':scene['scene_id'],'asset_id':asset.id,
                        'revalidated_trace':name+'.json','checks':{key:item['status'] for key,item in checks.items()},
                        'raw_traces':{key+'.json':file_hash(directory/(key+'.json')) for key in traces},
                        'new_model_requests':0,'approval_written':False}
        return None
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):
        return None


def _recover_job(settings, store, job):
    job_id = job['job_id']
    with dispatch_lock(_order_lock_path(store, job_id, 'autonomy.lock')):
        # recover_due captured job earlier; suspension may have won the lock
        # while that snapshot waited. Skip before status or counters change.
        with dispatch_lock(_order_lock_path(store, job_id, 'dispatch.lock')):
            current = policy(settings, job_id)
            if not current or not current['enabled']:
                return []
        return _recover_job_enabled(settings, store, job)


def _recover_job_enabled(settings, store, job):
    from atnos.production.order_recovery import recover_interrupted_orders
    from atnos.production.studio_script_recovery import recovery_snapshot, register_script_repair
    results = []
    job_id = job['job_id']
    recover_interrupted_orders(settings,job_ids=[job_id])
    state = store.get(job_id)
    if state['worker_live'] or state['status'] in {'ready','script_ready'}:
        return results
    from atnos.production.studio_script_recovery import repair_execution_scope
    if repair_execution_scope(store, job_id) == 'script_only':
        # Only recover the existing action. Never invent a new repair receipt,
        # budget or AV run; held/rejected/exhausted candidates need the operator.
        try:
            scoped_error = json.loads(state.get('error') or '{}')
        except (ValueError, TypeError):
            scoped_error = {}
        if state['can_resume'] and (state['status'] == 'queued'
                or state['status'] == 'blocked' and isinstance(scoped_error, dict)
                and scoped_error.get('category') == 'worker_interrupted'):
            dispatched = _dispatch_if_enabled(settings, store, job_id)
            if dispatched is not None:
                results.append(dispatched)
        return results
    if state['status'] == 'queued' and state['can_resume']:
        dispatched = _dispatch_if_enabled(settings, store, job_id)
        if dispatched is not None:
            results.append(dispatched)
        return results
    if state['status'] != 'blocked':
        return results
    current_policy=policy(settings,job_id)
    if (not current_policy or not current_policy['enabled']
            or not settings.atnos_local_work_unlimited and current_policy['recoveries_used']>=current_policy['max_recoveries']):
        return results
    try:
        error = json.loads(state.get('error') or '{}')
    except (ValueError,TypeError):
        if state.get('error') not in {'attempt_limit','cost_limit','provider_call_limit'}:
            return results
        error = {'category':state['error']}
    if not isinstance(error,dict):
        error = {'category':str(error)}
    if error.get('category') in {'cost_limit','provider_call_limit','studio_script_action_limit'}:
        # A fresh receipt/digest is not another authorization. Only a new
        # explicit user assessment may authorize another bounded Studio action.
        return results
    if (state.get('current_step')=='intake_review' and state['can_resume']
            and error.get('category')=='supplied_script_contract_pending'):
        dispatched = _dispatch_if_enabled(settings, store, job_id)
        if dispatched is not None:
            results.append(dispatched)
        return results  # Valid supplied input still passes the normal calculation/fact gates.
    interrupted = error.get('category') == 'worker_interrupted'
    dependency_evidence=None
    if (state.get('current_step')=='preflight'
            and error.get('category')=='dependency_unavailable'):
        if current_policy['last_failure_hash']!=_failure_hash(state,error):
            dependency_evidence=_dependencies_ready(store,state)
    context_failure = (state.get('current_step') == 'script_context'
                       and error.get('category') == 'attempt_limit')
    image_review_failure = False
    if (str(state.get('current_step','')).startswith('assets/image_review/')
            and error.get('category') == 'attempt_limit'):
        # Recover only a completed, now valid comparison. No inference,
        # fabricated approval or repeated request is authorized here.
        from atnos.production.wb_image_review import WBIllustrationChecks
        parts=state['current_step'].split('/')
        if len(parts)==4 and parts[-1]=='comparison':
            root=store.context(job_id).artifact_dir/'assets'/'automatic-review'
            traces=list(root.glob(parts[2]+'*/comparison.json'))
            if len(traces)==1:
                try:
                    trace=json.loads(traces[0].read_text('utf-8'))
                    body=trace['response']
                    if trace['status']=='response_received' and body.get('done') and body.get('done_reason')!='length':
                        WBIllustrationChecks.model_validate_json(body['message']['content'])
                        image_review_failure=True
                except (OSError,ValueError,KeyError,TypeError):
                    pass
    plan_failure=(state.get('current_step')=='image_generation'
                  and any(message in str(error.get('message','')) for message in (
                      'requires a supported physical illustration',
                      'Hypothetical image plan is missing its validated calculation claim ledger')))
    flux_evidence=None
    if (state.get('current_step')=='image_generation'
            and error.get('category')=='execution_failure'
            and error.get('message')=='Flux trial authorization integrity failed'):
        flux_evidence=_completed_flux_evidence(store,state)
    catalog_contract_failure=False
    format_evidence=None
    if (state.get('current_step')=='image_generation'
            and 'WB image review exhausted three distinct format attempts' in str(error.get('message',''))):
        format_evidence=_completed_review_evidence(store,state)
    if (state.get('current_step')=='image_generation' and 'reviewed_by' in str(error.get('message',''))
            and 'String should have at most 80 characters' in str(error.get('message',''))):
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
        try:
            WealthBlueprintAssetBrain(settings)  # New schema must really load the unchanged catalog.
            catalog_contract_failure=True
        except (ValueError,OSError):
            pass
    if plan_failure:
        from atnos.production.wb_image_api import image_selection_plan,_preflight_image_plan
        context=store.context(job_id)
        required=['script','script_calculations'] if (state['request'].get('intake') or {}).get('kind')=='user_hypothetical' else ['script']
        if any(not any(row['stage']==stage and row['status']=='done' and store._valid_files(context.artifact_dir,row['files_json'])
                       for row in state['checkpoints']) for stage in required):
            return results
        context.artifacts['script']=next((row['output'] for row in state['checkpoints'] if row['stage']=='script'),{})
        context.artifacts['script_calculations']=next((row['output'] for row in state['checkpoints'] if row['stage']=='script_calculations'),{})
        try:
            _preflight_image_plan(image_selection_plan(context))
        except (ValueError,RuntimeError,KeyError):
            return results  # Unsupported drawing remains blocked; no blind re-run.
    script_failure = str(state.get('current_step','')).startswith(('script_review/','script_repair/'))
    if script_failure:
        from atnos.production.studio_script_recovery import has_user_editorial_constraints
        if has_user_editorial_constraints(store, job_id):
            # Do not lose user acceptance targets while registering a new
            # generic automatic review; this used to turn an unchanged rejected
            # script into PASS after its three-operation limit was reached.
            return results
    from atnos.production.local_stage_recovery import local_stage_recovery_evidence
    local_tool_evidence = local_stage_recovery_evidence(store, state)
    from atnos.production.local_image_allowance import local_image_allowance_recovery_evidence
    allowance_evidence = local_image_allowance_recovery_evidence(store, state)
    from atnos.production.wb_flux_refinement import completed_refinement_candidate_evidence
    refinement_evidence = completed_refinement_candidate_evidence(store, state)
    from atnos.production.wb_review_evidence_repair import incomplete_review_recovery_evidence
    incomplete_critic_evidence = incomplete_review_recovery_evidence(store, state)
    if not any((interrupted,script_failure,context_failure,plan_failure,image_review_failure,catalog_contract_failure,format_evidence,flux_evidence,dependency_evidence,local_tool_evidence,allowance_evidence,refinement_evidence,incomplete_critic_evidence)):
        return results  # Billing, uncertain external requests and integrity errors need another remedy.
    snapshot = recovery_snapshot(store,job_id) if script_failure else None
    if script_failure and not snapshot.get('can_repair_script'):
        return results
    failure = _failure_hash(state,error,snapshot.get('issues') if snapshot else allowance_evidence or refinement_evidence or incomplete_critic_evidence)
    # Reserve before dispatch. Concurrent API instances cannot consume the same
    # recovery twice; a failed launch remains in the audit rather than refunded.
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute(f'''UPDATE {TABLE} SET recoveries_used=recoveries_used+1,
            last_failure_hash=?,updated_at=? WHERE job_id=? AND enabled=1
            AND (? OR recoveries_used<max_recoveries) AND COALESCE(last_failure_hash,'')<>?''',
            (failure,utc_now_iso(),job_id,settings.atnos_local_work_unlimited,failure)).rowcount
        if changed:
            _event(db,job_id,'autonomy','autonomous_recovery_reserved',
                   {'failure_hash':failure,'kind':'script_repair' if script_failure else 'context_cache_recovery' if context_failure else 'flux_completed_evidence_recovery' if flux_evidence else 'dependency_readiness_recovery' if dependency_evidence else 'local_tool_recovery' if local_tool_evidence else 'local_image_allowance_recovery' if allowance_evidence else 'completed_refinement_provenance_recovery' if refinement_evidence else 'incomplete_critic_evidence_recovery' if incomplete_critic_evidence else 'image_review_contract_recovery' if image_review_failure or catalog_contract_failure or format_evidence else 'asset_plan_recovery' if plan_failure else 'worker_interrupted',
                    **({'evidence':flux_evidence or dependency_evidence or format_evidence or local_tool_evidence or allowance_evidence or refinement_evidence or incomplete_critic_evidence} if flux_evidence or dependency_evidence or format_evidence or local_tool_evidence or allowance_evidence or refinement_evidence or incomplete_critic_evidence else {})})
        db.commit()
    if not changed:
        return results
    if not settings.atnos_local_work_unlimited and local_tool_evidence and state['profile']['production']['max_attempts_per_element'] < 2:
        store.authorize_stage_attempts(job_id, 'editing', 2,
            reason='One bounded autonomous retry of a verified local FFmpeg timeout; upstream hashes, counters and quality gates preserved.')
    if not settings.atnos_local_work_unlimited and (context_failure or image_review_failure):
        stage=state['current_step']
        checkpoint=next(row for row in state['checkpoints'] if row['stage']==stage)
        maximum=max(checkpoint['attempts'],state['profile']['production']['max_attempts_per_element'])+1
        store.authorize_stage_attempts(job_id,stage,maximum,
            reason='Authorized autonomous local recovery: one attempt to validate existing completed evidence after fixing its contract; consumed counters and quality gates preserved.')
    if script_failure:
        actionable = next((event['detail'].get('message','') for event in reversed(state['history'])
                           if event['event']=='stage_failed' and event.get('stage')==state['current_step']), '')
        register_script_repair(store,job_id,{
            'request_key':'auto-'+failure[:32],
            'note':('Automatic recovery explicitly authorized for this local order. Correct only the actual current validator findings; retain calculations, numeric assumptions, scene meaning, quality thresholds and all consumed budgets. Do not repeat rejected wording. No publication. Last actual rejected response: '+actionable[:1100]),
        }, origin='autonomous_recovery')
    dispatched = _dispatch_if_enabled(settings, store, job_id)
    if dispatched is not None:
        results.append(dispatched)
    return results


async def run_monitor(settings):
    while True:
        try:
            await asyncio.to_thread(recover_due,settings)
        except Exception:
            logger.exception('Autonomous production recovery failed; counters retained')
        await asyncio.sleep(30)
