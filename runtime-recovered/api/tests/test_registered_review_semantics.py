"""Recorded semantic failures: binding tests do not prove model reasoning."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from atnos.production.steps import order_script as scripts
from test_production_order_script import execution
from test_production_order_store import store

ROOT = Path(__file__).parent / 'fixtures/review_semantics_20261025'
def load(name):
    return json.loads((ROOT / name).read_text('utf-8-sig'))


def test_real_fixture_integrity():
    for name, digest in load('provenance.json')['fixtures'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest


def test_recorded_word_budget_check_cannot_report_an_unrelated_semantic_defect():
    draft = scripts.EditorialScript.model_validate(load('candidate.json'))
    requirements = load('feedback.json')['issues']
    verdict = scripts.decode_registered_review(load('review.json'), requirements, draft)
    # Legacy format/evidence accepts the exact quote, but criterion5 is a word
    # budget check and the model alleged an entirely different deadline defect.
    assert len(draft.segments[0].text.split()) == 16
    with pytest.raises(ValueError, match='deterministic criterion'):
        scripts.validate_review_machine_findings(verdict, [], requirements=requirements)


def test_real_word_budget_failure_is_retained_not_overridden():
    draft = scripts.EditorialScript.model_validate(load('candidate.json'))
    requirements = load('feedback.json')['issues']
    verdict = scripts.decode_registered_review(load('review.json'), requirements, draft)
    scripts.validate_review_machine_findings(verdict,
        [{'code': 'word_budget', 'segment_ids': ['s01']}], requirements=requirements)
    assert verdict.passed is False


def test_named_decoder_collects_evidence_before_judgment():
    draft = scripts.EditorialScript.model_validate(load('candidate.json'))
    wire = scripts.registered_review_wire_model(load('feedback.json')['issues'], draft).model_json_schema()
    keys = list(wire['properties'])
    assert keys.index('editorial_checks') < keys.index('issues') < keys.index('passed')
    fields = list(scripts.RegisteredCriterionCheck.model_json_schema()['properties'])
    assert fields.index('quote') < fields.index('satisfied')


def test_review_prompt_binds_codes_and_explains_actual_semantic_failure_classes(execution, monkeypatch):
    store, _, context = execution
    context.artifacts['scoped_script_guidance'] = load('feedback.json')
    context.artifacts['project_profile']['production']['word_budget'] = [66, 81]
    draft = scripts.EditorialScript.model_validate(load('candidate.json'))
    seen = []
    class StopBeforeInference(Exception): pass
    async def capture(settings, ctx, prompt, schema, **kwargs):
        seen.append(prompt)
        raise StopBeforeInference
    monkeypatch.setattr(scripts, 'ask_model', capture)
    before = deepcopy(context.artifacts)
    with pytest.raises(StopBeforeInference):
        asyncio.run(scripts.review(store.settings, context, draft))
    prompt = seen[0]
    assert '"code": "word_budget"' in prompt
    for rule in ('Reading a price is not enabling renewal',
                 'A check of an agreement is a check of its terms',
                 'Required confirmation is compliance, not a defect',
                 'An imperative verb with its object is not a bare object list'):
        assert rule in prompt
    assert 'Do not decide passed or invent issues before checking the evidence' in prompt
    assert context.artifacts == before


def test_review_system_does_not_instruct_reviewer_to_write_or_pad(execution):
    context = execution[2]
    context.artifacts['scoped_script_guidance'] = load('feedback.json')
    reviewer = scripts.registered_script_system_context(context, reviewing=True)
    author = scripts.registered_script_system_context(context, reviewing=False)
    assert 'Assess, do not author or repair the script' in reviewer
    assert 'Satisfy the whole narration budget through useful middle steps' not in reviewer
    assert 'Satisfy the whole narration budget through useful middle steps' in author
