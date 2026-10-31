from types import SimpleNamespace as NS
import pytest
from atnos.production.editorial_direction import research_direction
from atnos.production.order_store import ProductionBlocked

def scene(check='Inspect Neutron execution.', risk='Assess delays and financing.'):
    text=check+' '+risk
    words=text.split()
    return NS(scene_type='ImageScene',verified_series=None,narration=text,
        timing=NS(start_sec=10.,end_sec=18.),caption_words=[NS(text=w,start_sec=10+i*.5,end_sec=10.4+i*.5) for i,w in enumerate(words)],
        visual_relation={'kind':'research_check','objects':[{'role':'subject','label':'Research subject'},
            {'role':'check','label':check},{'role':'risk','label':risk}]})

def test_risk_action_uses_final_audio_and_keeps_exact_clauses():
    s=scene();d=research_direction(s)
    assert d['mode']=='execution_to_cash'
    assert d['risk_sec']==2.
    assert d['risk']=='Assess delays and financing.'
    assert d['measurement']=='schematic_not_company_data'
    assert 'narration_hash' in d

@pytest.mark.parametrize('mutation',['unsupported','mismatch','no_alignment','late_risk'])
def test_no_fallback_or_fake_cue(mutation):
    s=scene()
    if mutation=='unsupported':s=scene('Check something else.')
    if mutation=='mismatch':s.visual_relation['objects'][1]['label']='Made up check'
    if mutation=='no_alignment':s.caption_words=[]
    if mutation=='late_risk':s.caption_words[4].start_sec=17.7;s.caption_words[4].end_sec=17.9
    with pytest.raises(ProductionBlocked):research_direction(s)

def test_quality_contract_is_derived_instead_of_trusting_stored_plan():
    from atnos.production.steps.order_delivery import _remotion_intended_visual
    s=scene();s.visual_relation['editorial_direction']=research_direction(s)
    assert 'research relationships' in _remotion_intended_visual(s)
    s.visual_relation['editorial_direction']['risk_sec']=0
    with pytest.raises(ValueError,match='differs'):_remotion_intended_visual(s)

def test_editorial_review_retains_data_and_quality_limits():
    from atnos.production.visual_semantic_audit import _profile_contract
    contract=_profile_contract('wealth_blueprint','editorial_motion')
    assert 'invented data' in contract and 'quantitative axes' in contract
