"""Replay rejected LIVE data offline. Fixtures are never quality receipts."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from atnos.production.steps import order_script as scripts

FIXTURES = Path(__file__).parent / 'fixtures/wb_review_20261010'


def load(name):
    return json.loads((FIXTURES / name).read_text('utf8'))


@pytest.fixture
def draft():
    return scripts.EditorialScript.model_validate(load('script.json'))


@pytest.fixture
def targets():
    return load('requirements.json')


@pytest.fixture(autouse=True)
def forbid_model_transport(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail('Regression replay attempted real inference')
    monkeypatch.setattr(scripts, 'ask_ollama_json', forbidden)


def grounded_failed_review(draft):
    """Synthetic format example only, not an assessment of script quality."""
    value = load('review-1.json')
    value['editorial_checks'][3].update(segment_id='4', field='visual_intent', satisfied=False)
    segments = {s.id: s for s in draft.segments}
    for row in value['editorial_checks']:
        row['quote'] = getattr(segments[row['segment_id']], row['field'])
    value['issue_evidence'] = [
        {'issue_index': i, 'segment_id': issue['segment_ids'][0],
         'field': (issue.get('fields') or ['text'])[0],
         'quote': getattr(segments[issue['segment_ids'][0]], (issue.get('fields') or ['text'])[0])}
        for i, issue in enumerate(value['issues'])]
    return value


def test_live_fixture_provenance_and_distinct_requests():
    provenance = load('provenance.json')
    assert hashlib.sha256((FIXTURES / 'script.json').read_bytes()).hexdigest() == provenance['script_sha256']
    assert len({row['source_call'] for row in provenance['responses']}) == 3
    assert len({row['created_at'] for row in provenance['responses']}) == 3
    assert len({row['raw_sha256'] for row in provenance['responses']}) == 3
    for row in provenance['responses']:
        assert hashlib.sha256((FIXTURES / row['fixture']).read_bytes()).hexdigest() == row['fixture_sha256']


@pytest.mark.parametrize('attempt', [1, 2, 3])
def test_recorded_response_passed_legacy_schema_but_new_schema_rejects_wrong_pair(targets, attempt):
    response = load(f'review-{attempt}.json')
    jsonschema.validate(response, load('legacy-decoding-schema.json'))
    schema = scripts.editorial_review_generation_schema(targets)
    jsonschema.Draft202012Validator.check_schema(schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(response, schema)


@pytest.mark.parametrize('attempt', [1, 2, 3])
def test_recorded_response_reports_exact_missing_and_unexpected_pair(draft, targets, attempt):
    verdict = scripts.ScriptReview.model_validate(load(f'review-{attempt}.json'))
    with pytest.raises(ValueError) as error:
        scripts.validate_editorial_review(verdict, draft, targets)
    assert 'missing=[(3, "4")]' in str(error.value)
    assert 'unexpected=[(3, "5")]' in str(error.value)
    assert 'criterion=1' in str(error.value) and 'criterion=2' in str(error.value)
    assert 'missing=[2, 3, 4]' in str(error.value)
    assert len(str(error.value)) < 1500  # Existing bounded retry feedback retains all real faults.


@pytest.mark.parametrize('attempt', [1, 2, 3])
@pytest.mark.parametrize('check_index', [1, 2])
def test_real_ellipsized_quotes_remain_rejected_after_pair_fix(draft, targets, attempt, check_index):
    original = load(f'review-{attempt}.json')
    response = grounded_failed_review(draft)
    response['editorial_checks'][check_index]['quote'] = original['editorial_checks'][check_index]['quote']
    with pytest.raises(ValueError, match=f'criterion={check_index}') as error:
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(response), draft, targets)
    assert 'quote is absent' in str(error.value)
    assert 'field=' in str(error.value)


@pytest.mark.parametrize('attempt', [1, 2, 3])
def test_actual_missing_issue_evidence_stays_rejected(draft, targets, attempt):
    response = grounded_failed_review(draft)
    response['issue_evidence'] = load(f'review-{attempt}.json')['issue_evidence']
    with pytest.raises(ValueError, match=r'missing=\[2, 3, 4\]'):
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(response), draft, targets)


def test_grounded_failed_format_does_not_turn_into_pass(draft, targets):
    response = grounded_failed_review(draft)
    jsonschema.validate(response, scripts.editorial_review_generation_schema(targets))
    verdict = scripts.ScriptReview.model_validate(response)
    scripts.validate_editorial_review(verdict, draft, targets)
    assert verdict.passed is False and len(verdict.issues) == 5


@pytest.mark.parametrize('fault', ['missing', 'unexpected', 'duplicate'])
def test_pair_coverage_still_fails_closed(draft, targets, fault):
    response = grounded_failed_review(draft)
    rows = response['editorial_checks']
    if fault == 'missing':
        rows.pop(3)
    elif fault == 'unexpected':
        rows[3]['segment_id'] = '5'
    else:
        rows[3] = deepcopy(rows[4])
        rows[3]['reason'] = 'Another reason does not create a new criterion.'
    with pytest.raises(ValueError, match='exactly once'):
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(response), draft, targets)


def test_false_pass_cannot_hide_unmet_criterion(draft, targets):
    response = grounded_failed_review(draft)
    response.update(passed=True, issues=[], issue_evidence=[])
    with pytest.raises(ValueError, match='Unmet editorial criterion'):
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(response), draft, targets)


@pytest.mark.parametrize('fault', ['duplicate', 'missing', 'unexpected', 'absent_quote'])
def test_issue_evidence_diagnostics(draft, targets, fault):
    response = grounded_failed_review(draft)
    if fault == 'duplicate':
        response['issue_evidence'][4] = deepcopy(response['issue_evidence'][3])
    elif fault == 'missing':
        response['issue_evidence'].pop()
    elif fault == 'unexpected':
        response['issue_evidence'][4]['issue_index'] = 5
    else:
        response['issue_evidence'][4]['quote'] = 'A statement absent from this current draft.'
    with pytest.raises(ValueError, match='issue'):
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(response), draft, targets)


def test_ordinary_review_contract_is_unchanged():
    assert scripts.editorial_review_generation_schema([]) == scripts.generation_schema(scripts.ScriptReview)
    assert scripts.ScriptReview.model_validate({'passed': True, 'issues': []}).editorial_checks == []


def test_complete_positive_format_is_valid_without_becoming_operator_acceptance(draft, targets):
    response = grounded_failed_review(draft)
    response.update(passed=True, issues=[], issue_evidence=[])
    for row in response['editorial_checks']:
        row['satisfied'] = True
    verdict = scripts.ScriptReview.model_validate(response)
    scripts.validate_editorial_review(verdict, draft, targets)
    assert 'independent_semantic_review' not in verdict.model_dump()


def test_schema_binds_pairs_for_multiple_segments_and_arbitrary_ids(draft):
    targets = [{'segment_ids': ['1', '3']}, {'segment_ids': ['3', '4']}, {'segment_ids': ['5']}]
    response = grounded_failed_review(draft)
    for row, pair in zip(response['editorial_checks'], [(0, '1'), (0, '3'), (1, '3'), (1, '4'), (2, '5')]):
        row.update(criterion_index=pair[0], segment_id=pair[1])
    schema = scripts.editorial_review_generation_schema(targets)
    jsonschema.validate(response, schema)
    response['editorial_checks'][3]['segment_id'] = '1'
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(response, schema)
    arbitrary = scripts.editorial_review_generation_schema([{'segment_ids': ['s01', 'scene-2']}])
    assert 'scene-2' in json.dumps(arbitrary)


def test_normal_reviewer_sends_bound_schema_and_explicit_targets_without_real_model(draft, targets, monkeypatch):
    seen = []
    async def recorded_provider(settings, context, prompt, schema, **kwargs):
        assert schema is scripts.ScriptReview and kwargs['reviewing'] is True
        assert kwargs['reject_duplicate_keys'] is True
        seen.append((prompt, kwargs['decoding_schema']))
        # Explicit NEW wire fixture, never an automatic migration of LIVE data.
        public = grounded_failed_review(draft)
        for check in public['editorial_checks']:
            check['reason'] = 'Controlled short explanation; full current evidence and unmet criteria remain in the fixture.'
        evidence = {row['issue_index']:row for row in public['issue_evidence']}
        return {'review_format':'registered_named_v1', 'passed':public['passed'],
            'editorial_checks':{f'criterion_{row["criterion_index"]}_segment_{row["segment_id"]}':
                {key:value for key,value in row.items() if key not in {'criterion_index','segment_id'}}
                for row in public['editorial_checks']},
            'issues':[{**item, 'evidence':{key:value for key,value in evidence[index].items() if key != 'issue_index'}}
                      for index,item in enumerate(public['issues'])]}
    monkeypatch.setattr(scripts, 'ask_model', recorded_provider)
    monkeypatch.setattr(scripts, 'script_issues', lambda *_a, **_k: [])
    monkeypatch.setattr(scripts, 'revision_input', lambda *_a, **_k: {})
    monkeypatch.setattr(scripts, 'reviewed_source_facts', lambda *_a: {})
    monkeypatch.setattr(scripts, '_active_studio_script_bundle', lambda *_a, **_k: {'current_script': draft.model_dump()})
    from atnos.production import project_content_brain as brain, script_adaptation
    monkeypatch.setattr(brain, 'classify_script_claims', lambda *_a, **_k: {})
    monkeypatch.setattr(brain, 'attach_classifications_to_qa', lambda qa, _: qa)
    monkeypatch.setattr(script_adaptation, 'compiled_claims', lambda *_a: [])
    context = SimpleNamespace(artifacts={
        'studio_script_recovery': {'submitted_editorial_issues': targets},
        'project_profile': {'production': {'language': 'en', 'word_budget': [96, 118],
            'minimum_practical_share': .2, 'narrative_rules': {}}}})
    before = deepcopy(context.artifacts)
    result = asyncio.run(scripts.review(None, context, draft))
    assert len(seen) == 1
    assert seen[0][1] == scripts.editorial_review_generation_schema(targets, draft, named_checks=True)
    assert '"segment_ids":["s01"]' not in seen[0][0]
    assert 'CURRENT SEGMENT IDS (copy verbatim; never prefix or renumber)' in seen[0][0]
    assert 'EXACT CRITERION/SEGMENT PAIRS' in seen[0][0]
    assert 'Do not append ellipses' in seen[0][0]
    counts = json.loads(seen[0][0].split('CURRENT DETERMINISTIC NARRATION WORD COUNTS: ')[1])
    assert counts['total'] == 117 and counts['segments']['3'] == 33
    assert result['status'] == 'failed' and result['human_review'] == 'not_performed'
    assert context.artifacts == before
