from atnos.production.channels.wealth_blueprint.research_check_contract import research_check_relation

CONTEXT='Three stocks for a speculative watchlist, not a forecast.'

def test_binds_actual_named_check_and_risk_without_inventing_quantities():
    text='Compare SoFi member monetization against loan losses. Check valuation and dilution before investing.'
    result=research_check_relation(text,'SoFi: Research both sides','Qualitative labelled check-versus-risk diagram.',CONTEXT)
    assert result['kind']=='research_check'
    assert result['objects'][1]['label']==text.split('. ')[0]+'.'
    assert result['objects'][2]['label']=='Check valuation and dilution before investing.'
    assert not {'values','price','return','probability'} & result.keys()

def test_never_replaces_an_explicit_measured_chart_with_a_qualitative_diagram():
    assert research_check_relation('Compare SoFi growth and losses. Monitor credit risk.', 'SoFi: Growth',
        'Two bars showing the revenue and losses.',CONTEXT) is None

def test_unrelated_topic_or_company_mismatch_is_not_supported():
    text='Watch Neutron execution. Monitor delays and financing risk.'
    assert research_check_relation(text,'SoFi: Execution','Qualitative research comparison.',CONTEXT) is None
    assert research_check_relation('Watch SoFi growth. Monitor credit losses.','SoFi: Research','Qualitative research comparison.','Savings habit') is None


def test_actual_company_alias_and_possessive_keep_the_named_subject():
    assert research_check_relation('Track Hims repeat customers. Monitor regulation and costs.', 'Hims and Hers: Research check','Qualitative comparison.',CONTEXT)
    assert research_check_relation("Watch Rocket Lab’s Neutron execution. Monitor delays and financing.",'Rocket Lab: Research check','Qualitative comparison.',CONTEXT)
    assert research_check_relation('Inspect Rocket Lab Neutron execution. Check delays and financing.','Rocket Lab: Research check','Qualitative comparison.',CONTEXT)


def test_normal_planner_preserves_three_distinct_qualitative_research_boards():
    from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
    from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment,ScenePlannerInput
    lines=[('Speculative ideas need research, not blind confidence.','Research the business'),
       ('Watch Rocket Lab execution and cash conversion. Monitor delays and financing risk.','Rocket Lab: Research check'),
       ('Compare SoFi member monetization against loan losses. Check valuation and dilution risk.','SoFi: Research check'),
       ('Track Hims repeat customers and margins. Monitor regulation and acquisition costs.','Hims: Research check'),
       ('Follow Wealth Blueprint. Build wealth on purpose.','Wealth Blueprint')]
    segments=[NarrationSegment(id=f's{i+1:02d}',text=text,on_screen=title,
        visual_intent='Qualitative labelled check-versus-risk diagram.',start=i*7,end=(i+1)*7) for i,(text,title) in enumerate(lines)]
    plan=WealthBlueprintScenePlanner().plan(ScenePlannerInput(script=' '.join(s.text for s in segments),segments=segments,
        profile={'direction':CONTEXT,'disable_visual_story':True}))
    boards=[s for s in plan.scenes if (s.visual_intent.relation or {}).get('kind')=='research_check']
    assert len(boards)==3
    assert len({s.visual_intent.visible_change for s in boards})==3
    assert all(s.chart is None and s.layout_id=='comparison' for s in boards)
    assert plan.validation.ok
