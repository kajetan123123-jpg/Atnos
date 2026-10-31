"""Registered v5 input replay; isolated fixtures, no inference or acceptance."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from atnos.production.steps import order_script as scripts
from test_production_order_script import execution
from test_production_order_store import store

FIXTURES = Path(__file__).parent / 'fixtures/wb_review_v5_20261010'
REQUEST_SHA256 = 'b853657d5198b34e8d2a97f3489adc012deb4df1e9dce42b1179c647149e9ae3'


@pytest.fixture
def registered(execution):
    task_store, job, context = execution
    source = FIXTURES / 'registered-v5-request.json'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == REQUEST_SHA256
    request = json.loads(source.read_text('utf8'))
    request['issues'] = request.pop('editorial_issues')
    request['mode'] = 'hypothetical_review'
    context.artifacts['studio_script_recovery'] = request
    draft = scripts.EditorialScript.model_validate(json.loads((FIXTURES/'initial-script.json').read_text('utf8')))
    context.artifacts['script_calculations'] = {'claims': {c.id: c.model_dump(mode='json') for c in draft.claims}}
    issues = [deepcopy(request['issues'][0])]
    return task_store, job, context, draft, issues


def capture_prompt(monkeypatch, task_store, context, draft, issues):
    captured = {}
    class StopBeforeInference(Exception):
        pass
    async def stop(_settings, _context, prompt, schema, **kwargs):
        captured.update(prompt=prompt, schema=schema, decoding=kwargs['decoding_schema'])
        raise StopBeforeInference()
    monkeypatch.setattr(scripts, 'ask_model', stop)
    with pytest.raises(StopBeforeInference):
        asyncio.run(scripts.repair(task_store.settings, context, draft, issues))
    return captured


def test_registered_field_repair_retains_actual_semantic_targets_and_locked_context(registered, monkeypatch):
    task_store, job, context, draft, issues = registered
    before = task_store.get(job)
    captured = capture_prompt(monkeypatch, task_store, context, draft, issues)
    assert captured['schema'] is scripts.EditorialFieldRepair
    payload = json.loads(captured['prompt'].split('CURRENT AUTHORITATIVE INPUT: ', 1)[1])
    registered_context = payload['registered_repair']
    request = context.artifacts['studio_script_recovery']
    assert registered_context['acceptance_criteria'] == request['acceptance_criteria']
    assert registered_context['registered_note'] == request['note']
    assert registered_context['current_script'] == draft.model_dump(mode='json')
    assert registered_context['allowed_segment_ids'] == ['1']
    assert set(registered_context['locked_segment_ids']) == {'0', '2', '3', '4', '5'}
    assert 'only the registered meaning defects in the currently allowed segments' in payload['meaning']
    assert 'without changing the scene meaning' not in payload['meaning']
    assert captured['decoding']['$defs']['EditorialFieldPatch']['properties']['id']['enum'] == ['1']
    after = task_store.get(job)
    for field in ('status', 'current_step', 'provider_calls', 'checkpoints', 'history', 'request', 'profile'):
        assert after[field] == before[field]


def test_unregistered_field_repair_keeps_existing_meaning_preservation(execution, monkeypatch):
    task_store, _, context = execution
    draft = scripts.EditorialScript.model_validate(json.loads((FIXTURES/'initial-script.json').read_text('utf8')))
    context.artifacts['script_calculations'] = {'claims': {c.id: c.model_dump(mode='json') for c in draft.claims}}
    captured = capture_prompt(monkeypatch, task_store, context, draft,
        [{'code': 'naturalness', 'segment_ids': ['1'], 'detail': 'Use natural wording.'}])
    payload = json.loads(captured['prompt'].split('CURRENT AUTHORITATIVE INPUT: ', 1)[1])
    assert 'registered_repair' not in payload
    assert payload['meaning'] == 'Correct the current wording without changing the scene meaning or financial assumptions.'


def test_actual_v5_full_repair_path_does_not_lock_the_rejected_meaning(registered, monkeypatch):
    task_store, _, context, draft, _ = registered
    issues = deepcopy(context.artifacts['studio_script_recovery']['issues'])
    captured = capture_prompt(monkeypatch, task_store, context, draft, issues)
    assert captured['schema'] is scripts.AdaptedScriptRepair
    assert 'ACTIVE HYPOTHETICAL STUDIO REPAIR BUNDLE' in captured['prompt']
    assert 'Preserve meaning, currency, claims, and numbers unless' not in captured['prompt']
    assert 'Correct only the registered meaning defects in the currently allowed segments' in captured['prompt']
    for target in issues:
        assert target['detail'] in captured['prompt']
    allowed = captured['decoding']['$defs']['ScriptSegment']['properties']['id']['enum']
    assert set(allowed) == {'1', '3', '4', '5'}
    assert draft.segments[0].text in captured['prompt']
    assert draft.segments[2].text in captured['prompt']


def test_unregistered_full_repair_keeps_existing_preserve_meaning_rule(registered, monkeypatch):
    task_store, _, context, draft, _ = registered
    issues = deepcopy(context.artifacts.pop('studio_script_recovery')['issues'])
    captured = capture_prompt(monkeypatch, task_store, context, draft, issues)
    assert captured['schema'] is scripts.AdaptedScriptRepair
    assert 'ACTIVE HYPOTHETICAL STUDIO REPAIR BUNDLE' not in captured['prompt']
    assert 'Preserve meaning, currency, claims, and numbers unless' in captured['prompt']


def test_registered_field_patch_preserves_all_immutable_data(registered, monkeypatch):
    task_store, _, context, draft, issues = registered
    before = draft.model_dump(mode='json')
    replacement = {'id': '1',
        'text': 'Compare what the purchase gives you with the next best use of your money, then choose according to your needs.',
        'on_screen': 'COMPARE YOUR ALTERNATIVES'}
    async def local_fixture(*_args, **_kwargs):
        return {'patches': [replacement]}
    monkeypatch.setattr(scripts, 'ask_model', local_fixture)
    fixed = asyncio.run(scripts.repair(task_store.settings, context, draft, issues))
    for original, updated in zip(draft.segments, fixed.segments, strict=True):
        if original.id != '1':
            assert updated == original
        else:
            assert updated.model_dump(exclude={'text', 'on_screen'}) == original.model_dump(exclude={'text', 'on_screen'})
    assert fixed.claims == draft.claims
    assert fixed.title == draft.title and fixed.description == draft.description
    assert scripts.financial_fingerprint(fixed) == scripts.financial_fingerprint(draft)
    assert draft.model_dump(mode='json') == before


@pytest.mark.parametrize('fault', ['locked_scene', 'new_number', 'new_claim', 'visual_direction'])
def test_registered_context_does_not_expand_field_patch_permission(registered, monkeypatch, fault):
    task_store, _, context, draft, issues = registered
    patch = {'id': '1', 'text': 'Compare the purchase with a useful alternative before deciding.',
             'on_screen': 'COMPARE ALTERNATIVES'}
    if fault == 'locked_scene':
        patch['id'] = '2'
    elif fault == 'new_number':
        patch['text'] = 'Compare the purchase with saving twenty dollars for your goal.'
    elif fault == 'new_claim':
        patch['claim_ids'] = ['invented']
    else:
        patch['visual_intent'] = 'A newly invented Alex pose with a moving object.'
    async def local_fixture(*_args, **_kwargs):
        return {'patches': [patch]}
    monkeypatch.setattr(scripts, 'ask_model', local_fixture)
    before = draft.model_dump(mode='json')
    with pytest.raises(ValueError):
        asyncio.run(scripts.repair(task_store.settings, context, draft, issues))
    assert draft.model_dump(mode='json') == before
