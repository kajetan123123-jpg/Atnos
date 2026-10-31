"""Regression of the actual s04 COMPARISON -> statement production failure."""
import json
from pathlib import Path
from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
from atnos.production.channels.wealth_blueprint.scene_plan_models import ScenePlannerInput, NarrationSegment
from atnos.production.channels.wealth_blueprint.visual_decision import storyboard_blocks_render
from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest


def test_actual_reserve_script_keeps_qualitative_roles_through_manifest():
    # Recorded script is regression input, never a media quality receipt.
    script=json.loads((Path(__file__).parent/'fixtures/wb_chat_short_20261010/reserve-canonical.json').read_text('utf-8'))
    offset=0.0
    segments=[]
    for item in script['segments']:
        end=offset+len(item['text'].split())/2.3
        segments.append(NarrationSegment(id=item['id'],text=item['text'],start=offset,end=end,
                                        on_screen=item['on_screen'],visual_intent=item['visual_intent']))
        offset=end
    plan=WealthBlueprintScenePlanner().plan(ScenePlannerInput(script=script['text'],segments=segments))
    data=plan.model_dump(mode='json')
    assert storyboard_blocks_render(data) is None
    assert [s.visual_intent.relation.get('stage') for s in plan.scenes[:-1]] == ['need','identify','access','separate','review']
    assert all(s.chart is None and s.number_value is None and not s.use_alex for s in plan.scenes)
    manifest=scene_plan_to_project_manifest(job_id='isolated-reserve-regression',topic=script['title'],
                                           script=script['text'],plan=data,duration_hint=offset)
    scenes=manifest.model_dump(mode='json')['scenes']
    assert all(s['scene_type']=='HookScene' and not s['assets'] for s in scenes[:-1])
    assert all(s['semantic_overlay_kind']=='reserve_readiness' for s in scenes[:-1])
    assert scenes[3]['visual_relation']['stage']=='separate'
    assert {o['role'] for o in scenes[3]['visual_relation']['objects']} >= {'reserve','goal'}
    assert scenes[-1]['scene_type']=='CTAScene'


def test_explicit_transfer_does_not_become_reserve_board():
    relation=parse_scene_relation('Transfer from checking to savings for a future goal.',concept='weekly_transfer')
    assert relation['kind']=='transfer' and relation['source']=='checking'
    relation=parse_scene_relation('Transfer from checking to savings for a car repair.',concept='weekly_transfer')
    assert relation['kind']=='transfer' and relation['destination']=='savings'


def test_generic_financial_comparison_still_cannot_collapse_to_statement():
    plan={'scenes':[{'scene_id':f's{i}','layout_id':'icon_explainer' if i!=2 else 'fullscreen_statement',
                    'visual_intent':{'dominant_intent':'COMPARISON' if i==2 else 'ACTION'}} for i in range(4)]}
    assert 'COMPARISON collapsed' in storyboard_blocks_render(plan)


def test_quantified_reserve_is_not_silently_demoted_to_qualitative_board():
    assert parse_scene_relation('Keep an emergency fund of 3 months for car repairs.')['kind'] != 'reserve_readiness'


def test_explicit_native_directive_cannot_bypass_the_quantity_gate():
    assert parse_scene_relation('Keep an emergency fund of 3 months.',
        visual_context='identify (actual need and reserve)',reserve_context=True)['kind'] != 'reserve_readiness'


def test_concrete_breakdown_question_is_not_lost_to_a_statement_fallback():
    relation=parse_scene_relation('Imagine your car breaks down. Could you cover the repair today?')
    assert relation['kind']=='reserve_readiness'
    assert relation['stage']=='need'
    assert next(obj['label'] for obj in relation['objects'] if obj['role']=='expense')=='CAR REPAIR'


def test_breakdown_question_with_a_price_preserves_quantity_gate():
    assert parse_scene_relation('Your car breaks down. Pay $500 for the repair.')['kind']!='reserve_readiness'


def test_actual_93_word_hook_revision_preserves_director_context_through_manifest():
    # This recorded candidate lost its pronoun-based access/review beats in LIVE.
    # It is regression input, never proof of media acceptance.
    script=json.loads((Path(__file__).parent/'fixtures/wb_chat_short_20261010/hook-93-canonical.json').read_text('utf-8'))
    offset=0.0
    segments=[]
    for item in script['segments']:
        end=offset+len(item['text'].split())/2.3
        segments.append(NarrationSegment(id=item['id'],text=item['text'],start=offset,end=end,
                                        on_screen=item['on_screen'],visual_intent=item['visual_intent']))
        offset=end
    plan=WealthBlueprintScenePlanner().plan(ScenePlannerInput(script=script['text'],segments=segments))
    data=plan.model_dump(mode='json')
    assert storyboard_blocks_render(data) is None
    assert [s.visual_intent.relation.get('stage') for s in plan.scenes[:-1]]==['need','identify','access','separate','review']
    assert plan.scenes[1].visual_intent.relation['objects'][0]['label']=='CAR REPAIR'
    manifest=scene_plan_to_project_manifest(job_id='isolated-hook-regression',topic=script['title'],
                                           script=script['text'],plan=data,duration_hint=offset)
    scenes=manifest.model_dump(mode='json')['scenes']
    assert all(s['scene_type']=='HookScene' and not s['assets'] for s in scenes[:-1])
    assert all(s['semantic_overlay_kind']=='reserve_readiness' for s in scenes[:-1])
    assert scenes[-1]['scene_type']=='CTAScene'


def test_director_context_cannot_override_explicit_transfer_or_quantity():
    context='show READY RESERVE with questions about access conditions'
    relation=parse_scene_relation('Transfer from checking to savings.',visual_context=context,reserve_context=True)
    assert relation['kind']=='transfer'
    relation=parse_scene_relation('Keep 3 months of expenses.',visual_context=context,reserve_context=True)
    assert relation['kind']!='reserve_readiness'


def test_negative_director_clauses_do_not_select_forbidden_access_action():
    relation=parse_scene_relation('Label the emergency reserve separately from your future goal.',
        visual_context='compare READY RESERVE readiness with FUTURE GOAL without showing transfer language or access steps.',
        reserve_context=True)
    assert relation['kind']=='reserve_readiness' and relation['stage']=='separate'


def test_generic_pronouns_without_director_context_do_not_invent_a_reserve():
    assert parse_scene_relation('Check how you access these funds today.')['kind']!='reserve_readiness'
    assert parse_scene_relation('Compare two options.',reserve_context=True)['kind']!='reserve_readiness'


def test_actual_review_alias_and_positive_lens_direction_keep_reserve_roles():
    text='Review how these roles separate when personal circumstances change over time. Unexpected events may alter your needs significantly.'
    visual='Move magnifying glass attention from envelope to notebook while both remain visible on screen.'
    relation=parse_scene_relation(text,visual_context=visual,reserve_context=True)
    assert relation['kind']=='reserve_readiness' and relation['stage']=='review'
    assert parse_scene_relation(text,visual_context=visual)['kind']!='reserve_readiness'
    assert parse_scene_relation('Transfer from checking to savings.',visual_context=visual,reserve_context=True)['kind']=='transfer'
