from copy import deepcopy
import asyncio
import json
from pathlib import Path
import pytest

from atnos.production.channels.wealth_blueprint.deterministic import _source_physical_illustration_for
from atnos.production.wb_image_api import _native_scene_for_image_trial
from test_production_order_store import store


def reserve_scene():
    return {'layout_id': 'comparison', 'visual_focus': 'comparison',
            'meaning_contract': {'native_scene_role': 'reserve_readiness',
                                 'relation_kind': 'reserve_readiness',
                                 'object_roles': {'reserve': 'READY RESERVE'}},
            'visual_intent': {'relation': {'kind': 'reserve_readiness', 'stage': 'access',
                'objects': [{'role': 'reserve', 'label': 'READY RESERVE'}], 'unsupported_gap': None}}}


def test_real_supported_reserve_board_is_native_not_a_missing_still():
    assert _native_scene_for_image_trial(reserve_scene(), index=1, total=3, claims=[]) == 'reserve_readiness'


def test_native_marker_cannot_hide_wrong_relation_stage_roles_or_renderer():
    for field, value in [('kind', 'transfer'), ('stage', 'invented'), ('unsupported_gap', 'missing motion'),
                         ('objects', [{'role': 'goal', 'label': 'FUTURE GOAL'}])]:
        scene = deepcopy(reserve_scene())
        scene['visual_intent']['relation'][field] = value
        assert _native_scene_for_image_trial(scene, index=1, total=3, claims=[]) is None
    assert _native_scene_for_image_trial(reserve_scene(), index=2, total=3, claims=[]) is None


def test_car_still_does_not_claim_host_motion_or_generated_typography():
    assert _source_physical_illustration_for(['One stationary car in a quiet garage.'])['function'] == 'car_repair_garage'
    assert _source_physical_illustration_for(['One stationary car in a quiet garage; no host, sign or footage.'])['function'] == 'car_repair_garage'
    for intent in ['Car drives into a garage.', 'A parked car with Alex in a garage.',
                   'Footage of a stationary car in a garage.', 'A parked car with a sign in a garage.']:
        assert _source_physical_illustration_for([intent]) is None


def test_actual_director_aliases_keep_their_supported_stage():
    from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
    cases = [('Identify the repair invoice. Locate the reserve and check access.', 'identify (repair invoice and reserve)', 'identify'),
             ('Does the account require a waiting period?', 'access (reserve and questions about timing and conditions)', 'access'),
             ('Review both roles now.', 'review (review both roles)', 'review')]
    for narration, direction, stage in cases:
        relation = parse_scene_relation(narration, visual_context=direction, reserve_context=True)
        assert relation['kind'] == 'reserve_readiness' and relation['stage'] == stage
    assert parse_scene_relation('Transfer from checking to savings.', visual_context=cases[1][1], reserve_context=True)['kind'] == 'transfer'


@pytest.mark.parametrize('intent,expected', [
    ('native_reserve_readiness: identify (actual need and reserve)','identify'),
    ('native_reserve_readiness: access (reserve and questions about access)','access'),
    ('native_reserve_readiness: separate (reserve versus future goal)','separate'),
    ('native_reserve_readiness: review (review both roles)','review'),
])
def test_real_writer_namespace_keeps_actual_supported_action(intent,expected):
    from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
    relation=parse_scene_relation('Check these roles.',visual_context=intent,reserve_context=True)
    assert relation['kind']=='reserve_readiness' and relation['stage']==expected
    assert relation['unsupported_gap'] is None


def test_namespace_alone_cannot_certify_an_invented_action():
    from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
    relation=parse_scene_relation('An idea.',visual_context='native_reserve_readiness: levitate money',reserve_context=True)
    assert relation['kind']=='unsupported'


def test_real_separation_purpose_clause_does_not_become_an_ambiguous_transfer():
    from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
    actual='Separate your future goal notebook from this reserve envelope to prevent accidental spending on long-term dreams.'
    intent='separate (reserve versus future goal)'
    result=parse_scene_relation(actual,visual_context=intent,reserve_context=True)
    assert result['kind']=='reserve_readiness' and result['stage']=='separate'
    ambiguous=parse_scene_relation('Transfer money from an unnamed account to a new place.',
        visual_context=intent,reserve_context=True)
    assert ambiguous['kind']=='unsupported'
    review=parse_scene_relation('Review both roles to keep the reserve separate from plans for later years.',
        visual_context='review (review both roles)',reserve_context=True)
    assert review['kind']=='reserve_readiness' and review['stage']=='review'


def test_access_overview_and_conditional_terms_have_real_distinct_render_states():
    from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
    intent='access (reserve and questions about access, timing and conditions)'
    overview=parse_scene_relation('Check your withdrawal conditions first.',visual_context=intent,reserve_context=True)
    terms=parse_scene_relation('A balance is not ready cash if penalties or waiting periods apply.',visual_context=intent,reserve_context=True)
    assert overview['access_focus']=='overview' and terms['access_focus']=='terms'
    assert overview['final_state']!=terms['final_state']


def test_frozen_2d_reserve_writer_receives_real_asset_and_native_capabilities(store):
    from atnos.production.project_profile import ProductionOrder
    from atnos.production.steps.order_script import draft_prompt
    job = store.create(ProductionOrder(topic='Check an emergency fund for a car repair', delivery_mode='local_only', visual_style_id='D'))['id']
    context = store.context(job)
    context.artifacts['project_profile']['visual_style']['prompt'] = '2D editorial illustration, mint and indigo'
    prompt = draft_prompt(context, {})
    assert 'Write six distinct complete thoughts' in prompt
    assert 'one stationary car in a quiet garage' in prompt
    assert 'Do not prescribe a financial product or promise immediate access' in prompt
    assert 'Closing visual_intent is ONLY the Wealth Blueprint logo' in prompt


@pytest.mark.parametrize('topic', ['Check an emergency fund', 'An emergency medical bill', 'An emergency home repair'])
def test_reserve_writer_does_not_invent_car_for_other_needs(store, topic):
    from atnos.production.project_profile import ProductionOrder
    from atnos.production.steps.order_script import draft_prompt
    job=store.create(ProductionOrder(topic=topic,delivery_mode='local_only',visual_style_id='D'))['id']
    context=store.context(job)
    context.artifacts['project_profile']['visual_style']['prompt']='2D editorial illustration, mint and indigo'
    prompt=draft_prompt(context,{})
    assert 'opening visual_intent uses native reserve_readiness' in prompt
    assert 'opening visual_intent describes exactly one stationary car' not in prompt


@pytest.mark.parametrize('change_cta', [False, True])
def test_actual_short_draft_length_repair_uses_sentences_and_keeps_cta(store, monkeypatch, change_cta):
    from atnos.production.project_profile import ProductionOrder
    from atnos.production.steps import order_script as script_service
    job = store.create(ProductionOrder(topic='Check an emergency fund', delivery_mode='local_only', visual_style_id='D'))['id']
    context = store.context(job)
    context.artifacts['project_profile']['visual_style']['prompt'] = '2D editorial illustration, mint and indigo'
    script = script_service.EditorialScript.model_validate(json.loads((Path(__file__).parent/'fixtures/wb_chat_short_20261010/illustrated-reserve-short-draft.json').read_text('utf8')))
    issues = script_service.script_issues(script, context.artifacts['project_profile'])
    assert any(item['code'] == 'word_budget' for item in issues)
    async def model(settings, context, prompt, schema, **kwargs):
        assert schema is script_service.NarrationRepair
        assert 'Do not return arrays of words' in prompt
        assert 'The locked segments already contribute' in prompt
        assert 'Your replacement texts together must contain' in prompt
        assert 's06' not in kwargs['decoding_schema']['$defs']['NarrationReplacement']['properties']['id']['enum']
        return {'replacements': [{'id': 's06' if change_cta else 's02',
            'text': script.segments[1].text + ' Check how the account permits you to request the money.'}]}
    monkeypatch.setattr(script_service, 'ask_model', model)
    if change_cta:
        with pytest.raises(ValueError, match='locked hook or CTA'):
            asyncio.run(script_service.repair(store.settings, context, script, issues))
    else:
        fixed = asyncio.run(script_service.repair(store.settings, context, script, issues))
        assert fixed.segments[-1] == script.segments[-1] and fixed.segments[0] == script.segments[0]
        assert fixed.claims == script.claims
        assert not script_service.script_issues(fixed, context.artifacts['project_profile'])
