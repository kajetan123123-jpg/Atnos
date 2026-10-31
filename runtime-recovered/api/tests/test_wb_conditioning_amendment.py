"""Isolated legacy-lane conditioning; fixture proofs never authorize real images."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import psutil

import pytest

from atnos.production import wb_image_api as lane
from atnos.production.order_store import OrderConflict, ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production.steps import order_assets
from atnos.production import studio_script_recovery
REAL_ACCEPTANCE_VERIFIER=studio_script_recovery.verify_finalized_script_acceptance
REAL_PROCESS=psutil.Process
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts
from test_wb_image_api_production import trial, isolated_brain, prepared_executor
from test_wb_local_composition_bindings import binding
from test_wb_conditioning_replacement_history import failed_style_history
from test_wb_flux_style_candidate import rejected_plain_refinement
from test_wb_flux_refinement import rejected_flux_parent
from test_wb_local_flux_trial import local_trial


@pytest.fixture
def legacy(trial, binding, monkeypatch):
    task_store, job, reference = trial
    selected = lane.register_local_image_lane(task_store.settings,job,reference_path=reference,authorized=True)
    root = task_store.context(job).artifact_dir
    source=prepare_plan(task_store,job)
    mock_acceptance(monkeypatch,root,source)
    return task_store,job,selected,binding


def prepare_plan(task_store,job):
    script = next(row['output'] for row in task_store.get(job)['checkpoints'] if row['stage']=='script')
    source = order_assets.EditorialScript.model_validate({key:value for key,value in script.items()
                                                         if key in order_assets.EditorialScript.model_fields})
    plan = order_assets.ScenePlan(scenes=[order_assets.EditorialScene(segment_id=segment.id,kind='flow',
        semantic_goal='Synthetic transport test of current thought',labels=['Compare useful alternatives'],
        match_reason='Isolated native diagram for offline transport test') for segment in source.segments])
    with task_store.lease(job) as token:
        task_store.begin(job,token,'asset_planning',{'fixture':'normal planner result'})
        result=order_assets.write_scene_plan(task_store.context(job),source,plan,{'method':'isolated-fixture'})
        task_store.complete(job,token,'asset_planning',result.payload,[result.artifact_path])
        task_store.block(job,token,'asset_review',category='fixture_hold',message='Offline reference admission')
    return source


def mock_acceptance(monkeypatch,root,source):
    # The separate acceptance-verifier suite proves actual receipts. This suite
    # isolates conditioning transport and never represents this mock as LIVE.
    monkeypatch.setattr('atnos.production.studio_script_recovery.verify_finalized_script_acceptance',
        lambda store,job_id,script=None:{'job_id':job_id,'candidate_hash':content_hash(source.model_dump(mode='json')),
            'canonical_sha256':file_hash(root/'script.json'),'assessment_hash':'a'*64},raising=False)


def register(legacy, **changes):
    task_store,job,selected,layout=legacy
    root=task_store.context(job).artifact_dir
    values={'scene_id':'s01','binding':layout,'seed':531990040,
            'expected_canonical_sha256':file_hash(root/'script.json'),
            'expected_plan_sha256':file_hash(root/'scene_plan.json'),
            'reason':'Approved layout-only wiring fixture; no generation authorization.', 'authorized':True}
    values.update(changes)
    return lane.register_local_composition_amendment(task_store.settings,job,**values)


@pytest.mark.parametrize('seed',[0,2_147_483_647])
def test_conditioning_seed_matches_downstream_supported_bounds(legacy,seed):
    assert register(legacy,seed=seed)['seed']==seed


@pytest.mark.parametrize('seed',[-1,2_147_483_648,2**40,True])
def test_unsupported_seed_cannot_be_frozen_into_immutable_amendment(legacy,seed):
    task_store,job,_,_=legacy
    with pytest.raises(ValueError,match='seed'):register(legacy,seed=seed)
    assert not (task_store.context(job).artifact_dir/'image_conditioning_amendment.json').exists()


def test_action_admission_sees_amendment_committed_before_its_transaction(legacy,monkeypatch):
    from atnos.production import local_image_dispatch_budget as dispatch
    task_store,job,_,_=legacy
    original_connect=dispatch.connect
    first=True
    @contextmanager
    def raced_connect(settings):
        nonlocal first
        if first:
            first=False
            register(legacy)
        with original_connect(settings) as db:
            yield db
    monkeypatch.setattr(dispatch,'connect',raced_connect)
    with pytest.raises(OrderConflict,match='bind'):
        lane.register_local_image_dispatch_action(task_store.settings,job,
            action_key='fixture-raced-ordinary-action',max_new_requests=3,authorized=True)
    assert not (task_store.context(job).artifact_dir/dispatch.AUTH_FILE).exists()
    assert dispatch.local_image_action_status(task_store.settings,job) is None


@pytest.fixture(autouse=True)
def no_providers(monkeypatch):
    def forbidden(*_a,**_k):pytest.fail('Offline amendment test attempted a provider')
    monkeypatch.setattr(lane,'_generate_local_candidate',forbidden)
    monkeypatch.setattr(lane,'_generate_candidate',forbidden)
    monkeypatch.setattr('atnos.production.wb_local_flux_trial._submit_and_wait_once',forbidden)
    monkeypatch.setattr('atnos.production.wb_local_flux_trial.ColdFluxSession._launch',forbidden)


def test_legacy_receipt_is_preserved_and_amendment_does_not_authorize_generation(legacy):
    task_store,job,selected,layout=legacy
    root=task_store.context(job).artifact_dir
    old=(root/'image_local_authorization.json').read_bytes()
    before=task_store.get(job)
    with pytest.raises(OrderConflict,match='immutable'):
        lane.register_local_image_lane(task_store.settings,job,composition_references=[layout],authorized=True)
    amendment=register(legacy)
    assert amendment['base_lane_digest']==selected['digest']
    assert amendment['generation_authorized'] is False and amendment['appearance_approval']=='pending'
    assert amendment['seed']==531990040
    assert (root/'image_local_authorization.json').read_bytes()==old
    assert lane.load_image_lane(task_store.settings,job)['digest']==selected['digest']
    assert register(legacy)['digest']==amendment['digest']
    after=task_store.get(job)
    for key in ('provider_calls','reserved_cost_units','checkpoints','request','profile_hash','status'):
        assert after[key]==before[key]
    assert not (root/'local_image_dispatch_authorization.json').exists()


def test_layout_alone_cannot_dispatch_even_with_unlimited_local_policy(legacy,isolated_brain):
    task_store,job,_,_=legacy
    register(legacy)
    with task_store.lease(job) as token:
        with pytest.raises(ProductionBlocked,match='separate bounded dispatch'):
            asyncio.run(lane.produce_image_candidates(prepared_executor(task_store,job,token)))


def test_bound_single_dispatch_reaches_normal_generator_with_reference_and_seed(legacy,isolated_brain,monkeypatch):
    task_store,job,selected,layout=legacy
    amendment=register(legacy)
    action=lane.register_local_image_dispatch_action(task_store.settings,job,action_key='fixture-image43-one-request',
        max_new_requests=1,authorized=True,reason='Separate fixture authorization for exactly one POST.',
        conditioning_amendment_digest=amendment['digest'])
    assert action['conditioning_amendment_digest']==amendment['digest'] and action['used_new_requests']==0
    captured=[]
    class Stop(ProductionBlocked):pass
    def capture(settings,context,need,style,tags,authorization,fingerprint,previous_reference=None):
        captured.append((need,authorization,fingerprint))
        raise Stop('Offline boundary before model')
    monkeypatch.setattr(lane,'_generate_local_candidate',capture)
    with task_store.lease(job) as token:
        with pytest.raises(Stop):asyncio.run(lane.produce_image_candidates(prepared_executor(task_store,job,token)))
    assert len(captured)==1
    need,authorization,fingerprint=captured[0]
    assert need['seed']==531990040 and need['conditioning_amendment_digest']==amendment['digest']
    assert need['composition_reference_path']==str(Path(layout['reference_path']).resolve())
    assert need['composition_reference_sha256']==file_hash(Path(layout['reference_path']))
    assert authorization['digest']==selected['digest'] and len(fingerprint)==64


@pytest.mark.parametrize('fault',['approval','reference','script','plan','receipt'])
def test_tampering_aborts_before_any_generator(legacy,isolated_brain,fault):
    task_store,job,_,layout=legacy
    register(legacy)
    root=task_store.context(job).artifact_dir
    paths={'approval':Path(layout['approval_receipt_path']),'reference':Path(layout['reference_path']),
           'script':root/'script.json','plan':root/'scene_plan.json','receipt':root/'image_conditioning_amendment.json'}
    target=paths[fault]
    target.write_bytes(target.read_bytes()+b'\n')
    with task_store.lease(job) as token:
        with pytest.raises(ProductionBlocked,match='conditioning|Conditioning'):
            asyncio.run(lane.produce_image_candidates(prepared_executor(task_store,job,token)))


@pytest.mark.parametrize('changes',[
    {'authorized':False},{'seed':True},{'seed':-1},{'expected_canonical_sha256':'0'*64},
    {'expected_plan_sha256':'0'*64},{'scene_id':'../../outside'},
    {'history':{'script_path':'C:/outside.json','script_sha256':'0'*64}},
])
def test_bad_admission_does_not_register_an_amendment(legacy,changes):
    task_store,job,_,_=legacy
    with pytest.raises((ValueError,OrderConflict,ProductionBlocked)):register(legacy,**changes)
    assert not (task_store.context(job).artifact_dir/'image_conditioning_amendment.json').exists()


def test_active_worker_cannot_register_or_replace_conditioning(legacy):
    task_store,job,_,_=legacy
    with task_store.lease(job),pytest.raises(OrderConflict,match='stopped|active'):
        register(legacy)


def test_existing_amendment_cannot_change_seed_or_reset_dispatch_scope(legacy):
    task_store,job,_,_=legacy
    amendment=register(legacy)
    with pytest.raises(OrderConflict,match='immutable'):register(legacy,seed=123)
    with pytest.raises(OrderConflict,match='conditioning'):
        lane.register_local_image_dispatch_action(task_store.settings,job,action_key='unbound-image-fixture',
            max_new_requests=1,authorized=True)
    with pytest.raises(OrderConflict,match='one|single'):
        lane.register_local_image_dispatch_action(task_store.settings,job,action_key='bound-too-many-fixture',
            max_new_requests=3,authorized=True,conditioning_amendment_digest=amendment['digest'])


def test_real_independent_acceptance_is_required_not_a_loose_pass(legacy,monkeypatch):
    task_store,job,_,_=legacy
    def reject(*_a,**_k):raise ProductionBlocked('Independent semantic acceptance is missing')
    monkeypatch.setattr('atnos.production.studio_script_recovery.verify_finalized_script_acceptance',reject)
    with pytest.raises(ProductionBlocked,match='acceptance'):register(legacy)
    assert not (task_store.context(job).artifact_dir/'image_conditioning_amendment.json').exists()


def test_a_real_unassessed_canonical_cannot_use_the_fixture_acceptance_shortcut(legacy,monkeypatch):
    monkeypatch.setattr(studio_script_recovery,'verify_finalized_script_acceptance',REAL_ACCEPTANCE_VERIFIER)
    with pytest.raises(ProductionBlocked,match='acceptance|registration'):register(legacy)


def workflow_fixture(amendment):
    binding=amendment['binding']
    upload={'source_path':binding['reference_path'],'source_sha256':binding['reference_sha256'],
            'resolved_image_name':'fixture-upload.png'}
    graph={'4':{'class_type':'LoadImage','inputs':{'image':'fixture-upload.png'}},
        '5':{'class_type':'ImageScale','inputs':{'image':['4',0],'width':768,'height':1344}},
        '6':{'class_type':'VAEEncode','inputs':{'pixels':['5',0]}},
        '7':{'class_type':'KSampler','inputs':{'latent_image':['6',0],'seed':amendment['seed'],
            'denoise':(amendment['generator_identity'].get('model_profile') or {}).get('reference_denoise',.4)}}}
    return graph,upload


def dispatch_scope(task_store,job,token,action,index=0,scene='s01'):
    from atnos.production.local_image_dispatch_budget import LocalImageDispatchScope
    stage=f'assets/image_local/{scene}/fixture-{index}'
    task_store.begin(job,token,stage,{'fixture':index})
    return LocalImageDispatchScope(task_store.settings,job,token,stage,f'fixture-{index}',action['digest'])


@pytest.mark.parametrize('damage',['missing_upload','upload_hash','upload_name','wrong_seed','latent_disconnected','text_only','denoise'])
def test_real_post_admission_rejects_wrong_conditioning_before_consuming_request(legacy,damage):
    from atnos.production.local_image_dispatch_budget import reserve_local_image_dispatch,local_image_action_status
    task_store,job,_,_=legacy
    amendment=register(legacy)
    action=lane.register_local_image_dispatch_action(task_store.settings,job,action_key='fixture-pixel-post-admission',
        max_new_requests=1,authorized=True,conditioning_amendment_digest=amendment['digest'])
    graph,upload=workflow_fixture(amendment)
    if damage=='missing_upload':upload=None
    elif damage=='upload_hash':upload['source_sha256']='0'*64
    elif damage=='upload_name':upload['resolved_image_name']='different.png'
    elif damage=='wrong_seed':graph['7']['inputs']['seed']+=1
    elif damage=='latent_disconnected':graph['7']['inputs']['latent_image']=['empty-latent',0]
    elif damage=='text_only':graph['4']['class_type']='EmptyLatentImage'
    else:graph['7']['inputs']['denoise']=1
    with task_store.lease(job) as token:
        scope=dispatch_scope(task_store,job,token,action)
        with pytest.raises(ProductionBlocked,match='Conditioning'):
            reserve_local_image_dispatch(scope,graph,reference_upload=upload)
    assert local_image_action_status(task_store.settings,job)['used_new_requests']==0


def test_unknown_post_persists_across_fresh_worker_and_blocks_another_request(legacy):
    from atnos.production.local_image_dispatch_budget import reserve_local_image_dispatch,record_local_image_dispatch,local_image_action_status
    task_store,job,_,_=legacy
    amendment=register(legacy)
    action=lane.register_local_image_dispatch_action(task_store.settings,job,action_key='fixture-single-unknown-post',
        max_new_requests=1,authorized=True,conditioning_amendment_digest=amendment['digest'])
    graph,upload=workflow_fixture(amendment)
    with task_store.lease(job) as token:
        scope=dispatch_scope(task_store,job,token,action)
        reserve_local_image_dispatch(scope,graph,reference_upload=upload)
        record_local_image_dispatch(scope,status='unknown',error_kind='Offline fixture uncertain HTTP result')
        task_store.block(job,token,scope.stage,category='fixture_hold',message='Simulated uncertain outcome')
    with task_store.lease(job) as token:
        scope=dispatch_scope(task_store,job,token,action,index=1)
        with pytest.raises(ProductionBlocked,match='unknown'):
            reserve_local_image_dispatch(scope,graph,reference_upload=upload)
    current=local_image_action_status(task_store.settings,job)
    assert current['used_new_requests']==1 and current['remaining_new_requests']==0 and current['pending_or_unknown']


def test_bound_action_cannot_be_spent_on_another_scene(legacy):
    from atnos.production.local_image_dispatch_budget import reserve_local_image_dispatch,local_image_action_status
    task_store,job,_,_=legacy
    amendment=register(legacy)
    action=lane.register_local_image_dispatch_action(task_store.settings,job,action_key='fixture-scene-bound-action',
        max_new_requests=1,authorized=True,conditioning_amendment_digest=amendment['digest'])
    graph,upload=workflow_fixture(amendment)
    with task_store.lease(job) as token:
        scope=dispatch_scope(task_store,job,token,action,scene='s02')
        with pytest.raises(ProductionBlocked,match='different scene'):
            reserve_local_image_dispatch(scope,graph,reference_upload=upload)
    assert local_image_action_status(task_store.settings,job)['used_new_requests']==0


def test_registration_rechecks_lease_after_validation_race(legacy,monkeypatch):
    from atnos.production import wb_image_conditioning as conditioning
    task_store,job,_,_=legacy
    original=conditioning._inputs
    lease=task_store.lease(job)
    entered=False
    def raced(*args,**kwargs):
        nonlocal entered
        value=original(*args,**kwargs)
        lease.__enter__();entered=True
        return value
    monkeypatch.setattr(conditioning,'_inputs',raced)
    try:
        with pytest.raises(OrderConflict,match='raced|stopped'):register(legacy)
    finally:
        if entered:lease.__exit__(None,None,None)
    assert not (task_store.context(job).artifact_dir/'image_conditioning_amendment.json').exists()


def test_complete_prior_flux_lineage_reaches_standard_conditioned_generator(failed_style_history,binding,isolated_brain,monkeypatch):
    task_store,job,context,need,styled,selected,fallback=failed_style_history
    root=context.artifact_dir
    # Restore the class: replacing psutil.Process with a function also breaks
    # psutil's own isinstance/equality checks during the real store lease.
    monkeypatch.setattr(psutil,'Process',REAL_PROCESS)
    old_files={str(path):file_hash(path) for path in root.rglob('authorization.json')}
    old_fallback=file_hash(Path(fallback['evidence_path']))
    old_counts={row['stage']:row['attempts'] for row in task_store.get(job)['checkpoints'] if '/flux2-trial/' in row['stage']}
    # An updated current canonical and plan are fixture setup through normal
    # store stages. Actual independent acceptance is tested in its own suite.
    current=deepcopy(context.artifacts['script'])
    current['segments'][0]['text']='Compare what you need today with another useful use of your money.'
    with task_store.lease(job) as token:
        task_store.begin(job,token,'script',{'fixture':'new current script'})
        result=write_json(root/'script.json',current)
        task_store.complete(job,token,'script',current,[result.artifact_path])
        task_store.block(job,token,'script',category='fixture_hold',message='Normal setup for current fixture')
    source=prepare_plan(task_store,job)
    mock_acceptance(monkeypatch,root,source)
    legacy=(task_store,job,selected,binding)
    amendment=register(legacy,history=context.artifacts['conditioning_replacement_history'])
    assert len(amendment['prior_flux_proof']['lineage_digests'])==3
    lane.register_local_image_dispatch_action(task_store.settings,job,action_key='fixture-replaced-flux-one-post',
        max_new_requests=1,authorized=True,conditioning_amendment_digest=amendment['digest'])
    monkeypatch.setattr('atnos.production.wb_local_flux_trial.generate_flux_candidate',
                        lambda *_a,**_k:pytest.fail('Conditioned path invoked legacy Flux generator'))
    captured=[]
    class Stop(ProductionBlocked):pass
    def capture(settings,ctx,need,style,tags,authorization,fingerprint,previous_reference=None):
        captured.append(need)
        raise Stop('Fixture capture before any local generation')
    monkeypatch.setattr(lane,'_generate_local_candidate',capture)
    with task_store.lease(job) as token:
        with pytest.raises(Stop):asyncio.run(lane.produce_image_candidates(prepared_executor(task_store,job,token)))
    assert len(captured)==1 and captured[0]['seed']==531990040
    assert captured[0]['conditioning_amendment_digest']==amendment['digest']
    assert old_files=={str(path):file_hash(path) for path in root.rglob('authorization.json')}
    assert file_hash(Path(fallback['evidence_path']))==old_fallback
    assert old_counts=={row['stage']:row['attempts'] for row in task_store.get(job)['checkpoints'] if '/flux2-trial/' in row['stage']}


def test_scoped_normal_queue_passes_actual_upload_evidence_and_stops_second_post(legacy,monkeypatch):
    from atnos.production.local_image_dispatch_budget import local_image_dispatch_scope,local_image_action_status
    from atnos.production.comfy_channels.service import ScopedLocalImageGenerationService
    from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
    task_store,job,_,_=legacy
    amendment=register(legacy)
    action=lane.register_local_image_dispatch_action(task_store.settings,job,action_key='fixture-normal-queue-one-post',
        max_new_requests=1,authorized=True,conditioning_amendment_digest=amendment['digest'])
    graph,upload=workflow_fixture(amendment)
    posts=[]
    def transport(service,workflow):
        posts.append(deepcopy(workflow))
        return 'fixture-real-queue-boundary'
    monkeypatch.setattr(ComfyUiImageGenerationService,'_queue_prompt',transport)
    with task_store.lease(job) as token:
        scope=dispatch_scope(task_store,job,token,action)
        with local_image_dispatch_scope(task_store.settings,job,token,scope.stage,scope.fingerprint):
            service=ScopedLocalImageGenerationService(task_store.settings)
            service.runtime=SimpleNamespace(base_url='http://127.0.0.1:8188')
            service._reference_upload_evidence=upload
            assert service._queue_prompt(graph)=='fixture-real-queue-boundary'
            with pytest.raises(ProductionBlocked,match='One POST'):
                service._queue_prompt(graph)
    assert len(posts)==1 and local_image_action_status(task_store.settings,job)['used_new_requests']==1
