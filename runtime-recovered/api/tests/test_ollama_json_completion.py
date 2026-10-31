import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from atnos.production.steps import common

CLIENT = httpx.AsyncClient


@pytest.mark.parametrize('done,reason,rejected', [(False, None, True), (True, 'length', True), (True, 'stop', False)])
def test_even_parseable_json_requires_a_complete_response(tmp_path, monkeypatch, done, reason, rejected):
    response = {'done': done, 'done_reason': reason, 'message': {'content': '{"passed":true}'}}
    def respond(request):
        assert json.loads(request.content)['keep_alive'] == 0
        return httpx.Response(200, json=response)
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(common.httpx, 'AsyncClient', lambda **kwargs: CLIENT(transport=transport))
    settings = SimpleNamespace(ollama_timeout_s=10, ollama_base_url='http://local.invalid')
    trace = tmp_path / 'raw.json'
    call = common.ask_ollama_json(settings, 'Test completion', model='local-model', raw_response_path=trace)
    if rejected:
        with pytest.raises(ValueError, match='invalid or truncated'):
            asyncio.run(call)
    else:
        assert asyncio.run(call) == {'passed': True}
    assert json.loads(trace.read_text())['response']['done'] is done


@pytest.mark.parametrize('input_tokens,output_tokens', [(6031, 6257), (12180, 108)])
def test_thinking_context_exhaustion_is_a_non_retryable_capacity_failure(tmp_path, monkeypatch, input_tokens, output_tokens):
    # Token metadata from the failed LIVE review on 2026-10-25. No reasoning
    # content is needed to reproduce the resource boundary.
    raw = {'done': True, 'done_reason': 'length', 'prompt_eval_count': input_tokens,
           'eval_count': output_tokens, 'message': {'content': ''}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=raw))
    monkeypatch.setattr(common.httpx, 'AsyncClient', lambda **kwargs: CLIENT(transport=transport))
    settings = SimpleNamespace(ollama_timeout_s=10, ollama_base_url='http://local.invalid')
    trace = tmp_path / 'raw.json'
    with pytest.raises(common.OllamaResourceUnavailable, match='context capacity exhausted'):
        asyncio.run(common.ask_ollama_json(settings, 'Controlled review', model='fixture-model',
            think=True, options={'num_ctx': 12288}, raw_response_path=trace))
    assert json.loads(trace.read_text())['response'] == raw


@pytest.mark.parametrize('think,reason,input_tokens,output_tokens,ctx', [
    (False, 'length', 6031, 6257, 12288),
    (True, 'length', 6031, 100, 12288),
    (True, 'length', None, 6257, 12288),
    (True, 'length', '6031', 6257, 12288),
    (True, 'length', True, 12287, 12288),
    (True, 'length', -1, 12289, 12288),
    (True, 'length', 6031, 6257, 0),
    (True, 'stop', 6031, 6257, 12288),
])
def test_only_confirmed_thinking_context_exhaustion_changes_classification(monkeypatch, think, reason, input_tokens, output_tokens, ctx):
    raw = {'done': True, 'done_reason': reason, 'prompt_eval_count': input_tokens,
           'eval_count': output_tokens, 'message': {'content': '{"passed":true}'}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=raw))
    monkeypatch.setattr(common.httpx, 'AsyncClient', lambda **kwargs: CLIENT(transport=transport))
    settings = SimpleNamespace(ollama_timeout_s=10, ollama_base_url='http://local.invalid')
    call = common.ask_ollama_json(settings, 'Controlled input', model='fixture-model',
                                 think=think, options={'num_ctx': ctx})
    if reason == 'stop':
        assert asyncio.run(call) == {'passed': True}
    else:
        with pytest.raises(ValueError, match='invalid or truncated'):
            asyncio.run(call)
