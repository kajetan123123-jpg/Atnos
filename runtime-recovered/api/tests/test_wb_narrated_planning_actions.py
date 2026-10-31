from atnos.production.channels.wealth_blueprint.planning_desk_contract import ACTION_CHANGES,bind_planning_desk_intent,planning_desk_action
from atnos.production.channels.wealth_blueprint.scene_plan_models import VisualIntent
from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
import pytest

def test_supported_planning_changes_fit_the_persisted_visual_intent_contract():
    # The next planner/save/load boundary revalidates this model, unlike model_copy.
    for action,change in ACTION_CHANGES.items():
        intent=VisualIntent(viewer_should_understand=action,visible_change=change)
        assert VisualIntent.model_validate(intent.model_dump()).visible_change==change

@pytest.mark.parametrize('stage,text,expected_stage,action',[
 ('identify','Open your savings plan notebook to identify the emergency reserve and name one specific future purchase goal clearly.','separate','write-purposes'),
 ('separate','Inspect the single balance to locate any portion currently counted for both purposes simultaneously right now.','review','shared-balance'),
 ('review','Check which money is already assigned to both purposes twice.','review','shared-balance'),
 ('review','Check what is already assigned to the future purchase.','review','existing-assignments'),
 ('review','Assigning the same funds to two competing claims reduces availability for one purpose if an emergency occurs unexpectedly.','review','shared-balance'),
 ('identify','You have not spent your savings, but you may have promised them twice to different goals today.','identify','shared-balance'),
 ('identify','Look twice at your savings before deciding.','identify','purpose-context'),
])
def test_recorded_narration_selects_the_supported_action(stage,text,expected_stage,action):
    directed={'identify':'identify (actual need and reserve)','separate':'separate (reserve versus future goal)','review':'review (review both roles)'}[stage]
    relation=parse_scene_relation(text,visual_context=directed,reserve_context=True)
    result=bind_planning_desk_intent(VisualIntent(viewer_should_understand='Inspect actual planning roles',relation=relation),text)
    assert result.relation['stage']==expected_stage
    assert result.relation['planning_desk_action']==action
    if stage!=expected_stage:assert result.relation['planning_desk_stage_source']['directed_stage']==stage

def test_no_generic_money_keyword_invents_a_shared_claim_or_access_support():
    assert planning_desk_action('review','Check your future money goals.')=='review-both'
    with pytest.raises(ValueError):planning_desk_action('access','Check when money is available.')


def test_double_promise_repairs_stale_essential_expense_meaning_without_mutating_the_input():
    import copy
    text='You have not spent your savings, but you may have promised them twice to different goals today.'
    relation=parse_scene_relation(text,visual_context='identify (actual need and reserve)',reserve_context=True)
    assert relation['action']=='identify_need'
    before=copy.deepcopy(relation)
    result=bind_planning_desk_intent(VisualIntent(viewer_should_understand=relation['viewer_should_understand'],relation=relation),text)
    assert relation==before
    assert result.relation['action']=='compare_claims'
    assert [o['role'] for o in result.relation['objects']]==['shared_portion','reserve','goal']
    assert 'essential expense' not in result.viewer_should_understand
    for field in ('viewer_should_understand','initial_state','action','final_state','compliance_criterion'):
        assert getattr(result,field)==result.relation[field]
    assert VisualIntent.model_validate(result.model_dump()).relation==result.relation


def test_actual_expense_is_not_relabelled_as_a_double_promise():
    text='Identify the car repair and check the emergency reserve.'
    relation=parse_scene_relation(text,visual_context='identify (actual need and reserve)',reserve_context=True)
    result=bind_planning_desk_intent(VisualIntent(viewer_should_understand=relation['viewer_should_understand'],relation=relation),text)
    assert result.relation['action']=='identify_need'
    assert any(o['role']=='expense' for o in result.relation['objects'])
