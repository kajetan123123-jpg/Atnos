"""Ordinary scoped review uses existing grounded review; no inference/quality receipts."""
from copy import deepcopy
import asyncio
import json
from pathlib import Path

import pytest

from atnos.production.steps import order_script as scripts
from atnos.production.scoped_script_revision import EditorialDefect
from test_production_order_script import execution, script
from test_production_order_store import store


def requirements():
    return [{'code': 'brief_fidelity', 'segment_ids': ['s02'], 'field': 'on_screen',
             'detail': 'Require a meaningful viewer-facing headline, not a prop list.'}]


def wire(script):
    return {'review_format': 'registered_named_v1', 'passed': True, 'issues': [],
            'editorial_checks': {'criterion_0_segment_s02': {'satisfied': True,
                'field': 'on_screen', 'quote': script.segments[1].on_screen,
                'reason': 'Controlled structure fixture only.'}}}


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('Offline test attempted real inference')
    monkeypatch.setattr(scripts, 'ask_ollama_json', forbidden)


def test_explicit_field_is_additive_and_legacy_defect_serialization_is_unchanged():
    old = {'code': 'naturalness', 'segment_ids': ['s02'], 'detail': 'Correct the current awkward sentence.'}
    assert EditorialDefect.model_validate(old).model_dump(mode='json') == old
    assert EditorialDefect.model_validate({**old, 'field': 'on_screen'}).model_dump()['field'] == 'on_screen'
    with pytest.raises(ValueError):
        EditorialDefect.model_validate({**old, 'field': 'claims'})


@pytest.mark.parametrize('overrides', [
    {'forbidden_phrases': ['hidden deadline']},
    {'field': 'text', 'segment_ids': [], 'forbidden_phrases': ['hidden deadline']},
    {'field': 'text', 'forbidden_phrases': [' ']},
    {'field': 'text', 'forbidden_phrases': ['hidden', 'HIDDEN']},
])
def test_literal_constraints_require_explicit_bounded_scope(overrides):
    with pytest.raises(ValueError):
        EditorialDefect.model_validate({'code': 'brief_fidelity', 'segment_ids': ['s01'],
            'detail': 'Remove this explicitly disallowed wording.', **overrides})


def test_recorded_semantic_false_pass_cannot_override_registered_literal_exclusions():
    from atnos.production.scoped_script_revision import feedback_issues
    root = Path(__file__).parent / 'fixtures/wb_scoped_review_20261022'
    draft = scripts.EditorialScript.model_validate(json.loads((root / 'false-pass-candidate.json').read_text('utf-8')))
    saved = json.loads((root / 'false-pass-review.json').read_text('utf-8'))
    assert saved['status'] == 'passed'
    feedback = {'target_segments': [4, 4], 'issues': [dict(code='brief_fidelity',
        segment_ids=['s01'], field='text', detail='Remove the unsupported hidden-deadline allegation.',
        forbidden_phrases=['hidden deadline', 'when you signed up'])]}
    issues = feedback_issues(draft, feedback)
    assert len(issues) == 1 and issues[0]['segment_ids'] == ['s01']
    assert issues[0]['fields'] == ['text']
    draft.segments[0].text = 'Check whether your trial agreement includes automatic renewal and a cancellation cutoff.'
    draft.segments[1].text += ' Hidden deadline.'
    assert feedback_issues(draft, feedback) == []  # Never expand the registered scene scope.


@pytest.mark.parametrize('defect', ['bare_pass', 'missing_check', 'absent_quote', 'wrong_field', 'false_pass'])
def test_scoped_review_rejects_missing_or_ungrounded_receipt(script, defect):
    response = wire(script)
    check = response['editorial_checks']['criterion_0_segment_s02']
    if defect == 'bare_pass':
        response = {'passed': True, 'issues': []}
    elif defect == 'missing_check':
        response['editorial_checks'] = {}
    elif defect == 'absent_quote':
        check['quote'] = 'Not present in this actual current field.'
    elif defect == 'wrong_field':
        check.update(field='text', quote=script.segments[1].text)
    else:
        check['satisfied'] = False
    with pytest.raises(ValueError):
        scripts.decode_registered_review(response, requirements(), script)


def test_existing_validator_cannot_validate_headline_criterion_using_narration(script):
    verdict = scripts.ScriptReview(passed=True, issues=[], editorial_checks=[{
        'criterion_index': 0, 'segment_id': 's02', 'satisfied': True,
        'field': 'text', 'quote': script.segments[1].text, 'reason': 'Wrong field fixture.'}])
    with pytest.raises(ValueError, match='wrong registered field'):
        scripts.validate_editorial_review(verdict, script, requirements())


def test_recorded_pipeline_review_with_resolved_cta_complaint_is_not_a_grounded_receipt():
    root = Path(__file__).parent / 'fixtures/wb_scoped_review_20261022'
    draft = scripts.EditorialScript.model_validate(json.loads((root / 'candidate.json').read_text('utf-8')))
    saved = json.loads((root / 'pipeline-review.json').read_text('utf-8'))
    assert draft.segments[-1].text == 'Follow Wealth Blueprint. Build wealth on purpose.'
    assert any('closing line should be' in issue['detail'] for issue in saved['issues'])
    verdict = scripts.ScriptReview.model_validate({'passed': saved['status'] == 'passed',
        **{key: saved[key] for key in ('issues', 'editorial_checks', 'issue_evidence')}})
    with pytest.raises(ValueError, match='every registered criterion'):
        scripts.validate_editorial_review(verdict, draft, [
            {'segment_ids': ['s04'], 'field': 'text', 'detail': 'Preserve the requested channel CTA.'}])


def test_ordinary_scoped_review_uses_the_existing_named_wire(execution, script, monkeypatch):
    context = execution[2]
    context.artifacts['scoped_script_guidance'] = {'reason': 'Current operator feedback.', 'issues': requirements()}
    before = deepcopy(context.artifacts['scoped_script_guidance'])
    async def backend(settings, context, prompt, schema, **kwargs):
        assert 'registered_named_v1' in prompt
        assert 'criterion_0_segment_s02' in prompt
        assert kwargs['reject_duplicate_keys'] is True
        return wire(script)
    monkeypatch.setattr(scripts, 'ask_model', backend)
    result = asyncio.run(scripts.review(execution[0].settings, context, script))
    assert result['editorial_checks'][0]['field'] == 'on_screen'
    assert result['editorial_checks'][0]['quote'] == script.segments[1].on_screen
    assert context.artifacts['scoped_script_guidance'] == before
    # Only contract behavior; the controlled satisfied value is not quality evidence.
