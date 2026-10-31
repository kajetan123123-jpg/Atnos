"""Registered field edits reuse the normal writer/review/canonical workflow."""
import asyncio
from copy import deepcopy
import pytest
from pydantic import ValidationError

from atnos.production import scoped_script_revision as revision
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import ProductionBlocked
from atnos.production.steps import order_script
from test_scoped_script_revision import store, original_order, script, feedback


def request_for(script, feedback):
    data = feedback.model_dump(mode='json')
    data.update(target_segments=[len(script.segments), len(script.segments)], max_drafts=1,
                editable_fields={'s01': ['text'], 's02': ['visual_intent']}, issues=[
        {'code': 'naturalness', 'segment_ids': ['s01'], 'field': 'text',
         'detail': 'Correct the awkward opening without changing its topic or promise.'},
        {'code': 'unsupported_visual_intent', 'segment_ids': ['s02'], 'field': 'visual_intent',
         'detail': 'Describe the same physical desk without embedded lettering.'}])
    return data


def test_optional_scope_preserves_legacy_serialization(feedback):
    old = feedback.model_dump(mode='json')
    assert 'editable_fields' not in old
    assert revision.ScriptFeedback.model_validate(old).model_dump(mode='json') == old


@pytest.mark.parametrize('mask', [{'s01': ['claims']}, {'s01': []}, {'s01': ['text', 'text']},
                                  {'s01': ['visual_intent']}])
def test_invalid_or_uncovered_masks_are_rejected(script, feedback, mask):
    data = request_for(script, feedback)
    data['editable_fields'] = mask
    with pytest.raises(ValidationError):
        revision.ScriptFeedback.model_validate(data)


def test_recorded_field_edit_preserves_every_other_field_and_reviews_whole_candidate(
        store, original_order, script, feedback, monkeypatch):
    job, _ = original_order
    revision.register_feedback(store.settings, job, request_for(script, feedback), approved=True)
    old = script.model_dump(mode='json')
    seen = []
    async def writer(settings, context, prompt, schema, **kwargs):
        assert schema is order_script.EditorialFieldRepair
        assert kwargs['decoding_schema']['properties']['patches']['required'] == ['s01', 's02']
        seen.append('writer')
        return {'patches': {'s01': {'text': 'Controlled corrected opening for this isolated contract test.'},
                            's02': {'visual_intent': 'An illustrated desk and blank agreement card.'}}}
    async def reviewer(settings, context, candidate):
        seen.append('review')
        assert candidate.segments[0].text != script.segments[0].text
        for index, segment in enumerate(candidate.segments):
            actual = segment.model_dump(mode='json')
            expected = deepcopy(old['segments'][index])
            if index == 0: expected['text'] = actual['text']
            if index == 1: expected['visual_intent'] = actual['visual_intent']
            assert actual == expected
        assert candidate.claims == script.claims
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_contract_only'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert seen == ['writer', 'review']
    assert result['title'] == old['title'] and result['description'] == old['description']
    assert revision.load_feedback(store, job)['feedback']['editable_fields'] == {'s01': ['text'], 's02': ['visual_intent']}


def test_unrequested_patch_cannot_reach_review_or_change_canonical(
        store, original_order, script, feedback, monkeypatch):
    job, _ = original_order
    monkeypatch.setattr(store.settings, 'atnos_local_work_unlimited', True)
    revision.register_feedback(store.settings, job, request_for(script, feedback), approved=True)
    before = (store.context(job).artifact_dir / 'script.json').read_bytes()
    async def writer(*args, **kwargs):
        return {'patches': {'s01': {'text': 'Controlled opening.', 'on_screen': 'Unauthorized change'},
                            's02': {'visual_intent': 'Controlled illustration.'}}}
    async def reviewer(*args, **kwargs):
        pytest.fail('Unauthorized fields must be rejected before review')
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert (store.context(job).artifact_dir / 'script.json').read_bytes() == before


def test_visual_only_progress_keeps_narration_and_reaches_normal_review(
        store, original_order, script, feedback, monkeypatch):
    job, _ = original_order
    data = request_for(script, feedback)
    data['editable_fields'] = {'s02': ['visual_intent']}
    data['issues'] = [data['issues'][1]]
    revision.register_feedback(store.settings, job, data, approved=True)
    async def writer(*args, **kwargs):
        return {'patches': {'s02': {'visual_intent': 'Physical illustration of blank paper on a wooden desk.'}}}
    async def reviewer(settings, context, candidate):
        assert [s.text for s in candidate.segments] == [s.text for s in script.segments]
        guidance = order_script.active_repair_review_guidance(context)
        assert guidance['editable_fields'] == data['editable_fields']
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_contract_only'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert result['segments'][1]['visual_intent'] != script.segments[1].visual_intent
    assert result['qa']['status'] == 'passed'


def test_unknown_scene_is_rejected_before_registration_changes_any_history(
        store, original_order, script, feedback):
    job, _ = original_order
    before = store.get(job)
    data = request_for(script, feedback)
    data['editable_fields']['s99'] = ['text']
    data['issues'].append({'code': 'naturalness', 'segment_ids': ['s99'], 'field': 'text',
                           'detail': 'This nonexistent scene must not become editable.'})
    with pytest.raises(ValueError, match='outside the original|preserve existing'):
        revision.register_feedback(store.settings, job, data, approved=True)
    assert revision.load_feedback(store, job) is None
    after = store.get(job)
    assert before['provider_calls'] == after['provider_calls']
    assert before['checkpoints'] == after['checkpoints']


def test_editable_field_mask_is_part_of_the_immutable_request(store, original_order, script, feedback):
    from atnos.production.order_store import OrderConflict
    job, _ = original_order
    data = request_for(script, feedback)
    receipt = revision.register_feedback(store.settings, job, data, approved=True)
    assert revision.register_feedback(store.settings, job, data, approved=True)['status'] == 'already_registered'
    changed = deepcopy(data)
    del changed['editable_fields']['s01']
    with pytest.raises(OrderConflict):
        revision.register_feedback(store.settings, job, changed, approved=True)
    assert revision.load_feedback(store, job)['feedback_hash'] == receipt['feedback_hash']


def test_field_writer_omits_only_rejected_visual_reference_from_prompt_not_history(
        store, original_order, script, feedback):
    job, _ = original_order
    data = request_for(script, feedback)
    previous = script.model_copy(deep=True)
    previous.segments[1].visual_intent = 'REJECTED-TITLE-TOGGLE-REFERENCE'
    previous.segments[2].visual_intent = 'LOCKED-ILLUSTRATION-REFERENCE'
    before = previous.model_dump(mode='json')
    prompt = revision.scoped_field_writer_prompt(store.context(job), previous, data)
    assert 'REJECTED-TITLE-TOGGLE-REFERENCE' not in prompt
    assert 'LOCKED-ILLUSTRATION-REFERENCE' in prompt
    assert previous.segments[1].text in prompt
    assert previous.model_dump(mode='json') == before
    assert data['editable_fields'] == {'s01': ['text'], 's02': ['visual_intent']}
