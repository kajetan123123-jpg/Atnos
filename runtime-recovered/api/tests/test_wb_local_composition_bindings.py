"""Isolated reference transport tests; fixture approvals are never LIVE receipts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from PIL import Image
import pytest

from atnos.production import wb_image_api as lane
from atnos.production.order_store import OrderConflict, ProductionBlocked, file_hash
from atnos.production.steps.common import write_json
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts
from test_wb_image_api_production import trial, isolated_brain, prepared_executor


@pytest.fixture
def binding(tmp_path):
    reference = tmp_path / 'layout.png'
    Image.new('RGB', (768,1344), '#fff2d5').save(reference)
    proof = tmp_path / 'layout-user-approval-fixture.json'
    proof.write_text(json.dumps({'status':'USER_ACCEPTED_COMPOSITION_REFERENCE',
        'path':str(reference.resolve()), 'sha256':file_hash(reference),
        'user_answer':'Isolated fixture acceptance, not a production user answer.',
        'approval_scope':'Layout only; not finished artwork approval.',
        'visual_review':'One horizontal card and one shopping bag, no extra objects.'}),encoding='utf-8')
    return {'illustration_function':'bill_and_bag',
        'asset_subject':'one blank-faced teal paper slip beside one small unbranded shopping bag',
        'reference_path':str(reference), 'approval_receipt_path':str(proof)}


def select(trial, binding):
    store,job,style_reference=trial
    return lane.register_local_image_lane(store.settings,job,reference_path=style_reference,
        composition_references=[binding],authorized=True)


def test_v1_default_keeps_receipt_and_unconditioned_behavior(trial):
    store,job,reference=trial
    selected=lane.register_local_image_lane(store.settings,job,reference_path=reference,authorized=True)
    assert selected['version']=='wb-image-local-v1'
    assert 'composition_references' not in selected
    scene_needs=[(False,{'type':'background','illustration_function':'bill_and_bag'})]
    assert lane._apply_local_composition_bindings(scene_needs,selected) is scene_needs
    assert lane.register_local_image_lane(store.settings,job,reference_path=reference,
        authorized=True)['digest']==selected['digest']


def test_v2_immutable_hash_bound_layout_registration_preserves_order(trial,binding):
    store,job,_=trial
    before=store.get(job)
    selected=select(trial,binding)
    normalized=selected['composition_references'][0]
    assert selected['version']==lane.LOCAL_COMPOSITION_VERSION
    assert selected['provider']=='comfyui-local' and selected['reservation_usd_per_request']==0
    assert normalized['purpose']=='layout_only'
    assert normalized['reference_sha256']==file_hash(Path(binding['reference_path']))
    assert normalized['approval_receipt_sha256']==file_hash(Path(binding['approval_receipt_path']))
    assert selected['appearance_approval']=='pending'
    assert lane.load_image_lane(store.settings,job)['composition_references']==[normalized]
    assert select(trial,binding)['digest']==selected['digest']
    after=store.get(job)
    for key in ('profile_hash','provider_calls','reserved_cost_units','checkpoints','status'):
        assert after[key]==before[key]
    assert lane.register_local_image_lane(store.settings,job,authorized=True)['digest']==selected['digest']
    with pytest.raises(OrderConflict,match='immutable'):
        lane.register_local_image_lane(store.settings,job,composition_references=[],authorized=True)


def test_matching_binding_reaches_normal_generator_after_semantic_plan(trial,binding,isolated_brain,monkeypatch):
    store,job,_=trial
    selected=select(trial,binding)
    captured=[]
    class StopBeforeAnyModel(ProductionBlocked): pass
    def stop(settings,context,need,style,tags,authorization,fingerprint,previous_reference):
        captured.append((need,authorization,fingerprint))
        raise StopBeforeAnyModel('Isolated stop before acquisition and image POST')
    monkeypatch.setattr(lane,'_generate_local_candidate',stop)
    with store.lease(job) as token:
        executor=prepared_executor(store,job,token)
        expected=lane._preflight_image_plan(lane.image_selection_plan(executor.context))[0][1]
        assert expected['illustration_function']=='bill_and_bag'
        assert 'composition_reference_path' not in expected
        with pytest.raises(StopBeforeAnyModel):
            asyncio.run(lane.produce_image_candidates(executor))
    need,authorization,fingerprint=captured[0]
    assert need['composition_reference_path']==str(Path(binding['reference_path']).resolve())
    assert need['composition_reference_sha256']==selected['composition_references'][0]['reference_sha256']
    assert need['composition_reference_purpose']=='layout_only'
    assert need['asset_subject']==binding['asset_subject']
    assert authorization['digest']==selected['digest'] and len(fingerprint)==64
    assert len(captured)==1
    assert not any(row['stage'].startswith(('voice','render','remotion_render')) for row in store.get(job)['checkpoints'])


def test_same_function_wrong_subject_stops_before_acquisition(trial,binding,monkeypatch):
    selected=select(trial,binding)
    with pytest.raises(ProductionBlocked,match='differs'):
        lane._apply_local_composition_bindings([(False,{'type':'background','illustration_function':'bill_and_bag',
            'asset_subject':'one coffee cup'})],selected)


def test_binding_cannot_attach_to_an_unrelated_native_or_physical_scene(trial,binding):
    selected=select(trial,binding)
    for needs in ([(True,None)],[(False,{'type':'background','illustration_function':'coffee_reassurance',
        'asset_subject':'one plain unbranded ceramic coffee cup'})]):
        with pytest.raises(ProductionBlocked,match='no matching physical scene'):
            lane._apply_local_composition_bindings(needs,selected)


@pytest.mark.parametrize('tamper',['reference','approval','receipt'])
def test_current_load_rejects_changed_pixel_provenance_or_lane_receipt(trial,binding,tamper):
    store,job,_=trial
    select(trial,binding)
    if tamper=='reference':
        Image.new('RGB',(768,1344),'#171a3b').save(binding['reference_path'])
    elif tamper=='approval':
        proof=Path(binding['approval_receipt_path'])
        proof.write_text(proof.read_text('utf-8')+'\n',encoding='utf-8')
    else:
        receipt=store.context(job).artifact_dir/'image_local_authorization.json'
        data=json.loads(receipt.read_text('utf-8'))
        data['composition_references'][0]['purpose']='production'
        receipt.write_text(json.dumps(data),encoding='utf-8')
    before=store.get(job)
    with pytest.raises(ProductionBlocked,match='integrity'):
        lane.load_image_lane(store.settings,job)
    after=store.get(job)
    assert after['provider_calls']==before['provider_calls']
    assert after['checkpoints']==before['checkpoints']


@pytest.mark.parametrize('field,value',[
    ('illustration_function','random_object'),('asset_subject','one cup'),
    ('purpose','production'),('reference_path','C:/NotAllowed/layout.png'),
    ('approval_receipt_path','C:/NotAllowed/approval.json'),('reference_sha256','0'*64),
    ('approval_receipt_sha256','0'*64),('unrecognized_field','true')])
def test_registration_rejects_invalid_or_outside_e_binding(trial,binding,field,value):
    store,job,_=trial
    with pytest.raises(ValueError):
        select(trial,{**binding,field:value})
    assert lane.load_image_lane(store.settings,job) is None


def test_layout_approval_cannot_be_relabelled_for_another_supported_function(trial,binding):
    other={**binding,'illustration_function':'coffee_reassurance',
        'asset_subject':lane._composition_subject('coffee_reassurance')}
    with pytest.raises(ValueError,match='Legacy'):
        select(trial,other)


def test_registration_does_not_upgrade_an_existing_v1_authorization(trial,binding):
    store,job,reference=trial
    original=lane.register_local_image_lane(store.settings,job,reference_path=reference,authorized=True)
    with pytest.raises(OrderConflict,match='immutable'):
        select(trial,binding)
    assert lane.load_image_lane(store.settings,job)['digest']==original['digest']


def test_binding_rejects_paid_lane_and_cloud_override(trial,binding):
    store,job,reference=trial
    cloud=lane.register_image_lane(store.settings,job,reference_path=reference,authorized=True)
    with pytest.raises(ProductionBlocked,match='local ComfyUI'):
        lane._apply_local_composition_bindings([], {**cloud,'composition_references':[binding]})
    with pytest.raises(OrderConflict,match='direct local lane'):
        select(trial,binding)
    assert lane.load_image_lane(store.settings,job)['digest']==cloud['digest']


def test_registration_blocks_an_active_worker(trial,binding):
    store,job,_=trial
    with store.lease(job),pytest.raises(OrderConflict,match='stopped'):
        select(trial,binding)
    assert lane.load_image_lane(store.settings,job) is None


@pytest.mark.parametrize('status',['running','done'])
def test_registration_blocks_active_or_completed_media_even_after_worker_stops(trial,binding,status):
    store,job,_=trial
    with store.lease(job) as token:
        store.begin(job,token,'voice/test-fixture',{'test':'media admission'})
        if status=='done':
            artifact=write_json(store.context(job).artifact_dir/'audio-fixture.json',{'fixture':True})
            store.complete(job,token,'voice/test-fixture',artifact.payload,[artifact.artifact_path])
        store.block(job,token,'voice/test-fixture',category='fixture_hold',message='isolated fixture')
    with pytest.raises(OrderConflict,match='audiovisual'):
        select(trial,binding)
    assert lane.load_image_lane(store.settings,job) is None


def test_registration_rechecks_worker_lease_after_binding_validation_race(trial,binding,monkeypatch):
    store,job,_=trial
    validate=lane._composition_bindings
    worker_lease=store.lease(job)
    entered=False
    def validate_with_new_worker(value):
        nonlocal entered
        result=validate(value)
        if not entered:
            worker_lease.__enter__()
            entered=True
        return result
    monkeypatch.setattr(lane,'_composition_bindings',validate_with_new_worker)
    try:
        with pytest.raises(OrderConflict,match='raced'):
            select(trial,binding)
    finally:
        if entered:
            worker_lease.__exit__(None,None,None)
    assert lane.load_image_lane(store.settings,job) is None
