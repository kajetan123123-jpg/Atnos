"""Thinking is a recorded local-review option, never an implicit profile change."""
import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError, create_model

from atnos.production import scoped_script_revision as scoped, script_review_model_lane as lane
from atnos.production.order_store import ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.steps import order_script as scripts
from test_production_order_script import execution
from test_production_order_store import store
from test_scoped_script_revision import original_order, feedback, script


def test_review_thinking_is_strict_explicit_and_omitted_from_legacy(feedback):
    old = feedback.model_dump(mode='json')
    assert 'review_thinking' not in old
    enabled = scoped.ScriptFeedback.model_validate({**old, 'review_thinking': True})
    assert enabled.model_dump(mode='json')['review_thinking'] is True
    for value in ('true', 1, False):
        with pytest.raises(ValidationError):
            scoped.ScriptFeedback.model_validate({**old, 'review_thinking': value})


def test_registration_requires_existing_verified_local_reviewer_receipt(store, original_order, feedback):
    job, _ = original_order
    before = store.get(job)
    with pytest.raises(ProductionBlocked):
        scoped.register_feedback(store.settings, job,
            {**feedback.model_dump(mode='json'), 'review_thinking': True}, approved=True)
    assert scoped.load_feedback(store, job) is None
    after = store.get(job)
    assert before['checkpoints'] == after['checkpoints']
    assert before['provider_calls'] == after['provider_calls']


@pytest.mark.parametrize('kind', ['review', 'author', 'asset'])
def test_only_registered_pinned_review_gets_thinking(execution, monkeypatch, kind):
    store, _, context = execution
    guidance = {'reason': 'Evaluate the current script with bounded local reasoning.', 'issues': [
        {'code': 'naturalness', 'segment_ids': ['s01'], 'field': 'text',
         'detail': 'Judge actual grammar, not a stale allegation.'}], 'review_thinking': True}
    context.artifacts['scoped_script_guidance'] = guidance
    receipt = {'digest': 'isolated-receipt', 'model': lane.MODEL, 'model_digest': 'isolated-pin',
               'endpoint': lane.ENDPOINT, 'execution_scope': lane.EXECUTION_SCOPE}
    context.artifacts['_active_script_review_model_lane'] = receipt
    monkeypatch.setattr(lane, 'load_script_review_model_lane', lambda *a, **k: receipt)
    monkeypatch.setattr(scoped, 'load_feedback', lambda *a, **k: {
        'feedback_hash': content_hash(guidance), 'feedback': deepcopy(guidance)})
    original = deepcopy(context.artifacts)
    async def backend(settings, prompt, **kwargs):
        assert kwargs['think'] is (kind == 'review')
        assert kwargs['options']['num_ctx'] == original['project_profile']['production']['script_model']['context_tokens']
        return {'controlled': True}
    monkeypatch.setattr(scripts, 'ask_ollama_json', backend)
    schema = scripts.ScriptReview if kind == 'review' else scripts.EditorialScript if kind == 'author' else create_model('OtherAsset', value=(str, ...))
    asyncio.run(scripts.ask_model(store.settings, context, 'Controlled input.', schema, reviewing=kind == 'review'))
    audit = json.loads(next((context.artifact_dir / 'model_calls').glob('*.json')).read_text('utf-8'))
    assert audit['thinking'] is (kind == 'review')
    assert audit.get('registered_review_thinking', False) is (kind == 'review')
    assert context.artifacts == original


@pytest.mark.parametrize('fault', ['missing_receipt', 'unregistered_context'])
def test_unregistered_thinking_context_cannot_dispatch_model(execution, monkeypatch, fault):
    store, _, context = execution
    context.artifacts['scoped_script_guidance'] = {'review_thinking': True}
    if fault != 'missing_receipt':
        receipt = {'digest': 'isolated', 'model': lane.MODEL, 'model_digest': 'pin',
                   'endpoint': lane.ENDPOINT, 'execution_scope': lane.EXECUTION_SCOPE}
        context.artifacts['_active_script_review_model_lane'] = receipt
        monkeypatch.setattr(lane, 'load_script_review_model_lane', lambda *a, **k: receipt)
    monkeypatch.setattr(scoped, 'load_feedback', lambda *a, **k: None)
    async def forbidden(*args, **kwargs): pytest.fail('No authorized review model call expected')
    monkeypatch.setattr(scripts, 'ask_ollama_json', forbidden)
    with pytest.raises(ProductionBlocked):
        asyncio.run(scripts.ask_model(store.settings, context, 'Controlled input.', scripts.ScriptReview, reviewing=True))


@pytest.mark.parametrize('guidance', [None, 'Historical note is not a registered option'])
def test_nonregistered_context_preserves_frozen_thinking_default(execution, monkeypatch, guidance):
    store, _, context = execution
    context.artifacts['scoped_script_guidance'] = guidance
    async def backend(settings, prompt, **kwargs):
        assert kwargs['think'] is False
        return {'controlled': True}
    monkeypatch.setattr(scripts, 'ask_ollama_json', backend)
    asyncio.run(scripts.ask_model(store.settings, context, 'Controlled input.', scripts.ScriptReview, reviewing=True))
