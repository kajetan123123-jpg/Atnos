from copy import deepcopy
from types import SimpleNamespace
import jsonschema
import pytest
from atnos.production.steps.order_script import EditorialScript, generation_schema, bind_requested_native_reserve_scenes


def context(direction='Use native reserve_readiness checks for this emergency reserve.'):
    return SimpleNamespace(artifacts={'production_order':{'direction':direction},
        'project_profile':{'visual_style':{'prompt':'2D editorial drawing'}}})


def segment(kind,intent):
    return {'id':'s01','type':kind,'text':'Check your actual account conditions.',
        'visual_intent':intent,'on_screen':'CHECK ACCESS','claim_ids':[],
        'fact_ids':[],'pronunciation_terms':[]}


def test_actual_bad_every_scene_wordmark_response_cannot_decode_as_a_body():
    schema=bind_requested_native_reserve_scenes(generation_schema(EditorialScript),context())
    scene_schema={**schema,'properties':{'segments':schema['properties']['segments']},'required':['segments']}
    bad={'segments':[segment('practical','Wealth Blueprint logo')]}
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(bad,scene_schema)
    good={'segments':[{**segment('hook' if i==1 else 'practical',
        'access (reserve and questions about access, timing and conditions)'), 'id':f's{i:02d}'} for i in range(1,5)]
        +[{**segment('cta','Wealth Blueprint logo only, no host or props'),'id':'s05'}]}
    jsonschema.validate(good,scene_schema)


def test_ordinary_other_visual_requests_keep_their_existing_schema():
    schema=generation_schema(EditorialScript)
    original=deepcopy(schema)
    assert bind_requested_native_reserve_scenes(schema,context('Explain opportunity cost.'))==original
    assert schema==original


def test_explicit_requested_cta_is_kept_verbatim_without_inventing_a_new_closing():
    c=context('Use native reserve_readiness. CTA: Follow Wealth Blueprint. Build wealth on purpose.')
    schema=bind_requested_native_reserve_scenes(generation_schema(EditorialScript),c)
    assert schema['$defs']['NativeReserveClosing']['properties']['text']['enum']==[
        'Follow Wealth Blueprint. Build wealth on purpose.']
