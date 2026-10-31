"""Real rejected v7 writer responses; offline constraints, not quality receipts."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from atnos.production.steps import order_script as scripts
from test_production_order_script import execution
from test_production_order_store import store
from test_registered_review_v6_regression import script_data, load as load_v6

FIXTURES = Path(__file__).parent/'fixtures/wb_review_v7_20261016'
CALLS = ['8a51a5dd89be4c639bcecaea306a444f','e1ab719babfd40b1bed7821e18a30940','f93c00e1187b4530993d237edbc4ab2c']


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    async def forbidden(*_a, **_k):
        pytest.fail('Offline frozen-role regression attempted inference')
    monkeypatch.setattr(scripts,'ask_ollama_json',forbidden)


@pytest.fixture
def controlled(execution):
    task_store, _, context = execution
    draft = scripts.EditorialScript.model_validate(script_data('canonical'))
    request = load_v6('request')
    issues = deepcopy(request['editorial_issues'])
    context.artifacts['studio_script_recovery'] = {**request, 'mode':'hypothetical_review','origin':'studio_user',
        'issues':issues, 'submitted_editorial_issues':issues}
    context.artifacts['script_calculations'] = {'claims':{c.id:c.model_dump(mode='json') for c in draft.claims}}
    return task_store,context,draft,issues


@pytest.mark.parametrize('call_id', CALLS)
def test_real_type_changes_passed_recorded_grammar_but_are_now_rejected_early(controlled, call_id):
    _,context,draft,issues = controlled
    call = json.loads((FIXTURES/(call_id+'.json')).read_text('utf8'))
    raw = json.loads((FIXTURES/(call_id+'.raw.json')).read_text('utf8'))['response']
    assert raw['done_reason'] == 'stop'
    assert json.loads(raw['message']['content']) == call['response']
    assert call['response']['replacements'][0]['type'] == 'explanation'
    assert draft.segments[1].type == 'practical'
    jsonschema.validate(call['response'],call['decoding_schema'])
    with pytest.raises(ValueError,match='frozen scene roles or claim bindings'):
        scripts.validate_repair_response(context,draft,issues,call['response'],defer_editorial_validation=True)


def test_controlled_prompt_and_decoder_agree_with_frozen_candidate_roles(controlled, monkeypatch):
    task_store,context,draft,issues = controlled
    class Stop(Exception): pass
    seen={}
    async def capture(settings,ctx,prompt,schema,**kwargs):
        seen.update(prompt=prompt,schema=schema,decoding=kwargs['decoding_schema'])
        raise Stop()
    monkeypatch.setattr(scripts,'ask_model',capture)
    with pytest.raises(Stop):
        asyncio.run(scripts.repair(task_store.settings,context,draft,issues))
    assert 'Keep explanations without an actionable instruction as type=explanation' not in seen['prompt']
    assert 'Preserve every scene type exactly' in seen['prompt']
    assert '"5": "cta"' in seen['prompt']
    assert seen['schema'] is scripts.EditorialFieldRepair
    patches = seen['decoding']['properties']['patches']
    assert set(patches['required']) == {'1', '3', '4', '5'}
    assert all(not {'type', 'claim_ids', 'fact_ids'} & set(row['properties'])
               for row in patches['properties'].values())
    for call_id in CALLS:
        call=json.loads((FIXTURES/(call_id+'.json')).read_text('utf8'))
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(call['response'],seen['decoding'])
        response=deepcopy(call['response'])
        response['replacements'][0]['type']='practical'
        # Current field-map decoding cannot accept any complete segment output,
        # even with the historical role corrected. Direct replay below still
        # exercises the independent runtime stagnation guard on the old wire.
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(response,seen['decoding'])
        with pytest.raises(ValueError,match='unchanged current repair targets'):
            scripts.validate_repair_response(context,draft,issues,response,defer_editorial_validation=True)


def test_uncontrolled_full_repair_keeps_existing_reclassification_permission(controlled, monkeypatch):
    task_store,context,draft,issues=controlled
    context.artifacts.pop('studio_script_recovery')
    seen=[]
    class Stop(Exception): pass
    async def capture(settings,ctx,prompt,schema,**kwargs):
        seen.append(prompt)
        raise Stop()
    monkeypatch.setattr(scripts,'ask_model',capture)
    with pytest.raises(Stop): asyncio.run(scripts.repair(task_store.settings,context,draft,issues))
    assert 'Keep explanations without an actionable instruction as type=explanation' in seen[0]


def test_frozen_role_binding_preserves_reviewed_fact_enums(controlled):
    _,context,draft,issues=controlled
    ids={sid for item in issues for sid in item['segment_ids']}
    base=scripts.bind_reviewed_fact_ids(scripts.repair_generation_schema(scripts.AdaptedScriptRepair,set(),ids),{'verified-fact':{}})
    result=scripts.bind_frozen_repair_roles(base,draft,ids)
    assert base['properties']['replacements']['items'] == {'$ref':'#/$defs/ScriptSegment'}
    variants=result['properties']['replacements']['items']['anyOf']
    assert len(variants)==4
    assert all(item['properties']['fact_ids']['items']['enum']==['verified-fact'] for item in variants)
    assert {item['properties']['id']['enum'][0]:item['properties']['type']['enum'][0] for item in variants} == {
        '1':'practical','3':'practical','4':'practical','5':'cta'}
