"""Instruction priority is an actual transport contract, not a claimed semantic PASS."""
import asyncio
import hashlib
import json
from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest

from atnos.core.config import Settings
from atnos.production.steps import common, order_script
from test_production_order_script import execution, script
from test_production_order_store import store


@pytest.mark.parametrize('system_prompt', [None, 'Current registered constraints take priority.'])
def test_production_transport_preserves_legacy_or_sends_real_system_role(system_prompt):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'done': True, 'message': {'content': '{"ok":true}'}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with patch('atnos.production.steps.common.httpx.AsyncClient', return_value=client):
        result = asyncio.run(common.ask_ollama_json(Settings(_env_file=None), 'Reference data.',
            model='isolated-test', system_prompt=system_prompt))
    assert result == {'ok': True} and len(requests) == 1
    expected = ([{'role': 'system', 'content': system_prompt}] if system_prompt else [])
    assert requests[0]['messages'] == expected + [{'role': 'user', 'content': 'Reference data.'}]
    assert requests[0]['keep_alive'] == 0


@pytest.mark.parametrize('reviewing', [False, True])
def test_registered_priority_reaches_transport_and_normal_model_call_audit(execution, script, monkeypatch, reviewing):
    store, _, context = execution
    guidance = {'reason': 'Correct the current stated defect.', 'issues': [
        {'code': 'brief_fidelity', 'segment_ids': ['s01'], 'field': 'text',
         'detail': 'Introduce conditional renewal without a hidden deadline allegation.',
         'forbidden_phrases': ['hidden deadline']}]}
    context.artifacts['scoped_script_guidance'] = guidance
    original_request = deepcopy(context.artifacts['production_order'])
    sent = []
    async def backend(settings, prompt, **kwargs):
        assert prompt == 'Historical brief reference.'
        system = kwargs['system_prompt']
        sent.append(system)
        assert 'CURRENT registered' in system and 'hidden deadline' in system
        assert 'authorized edit scope' in system and 'frozen financial facts' in system
        assert ('Independently assess' if reviewing else 'Author a complete') in system
        return {'controlled': True}
    monkeypatch.setattr(order_script, 'ask_ollama_json', backend)
    result = asyncio.run(order_script.ask_model(store.settings, context,
        'Historical brief reference.', order_script.ScriptReview if reviewing else order_script.EditorialScript,
        reviewing=reviewing))
    assert result == {'controlled': True}
    calls = list((context.artifact_dir / 'model_calls').glob('*.json'))
    assert len(calls) == 1
    audit = json.loads(calls[0].read_text('utf-8'))
    assert audit['system_prompt_version'] == 1
    from atnos.core.logging import redact_secrets
    assert audit['system_prompt_sha256'] == hashlib.sha256(sent[0].encode('utf-8')).hexdigest()
    assert audit['system_prompt'] == redact_secrets(sent[0])
    assert context.artifacts['production_order'] == original_request
    assert context.artifacts['scoped_script_guidance'] == guidance


def test_plain_orders_do_not_get_registered_priority(execution):
    context = execution[2]
    assert order_script.registered_script_system_context(context, reviewing=False) is None
    assert order_script.registered_script_system_context(context, reviewing=True) is None


@pytest.mark.parametrize('reviewing', [False, True])
def test_scoped_script_priority_does_not_leak_into_asset_schema(execution, monkeypatch, reviewing):
    from pydantic import create_model
    store, _, context = execution
    context.artifacts['scoped_script_guidance'] = {'reason': 'Script correction only.', 'issues': [
        {'code': 'brief_fidelity', 'segment_ids': ['s01'], 'field': 'text',
         'detail': 'A script-only current criterion.'}]}
    async def backend(settings, prompt, **kwargs):
        assert 'system_prompt' not in kwargs
        return {'value': 'controlled asset result'}
    monkeypatch.setattr(order_script, 'ask_ollama_json', backend)
    schema = create_model('AssetPlanningProbe', value=(str, ...))
    result = asyncio.run(order_script.ask_model(store.settings, context, 'Plan assets.', schema, reviewing=reviewing))
    assert result == {'value': 'controlled asset result'}


def test_complete_sentence_repair_is_a_script_authoring_contract():
    from atnos.production.narration_schema import sentence_narration_schema
    from pydantic import create_model
    schema, _ = sentence_narration_schema({
        'target_words_by_segment': {'s02': 22, 's03': 23},
        'replacement_total_range': [40, 50]})
    assert order_script._is_script_authoring_schema(schema)
    unrelated = create_model('CompleteNarrationSentences', value=(str, ...))
    assert not order_script._is_script_authoring_schema(unrelated)


def test_sentence_repair_uses_registered_author_lane_and_current_instruction(execution, monkeypatch):
    from atnos.production.narration_schema import sentence_narration_schema
    from atnos.production import script_model_lane
    store, _, context = execution
    receipt = {'digest': 'isolated-receipt', 'model': 'isolated-registered-author',
               'model_digest': 'isolated-model-pin', 'endpoint': 'http://127.0.0.1:11436',
               'execution_scope': 'normal_produce_script_authoring'}
    context.artifacts['_active_script_model_lane'] = receipt
    context.artifacts['scoped_script_guidance'] = {'reason': 'Current correction.', 'issues': [
        {'code': 'word_budget', 'segment_ids': ['s02'], 'field': 'text',
         'detail': 'Preserve the substantive check while meeting the narration budget.'}]}
    def load(settings, job_id, *, verify_runtime):
        assert verify_runtime is True and job_id == context.job_id
        return receipt
    monkeypatch.setattr(script_model_lane, 'load_script_model_lane', load)
    async def backend(settings, prompt, **kwargs):
        assert kwargs['model'] == receipt['model']
        assert str(settings.ollama_base_url).rstrip('/') == receipt['endpoint']
        assert 'CURRENT registered' in kwargs['system_prompt']
        return {'s02': 'Controlled response; not a quality receipt.'}
    monkeypatch.setattr(order_script, 'ask_ollama_json', backend)
    schema, _ = sentence_narration_schema({'target_words_by_segment': {'s02': 22},
                                         'replacement_total_range': [20, 24]})
    asyncio.run(order_script.ask_model(store.settings, context, 'Repair narration.', schema))
    audit = json.loads(next((context.artifact_dir / 'model_calls').glob('*.json')).read_text('utf-8'))
    assert audit['script_model_lane_receipt_digest'] == receipt['digest']
    assert audit['system_prompt_version'] == 1
