"""Regression for the actual Flux critique contradicting its blind observation."""
import json
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from atnos.production import wb_image_review as review
from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash


def observation():
    return {
        'objects_and_positions':[
            'One large cream rectangular canvas stands upright on the wooden surface at left.',
            'One teal shopping bag with two black handles stands on the wooden surface at right.',
        ],
        'object_inventory_complete':True,
        'setting':'Two props stand on a wooden table; the purpose of the room is not established.',
        'materials_and_textures':['Wood grain on the supporting surface; paper texture on the bag.'],
        'palette_and_regions':['Deep navy background, cream canvas, teal bag, warm gold-lit tabletop.'],
        'lighting_and_depth':'Warm directional lighting from left casts soft shadows with clear foreground depth.',
        'drawing_medium_and_linework':'A digital still life with painted shading and restrained outlined edges.',
        'framing_and_readability':'Both props are fully visible and separated; the canvas is large and upright.',
        'caption_areas':['Quiet dark navy upper half with no objects or text.'],
        'visible_text_and_marks':'No visible text, numbers, labels or logos on either prop or background.',
        'visible_defects':[],
        'uncertainty':'The room purpose cannot be determined from the visible wooden surface.',
    }


def grounded_answer(observed, status='passed'):
    fields={
        'required_objects_and_counts':['objects_and_positions','object_inventory_complete'],
        'composition_and_readability':['objects_and_positions','framing_and_readability','caption_areas'],
        'style_palette_and_lighting':['materials_and_textures','palette_and_regions','lighting_and_depth','drawing_medium_and_linework'],
        'integrity_and_no_embedded_text':['visible_text_and_marks','visible_defects'],
    }
    result={}
    for name,refs in fields.items():
        value=observed[refs[0]]
        result[name]={'status':status,'observation_fields':refs,
                      'evidence':value[0] if isinstance(value,list) else value}
    return result


def test_wb_observation_has_medium_palette_materials_light_and_framing_not_fiction_fields():
    observed=review.IllustrationObservation.model_validate(observation())
    assert 'navy' in observed.palette_and_regions[0]
    assert 'Warm directional' in observed.lighting_and_depth
    old={'subjects':[],'clothing_and_headwear':[],'objects_and_positions':[],
         'actions_and_poses':[],'setting':'Still life composition','style_and_mood':'Digital illustration',
         'visible_defects':[],'uncertainty':'No obscured areas visible'}
    with pytest.raises(ValidationError):
        review.IllustrationObservation.model_validate(old)
    assert 'prior/future action' in review.OBSERVATION_PROMPT
    assert 'unknown, NOT proof' in review.OBSERVATION_PROMPT


def test_actual_flat_light_negation_never_becomes_an_observed_fact():
    observed=observation()
    raw=grounded_answer(observed)
    raw['style_palette_and_lighting'].update(status='failed',
        evidence='The candidate uses flat, even illumination with no directional light or shadow contrast.')
    normalized=review.grounded_checks(raw,observed)
    item=normalized['style_palette_and_lighting']
    assert 'Warm directional lighting' in item['evidence']
    assert 'flat, even' not in item['evidence']
    assert not review.verdict(normalized)['passed']


def test_a_real_wrong_money_prop_can_still_be_a_grounded_rejection():
    observed=observation()
    raw=grounded_answer(observed)
    raw['required_objects_and_counts']['status']='failed'
    normalized=review.grounded_checks(raw,observed)
    assert normalized['required_objects_and_counts']['status']=='failed'
    assert 'canvas' in normalized['required_objects_and_counts']['evidence']
    result=review.verdict(normalized)
    assert not result['passed']
    assert result['user_approved'] is False and result['human_review']=='not_performed'


def test_phone_count_mismatch_overrides_false_model_pass():
    """Regression for the rejected candidate that depicted several phones."""
    observed = observation()
    observed['objects_and_positions'] = [
        'Three separate smartphones are visible side by side in the central foreground.'
    ]
    raw = grounded_answer(observed)
    raw['required_objects_and_counts'].update(status='passed', evidence='The scene has exactly one phone.')
    need = {'asset_subject': 'one smartphone', 'asset_composition': 'Exactly one phone in frame.'}
    normalized = review.grounded_checks(raw, observed, need=need)
    verdict = review.verdict(normalized)
    assert normalized['required_objects_and_counts']['status'] == 'failed'
    assert 'Three separate smartphones' in normalized['required_objects_and_counts']['evidence']
    assert verdict['passed'] is False
    assert verdict['user_approved'] is False


@pytest.mark.parametrize('description', [
    'One smartphone is centered in the frame.',
    'Exactly one phone appears beside a blank card.',
])
def test_phone_count_guard_accepts_complete_single_phone_observation(description):
    observed = observation()
    observed['objects_and_positions'] = [description]
    raw = grounded_answer(observed)
    need = {'asset_subject': 'one smartphone', 'asset_composition': 'Exactly one phone in frame.'}
    assert review.grounded_checks(raw, observed, need=need)['required_objects_and_counts']['status'] == 'passed'


@pytest.mark.parametrize('value',[[],['Unknown palette; colors not established by the observation.']])
def test_missing_or_unclear_palette_cannot_become_absence_or_a_pass(value):
    observed=observation()
    observed['palette_and_regions']=value
    raw=grounded_answer(observed)
    raw['style_palette_and_lighting']['status']='failed'
    normalized=review.grounded_checks(raw,observed)
    assert normalized['style_palette_and_lighting']['status']=='uncertain'


def test_invented_office_props_cannot_become_pixel_evidence():
    observed=observation()
    raw=grounded_answer(observed)
    raw['composition_and_readability'].update(status='failed',
        evidence='There is no laptop, lamp or books, so the home-office desk is missing.')
    item=review.grounded_checks(raw,observed)['composition_and_readability']
    assert 'laptop' not in item['evidence'] and 'lamp' not in item['evidence']


def test_pass_requires_every_essential_dimension_and_an_exact_fact():
    observed=observation()
    raw=grounded_answer(observed)
    assert review.verdict(review.grounded_checks(raw,observed))['passed']
    raw['style_palette_and_lighting']['observation_fields']=['materials_and_textures']
    assert review.grounded_checks(raw,observed)['style_palette_and_lighting']['status']=='uncertain'
    raw=grounded_answer(observed)
    raw['required_objects_and_counts']['observation_fields']=['setting']
    assert review.grounded_checks(raw,observed)['required_objects_and_counts']['status']=='uncertain'


def test_uncertain_only_review_does_not_prove_generator_failure(tmp_path):
    image=tmp_path/'image.png'; image.write_bytes(b'pixels')
    identity={'image_sha256':file_hash(image),'contract':1}
    key=content_hash(identity)
    directory=tmp_path/'assets'/'automatic-review'/key; directory.mkdir(parents=True)
    trace=directory/'observation.json'; trace.write_text('{}','utf-8')
    checks={name:{'status':'uncertain','evidence':'The required dimensions are not established.'}
            for name in review.WBIllustrationChecks.model_fields}
    result={**review.verdict(checks),'asset_id':'candidate','identity':identity,
            'evidence_files':{'observation.json':file_hash(trace)}}
    (directory/'review.json').write_text(json.dumps(result),'utf-8')
    asset=SimpleNamespace(id='candidate',quality_status='REJECTED',
                          reviewed_by='atnos_automatic_visual_qc:'+key)
    brain=SimpleNamespace(resolve_file=lambda _:image)
    assert review.proven_rejection_count(SimpleNamespace(artifact_dir=tmp_path),brain,[asset])==0


def test_v2_qualification_rechecks_grounded_facts_and_bounded_format_sources(tmp_path):
    observed=observation(); raw=grounded_answer(observed)
    image=tmp_path/'image.png'; image.write_bytes(b'pixels')
    identity={'image_sha256':file_hash(image),'contract':2,
              'observation_contract':review.OBSERVATION_CONTRACT}
    key=content_hash(identity)
    directory=tmp_path/'assets'/'automatic-review'/key; directory.mkdir(parents=True)
    sources={'observation':'observation-format-1-validated.json',
             'comparison':'comparison-format-2-validated.json'}
    for name,payload in ((sources['observation'],observed),(sources['comparison'],raw)):
        (directory/name).write_text(json.dumps(payload),'utf-8')
    result={**review.verdict(review.grounded_checks(raw,observed)),
            'asset_id':'candidate','identity':identity,'grounding_sources':sources,
            'evidence_files':{name:file_hash(directory/name) for name in sources.values()}}
    report=directory/'review.json'; report.write_text(json.dumps(result),'utf-8')
    context=SimpleNamespace(artifact_dir=tmp_path)
    asset=SimpleNamespace(id='candidate',reviewed_by='atnos_automatic_visual_qc:'+key)
    review.verify_qualification(context,asset,image)
    result['checks']['style_palette_and_lighting']['evidence']='A fabricated favorable appearance assessment.'
    report.write_text(json.dumps(result),'utf-8')
    with pytest.raises(ProductionBlocked,match='invalid'):
        review.verify_qualification(context,asset,image)


def test_new_contract_does_not_reuse_legacy_review_identity():
    legacy={'image_sha256':'same-pixels','reference_sha256':'same-reference','contract':1}
    current={**legacy,'contract':2,'observation_contract':review.OBSERVATION_CONTRACT}
    assert content_hash(current)!=content_hash(legacy)


def test_paraphrased_or_invented_evidence_is_replaced_by_facts_without_a_format_loop(tmp_path,monkeypatch):
    observed=observation(); answers=[]
    first=grounded_answer(observed,'failed')
    first['style_palette_and_lighting']['evidence']='No directional light or shadow contrast is present.'
    def inference(settings,directory,name,**kwargs):
        answers.append(name)
        return review.GroundedWBIllustrationChecks.model_validate(first)
    monkeypatch.setattr(review,'_infer',inference)
    async def run(stage,inputs,operation,**kwargs):
        return (await operation()).payload
    executor=SimpleNamespace(store=SimpleNamespace(settings=SimpleNamespace()),run=run)
    result=asyncio.run(review.infer_review(executor,tmp_path,'a'*64,{}, {},'comparison','Truth',
        review.GroundedWBIllustrationChecks,payload_validator=lambda p:review.validate_grounding_format(p,observed)))
    assert answers==['comparison']
    assert review.grounded_checks(result,observed)['required_objects_and_counts']['status']=='failed'
    assert 'Warm directional' in review.grounded_checks(result,observed)['style_palette_and_lighting']['evidence']
    assert review._validated_source(tmp_path,'comparison',review.GroundedWBIllustrationChecks)=='comparison-validated.json'


def completed_comparison(observed,answer,need=None,style=None):
    from atnos.production.steps.order_script import generation_schema
    inputs={'prompt':'Evaluate pixels only. '+json.dumps({'candidate_observation':observed,
            'required_scene':need or {},'style_direction':style or {}}),
            'schema':generation_schema(review.GroundedWBIllustrationChecks),
            'model_revision':{'name':'reviewer','digest':'a'*64},'image_sha256':None,
            'options':{'temperature':0,'num_ctx':8192,'num_predict':2200},'think':False}
    return {'status':'response_received','input_hash':content_hash(inputs),'inputs':inputs,
            'response':{'done':True,'done_reason':'stop','message':{'content':json.dumps(answer)}}}


def test_completed_cached_negative_revalidates_without_model_or_post_and_archives_old_error(tmp_path,monkeypatch):
    from atnos.production.story_image_review import _infer
    observed=observation(); raw=grounded_answer(observed,'failed')
    raw['style_palette_and_lighting']['evidence']=' '.join(observed['palette_and_regions']+observed['materials_and_textures'])
    record=completed_comparison(observed,raw)
    (tmp_path/'comparison.json').write_text(json.dumps(record),'utf-8')
    old_error={'_review_format_error':'Earlier exact-one-copy constraint rejected a truthful concatenation.'}
    (tmp_path/'comparison-validated.json').write_text(json.dumps(old_error),'utf-8')
    monkeypatch.setattr('atnos.production.story_image_review.httpx.Client',
                        lambda **_:pytest.fail('Completed comparison made a POST'))
    monkeypatch.setattr(review,'_infer',_infer)
    contracts=[]
    async def run(stage,inputs,operation,**kwargs):
        contracts.append((inputs['validation_contract'],kwargs['local_model_call']))
        return (await operation()).payload
    executor=SimpleNamespace(store=SimpleNamespace(settings=SimpleNamespace()),run=run)
    result=asyncio.run(review.infer_review(executor,tmp_path,'a'*64,{},record['inputs']['model_revision'],
        'comparison',record['inputs']['prompt'],review.GroundedWBIllustrationChecks,
        payload_validator=lambda p:review.validate_grounding_format(p,observed)))
    assert contracts==[(3,False)]
    assert review.grounded_checks(result,observed)['required_objects_and_counts']['status']=='failed'
    assert json.loads((tmp_path/'comparison-validation-contract-2-error.json').read_text('utf-8'))==old_error
    assert json.loads((tmp_path/'comparison.json').read_text('utf-8'))==record


def test_cached_parser_checks_original_request_and_keeps_negative_verdict():
    observed=observation(); raw=grounded_answer(observed,'failed')
    record=completed_comparison(observed,raw,need={'object':'banknote'},style={'id':'D'})
    result=review.revalidate_cached_comparison(record,observed,need={'object':'banknote'},style={'id':'D'})
    assert result['required_objects_and_counts']['status']=='failed'
    with pytest.raises(ValueError,match='does not match'):
        review.revalidate_cached_comparison(record,observed,need={'object':'coin'})
    record['response']['done_reason']='length'
    with pytest.raises(ValueError,match='not an intact'):
        review.revalidate_cached_comparison(record,observed)


def test_inventory_completeness_alone_does_not_prove_missing_objects():
    observed=observation(); raw=grounded_answer(observed,'failed')
    raw['required_objects_and_counts'].update(observation_fields=['object_inventory_complete'],
                                             evidence='object_inventory_complete: true')
    assert review.grounded_checks(raw,observed)['required_objects_and_counts']['status']=='uncertain'


def test_v2_all_uncertain_does_not_count_but_an_intact_failed_dimension_does(tmp_path):
    observed=observation(); raw=grounded_answer(observed,'uncertain')
    image=tmp_path/'image.png'; image.write_bytes(b'pixels')
    identity={'image_sha256':file_hash(image),'contract':2,'observation_contract':review.OBSERVATION_CONTRACT}
    key=content_hash(identity)
    directory=tmp_path/'assets'/'automatic-review'/key; directory.mkdir(parents=True)
    sources={'observation':'observation-validated.json','comparison':'comparison-validated.json'}
    asset=SimpleNamespace(id='candidate',quality_status='REJECTED',reviewed_by='atnos_automatic_visual_qc:'+key)
    brain=SimpleNamespace(resolve_file=lambda _:image)
    context=SimpleNamespace(artifact_dir=tmp_path)
    def save():
        for name,payload in ((sources['observation'],observed),(sources['comparison'],raw)):
            (directory/name).write_text(json.dumps(payload),'utf-8')
        result={**review.verdict(review.grounded_checks(raw,observed)),
                'asset_id':'candidate','identity':identity,'grounding_sources':sources,
                'evidence_files':{name:file_hash(directory/name) for name in sources.values()}}
        (directory/'review.json').write_text(json.dumps(result),'utf-8')
    save()
    assert review.proven_rejection_count(context,brain,[asset])==0
    raw['required_objects_and_counts']['status']='failed'
    raw['required_objects_and_counts']['evidence']='The required bill is not depicted; the observed item is a canvas.'
    save()
    assert review.proven_rejection_count(context,brain,[asset])==1
