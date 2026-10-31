from copy import deepcopy
import pytest

from atnos.production.calculation_plan import CalculationPlan, compile_plan
from atnos.production.chart_series import compound_checkpoints, validate_compound_checkpoints
from atnos.production.channels.wealth_blueprint.deterministic import _build_chart_cue
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.project_manifest import ProjectManifest


def claim():
    plan = CalculationPlan.model_validate({'rationale':'One hypothetical opportunity-cost example.', 'examples':[
        {'id':'c1','kind':'lump_sum','purpose':'Show the hypothetical future of one ten-dollar deposit.',
         'currency':'dollars','principal':10,'annual_rate':.08,'years':30,'spoken_rounding':1}]})
    return compile_plan(plan,'dollars')['claims']['c1']


def chart():
    return _build_chart_cue(focus='chart', narration='If ten dollars earned eight percent a year for thirty years, it could grow to roughly one hundred dollars.',
        title='The other path', claims=[claim()]).model_dump(mode='json')


def manifest():
    return scene_plan_to_project_manifest(job_id='test-compound',topic='The $10 Trap',script='Hypothetical.',
        plan={'scenes':[
             {'scene_id':'s00','start_sec':0,'end_sec':2,'layout_id':'fullscreen_statement','visual_focus':'statement',
              'on_screen_text':'The choice','narration_excerpt':'Consider the other path.'},
             {'scene_id':'s01','start_sec':2,'end_sec':11,'layout_id':'chart_focus','visual_focus':'chart',
              'on_screen_text':'The other path','narration_excerpt':'A hypothetical path, not a forecast.', 'chart':chart()},
             {'scene_id':'s02','start_sec':11,'end_sec':13,'layout_id':'fullscreen_statement','visual_focus':'statement',
              'on_screen_text':'Pause first','narration_excerpt':'Pause before choosing.'}]},
        duration_hint=13,verified_claims=[claim()])


def test_one_claim_derives_four_dated_balances_not_rate_or_horizon_as_money():
    series = compound_checkpoints(claim())
    assert series['years'] == [0,10,20,30]
    assert series['display_values'] == [10,22,47,101]
    assert series['values'] == pytest.approx([10,21.5892499727,46.6095714385,100.6265688907])
    assert series['assumption_label'] == 'Hypothetical · 8% annually'
    assert series['convention'] == 'annual_compounding_no_additional_deposits'
    assert 8 not in series['values'] and 30 not in series['values']


@pytest.mark.parametrize('field,value',[('values',[10,22,47,101]),('years',[0,1,2,3]),('display_values',[10,22,47,100]),
    ('calculation_hash','f'*64),('annual_rate',.10),('assumption_label','Guaranteed · 8% annually')])
def test_modified_derived_evidence_is_rejected(field,value):
    series=compound_checkpoints(claim());series[field]=value
    with pytest.raises(ValueError,match='verified calculation'):
        validate_compound_checkpoints(series,[claim()])


def test_unknown_and_duplicate_claims_are_rejected():
    series=compound_checkpoints(claim())
    for claims in ([],[claim(),claim()]):
        with pytest.raises(ValueError,match='unknown or duplicate'):
            validate_compound_checkpoints(series,claims)


@pytest.mark.parametrize('operands', [[10,.08,0],[10,-.08,30],[10,1.1,30],[0,.08,30],[10,.08,30.5]])
def test_bad_conventions_fail_closed(operands):
    changed=claim();changed['operands']=operands
    with pytest.raises(ValueError):
        compound_checkpoints(changed)


def test_normal_planner_and_adapter_preserve_verified_claim_and_unspoken_checkpoints():
    planned=chart()
    assert planned['chart_type']=='COMPOUND_GROWTH'
    assert planned['claim_ids']==['c1']
    result=manifest()
    assert result.scenes[1].verified_series==planned['verified_series']
    assert result.calculation_claims==[claim()]
    assert result.scenes[1].chart_labels==['YEAR 0','YEAR 10','YEAR 20','YEAR 30']
    changed=deepcopy(result.model_dump(mode='json'));changed['scenes'][1]['chart_values'][1]=80
    with pytest.raises(ValueError,match='bound'):
        ProjectManifest.model_validate(changed)


def test_manifest_refuses_unbound_or_missing_verified_claim():
    changed=manifest().model_dump(mode='json');changed['calculation_claims']=[]
    with pytest.raises(ValueError,match='unknown'):
        ProjectManifest.model_validate(changed)


def test_normal_planner_routes_the_supplied_hypothetical_sentence_to_derived_growth():
    from atnos.production.channels.wealth_blueprint import WealthBlueprintScenePlanner
    from atnos.production.channels.wealth_blueprint.scene_plan_models import ScenePlannerInput, NarrationSegment
    rows=[NarrationSegment(id='s01',text='Your next small choice has another path.',start=0,end=3),
          NarrationSegment(id='s02',text='If that $10 earned an average 8% a year for 30 years, it could grow to roughly $100.',start=3,end=12),
          NarrationSegment(id='s03',text='Pause and consider the future before you spend.',start=12,end=15)]
    plan=WealthBlueprintScenePlanner().plan(ScenePlannerInput(script=' '.join(row.text for row in rows),
        segments=rows,claims=[claim()],segment_claim_map={'s02':['c1']}))
    derived=[scene for scene in plan.scenes if scene.chart and scene.chart.verified_series]
    assert len(derived)==1
    assert derived[0].chart.verified_series['display_values']==[10,22,47,101]
