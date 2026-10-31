"""Recovery of intact completed Flux output; no generation or weaker approval."""
import json
from pathlib import Path

from PIL import Image
import pytest

from atnos.production import wb_local_flux_trial as flux
from atnos.production.asset_brain.models import AssetRecord,AssetGenerationProvenance
from atnos.production.order_autonomy import enable,policy,recover_due
from atnos.production.order_store import file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from test_wb_local_flux_trial import local_trial,register
from test_wb_image_api_production import trial,isolated_brain
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts


@pytest.fixture
def completed_flux(local_trial,isolated_brain,monkeypatch):
    store,job,context,_,_=local_trial
    receipt=register(local_trial)
    folder=context.artifact_dir/'assets/flux-fixture'
    folder.mkdir(parents=True)
    image=folder/'image.png'
    Image.new('RGB',(768,1344),'#132938').save(image)
    graph=write_json(folder/'workflow.json',receipt['workflow']).artifact_path
    history=write_json(folder/'history.json',{'actual-id':{'status':{'completed':True,'status_str':'success'}}}).artifact_path
    evidence=write_json(folder/'metadata.json',{'trial_digest':receipt['digest'],
        'workflow_sha256':receipt['workflow_sha256'],'image_sha256':file_hash(image),
        'history_sha256':file_hash(history),'user_approved':False}).artifact_path
    asset=AssetRecord(id='intact-completed-flux',type='background',channel='wealth_blueprint',
        production_role='background',source='comfyui-flux-trial',quality_status='AWAITING_REVIEW',
        file_path=str(image),generation=AssetGenerationProvenance(prompt='unprinted bill and bag',
            workflow_id=flux.VERSION,model='isolated fixture',generated_at='2026-10-08',
            source_workflow=flux.VERSION,channel='wealth_blueprint',fingerprint=receipt['digest'],
            evidence_path=str(evidence)))
    isolated_brain.append(asset)
    stage=f"assets/image_local/flux2-trial/s01/{receipt['digest'][:16]}"
    payload={'asset_id':asset.id,'output_path':str(image),'trial_digest':receipt['digest']}
    report=write_json(folder/'candidate.json',payload).artifact_path
    with store.lease(job) as token:
        store.begin(job,token,stage,{'receipt':receipt['digest']},local_model_call=True)
        store.complete(job,token,stage,payload,[report,image,graph,history,evidence])
        store.block(job,token,'image_generation',category='execution_failure',
                    message='Flux trial authorization integrity failed')
    enable(store.settings,job,reason='User authorizes reuse of intact local production evidence after backend correction.',max_recoveries=1)
    monkeypatch.setattr(flux.ColdFluxSession,'_launch',lambda _:pytest.fail('Recovery launched a GPU process'))
    monkeypatch.setattr(flux,'generate_flux_candidate',lambda *args:pytest.fail('Recovery submitted another Flux request'))
    return store,job,context,receipt,asset,stage


def test_completed_flux_receipt_recovers_once_without_new_post_or_counter_reset(completed_flux,monkeypatch):
    store,job,context,receipt,asset,stage=completed_flux
    # Same transient feedback expansion as normal produce_script. It must not
    # alter immutable production inputs or recreate a receipt.
    monkeypatch.setattr('atnos.production.studio_script_recovery.load_recovery_request',lambda *args:{
        'mode':'normal_review','note':'Correct actual narration defects only.',
        'issues':[],'base_feedback':{}})
    before=store.get(job)
    dispatched=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda self,jid:dispatched.append(jid) or {'id':jid})
    assert recover_due(store.settings)==[{'id':job}]
    assert recover_due(store.settings)==[]
    assert dispatched==[job]
    after=store.get(job)
    assert after['provider_calls']==before['provider_calls']==1
    assert after['profile_hash']==before['profile_hash']
    assert after['reserved_cost_units']==before['reserved_cost_units']
    assert next(row for row in after['checkpoints'] if row['stage']==stage)['attempts']==1
    assert asset.approved is False and asset.quality_status=='AWAITING_REVIEW'
    assert policy(store.settings,job)['recoveries_used']==1
    event=next(row for row in after['history'] if row['event']=='autonomous_recovery_reserved')
    assert event['detail']['kind']=='flux_completed_evidence_recovery'
    assert event['detail']['evidence']['new_flux_requests']==0


@pytest.mark.parametrize('tamper',['receipt','image','workflow','model','checkpoint','pending_no_image','rejection_without_proof'])
def test_real_flux_integrity_damage_never_dispatches_or_consumes_recovery(completed_flux,local_trial,monkeypatch,tamper):
    store,job,context,receipt,asset,stage=completed_flux
    if tamper=='receipt':
        path=context.artifact_dir/'flux_trial/s01/authorization.json'
        payload=json.loads(path.read_text('utf-8'));payload['workflow_sha256']='tampered'
        path.write_text(json.dumps(payload),encoding='utf-8')
    elif tamper=='image':
        Image.new('RGB',(768,1344),'#ffffff').save(asset.file_path)
    elif tamper=='workflow':
        (context.artifact_dir/'assets/flux-fixture/workflow.json').write_text('{}',encoding='utf-8')
    elif tamper=='model':
        local_trial[-1]['unet'].write_bytes(b'changed model')
    elif tamper in {'checkpoint','pending_no_image'}:
        from atnos.intelligence.storage import connect
        with connect(store.settings) as db:
            if tamper=='checkpoint':
                db.execute("UPDATE pipeline_stage_checkpoints SET status='failed' WHERE job_id=? AND stage=?",(job,stage))
            else:
                db.execute('DELETE FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?',(job,stage))
            db.commit()
    else:
        asset.quality_status='REJECTED'
    before=store.get(job)
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *args:pytest.fail('Damaged or absent completed evidence was retried'))
    assert recover_due(store.settings)==[]
    after=store.get(job)
    assert after['provider_calls']==before['provider_calls']
    assert after['profile_hash']==before['profile_hash']
    assert policy(store.settings,job)['recoveries_used']==0


def test_completed_rejected_flux_preserves_negative_pixel_verdict(completed_flux,monkeypatch):
    store,job,context,receipt,asset,stage=completed_flux
    identity={'image_sha256':file_hash(Path(asset.file_path)),'scene':'s01','review_version':'fixture'}
    key=content_hash(identity)
    folder=context.artifact_dir/'assets/automatic-review'/key
    folder.mkdir(parents=True)
    checks={name:{'status':'failed' if name=='required_objects_and_counts' else 'passed',
                  'evidence':'Actual pixel evidence from an independently completed review.'}
            for name in ('required_objects_and_counts','composition_and_readability',
                         'style_palette_and_lighting','integrity_and_no_embedded_text')}
    evidence=write_json(folder/'observation.json',{'objects':['extra duplicated bag']}).artifact_path
    write_json(folder/'review.json',{'identity':identity,'asset_id':asset.id,'checks':checks,
        'evidence_files':{evidence.name:file_hash(evidence)},'user_approved':False})
    asset.quality_status='REJECTED'
    asset.reviewed_by='atnos_automatic_visual_qc:'+key
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',lambda self,jid:{'id':jid})
    assert recover_due(store.settings)==[{'id':job}]
    assert asset.quality_status=='REJECTED' and asset.approved is False
    assert store.get(job)['provider_calls']==1
