from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from atnos.production.narration_revision import NarrationRevisionRequired
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked
from atnos.production.steps.common import write_json
from atnos.production.steps.common import OllamaResourceUnavailable
from test_production_order_store import store, create


def test_model_memory_failure_is_not_repeated_and_keeps_call_history(store):
    job = create(store)['id']
    calls = []
    async def oom():
        calls.append('request')
        raise OllamaResourceUnavailable('Model load failed: unable to allocate GPU memory')
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='resource_unavailable'):
            asyncio.run(StageExecutor(store, job, token).run('script_draft', {}, oom, local_model_call=True))
    checkpoint = next(row for row in store.get(job)['checkpoints'] if row['stage'] == 'script_draft')
    assert calls == ['request'] and checkpoint['attempts'] == 1
    assert store.get(job)['provider_calls'] == 1
    assert 'resource_unavailable' in checkpoint['error_json']


def test_thinking_context_exhaustion_stops_real_adapter_after_one_counted_attempt(store, monkeypatch):
    from atnos.production.steps import common
    client = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'done': True, 'done_reason': 'length',
            'prompt_eval_count': 6031, 'eval_count': 6257, 'message': {'content': ''}})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(common.httpx, 'AsyncClient', lambda **kwargs: client(transport=transport))
    job = create(store)['id']
    before = store.get(job)
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        trace = executor.context.artifact_dir / 'raw-context-failure.json'
        async def review():
            await common.ask_ollama_json(store.settings, 'Controlled review', model='fixture-model',
                think=True, options={'num_ctx': 12288}, raw_response_path=trace)
            pytest.fail('No completion or canonical write after exhausted context')
        with pytest.raises(ProductionBlocked, match='resource_unavailable'):
            asyncio.run(executor.run('script_review', {'candidate': 'isolated'}, review, local_model_call=True))
    after = store.get(job)
    checkpoint = next(row for row in after['checkpoints'] if row['stage'] == 'script_review')
    assert len(requests) == checkpoint['attempts'] == 1
    assert after['provider_calls'] == before['provider_calls'] + 1
    assert after['status'] == 'blocked'
    assert 'context capacity exhausted' in checkpoint['error_json']
    assert trace.exists() and not (executor.context.artifact_dir / 'script.json').exists()
    assert requests[0]['think'] is True and requests[0]['options']['num_ctx'] == 12288


def test_transient_server_failure_opens_circuit_after_three_calls(store, monkeypatch):
    job = create(store)['id']
    calls, delays = [], []
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr('atnos.production.order_execution.asyncio.sleep', sleep)
    async def unavailable():
        calls.append('request')
        response = httpx.Response(503, request=httpx.Request('POST', 'http://local.invalid/api/chat'))
        response.raise_for_status()
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='dependency_circuit_open'):
            asyncio.run(StageExecutor(store, job, token).run('script_draft', {}, unavailable, local_model_call=True))
    assert len(calls) == 3 and delays == [2.0, 4.0]
    assert store.get(job)['provider_calls'] == 3


def test_deterministic_validation_stops_after_one_attempt_and_keeps_checkpoint(store):
    job = create(store)['id']
    calls = []

    async def invalid_input():
        calls.append('unchanged')
        raise ValueError('Artifact hash does not match the verified input')

    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        with pytest.raises(ProductionBlocked, match='invalid_input'):
            asyncio.run(executor.run('assembly_validation', {}, invalid_input))
    state = store.get(job)
    checkpoint = next(row for row in state['checkpoints'] if row['stage'] == 'assembly_validation')
    assert calls == ['unchanged']
    assert checkpoint['attempts'] == 1
    assert 'invalid_input' in checkpoint['error_json']
    assert state['status'] == 'blocked'
    assert state['provider_calls'] == 0


@pytest.mark.parametrize('call_flag', ['local_model_call', 'provider_call'])
def test_model_output_can_be_regenerated_with_feedback_and_counted_budget(store, call_flag):
    job = create(store)['id']
    feedback = []
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)

        async def model():
            feedback.append(executor.context.artifacts.get('model_retry_feedback'))
            if len(feedback) == 1:
                raise ValueError('Missing scene action')
            return write_json(executor.context.artifact_dir / 'model.json', {'action': 'show the transfer'})

        result = asyncio.run(executor.run('model_fixture', {}, model,
            estimated_cost_usd=0.001 if call_flag == 'provider_call' else 0, **{call_flag: True}))
    assert result == {'action': 'show the transfer'}
    assert feedback == ['', 'Missing scene action']
    assert store.get(job)['provider_calls'] == 2


def test_transient_timeout_still_retries_without_repeating_completed_work(store):
    job = create(store)['id']
    calls = []
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)

        async def transient():
            calls.append('request')
            if len(calls) == 1:
                raise httpx.ReadTimeout('Temporary timeout')
            return write_json(executor.context.artifact_dir / 'request.json', {'ok': True})

        assert asyncio.run(executor.run('request_fixture', {}, transient)) == {'ok': True}
        assert asyncio.run(executor.run('request_fixture', {}, transient)) == {'ok': True}
    assert calls == ['request', 'request']


def test_auth_failure_is_not_retried_even_in_model_stage(store):
    job = create(store)['id']
    calls = []

    async def unauthorized():
        calls.append('request')
        response = httpx.Response(401, request=httpx.Request('POST', 'https://fixture.invalid/generate'))
        response.raise_for_status()

    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='dependency_failure'):
            asyncio.run(StageExecutor(store, job, token).run('model_fixture', {}, unauthorized,
                provider_call=True, estimated_cost_usd=0.001))
    assert calls == ['request']
    assert store.get(job)['provider_calls'] == 1


def test_practical_share_revision_is_not_retried_as_identical_assembly(store, monkeypatch):
    # This unit controls retry classification, not the independent finance gate.
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    job = create(store)['id']
    calls = []
    report = {'reason': 'Measured practical_share=0.1', 'next_stage': 'script_repair'}

    async def assembly():
        calls.append('assemble')
        raise NarrationRevisionRequired(report)

    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        with pytest.raises(NarrationRevisionRequired):
            asyncio.run(executor.run('voice', {}, assembly))
        assert executor.context.artifacts['script_revision_request'] == report
    assert calls == ['assemble']
    checkpoint = next(row for row in store.get(job)['checkpoints'] if row['stage'] == 'voice')
    assert json.loads(checkpoint['error_json'])['category'] == 'script_revision_required'


def test_persistent_service_failure_opens_circuit_after_three_counted_attempts(store, monkeypatch):
    job = create(store)['id']
    calls, delays = [], []
    async def pause(seconds):
        delays.append(seconds)
    monkeypatch.setattr('atnos.production.order_execution.asyncio.sleep', pause)
    async def unavailable():
        calls.append('request')
        response = httpx.Response(500, request=httpx.Request('POST', 'http://127.0.0.1:11435/api/chat'))
        response.raise_for_status()
    before = store.get(job)
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='dependency_circuit_open'):
            asyncio.run(StageExecutor(store, job, token).run('model_fixture', {}, unavailable, local_model_call=True))
    after = store.get(job)
    assert len(calls) == 3 and delays == [2.0, 4.0]
    assert after['provider_calls'] == before['provider_calls'] + 3
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    row = next(r for r in after['checkpoints'] if r['stage'] == 'model_fixture')
    assert row['attempts'] == 3 and row['status'] == 'failed'
    assert json.loads(row['error_json'])['category'] == 'dependency_circuit_open'
    assert after['profile']['production']['max_attempts_per_element'] == before['profile']['production']['max_attempts_per_element']


def test_local_model_oom_stops_after_one_attempt_without_regenerating_same_request(store):
    from atnos.production.steps.common import OllamaResourceUnavailable
    job = create(store)['id']
    calls = []
    async def oom():
        calls.append('load')
        raise OllamaResourceUnavailable('Ollama cannot load the model: cudaMalloc failed: out of memory')
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='resource_unavailable'):
            asyncio.run(StageExecutor(store, job, token).run('model_fixture', {}, oom, local_model_call=True))
    assert calls == ['load'] and store.get(job)['provider_calls'] == 1


def test_ollama_body_identifies_oom_as_non_retryable_resource_failure(store, monkeypatch):
    from atnos.production.steps.common import ask_ollama_json, OllamaResourceUnavailable
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            return httpx.Response(500, request=httpx.Request('POST', url),
                                  json={'error': 'llama-server reported out-of-memory during startup: unable to allocate ROCm0 buffer'})
    monkeypatch.setattr('atnos.production.steps.common.httpx.AsyncClient', Client)
    with pytest.raises(OllamaResourceUnavailable, match='unable to allocate ROCm0'):
        asyncio.run(ask_ollama_json(store.settings, 'Controlled probe, no actual external call', model='fixture-model'))
