"""Isolated fixture QA is not an approval receipt for real covers."""
import copy
import json
from pathlib import Path
import pytest
from atnos.production.order_store import _fresh_cover_proof, checkpoint_resolved, file_hash
from test_production_order_store import store,create


def payloads():
    return {
        'covers':{'visual':{'renderer':'remotion_reserve_frame','source_sha256':'a'*64,'manifest_sha256':'b'*64},'sha256':{'landscape':'c'*64}},
        'media_qa':{'status':'passed','video_sha256':'a'*64},
        'semantic/cover_landscape':{'status':'passed','sha256':'c'*64,'verdict':{'status':'pass','passed':True}},
    }


def rows_for(values):
    return [{'stage':key,'status':'done','input_hash':key,'output_json':json.dumps(value)} for key,value in values.items()]


@pytest.mark.parametrize('defect',['cover_failed','false_pass','stale_image','stale_video','legacy_base','missing_manifest'])
def test_failed_cover_repair_cannot_retire_on_incomplete_or_stale_quality(defect):
    values=payloads()
    if defect=='cover_failed': values['semantic/cover_landscape']['status']='failed'
    if defect=='false_pass': values['semantic/cover_landscape']['verdict']['passed']=False
    if defect=='stale_image': values['semantic/cover_landscape']['sha256']='old'
    if defect=='stale_video': values['media_qa']['video_sha256']='old'
    if defect=='legacy_base': values['covers']['visual']['renderer']='legacy_flow'
    if defect=='missing_manifest': values['covers']['visual'].pop('manifest_sha256')
    assert _fresh_cover_proof(rows_for(values),'landscape') is None


@pytest.mark.parametrize('renderer', ['remotion_reserve_frame', 'remotion_research_frame'])
def test_real_store_preserves_failed_attempts_and_requires_current_qa(store,monkeypatch,renderer):
    from atnos.integrations import claim_evidence_gate
    monkeypatch.setattr(claim_evidence_gate,'ensure_media_claim_gate',lambda **kwargs:{'blocked':False})
    job=create(store)['id']; root=store.context(job).artifact_dir
    with store.lease(job) as token:
        store.begin(job,token,'cover_quality/landscape/2',{'old_failed_selection':True},local_model_call=True)
        store.fail(job,token,'cover_quality/landscape/2',category='fixture_failure',message='OFFLINE identical image')
        store.reconcile_cover_checkpoints(job,token)
        old=next(r for r in store.get(job)['checkpoints'] if r['stage']=='cover_quality/landscape/2')
        assert old['status']=='failed'
        values=payloads()
        values['covers']['visual']['renderer']=renderer
        for stage,payload in values.items():
            store.begin(job,token,stage,{'fixture':stage})
            path=root/(stage.replace('/','-')+'.json'); path.write_text(json.dumps(payload),encoding='utf-8')
            store.complete(job,token,stage,payload,[path])
        store.reconcile_cover_checkpoints(job,token)
        state=store.get(job); resolved=next(r for r in state['checkpoints'] if r['stage']==old['stage'])
        assert resolved['status']=='superseded' and resolved['attempts']==old['attempts']==1
        assert state['provider_calls']==1 and state['reserved_cost_units']==0
        assert checkpoint_resolved(resolved,state['checkpoints'])
        changed=copy.deepcopy(state['checkpoints'])
        qa=next(r for r in changed if r['stage']=='semantic/cover_landscape')
        qa['input_hash']='another-review'
        assert not checkpoint_resolved(resolved,changed)
        prior_proof=copy.deepcopy(resolved['output']['superseded_by'])
        values['covers']['sha256']['landscape']='d'*64
        values['covers']['visual']['source_sha256']='e'*64
        values['media_qa']['video_sha256']='e'*64
        values['semantic/cover_landscape']['sha256']='d'*64
        for stage,payload in values.items():
            store.begin(job,token,stage,{'fixture':stage,'new_render':True})
            path=root/(stage.replace('/','-')+'.json'); path.write_text(json.dumps(payload),encoding='utf-8')
            store.complete(job,token,stage,payload,[path])
        store.reconcile_cover_checkpoints(job,token)
        updated=store.get(job)
        rebound=next(r for r in updated['checkpoints'] if r['stage']==old['stage'])
        assert checkpoint_resolved(rebound,updated['checkpoints'])
        assert rebound['attempts']==old['attempts'] and updated['provider_calls']==1
        assert rebound['output']['previous_status']=='failed'
        assert rebound['output']['supersession_history']==[prior_proof]
        store.reconcile_cover_checkpoints(job,token)
        again=next(r for r in store.get(job)['checkpoints'] if r['stage']==old['stage'])
        assert again['output']==rebound['output']
