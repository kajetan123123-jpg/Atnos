"""Real v5 response replay. No inference and no production acceptance receipts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from atnos.production.steps import order_script as scripts

FIXTURES = Path(__file__).parent / 'fixtures/wb_review_v5_20261010'


def load(name):
    return json.loads((FIXTURES / name).read_text('utf8'))


@pytest.fixture(autouse=True)
def forbid_inference(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail('Offline regression attempted real inference')
    monkeypatch.setattr(scripts, 'ask_ollama_json', forbidden)


def test_v5_fixture_provenance():
    provenance = load('provenance.json')
    assert provenance['scope'] == 'OFFLINE_REGRESSION_NOT_ACCEPTANCE_RECEIPT'
    for filename, digest in provenance['script_sha256'].items():
        assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == digest
    assert len({row['source_call'] for row in provenance['responses']}) == 3
    for row in provenance['responses']:
        assert hashlib.sha256((FIXTURES / row['fixture']).read_bytes()).hexdigest() == row['fixture_sha256']


@pytest.mark.parametrize('attempt,source', [(1, 'initial-script.json'), (3, 'latest-script.json')])
def test_real_wrong_issue_ids_pass_v26_decode_but_fail_current_bound_schema(attempt, source):
    response = load(f'review-{attempt}.json')
    jsonschema.validate(response, load('v26-decoding-schema.json'))
    draft = scripts.EditorialScript.model_validate(load(source))
    schema = scripts.editorial_review_generation_schema(load('requirements.json'), draft)
    jsonschema.Draft202012Validator.check_schema(schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(response, schema)


@pytest.mark.parametrize('attempt,source', [(1, 'initial-script.json'), (3, 'latest-script.json')])
def test_real_bad_ids_and_quotes_still_fail_runtime(attempt, source):
    draft = scripts.EditorialScript.model_validate(load(source))
    with pytest.raises(ValueError) as error:
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(load(f'review-{attempt}.json')),
                                          draft, load('requirements.json'))
    assert 'unsupported segment' in str(error.value)
    assert 'quote is absent' in str(error.value)
    assert 'Every current review issue' in str(error.value)


def test_real_complete_failed_review_remains_failed_not_operator_accepted():
    response = load('review-2.json')
    draft = scripts.EditorialScript.model_validate(load('initial-script.json'))
    targets = load('requirements.json')
    jsonschema.validate(response, scripts.editorial_review_generation_schema(targets, draft))
    verdict = scripts.ScriptReview.model_validate(response)
    scripts.validate_editorial_review(verdict, draft, targets)
    assert verdict.passed is False
    assert len(verdict.issues) == 5
    assert 'independent_semantic_review' not in verdict.model_dump()


@pytest.mark.parametrize('location', ['issues', 'issue_evidence'])
def test_schema_rejects_wrong_scene_even_when_other_array_is_correct(location):
    response = load('review-2.json')
    if location == 'issues':
        response[location][0]['segment_ids'] = ['s01']
    else:
        response[location][0]['segment_id'] = 's01'
    draft = scripts.EditorialScript.model_validate(load('initial-script.json'))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(response, scripts.editorial_review_generation_schema(load('requirements.json'), draft))


def test_bound_schema_uses_actual_arbitrary_ids_including_untargeted_scenes():
    data = load('initial-script.json')
    mapping = {segment['id']: 'scene-' + segment['id'] for segment in data['segments']}
    for segment in data['segments']:
        segment['id'] = mapping[segment['id']]
    draft = scripts.EditorialScript.model_validate(data)
    targets = deepcopy(load('requirements.json'))
    for target in targets:
        target['segment_ids'] = [mapping[sid] for sid in target['segment_ids']]
    response = deepcopy(load('review-2.json'))
    for issue in response['issues']:
        issue['segment_ids'] = [mapping[sid] for sid in issue['segment_ids']]
    for key in ('editorial_checks', 'issue_evidence'):
        for row in response[key]:
            row['segment_id'] = mapping[row['segment_id']]
    schema = scripts.editorial_review_generation_schema(targets, draft)
    jsonschema.validate(response, schema)
    actual_ids = sorted(mapping.values())
    assert schema['$defs']['ScriptIssue']['properties']['segment_ids']['items']['enum'] == actual_ids
    assert schema['$defs']['CurrentIssueEvidence']['properties']['segment_id']['enum'] == actual_ids
    assert 'scene-0' in actual_ids  # A new actual defect may concern an untargeted scene.


def test_ordinary_review_schema_is_unchanged_even_with_current_script():
    draft = scripts.EditorialScript.model_validate(load('initial-script.json'))
    assert scripts.editorial_review_generation_schema([], draft) == scripts.generation_schema(scripts.ScriptReview)


def test_real_unquoted_issue_count_mismatch_is_not_silently_repaired():
    response = load('review-3.json')
    assert len(response['issues']) == 4 and len(response['issue_evidence']) == 5
    draft = scripts.EditorialScript.model_validate(load('latest-script.json'))
    with pytest.raises(ValueError, match=r'unexpected=\[4\]'):
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(response), draft, load('requirements.json'))
