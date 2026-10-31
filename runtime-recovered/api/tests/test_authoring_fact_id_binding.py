"""The actual failed authoring pattern must not enter decoding again."""
import copy
import asyncio
import json
from pathlib import Path
import jsonschema
from pydantic import create_model
from atnos.production.steps import order_script as scripts
from test_production_order_script import execution
from test_production_order_store import store
import pytest
from atnos.production.steps.order_script import (
    AdaptedScript, AdaptedScriptRepair, EditorialScript,
    bind_reviewed_fact_ids, generation_schema, repair_generation_schema,
)


@pytest.mark.parametrize('model', [EditorialScript, AdaptedScript, AdaptedScriptRepair])
def test_no_reviewed_source_cannot_decode_invented_fact_ids(model):
    original = generation_schema(model)
    previous = copy.deepcopy(original)
    schema = bind_reviewed_fact_ids(original, {})
    assert schema['$defs']['ScriptSegment']['properties']['fact_ids']['maxItems'] == 0
    assert original == previous


def test_only_actual_reviewed_ids_decode_and_segment_scope_remains():
    original = repair_generation_schema(AdaptedScriptRepair, set(), {'s02'})
    schema = bind_reviewed_fact_ids(original, {'cfpb_reserve': {'statement': 'A reserve is for unexpected expenses.'}})
    segment = schema['$defs']['ScriptSegment']['properties']
    assert segment['fact_ids']['items']['enum'] == ['cfpb_reserve']
    assert segment['id']['enum'] == ['s02']
    assert 'title' not in schema['properties']


def test_actual_live_draft_passed_old_decoding_but_fails_bound_decoding():
    response = json.loads((Path(__file__).parent/'fixtures/wb_chat_short_20261010/invented-fact-ids.json').read_text('utf-8'))
    response.pop('claims')  # The adapted author returns no claims; Atnos binds them.
    legacy = generation_schema(AdaptedScript)
    jsonschema.validate(response, legacy)
    bound = bind_reviewed_fact_ids(legacy, {})
    jsonschema.Draft202012Validator.check_schema(bound)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(response, bound)
    for segment in response['segments']:
        segment['fact_ids'] = []
    jsonschema.validate(response, bound)


def test_guided_writer_keeps_registered_author_model_and_endpoint(execution, monkeypatch):
    settings = execution[0].settings
    context = execution[2]
    lane = {'digest':'a'*64, 'model':'qwen3.5:9b', 'model_digest':'b'*64,
            'endpoint':'http://127.0.0.1:11436', 'execution_scope':'normal_produce_script_authoring'}
    context.artifacts['_active_script_model_lane'] = lane
    monkeypatch.setattr('atnos.production.script_model_lane.load_script_model_lane', lambda *a, **k: lane)
    captured = []
    async def fake_ollama(settings, prompt, **kwargs):
        captured.append((settings.ollama_base_url, kwargs['model']))
        return {}
    monkeypatch.setattr(scripts, 'ask_ollama_json', fake_ollama)
    guided = create_model('GuidedWBScript', __base__=AdaptedScript)
    asyncio.run(scripts.ask_model(settings,context,'Isolated routing test',guided))
    assert captured == [('http://127.0.0.1:11436','qwen3.5:9b')]
    assert not scripts._is_script_authoring_schema(scripts.ScriptReview)
