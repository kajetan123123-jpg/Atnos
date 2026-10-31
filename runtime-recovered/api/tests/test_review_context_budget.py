"""Real registered review context must leave room without dropping requirements."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from atnos.production.steps import order_script as scripts
from test_production_order_script import execution, script
from test_production_order_store import store
from test_scoped_script_revision import original_order, feedback, four_thoughts

ROOT = Path(__file__).parent / 'fixtures' / 'review_context_20261025'


def load(name):
    return json.loads((ROOT / name).read_text('utf-8-sig'))


def section(prompt, left, right):
    return json.loads(prompt.split(left, 1)[1].split(right, 1)[0])


def test_real_context_is_compacted_without_losing_notes_or_current_criteria(execution, monkeypatch):
    store, _, context = execution
    old = load('legacy-call.json')
    history = section(old['prompt'], ' HISTORICAL EDITORIAL FEEDBACK: ', ' ACTIVE REGISTERED REPAIR: ')
    feedback = load('feedback.json')
    context.artifacts['scoped_script_guidance'] = feedback
    context.artifacts['project_profile']['feedback_rules'] = history
    context.artifacts['project_profile']['production'].update(
        load('writing-profile.json'))
    old_input = section(old['prompt'], ' INPUT: ', ' REVIEWED SOURCE FACTS: ')
    monkeypatch.setattr(scripts, 'revision_input', lambda *args, **kwargs: deepcopy(old_input))
    candidate = scripts.EditorialScript.model_validate(load('candidate.json'))
    preserved = deepcopy(context.artifacts)
    captured = []
    class StopBeforeModel(Exception): pass
    async def model(settings, context, prompt, schema, **kwargs):
        captured.append(prompt)
        raise StopBeforeModel
    monkeypatch.setattr(scripts, 'ask_model', model)
    with pytest.raises(StopBeforeModel):
        asyncio.run(scripts.review(store.settings, context, candidate))
    prompt = captured[0]
    assert len(prompt) < len(old['prompt']) - 4000
    print(json.dumps({'legacy_user_chars': len(old['prompt']), 'compact_user_chars': len(prompt),
                      'preserved_history_notes': len([r for r in history if 'note' in r])}))
    assert ' ACTIVE REGISTERED REPAIR: ' not in prompt
    compact = section(prompt, ' HISTORICAL EDITORIAL FEEDBACK: ', ' CURRENT REGISTERED REPAIR: ')
    assert [r['note'] for r in compact if 'note' in r] == [r['note'] for r in history if 'note' in r]
    assert [r['category'] for r in compact if 'category' in r] == [r['category'] for r in history if 'category' in r]
    system = scripts.registered_script_system_context(context, reviewing=True)
    for target in feedback['issues']:
        assert target['detail'] in system
    for index, target in enumerate(feedback['issues']):
        for sid in target['segment_ids']:
            assert f'criterion_{index}_segment_{sid}' in prompt
    assert context.artifacts == preserved


def test_unknown_history_fields_are_preserved_and_known_audit_metadata_only_is_removed(execution):
    context = execution[2]
    rules = [{'id': 'rule-1', 'source_job_id': 'old-job', 'project': 'wealth_blueprint',
              'series_key': None, 'scope': 'channel', 'category': 'visual',
              'note': 'Zachowaj każde wymaganie, również polskie znaki.', 'created_at': 'old', 'active': True},
             {'category': 'future', 'note': 'Do not discard unknown policy fields.', 'new_policy': {'strict': True}}]
    context.artifacts['project_profile']['feedback_rules'] = rules
    before = deepcopy(rules)
    compact = scripts.compact_editorial_review_feedback(context)
    assert compact[0]['id'] == 'rule-1' and compact[0]['note'] == rules[0]['note']
    assert 'source_job_id' not in compact[0] and 'created_at' not in compact[0]
    assert compact[1] == rules[1]
    assert rules == before


def test_named_reason_is_bounded_in_decoder_and_runtime_but_keeps_evidence(script):
    targets = [{'code': 'naturalness', 'segment_ids': ['s01'], 'field': 'text', 'detail': 'Check naturalness.'}]
    raw = {'review_format': 'registered_named_v1', 'passed': True, 'issues': [], 'editorial_checks': {
        'criterion_0_segment_s01': {'satisfied': True, 'field': 'text',
                                  'quote': script.segments[0].text, 'reason': 'The current opening is natural.'}}}
    schema = scripts.editorial_review_generation_schema(targets, script, named_checks=True)
    jsonschema.validate(raw, schema)
    scripts.decode_registered_review(raw, targets, script)
    raw['editorial_checks']['criterion_0_segment_s01']['reason'] = 'Counting words repeatedly ' * 30
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(raw, schema)
    with pytest.raises(ValidationError): scripts.decode_registered_review(raw, targets, script)


def test_real_complete_but_inconsistent_review_stays_rejected():
    raw = json.loads(load('invalid-complete-review.json')['response']['message']['content'])
    with pytest.raises(ValueError):
        scripts.decode_registered_review(raw, load('feedback.json')['issues'],
                                        scripts.EditorialScript.model_validate(load('candidate.json')))


def test_actual_legible_document_request_is_a_visual_defect_even_if_reviewer_passes():
    from atnos.production.scoped_script_revision import feedback_issues, _unsupported_still_illustration
    candidate = scripts.EditorialScript.model_validate(load('candidate.json'))
    issues = feedback_issues(candidate, load('feedback.json'))
    assert any(i['code'] == 'unsupported_visual_intent' and 's02' in i['segment_ids'] for i in issues)
    assert _unsupported_still_illustration("A paper agreement titled 'Terms'.")
    assert not _unsupported_still_illustration('A blank card and calendar. No readable text or titled documents.')
    assert not _unsupported_still_illustration('A blank agreement card. Renderer adds all text as overlays.')


def test_changed_review_contract_resumes_intact_draft_without_reauthoring_or_reset(
        store, original_order, feedback, four_thoughts, monkeypatch):
    from atnos.production.scoped_script_revision import register_feedback
    from atnos.production.order_execution import StageExecutor, produce_script
    from atnos.production.order_store import ProductionBlocked
    job, _ = original_order
    monkeypatch.setattr(store.settings, 'atnos_local_work_unlimited', True)
    request = feedback.model_copy(update={'max_drafts': 1})
    register_feedback(store.settings, job, request, approved=True)
    calls = []
    async def writer(*args, **kwargs):
        calls.append('writer')
        return four_thoughts.model_dump(mode='json')
    async def broken_review(*args, **kwargs):
        calls.append('invalid-review')
        raise ValueError('Recorded reviewer format failure, controlled fixture.')
    monkeypatch.setattr(scripts, 'ask_model', writer)
    monkeypatch.setattr(scripts, 'review', broken_review)
    monkeypatch.setattr(scripts, 'SCRIPT_VALIDATOR_VERSION', 40)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    before = store.get(job)
    old_review = next(row for row in before['checkpoints'] if '/review/' in row['stage'])
    assert old_review['status'] == 'failed' and old_review['attempts'] == 3
    async def valid_review(*args, **kwargs):
        calls.append('current-review')
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_contract_only'}
    monkeypatch.setattr(scripts, 'review', valid_review)
    monkeypatch.setattr(scripts, 'SCRIPT_VALIDATOR_VERSION', 41)
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
    after = store.get(job)
    current_review = next(row for row in after['checkpoints'] if row['stage'] == old_review['stage'])
    assert calls == ['writer', 'invalid-review', 'invalid-review', 'invalid-review', 'current-review']
    assert after['provider_calls'] == before['provider_calls'] + 1
    assert current_review['attempts'] == 4
    assert result['qa']['review_contract_version'] == 41
