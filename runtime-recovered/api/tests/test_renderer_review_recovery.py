"""Offline registry/lease fixtures, not actual media or model acceptance."""
import asyncio
import copy
import json
import pytest
from atnos.production import remotion_quality_repair as repair
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked,file_hash
from atnos.production.project_manifest import save_project_manifest
from atnos.production.project_profile import ProductionOrder,content_hash
from atnos.production.steps.common import write_json
from test_production_order_store import store
from test_production_order_script import script
from test_semantic_qa_remotion_scene_gate import _minimal_manifest

@pytest.fixture
def trial(store,script,monkeypatch):
    job=store.create(ProductionOrder(topic='Review renewal lifecycle fixture',delivery_mode='local_only'))['id']
    root=store.context(job).artifact_dir
    feedback={'feedback_hash':'a'*64,'feedback':{'reason':'Offline preserved constraints','issues':[],'target_segments':[6,6]}}
    monkeypatch.setattr('atnos.production.scoped_script_revision.load_feedback',lambda *_:feedback)
    monkeypatch.setattr('atnos.production.channels.wealth_blueprint.remotion_order_edit.renderer_source_fingerprint',lambda:'c'*64)
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **_: {'blocked':False})
    payload={**script.model_dump(mode='json'),'qa':{'status':'passed','review_contract_version':1}}
    with store.lease(job) as token:
        for stage,data,name in [('script',payload,'script.json'),
            ('script_scoped/'+feedback['feedback_hash'][:12]+'/accepted',payload,'accepted.json'),
            ('voice',{'fixture':'cached voice'},'voice.json'),('captions',{'fixture':'cached captions'},'captions.json'),
            ('asset_planning',{'fixture':'cached planning'},'scene_plan.json')]:
            store.begin(job,token,stage,{'fixture':stage})
            result=write_json(root/name,data);store.complete(job,token,stage,data,[result.artifact_path])
        save_project_manifest(root/'project_manifest.json',_minimal_manifest(job,[('s01',0,5)]))
        for stage in ('remotion_render','remotion_mux'):
            store.begin(job,token,stage,{'old':'render'})
            result=write_json(root/(stage+'.json'),{'fixture':stage})
            store.complete(job,token,stage,result.payload,[result.artifact_path])
        store.fail(job,token,'remotion_render',category='execution_failure',message='Fixture failed render')
        store.block(job,token,'script_feedback',category='script_review_contract_changed',message='Fixture old contract')
    state=store.get(job)
    binding=repair.RendererReviewRecovery(expected_request_hash=content_hash(state['request']),
        expected_profile_hash=state['profile_hash'],expected_upstream_hash=repair.renderer_refresh_upstream_hash(state['checkpoints']),
        expected_canonical_sha256=file_hash(root/'script.json'),expected_manifest_sha256=file_hash(root/'project_manifest.json'),
        expected_accepted_hash=content_hash(payload),accepted_stage='script_scoped/'+feedback['feedback_hash'][:12]+'/accepted')
    return store,job,binding,feedback

def register(trial,**kwargs):
    store,job,binding,_=trial
    return asyncio.run(repair.register_renderer_refresh(store,job,expected_package_sha256=None,
        reason='User requested finalization of this unchanged local film with its revised renderer.',authorized=True,
        review_recovery=binding,visual_variant='editorial_motion',**kwargs))

def test_registry_archives_failed_render_preserves_upstream_and_rehydrates(trial,monkeypatch):
    store,job,binding,_=trial;before=copy.deepcopy(store.get(job));root=store.context(job).artifact_dir
    monkeypatch.setattr('atnos.production.publication_coordinator.verified_package',lambda *_a,**_k:pytest.fail('No package exists; never fake a PASS'))
    result=register(trial);after=store.get(job)
    assert after['status']=='blocked' and after['current_step']=='rendering'
    assert after['provider_calls']==before['provider_calls'] and after['reserved_cost_units']==before['reserved_cost_units']
    for row in before['checkpoints']:assert next(r for r in after['checkpoints'] if r['stage']==row['stage'])==row
    assert after['history'][:len(before['history'])]==before['history']
    assert file_hash(root/'script.json')==binding.expected_canonical_sha256
    with store.lease(job) as token:
        restored=repair.load_renderer_refresh(StageExecutor(store,job,token))
    assert restored['digest']==result['request_digest'] and restored['before_package_sha256'] is None
    assert any(e['original_path']=='remotion_render.json' for e in restored['archived_files'])
    assert restored['user_appearance_approved'] is False
    # A normal replan must not be mistaken for changing the immutable before-plan.
    current=_minimal_manifest(job,[('s01',0,6)]);save_project_manifest(root/'project_manifest.json',current)
    with store.lease(job) as token:assert repair.load_renderer_refresh(StageExecutor(store,job,token))['digest']==result['request_digest']

@pytest.mark.parametrize('mutation',['request','profile','upstream','canonical','manifest','accepted','stage','missing-voice','error','active'])
def test_rejects_stale_or_unqualified_admission_without_resetting_anything(trial,mutation):
    store,job,binding,_=trial;root=store.context(job).artifact_dir
    fields={'request':'expected_request_hash','profile':'expected_profile_hash','upstream':'expected_upstream_hash',
        'canonical':'expected_canonical_sha256','manifest':'expected_manifest_sha256','accepted':'expected_accepted_hash'}
    if mutation in fields:trial=(store,job,binding.model_copy(update={fields[mutation]:'f'*64}),trial[3])
    elif mutation=='stage':trial=(store,job,binding.model_copy(update={'accepted_stage':'script_scoped/'+'b'*12+'/accepted'}),trial[3])
    elif mutation=='missing-voice':(root/'voice.json').unlink()
    elif mutation=='error':
        with store.lease(job) as token:store.block(job,token,'script_feedback',category='something_else',message='Unrelated failure')
    before=copy.deepcopy(store.get(job))
    if mutation=='active':
        with store.lease(job):
            with pytest.raises((ProductionBlocked,RuntimeError)):register(trial)
    else:
        with pytest.raises(ProductionBlocked):register(trial)
    after=store.get(job)
    assert after['checkpoints']==before['checkpoints'] and after['provider_calls']==before['provider_calls']

@pytest.mark.parametrize('status',['passed','failed'])
def test_renewal_uses_existing_review_stage_and_never_rewrites_or_creates_media(trial,monkeypatch,status):
    from atnos.production.scoped_script_revision import renew_renderer_review
    store,job,binding,feedback=trial;register(trial);root=store.context(job).artifact_dir
    calls=[]
    async def review(*_):
        calls.append(1)
        return {'status':status,'issues':[] if status=='passed' else [{'code':'naturalness','detail':'Fixture current review rejects wording'}]}
    monkeypatch.setattr('atnos.production.steps.order_script.review',review)
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token);ex.context.artifacts['_renderer_refresh']=repair.load_renderer_refresh(ex)
        accepted=next(r for r in store.get(job)['checkpoints'] if r['stage']==binding.accepted_stage)
        if status=='passed':
            verdict=asyncio.run(renew_renderer_review(ex,feedback,accepted,accepted['output'],{}))
            assert verdict['status']=='passed' and verdict['scope']=='review_only'
            assert asyncio.run(renew_renderer_review(ex,feedback,accepted,accepted['output'],{}))==verdict
        else:
            with pytest.raises(ProductionBlocked,match='did not pass'):
                asyncio.run(renew_renderer_review(ex,feedback,accepted,accepted['output'],{}))
    assert len(calls)==1 and file_hash(root/'script.json')==binding.expected_canonical_sha256
    assert store.get(job)['status']!='ready'
    assert not (root/'assets/final_short.mp4').exists()
