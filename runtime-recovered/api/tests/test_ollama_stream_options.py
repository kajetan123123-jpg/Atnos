"""Streaming must honor the same caller constraints as ordinary chat; no model runs."""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from atnos.core.config import Settings
from atnos.llm.ollama import OllamaChatBackend
from atnos.providers.base import ProviderResponseError, ProviderUnavailableError


@pytest.mark.parametrize('context, expected', [(4096, 4096), (1024, 2048), (16384, 8192)])
@pytest.mark.parametrize('streaming', [False, True])
def test_explicit_context_and_schema_apply_to_both_transport_modes(context, expected, streaming):
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}}
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': '{"answer":"ok"}'}, 'done': True})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    backend = OllamaChatBackend(settings=Settings(_env_file=None), model='isolated-test',
                                context_tokens=context, output_schema=schema, think=False)
    with patch('atnos.llm.ollama.httpx.AsyncClient', return_value=client):
        asyncio.run(backend.chat_complete(messages=[{'role': 'user', 'content': 'test'}],
            tools=[{'type': 'function', 'function': {'name': 'echo'}}],
            on_token=(lambda _: None) if streaming else None))
    assert len(requests) == 1
    assert requests[0]['options']['num_ctx'] == expected
    assert requests[0]['format'] == schema
    assert requests[0]['think'] is False


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('status', [400, 500, 503])
def test_provider_error_is_bounded_redacted_and_not_retried(streaming, status):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={
            'error': 'runner failed password=do-not-leak Bearer private-token ' + 'x' * 2000,
            'messages': 'PRIVATE REQUEST ECHO'})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    backend = OllamaChatBackend(settings=Settings(_env_file=None), model='isolated-test')
    error = ProviderUnavailableError if status >= 500 else ProviderResponseError
    with patch('atnos.llm.ollama.httpx.AsyncClient', return_value=client), pytest.raises(error) as caught:
        asyncio.run(backend.chat_complete(messages=[{'role': 'user', 'content': 'private'}],
            on_token=(lambda _: None) if streaming else None))
    message = str(caught.value)
    assert 'runner failed' in message
    assert 'do-not-leak' not in message and 'private-token' not in message
    assert 'PRIVATE REQUEST ECHO' not in message and len(message) < 600
    if status >= 500:
        assert f'HTTP {status}' in message
    assert len(requests) == 1


@pytest.mark.parametrize('body', ['private non-JSON body', '{"message":"private echo"}'])
def test_unknown_error_body_is_not_echoed_or_given_an_invented_cause(body):
    response = httpx.Response(500, text=body)
    assert OllamaChatBackend._extract_error_message(response) == 'Ollama returned status 500.'
