"""A field-only operation cannot normalize a narration outside its field mask."""
from copy import deepcopy
import pytest
from atnos.production.steps import order_script as scripts
from test_registered_editorial_field_map import registered_v8
from test_production_order_script import execution
from test_production_order_store import store


@pytest.mark.parametrize('target',['visual_intent','on_screen'])
def test_field_mask_preserves_unnormalized_narration_in_same_segment(registered_v8,target):
    _,_,context,original,_=registered_v8
    script=original.model_copy(deep=True)
    script.segments[4].text='Compare what matters most to you'
    code='unsupported_visual_intent' if target=='visual_intent' else 'naturalness'
    issues=[{'code':code,'segment_ids':['4'],'fields':[target],'detail':'Only this explicit field may change.'}]
    mask={'4':[target]}
    value='A static empty balance scale on cream paper, revealed and held by the renderer.' if target=='visual_intent' else 'COMPARE WHAT MATTERS'
    raw=scripts.expand_editorial_patches(script,issues,{'patches':{'4':{target:value}}},fields_by_segment=mask)
    fixed=scripts.validate_repair_response(context,script,issues,raw,
        defer_editorial_validation=True,fields_by_segment=mask)
    assert fixed.segments[4].text==script.segments[4].text
    for old,new in zip(script.segments,fixed.segments,strict=True):
        assert old.model_dump(exclude=set(mask.get(old.id,[])))==new.model_dump(exclude=set(mask.get(old.id,[])))


def test_validator_independently_rejects_mutation_outside_field_mask(registered_v8):
    _,_,context,script,issues=registered_v8
    issues=[issues[3]]
    mask={'4':['visual_intent']}
    raw=scripts.expand_editorial_patches(script,issues,{'patches':{'4':{
        'visual_intent':'An intact static scale with empty pans, revealed and held by the renderer.'}}},fields_by_segment=mask)
    raw['replacements'][0]['on_screen']='UNAUTHORIZED HEADLINE'
    with pytest.raises(ValueError,match='field scope'):
        scripts.validate_repair_response(context,script,issues,raw,
            defer_editorial_validation=True,fields_by_segment=mask)
