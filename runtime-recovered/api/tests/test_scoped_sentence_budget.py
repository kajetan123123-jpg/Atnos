"""Whole-script bounds use the existing sentence allocator, never text clipping."""
from copy import deepcopy
from types import SimpleNamespace
import re
import json
from pathlib import Path
import asyncio

import jsonschema
import pytest
from pydantic import ValidationError

from atnos.production import scoped_script_revision as revision
from atnos.production.steps import order_script
from test_scoped_script_revision import four_thoughts, original_order, store, script, feedback


def setup(previous):
    context = SimpleNamespace(artifacts={'project_profile': {'production': {
        'word_budget': [66, 81], 'language': 'en'}}})
    feedback = {'target_segments': [4, 4], 'issues': [
        {'code': 'word_budget', 'field': 'text',
         'segment_ids': [s.id for s in previous.segments], 'detail': 'Meet the current narration budget.'}]}
    schema = order_script.generation_schema(order_script.EditorialScript)
    return schema, context, feedback


def test_full_correction_decoder_and_runtime_bound_complete_sentences(four_thoughts):
    schema, context, feedback = setup(four_thoughts)
    before = deepcopy(schema)
    bound, sentences = revision.bind_scoped_sentence_budget(schema, context, feedback, four_thoughts)
    assert schema == before
    jsonschema.Draft202012Validator.check_schema(bound)
    assert sentences is not None
    ranges = []
    for field in sentences.model_fields:
        pattern = sentences.model_json_schema()['properties'][field]['pattern']
        low, high = map(int, re.search(r'\{(\d+),(\d+)\}', pattern).groups())
        ranges.append((low+1, high+1))
    assert sum(a for a, _ in ranges) == 66
    assert sum(b for _, b in ranges) == 81
    assert max(b for _, b in ranges) <= 32
    raw = four_thoughts.model_dump(mode='json')
    for segment, (low, _) in zip(raw['segments'], ranges):
        segment['text'] = ' '.join(['Controlled'] * low)
    jsonschema.validate(raw, bound)
    sentences.model_validate({s['id']: s['text'] for s in raw['segments']})
    for count in (1, 40):
        bad = deepcopy(raw)
        bad['segments'][1]['text'] = ' '.join(['Controlled'] * count)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, bound)
        with pytest.raises(ValidationError):
            sentences.model_validate({s['id']: s['text'] for s in bad['segments']})


@pytest.mark.parametrize('scope', ['variable_count', 'partial', 'claims', 'facts', 'non_english'])
def test_other_contracts_are_not_reallocated(four_thoughts, scope):
    schema, context, feedback = setup(four_thoughts)
    if scope == 'variable_count': feedback['target_segments'] = [4, 5]
    elif scope == 'partial': feedback['issues'][0]['segment_ids'] = ['s02']
    elif scope == 'claims': feedback['restore_calculation_ids'] = ['c1']
    elif scope == 'facts': four_thoughts.segments[1].fact_ids = ['real-fact']
    else: context.artifacts['project_profile']['production']['language'] = 'pl'
    result, model = revision.bind_scoped_sentence_budget(schema, context, feedback, four_thoughts)
    assert result == schema and model is None


def test_native_scene_binding_is_preserved(four_thoughts):
    schema, context, feedback = setup(four_thoughts)
    schema['properties']['segments']['items'] = {'anyOf': [{'$ref': '#/$defs/ScriptSegment'}]}
    before = deepcopy(schema)
    result, model = revision.bind_scoped_sentence_budget(schema, context, feedback, four_thoughts)
    assert result == before and model is None


@pytest.mark.parametrize('name', ['overlong-121', 'short-59'])
def test_actual_failed_model_responses_are_rejected_by_decoder_and_runtime(name):
    raw = json.loads((Path(__file__).parent / 'fixtures' / 'scoped_sentence_budget_20261025'
                      / (name + '.json')).read_text('utf-8'))
    previous = order_script.EditorialScript.model_validate(raw)
    schema, context, feedback = setup(previous)
    bound, sentences = revision.bind_scoped_sentence_budget(schema, context, feedback, previous)
    assert sentences is not None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, bound)
    with pytest.raises(ValidationError):
        sentences.model_validate({s['id']: s['text'] for s in raw['segments']})


def test_actual_budget_compliant_candidate_still_fails_visual_content_gate():
    raw = json.loads((Path(__file__).parent / 'fixtures' / 'scoped_sentence_budget_20261025'
                      / 'budget-pass-content-fail-72.json').read_text('utf-8'))
    script = order_script.EditorialScript.model_validate(raw)
    schema, context, feedback = setup(script)
    bound, sentences = revision.bind_scoped_sentence_budget(schema, context, feedback, script)
    jsonschema.validate(raw, bound)
    sentences.model_validate({s.id: s.text for s in script.segments})
    feedback['issues'].append({'code': 'unsupported_visual_intent',
        'segment_ids': [s.id for s in script.segments], 'field': 'visual_intent',
        'detail': 'Use physical still illustrations without embedded lettering; renderer supplies text.'})
    issues = revision.feedback_issues(script, feedback)
    assert any(i['code'] == 'unsupported_visual_intent' for i in issues)


def test_normal_registered_correction_rejects_decoder_ignoring_response_before_review_or_canonical(
        store, original_order, script, feedback, monkeypatch):
    from atnos.production.order_execution import StageExecutor, produce_script
    from atnos.production.order_store import ProductionBlocked
    job, _ = original_order
    # Match the actual local runtime policy. The legacy finite fixture instead
    # stops at its historical provider-call ceiling, not the local retry pause.
    monkeypatch.setattr(store.settings, 'atnos_local_work_unlimited', True)
    ids = [s.id for s in script.segments]
    request = feedback.model_copy(update={'target_segments': (len(ids), len(ids)), 'max_drafts': 1,
        'issues': [revision.EditorialDefect(code='word_budget', field='text', segment_ids=ids,
                    detail='Write within the unchanged total narration budget using useful full thoughts.')]})
    revision.register_feedback(store.settings, job, request, approved=True)
    canonical = (store.context(job).artifact_dir / 'script.json').read_bytes()
    calls = []
    async def writer(settings, context, prompt, response_model, **kwargs):
        assert 'ScopedSentenceBudget1' in kwargs['decoding_schema']['$defs']
        calls.append(True)
        raw = script.model_dump(mode='json')
        for segment in raw['segments']:
            segment['text'] = ' '.join(['Controlled'] * 40)
        return raw
    async def reviewer(*args, **kwargs):
        pytest.fail('Out-of-budget decoder response must not reach review')
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert 1 <= len(calls) <= 3
    assert (store.context(job).artifact_dir / 'script.json').read_bytes() == canonical
    assert not any(row['stage'].endswith('/accepted') for row in store.get(job)['checkpoints'])
