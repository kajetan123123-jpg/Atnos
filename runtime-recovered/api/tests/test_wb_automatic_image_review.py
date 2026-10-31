import json
import asyncio
from types import SimpleNamespace
import pytest
from atnos.production.wb_image_review import verdict, verify_qualification
from atnos.production.project_profile import content_hash
from atnos.production.order_store import file_hash, ProductionBlocked


def checks(status='passed'):
    return {name:{'status':status,'evidence':'A concrete observation supporting this assessment.'}
            for name in ('required_objects_and_counts','composition_and_readability',
                         'style_palette_and_lighting','integrity_and_no_embedded_text')}


def test_uncertainty_does_not_approve_an_image():
    result=verdict(checks('uncertain'))
    assert not result['passed']
    assert len(result['blockers'])==4
    assert result['user_approved'] is False
    assert verdict(checks())['human_review']=='not_performed'


def test_long_negative_evidence_remains_a_rejection_not_a_retry():
    data=checks()
    data['required_objects_and_counts']={'status':'failed','evidence':'Observed a woman and dog instead of the requested bill and bag. '*10}
    result=verdict(data)
    assert result['blockers']==['required_objects_and_counts']
    assert len(result['checks']['required_objects_and_counts']['evidence'])>400


def test_empty_setting_remains_missing_evidence_without_inventing_a_scene():
    from atnos.production.wb_image_review import IllustrationObservation
    data={'objects_and_positions':[],'object_inventory_complete':False,
          'setting':'','materials_and_textures':[],'palette_and_regions':[],
          'lighting_and_depth':'','drawing_medium_and_linework':'A muted painted drawing.',
          'framing_and_readability':'','caption_areas':[],'visible_text_and_marks':'',
          'visible_defects':[],'uncertainty':''}
    result=IllustrationObservation.model_validate(data)
    assert 'not established' in result.setting.casefold()
    assert 'must remain uncertain' in result.uncertainty
    assert 'desk' not in result.setting and not result.objects_and_positions


def test_backend_change_requires_intact_negative_pixel_evidence(tmp_path):
    from atnos.production.wb_image_review import proven_rejection_count
    assets=[]
    for name in ('first','second'):
        image=tmp_path/(name+'.png');image.write_bytes(name.encode())
        identity={'image_sha256':file_hash(image),'contract':1}
        key=content_hash(identity)
        directory=tmp_path/'assets'/'automatic-review'/key;directory.mkdir(parents=True)
        trace=directory/'observation.json';trace.write_text('{}','utf-8')
        payload={**verdict(checks('failed')),'asset_id':name,'identity':identity,
                 'evidence_files':{'observation.json':file_hash(trace)}}
        (directory/'review.json').write_text(json.dumps(payload),'utf-8')
        assets.append(SimpleNamespace(id=name,quality_status='REJECTED',file_path=image,
                      reviewed_by='atnos_automatic_visual_qc:'+key))
    brain=SimpleNamespace(resolve_file=lambda asset:asset.file_path)
    context=SimpleNamespace(artifact_dir=tmp_path)
    assert proven_rejection_count(context,brain,assets)==2
    assets[0].file_path.write_bytes(b'changed pixels')
    assert proven_rejection_count(context,brain,assets)==1


def test_completed_review_is_revalidated_without_another_request(tmp_path,monkeypatch):
    from atnos.production.story_image_review import _infer
    from atnos.production.wb_image_review import WBIllustrationChecks
    from atnos.production.steps.order_script import generation_schema
    data=checks('failed')
    for item in data.values():
        item['evidence']='Required physical objects were absent in the observed illustration. '*8
    revision={'name':'reviewer','digest':'revision'}
    identity={'prompt':'Check actual pixels','schema':generation_schema(WBIllustrationChecks),
              'model_revision':revision,'image_sha256':None,
              'options':{'temperature':0,'num_ctx':8192,'num_predict':2200},'think':False}
    (tmp_path/'comparison.json').write_text(json.dumps({'status':'response_received',
        'input_hash':content_hash(identity),'inputs':identity,
        'response':{'done':True,'done_reason':'stop','message':{'content':json.dumps(data)}}}),'utf-8')
    monkeypatch.setattr('atnos.production.story_image_review.httpx.Client',
                        lambda **_: pytest.fail('Cached completed review sent another request'))
    result=_infer(SimpleNamespace(),tmp_path,'comparison',prompt=identity['prompt'],
                  schema=WBIllustrationChecks,revision=revision)
    assert not verdict(result.model_dump())['passed']


def test_qualified_asset_requires_intact_pixel_and_review_evidence(tmp_path):
    image=tmp_path/'image.png'
    image.write_bytes(b'image-content')
    identity={'image_sha256':file_hash(image),'reference_sha256':'reference','contract':1}
    key=content_hash(identity)
    directory=tmp_path/'assets'/'automatic-review'/key
    directory.mkdir(parents=True)
    evidence=directory/'observation.json'
    evidence.write_text('{}','utf-8')
    result={**verdict(checks()),'identity':identity,'asset_id':'candidate',
            'evidence_files':{'observation.json':file_hash(evidence)}}
    (directory/'review.json').write_text(json.dumps(result),'utf-8')
    context=SimpleNamespace(artifact_dir=tmp_path)
    asset=SimpleNamespace(id='candidate',reviewed_by='atnos_automatic_visual_qc:'+key)
    verify_qualification(context,asset,image)
    evidence.write_text('{"tampered":true}','utf-8')
    with pytest.raises(ProductionBlocked):
        verify_qualification(context,asset,image)


def test_mutated_approved_image_cannot_be_reused(tmp_path):
    context=SimpleNamespace(artifact_dir=tmp_path)
    asset=SimpleNamespace(id='candidate',reviewed_by='atnos_automatic_visual_qc:'+'a'*64)
    image=tmp_path/'changed.png'
    image.write_bytes(b'changed')
    with pytest.raises(ProductionBlocked):
        verify_qualification(context,asset,image)


def test_format_repair_uses_new_trace_and_preserves_negative_verdict(tmp_path,monkeypatch):
    from atnos.production.wb_image_review import infer_review,WBIllustrationChecks
    calls=[]
    def inference(settings,directory,name,**kwargs):
        calls.append((name,kwargs['prompt']))
        if len(calls)==1:
            raise ValueError('Missing required evidence field')
        return WBIllustrationChecks.model_validate(checks('failed'))
    monkeypatch.setattr('atnos.production.wb_image_review._infer',inference)
    async def run(stage,inputs,operation,**kwargs):
        return (await operation()).payload
    executor=SimpleNamespace(store=SimpleNamespace(settings=SimpleNamespace()),run=run)
    result=asyncio.run(infer_review(executor,tmp_path,'a'*64,{}, {},'comparison','Original truth',WBIllustrationChecks))
    assert [c[0] for c in calls]==['comparison','comparison-format-1']
    assert 'negative findings' in calls[1][1]
    assert not verdict(result)['passed']


def test_format_repair_is_bounded_and_unknown_outcome_is_not_repeated(tmp_path,monkeypatch):
    from atnos.production.wb_image_review import infer_review,WBIllustrationChecks
    calls=[]
    def inference(*args,**kwargs):
        calls.append(args[2])
        raise ValueError('Invalid JSON')
    monkeypatch.setattr('atnos.production.wb_image_review._infer',inference)
    async def run(stage,inputs,operation,**kwargs):
        return (await operation()).payload
    executor=SimpleNamespace(store=SimpleNamespace(settings=SimpleNamespace()),run=run)
    with pytest.raises(ProductionBlocked,match='three distinct'):
        asyncio.run(infer_review(executor,tmp_path,'a'*64,{}, {},'comparison','Truth',WBIllustrationChecks))
    assert len(calls)==3
    calls.clear()
    def interrupted(*args,**kwargs):
        calls.append(args[2])
        raise RuntimeError('Previous request outcome uncertain')
    monkeypatch.setattr('atnos.production.wb_image_review._infer',interrupted)
    with pytest.raises(RuntimeError,match='uncertain'):
        asyncio.run(infer_review(executor,tmp_path,'a'*64,{}, {},'comparison','Truth',WBIllustrationChecks))
    assert len(calls)==1
