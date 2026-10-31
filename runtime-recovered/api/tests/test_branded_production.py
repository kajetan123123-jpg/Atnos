import json
import os
from pathlib import Path

import pytest
from PIL import Image

from atnos.production.brand_contract import BrandKit,freeze_brand,verified_asset
from atnos.production.branded_production import validate_narration
from atnos.production.order_store import ProductionBlocked,file_hash
from atnos.production.project_profile import ProductionOrder,content_hash
from test_production_order_store import store,create,complete_file


def install_kit(store):
    source=Path(__file__).parents[1]/'config/brand-kits/wealth-blueprint-alex-01/brand.json'
    root=store.settings.data_dir/'brand-kits/test-alex'
    root.mkdir(parents=True)
    (root/'brand.json').write_bytes(source.read_bytes())
    (root/'USER-BRIEF.txt').write_text('User reference brief')
    Image.new('RGB',(50,50),'navy').save(root/'visual-reference.png')
    return root


def test_brand_sources_are_frozen_and_old_orders_stay_compatible(store):
    root=install_kit(store)
    state=store.create(ProductionOrder(topic='A raise',brand_kit='test-alex',delivery_mode='local_only'))
    frozen=state['profile']['brand_kit']
    (root/'USER-BRIEF.txt').write_text('Changed reference')
    assert store.get(state['id'])['profile']['brand_kit']==frozen
    assert frozen['sources']['brief_file']['sha256']!=file_hash(root/'USER-BRIEF.txt')
    assert 'brand_kit' not in create(store)['request']
    with pytest.raises(ValueError):ProductionOrder(topic='A raise',brand_kit='test-alex')


def test_brand_source_traversal_and_token_drift_are_rejected(store):
    root=install_kit(store)
    kit=json.loads((root/'brand.json').read_text())
    kit['brief_file']='../outside.txt'
    (root/'brand.json').write_text(json.dumps(kit))
    with pytest.raises(ValueError,match='inside'):freeze_brand(store.settings,'test-alex')
    kit['token_allocation']['housing']=1
    with pytest.raises(ValueError,match='seven-token'):BrandKit.model_validate(kit)


def test_external_images_bind_to_exact_request_and_bytes(tmp_path):
    folder=tmp_path/'brand_assets';folder.mkdir()
    image=folder/'alex_thinking.png'
    im=Image.new('RGBA',(768,1024),(0,0,0,0));im.putpixel((300,300),(20,30,40,255));im.save(image)
    request={'asset_id':'alex_thinking','prompt':'immutable prompt'}
    receipt={'request_hash':content_hash(request),'relative_file':'brand_assets/alex_thinking.png','sha256':file_hash(image),'provider':'codex_imagegen_supervised_relay'}
    (folder/'alex_thinking.json').write_text(json.dumps(receipt))
    assert verified_asset(tmp_path,request)==image
    with pytest.raises(ValueError,match='another'):verified_asset(tmp_path,{**request,'prompt':'changed'})
    im.putpixel((1,1),(100,0,0,255));im.save(image)
    with pytest.raises(ValueError,match='changed'):verified_asset(tmp_path,request)


def test_explicit_stage_extension_preserves_counts_and_other_limits(store):
    state=create(store);job=state['id']
    with store.lease(job) as token:
        for i in range(3):
            store.begin(job,token,'script_draft',{'attempt':i},local_model_call=True)
            store.fail(job,token,'script_draft',category='invalid_output',message='Rejected')
        with pytest.raises(ProductionBlocked,match='attempt_limit'):store.begin(job,token,'script_draft',{})
    updated=store.authorize_stage_attempts(job,'script_draft',5,reason='Explicit user authorization for two further repair attempts')
    assert updated['provider_calls']==3
    assert updated['profile_hash']==state['profile_hash']
    assert updated['checkpoints'][0]['attempts']==3
    assert updated['can_resume']
    with store.lease(job) as token:
        store.begin(job,token,'script_draft',{'new_prompt':2},local_model_call=True)
        assert store.get(job)['provider_calls']==4
        assert store.get(job)['checkpoints'][0]['attempts']==4
        with pytest.raises(ValueError):store.begin(job,token,'image_request/test',{},external_tool_call=True,provider_call=True,estimated_cost_usd=.2)
        store.begin(job,token,'image_request/test',{},external_tool_call=True)
        assert store.get(job)['provider_calls']==5
        assert store.get(job)['reserved_cost_units']==0


def test_dispatch_owner_adopts_startup_only_before_lease(store):
    job=create(store)['id']
    store.record_dispatched_worker(job,os.getpid())
    assert store.is_dispatched_worker(job)
    with store.lease(job):assert not store.is_dispatched_worker(job)
    assert not store.is_dispatched_worker(job)


def test_clean_decoder_empty_log_is_not_missing_stage_evidence(store, monkeypatch):
    from atnos.production import branded_finish as finish
    from atnos.production import qa_gate
    job=create(store)['id']
    root=store.context(job).artifact_dir
    (root/'short.mp4').write_bytes(b'test video bytes')
    def media_command(command, log, timeout=400):
        log.write_text('', encoding='utf-8')
        if '-frames:v' in command:
            Image.new('RGB',(8,8),'navy').save(command[-1])
    monkeypatch.setattr(finish,'run',media_command)
    monkeypatch.setattr(finish,'resolve_ffmpeg',lambda:'ffmpeg')
    monkeypatch.setattr(qa_gate,'probe_media',lambda _: {'width':1080,'height':1920,'duration_s':45})
    checked=finish.check_branded_media(root,{'segments':[{'id':'s01','start':0,'end':4}]})
    assert (root/'decode.log').stat().st_size==0
    assert checked.payload['decoder_exit_code']==0
    with store.lease(job) as token:
        store.begin(job,token,'media_qa',{})
        store.complete(job,token,'media_qa',checked.payload,[checked.artifact_path,*checked.evidence_paths])
    assert store.get(job)['checkpoints'][0]['status']=='done'


def test_failed_decoder_never_passes_media_qa(tmp_path,monkeypatch):
    from atnos.production import branded_finish as finish
    from atnos.production import qa_gate
    monkeypatch.setattr(qa_gate,'probe_media',lambda _: {'width':1080,'height':1920,'duration_s':45})
    monkeypatch.setattr(finish,'resolve_ffmpeg',lambda:'ffmpeg')
    def failed(*args,**kwargs):raise RuntimeError('Invalid video stream')
    monkeypatch.setattr(finish,'run',failed)
    with pytest.raises(RuntimeError,match='Invalid video'):
        finish.check_branded_media(tmp_path,{'segments':[]})
    assert not (tmp_path/'media_qa.json').exists()


def test_export_refuses_unfinished_production(store):
    from atnos.production.brand_delivery import export_brand_trial
    with pytest.raises(ProductionBlocked,match='completed local'):
        export_brand_trial(store,create(store)['id'],'E:/Rolki/Wealth-Blueprint/test')
