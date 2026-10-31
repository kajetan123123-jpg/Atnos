"""Explicit literal obligations prevent the recorded empty-calendar false PASS.

These checks prove mention of registered cues, never semantic direction or art quality.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from atnos.production.scoped_script_revision import ScriptFeedback, feedback_issues
from atnos.production.steps import order_script as scripts
from test_scoped_script_revision import store, original_order, feedback, script

ROOT = Path(__file__).parent / 'fixtures/visual_review_20261026'

def load(name):
    return json.loads((ROOT/name).read_text('utf-8'))

def constrained_feedback():
    feedback = load('feedback.json')
    feedback['issues'].append({'code': 'unsupported_visual_intent', 'segment_ids': ['s03'],
        'field': 'visual_intent',
        'detail': 'Show a calendar, reminder and separate cutoff; the reminder is earlier than the cutoff. Mention each cue explicitly. This literal check does not itself prove correct order.',
        'required_phrase_groups': [['calendar'], ['reminder'], ['cutoff', 'deadline'], ['before', 'earlier']]})
    return ScriptFeedback.model_validate(feedback).model_dump(mode='json')

def test_real_source_fixture_integrity():
    for name, digest in load('provenance.json')['fixtures'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest

def test_recorded_pass_does_not_satisfy_new_explicit_visual_obligations():
    candidate = scripts.EditorialScript.model_validate(load('candidate.json'))
    verdict = load('review.json')
    assert verdict['status'] == 'passed'
    assert any(check['criterion_index'] == 6 and check['segment_id'] == 's03' and check['satisfied']
               for check in verdict['editorial_checks'])
    # Historical request had no literal-inclusion contract; it is not rewritten.
    assert not feedback_issues(candidate, load('feedback.json'))
    issues = feedback_issues(candidate, constrained_feedback())
    assert any(issue['segment_ids'] == ['s03'] and 'literal inclusion' in issue['detail'] for issue in issues)

def test_normal_revision_exhausts_only_bounded_writer_retry_for_missing_literal_requirements(
        store, original_order, monkeypatch):
    from atnos.production.scoped_script_revision import register_feedback
    from atnos.production.order_execution import StageExecutor, produce_script
    from atnos.production.order_store import ProductionBlocked, file_hash
    job, _ = original_order
    root = store.context(job).artifact_dir
    canonical = file_hash(root/'script.json')
    target = constrained_feedback()['issues'][-1]
    request = ScriptFeedback(editor='Controlled offline operator', reason='Require explicit visual cues in this isolated regression.',
        issues=[target], target_segments=(4, 4), max_drafts=1)
    registered = register_feedback(store.settings, job, request, approved=True)
    calls = []
    async def author(*args, **kwargs):
        calls.append('writer')
        return load('candidate.json')
    async def incorrect_review(*args, **kwargs):
        raise AssertionError('Invalid literal output must not reach review')
    monkeypatch.setattr(scripts, 'ask_model', author)
    monkeypatch.setattr(scripts, 'review', incorrect_review)
    with store.lease(job, script_only=True) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    after = store.get(job)
    prefix = 'script_scoped/' + registered['feedback_hash'][:12]
    draft = next(row for row in after['checkpoints'] if row['stage'] == prefix + '/draft/0')
    assert draft['status'] == 'failed'
    assert 'literal inclusion' in draft['error_json']
    assert len(calls) == draft['attempts'] == 3
    assert not any(row['stage'] == prefix + '/review/0' for row in after['checkpoints'])
    assert not any(row['stage'] == prefix + '/accepted' for row in after['checkpoints'])
    assert file_hash(root/'script.json') == canonical
    with store.lease(job, script_only=True) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert len(calls) == 3  # A fresh executor cannot restart the consumed budget.
    assert file_hash(root/'script.json') == canonical

def test_normal_writer_retry_can_fix_literal_output_before_one_review(store, original_order, monkeypatch):
    from atnos.production.scoped_script_revision import register_feedback
    from atnos.production.order_execution import StageExecutor, produce_script
    job, _ = original_order
    request = ScriptFeedback(editor='Controlled offline operator', reason='Require explicit visual cues in this isolated retry regression.',
        issues=[constrained_feedback()['issues'][-1]], target_segments=(4, 4), max_drafts=1)
    register_feedback(store.settings, job, request, approved=True)
    calls = []
    async def author(*args, **kwargs):
        calls.append('writer')
        raw = load('candidate.json')
        if len(calls) > 1:
            raw['segments'][2]['visual_intent'] = 'A calendar has a reminder before a separate cutoff mark.'
        return raw
    async def review(*args, **kwargs):
        calls.append('review')
        return {'status': 'passed', 'issues': []}
    monkeypatch.setattr(scripts, 'ask_model', author)
    monkeypatch.setattr(scripts, 'review', review)
    with store.lease(job, script_only=True) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert calls == ['writer', 'writer', 'review']
    assert result['qa']['status'] == 'passed'

@pytest.mark.parametrize('visual', [
    'A calendar has a reminder mark earlier than a separate cutoff mark.',
    'A calendar shows a reminder before a distinct deadline marker.',
])
def test_explicit_alternatives_are_accepted_without_modifying_candidate(visual):
    raw = load('candidate.json')
    raw['segments'][2]['visual_intent'] = visual
    candidate = scripts.EditorialScript.model_validate(raw)
    before = candidate.model_dump(mode='json')
    assert not feedback_issues(candidate, constrained_feedback())
    assert candidate.model_dump(mode='json') == before

def test_narration_or_another_scene_cannot_supply_missing_visual_cues():
    raw = load('candidate.json')
    raw['segments'][0]['visual_intent'] = 'A calendar shows a reminder before a cutoff.'
    assert feedback_issues(scripts.EditorialScript.model_validate(raw), constrained_feedback())

def test_empty_optional_field_preserves_legacy_feedback_hash_and_reviewer_receives_new_field():
    raw = load('feedback.json')
    legacy = ScriptFeedback.model_validate(raw).model_dump(mode='json')
    assert legacy == raw
    constrained = constrained_feedback()
    context = type('Context', (), {'artifacts': {'scoped_script_guidance': constrained}})()
    current = scripts.active_repair_review_guidance(context)
    assert current['issues'][-1]['required_phrase_groups'] == constrained['issues'][-1]['required_phrase_groups']

@pytest.mark.parametrize('groups, field, segments', [
    ([[]], 'visual_intent', ['s03']),
    ([['x']], 'visual_intent', ['s03']),
    ([['reminder', 'REMINDER']], 'visual_intent', ['s03']),
    ([['calendar']], None, ['s03']),
    ([['calendar']], 'visual_intent', []),
])
def test_bad_literal_contract_is_rejected(groups, field, segments):
    raw = load('feedback.json')
    raw['issues'].append({'code': 'visual_relation', 'detail': 'Explicit calendar relation requirement.',
        'field': field, 'segment_ids': segments, 'required_phrase_groups': groups})
    with pytest.raises(ValidationError):
        ScriptFeedback.model_validate(raw)

def test_literal_contract_rejects_an_impossible_required_and_forbidden_group():
    raw = constrained_feedback()
    raw['issues'][-1]['forbidden_phrases'] = ['cutoff', 'deadline']
    with pytest.raises(ValidationError, match='only forbidden'):
        ScriptFeedback.model_validate(raw)
