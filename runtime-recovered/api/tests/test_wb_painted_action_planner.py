"""Real full-trial layout-repeat failure; fixtures are not quality approvals."""
from pathlib import Path
import json
from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment,ScenePlannerInput
from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest,apply_native_illustration_system

def recorded():
    script=json.loads((Path(__file__).parent/'fixtures/wb_batch_20261013/painted-full-trial.json').read_text('utf8'))
    segments=[];offset=0
    for item in script['segments']:
        end=offset+len(item['text'].split())/2.5
        segments.append(NarrationSegment(id=item['id'],text=item['text'],on_screen=item['on_screen'],visual_intent=item['visual_intent'],start=offset,end=end))
        offset=end
    data=ScenePlannerInput(script=' '.join(s.text for s in segments),segments=segments,profile={'disable_visual_story':True,
        'native_illustration_system':'wb_planning_desk_painted_v1',
        'direction':'Use supported native reserve_readiness identify, separate and review scenes ONLY. No unrelated host or transfer.'},
        design_system={'visual_style_id':'D'})
    return script,WealthBlueprintScenePlanner().plan(data)

def test_actual_review_actions_survive_same_world_planning_and_bind_to_the_renderer():
    script,plan=recorded()
    assert plan.validation.ok
    actions=[s.visual_intent.relation['planning_desk_action'] for s in plan.scenes if s.visual_intent.relation and s.visual_intent.relation.get('kind')=='reserve_readiness']
    assert actions==['account-to-purposes','write-purposes','shared-balance','existing-assignments','revisit-goal']
    manifest=scene_plan_to_project_manifest(job_id='isolated-painted-plan',topic=script['title'],script=' '.join(s['text'] for s in script['segments']),plan=plan.model_dump(mode='json'),duration_hint=plan.duration_sec)
    manifest=apply_native_illustration_system(manifest,'wb_planning_desk_painted_v1')
    assert manifest.brand.illustrationSystem=='wb_planning_desk_painted_v1'
    assert [s.visual_relation['planning_desk_action'] for s in manifest.scenes if s.visual_relation and s.visual_relation.get('kind')=='reserve_readiness']==actions

def test_rule_seven_still_rejects_a_repeated_action_with_only_different_text():
    _,plan=recorded()
    plan.scenes[3].visual_intent=plan.scenes[2].visual_intent.model_copy(deep=True)
    result=validate_scene_plan(plan)
    assert any(i.code=='layout_repeat' for i in result.issues)

def test_recorded_double_claim_audit_and_conditional_consequence_are_different_actions():
    fixtures=Path(__file__).parents[2]/'remotion-renderer/tests/fixtures'
    canonical=json.loads((fixtures/'planning-story-canonical.json').read_text(encoding='utf8'))
    voice=json.loads((fixtures/'planning-story-voice.json').read_text(encoding='utf8'))
    data=ScenePlannerInput(script=' '.join(s['text'] for s in canonical['segments']),
        segments=[NarrationSegment(id=s['id'],text=s['text'],visual_intent=s['visual_intent'],on_screen=s['on_screen'],
            start=v['timing']['start_sec'],end=v['timing']['end_sec']) for s,v in zip(canonical['segments'],voice,strict=True)],
        profile={'disable_visual_story':True,'native_illustration_system':'wb_planning_desk_painted_v1',
            'direction':'Use supported native reserve_readiness identify, separate and review actions only. No compulsory investing or invented amounts.'},
        design_system={'visual_style_id':'D'})
    plan=WealthBlueprintScenePlanner().plan(data)
    assert plan.validation.ok and len(plan.scenes)==6
    audit,conditional=plan.scenes[2:4]
    assert audit.visual_intent.relation['planning_desk_action']==conditional.visual_intent.relation['planning_desk_action']=='shared-balance'
    assert 'question' in audit.visual_intent.visible_change
    assert 'emergency scenario' in conditional.visual_intent.visible_change
    assert 'only afterwards' in conditional.visual_intent.visible_change
    # Removing the actual state change must still fail; no blanket rule waiver.
    conditional.visual_intent=audit.visual_intent.model_copy(deep=True)
    assert any(i.code=='layout_repeat' for i in validate_scene_plan(plan).issues)
