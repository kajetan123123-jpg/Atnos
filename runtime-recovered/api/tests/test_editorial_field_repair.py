import pytest
from atnos.production.steps.order_script import (
    EditorialScript, EditorialFieldRepair, expand_editorial_patches,
    _is_script_authoring_schema,
)


def source():
    return EditorialScript(title='A decision', description='A practical decision about spending.', segments=[
        dict(id=str(i), type='practical', text='Compare your options before making a purchase.',
             on_screen='COMPARE OPTIONS', visual_intent='An intact coffee cup on a wooden desk.',
             claim_ids=[], fact_ids=[], pronunciation_terms=['coffee']) for i in range(4)])


def test_field_patch_preserves_scene_type_claims_and_untargeted_segments():
    script = source()
    before = script.model_dump()
    result = expand_editorial_patches(script, [{'code':'naturalness','segment_ids':['1']}],
        {'patches':[{'id':'1','text':'Decide whether this purchase is worth delaying your goal.',
                     'on_screen':'MAKE A DELIBERATE CHOICE'}]})
    replacement = result['replacements'][0]
    assert replacement['text'] != before['segments'][1]['text']
    for field in ('type','visual_intent','claim_ids','fact_ids','pronunciation_terms'):
        assert replacement[field] == before['segments'][1][field]
    assert script.model_dump() == before
    assert _is_script_authoring_schema(EditorialFieldRepair)


@pytest.mark.parametrize('ids', [['0'], ['1','1'], ['unknown']])
def test_field_patch_rejects_unauthorized_or_duplicate_ids(ids):
    with pytest.raises(ValueError):
        expand_editorial_patches(source(), [{'code':'naturalness','segment_ids':['1']}],
            {'patches':[dict(id=sid,text='A complete revised sentence.',on_screen='A CLEAR IDEA') for sid in ids]})


def test_field_patch_cannot_mutate_calculations_or_classification():
    with pytest.raises(ValueError):
        expand_editorial_patches(source(), [{'code':'naturalness','segment_ids':['1']}],
            {'patches':[dict(id='1',text='A complete revised sentence.',on_screen='A CLEAR IDEA', type='explanation')]})
