"""Exact failed v6 responses and synthetic wire probes: offline, never receipts."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import jsonschema
import pytest

from atnos.production.steps import common, order_script as scripts
from test_production_order_script import execution
from test_production_order_store import store

FIXTURES = Path(__file__).parent/'fixtures/wb_review_v6_20261016'
AUTHOR = 'a6391b3c758e4865bed19500c40106e4'
REVIEWS = ['c2f5da30d71c4c1ab6a3dd61880a8217', '386c8efe45d8499ca81bd36262858162',
           'b155bef1789841108841b1bf05a9bede']


def load(name):
    return json.loads((FIXTURES/(name+'.json')).read_text('utf8'))


def script_data(name):
    return {key:value for key,value in load(name).items() if key in scripts.EditorialScript.model_fields}


@pytest.fixture(autouse=True)
def forbid_inference(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail('Offline regression attempted a real provider call')
    monkeypatch.setattr(scripts, 'ask_ollama_json', forbidden)


@pytest.fixture
def draft():
    return scripts.EditorialScript.model_validate(script_data('candidate'))


@pytest.fixture
def targets():
    return load('request')['editorial_issues']


def synthetic_wire(draft, targets):
    """Deliberately failed format example, not a semantic assessment."""
    by_id = {s.id: s for s in draft.segments}
    value = {'review_format': 'registered_named_v1', 'passed': False, 'issues': [], 'editorial_checks': {}}
    for index, target in enumerate(targets):
        for sid in dict.fromkeys(target['segment_ids']):
            field = 'visual_intent' if target['code'] == 'unsupported_visual_intent' else 'text'
            value['editorial_checks'][f'criterion_{index}_segment_{sid}'] = {
                'satisfied': False, 'field': field, 'quote': getattr(by_id[sid], field),
                'reason': 'Synthetic failed check for format validation only.'}
            value['issues'].append({'code': target['code'], 'segment_ids': [sid],
                'detail': 'Synthetic failed issue; not an acceptance receipt.',
                'evidence': {'segment_id': sid, 'field': field, 'quote': getattr(by_id[sid], field)}})
    return value


def test_exact_fixture_provenance():
    provenance = load('provenance')
    assert provenance['scope'] == 'EXACT_FAILED_V6_OFFLINE_REGRESSION_NOT_ACCEPTANCE_RECEIPT'
    for row in provenance['files']:
        assert hashlib.sha256((FIXTURES/row['fixture']).read_bytes()).hexdigest() == row['sha256']


@pytest.mark.parametrize('call_id', REVIEWS)
def test_real_bad_responses_were_allowed_by_old_decoder_but_remain_rejected(draft, targets, call_id):
    call = load(call_id)
    raw = call['response']
    jsonschema.validate(raw, call['decoding_schema'])  # Reproduces the real structural gap.
    with pytest.raises(ValueError):
        scripts.validate_editorial_review(scripts.ScriptReview.model_validate(raw), draft, targets)
    with pytest.raises(ValueError):
        scripts.decode_registered_review(raw, targets, draft)  # Never repairs a legacy response.


def test_named_wire_and_inline_evidence_map_losslessly_to_existing_public_review(draft, targets):
    raw = synthetic_wire(draft, targets)
    before = deepcopy(raw)
    schema = scripts.editorial_review_generation_schema(targets, draft, named_checks=True)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(raw, schema)
    verdict = scripts.decode_registered_review(raw, targets, draft)
    scripts.validate_editorial_review(verdict, draft, targets)
    assert verdict.passed is False and len(verdict.issues) == 5
    for index, item in enumerate(raw['issues']):
        assert verdict.issue_evidence[index].model_dump() == {'issue_index': index, **item['evidence']}
        assert verdict.issues[index].detail == item['detail']
    assert raw == before
    assert 'independent_semantic_review' not in verdict.model_dump()


@pytest.mark.parametrize('fault', ['missing_check', 'extra_check', 'list_checks', 'missing_evidence',
    'foreign_id', 'extra_evidence_index', 'absent_quote', 'false_pass', 'invented_field',
    'evidence_from_other_scene', 'string_boolean', 'false_pass_with_issues'])
def test_new_wire_fails_closed(draft, targets, fault):
    raw = synthetic_wire(draft, targets)
    checks = raw['editorial_checks']
    key = next(iter(checks))
    if fault == 'missing_check': checks.pop(key)
    elif fault == 'extra_check': checks['criterion_900_segment_missing'] = deepcopy(checks[key])
    elif fault == 'list_checks': raw['editorial_checks'] = list(checks.values())
    elif fault == 'missing_evidence': raw['issues'][0].pop('evidence')
    elif fault == 'foreign_id': raw['issues'][0]['evidence']['segment_id'] = 's01'
    elif fault == 'extra_evidence_index': raw['issues'][0]['evidence']['issue_index'] = 999
    elif fault == 'absent_quote': checks[key]['quote'] = 'Not present in the current script.'
    elif fault == 'false_pass': raw.update(passed=True, issues=[])
    elif fault == 'false_pass_with_issues': raw['passed'] = True
    elif fault == 'string_boolean': checks[key]['satisfied'] = 'false'
    elif fault == 'evidence_from_other_scene':
        raw['issues'][0]['evidence'].update(segment_id='0', quote=draft.segments[0].text)
    else: checks[key]['field'] = 'detail'
    with pytest.raises(ValueError):
        verdict = scripts.decode_registered_review(raw, targets, draft)
        scripts.validate_editorial_review(verdict, draft, targets)


def test_named_schema_cannot_duplicate_or_skip_one_criterion(draft, targets):
    raw = synthetic_wire(draft, targets)
    keys = list(raw['editorial_checks'])
    raw['editorial_checks'].pop(keys[-1])
    schema = scripts.editorial_review_generation_schema(targets, draft, named_checks=True)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, schema)
    # JSON with a repeated object key must be rejected before dict parsing loses it.
    repeated = '{"editorial_checks":{"' + keys[0] + '":{},"' + keys[0] + '":{}}}'
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        common.parse_json(repeated, reject_duplicate_keys=True)


@pytest.mark.parametrize('fenced', [False, True])
def test_duplicate_key_guard_reaches_transport_without_real_network(tmp_path, monkeypatch, fenced):
    real_client = httpx.AsyncClient
    content = '{"passed":true,"passed":false}'
    if fenced: content = '```json\n'+content+'\n```'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'done': True, 'done_reason': 'stop', 'message': {'content': content}}))
    monkeypatch.setattr(common.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport))
    settings = SimpleNamespace(ollama_timeout_s=10, ollama_base_url='http://offline.invalid')
    trace = tmp_path/'duplicate.raw.json'
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        asyncio.run(common.ask_ollama_json(settings, 'Offline probe', model='fixture',
                    raw_response_path=trace, reject_duplicate_keys=True))
    assert json.loads(trace.read_text('utf8'))['response']['message']['content'] == content


def test_real_author_ids_are_complete_but_unchanged_current_targets_fail(execution, targets):
    context = execution[2]
    original = scripts.EditorialScript.model_validate(script_data('canonical'))
    context.artifacts['script_calculations'] = {'claims': {c.id:c.model_dump(mode='json') for c in original.claims}}
    context.artifacts['studio_script_recovery'] = {'origin':'studio_user', 'mode':'hypothetical_review',
        'execution_scope':'script_only', 'issues':targets, 'submitted_editorial_issues':targets}
    raw = load(AUTHOR)['response']
    assert {r['id'] for r in raw['replacements']} == {'1', '3', '4', '5'}
    with pytest.raises(ValueError, match='unchanged current repair targets') as error:
        scripts.validate_repair_response(context, original, targets, raw, defer_editorial_validation=True)
    assert '3:text' in str(error.value) and '5:text/on_screen' in str(error.value)
    assert original == scripts.EditorialScript.model_validate(script_data('canonical'))


@pytest.mark.parametrize('origin,scope', [('autonomous_recovery','script_only'), ('studio_user','production')])
def test_noop_guard_preserves_other_existing_repair_contracts(execution, targets, origin, scope):
    context = execution[2]
    original = scripts.EditorialScript.model_validate(script_data('canonical'))
    context.artifacts['script_calculations'] = {'claims': {c.id:c.model_dump(mode='json') for c in original.claims}}
    context.artifacts['studio_script_recovery'] = {'origin':origin, 'execution_scope':scope}
    fixed = scripts.validate_repair_response(context, original, targets, load(AUTHOR)['response'],
                                               defer_editorial_validation=True)
    assert fixed.segments[3] == original.segments[3]  # Existing partial repair remains legal here.


def test_historical_targets_do_not_force_new_edits(execution, targets):
    context = execution[2]
    original = scripts.EditorialScript.model_validate(script_data('canonical'))
    context.artifacts['script_calculations'] = {'claims': {c.id:c.model_dump(mode='json') for c in original.claims}}
    context.artifacts['studio_script_recovery'] = {'origin':'studio_user', 'execution_scope':'script_only',
        'submitted_editorial_issues':targets}
    current = [targets[3]]  # Only the current visual defect; prior targets are context.
    replacement = deepcopy(original.segments[4].model_dump())
    replacement['visual_intent'] = 'An intact static balance scale compares two alternatives, without a host or text.'
    fixed = scripts.validate_repair_response(context, original, current, {'replacements':[replacement]},
                                               defer_editorial_validation=True)
    assert fixed.segments[3] == original.segments[3] and fixed.segments[5] == original.segments[5]
    assert fixed.claims == original.claims and fixed.segments[0] == original.segments[0]
    assert fixed.segments[2] == original.segments[2]


def test_ordinary_contract_and_duplicate_parser_default_unchanged(draft):
    assert scripts.editorial_review_generation_schema([], draft) == scripts.generation_schema(scripts.ScriptReview)
    assert scripts.ScriptReview.model_validate({'passed':True, 'issues':[]}).passed
    assert common.parse_json('{"key":1,"key":2}') == {'key':2}


def test_named_checks_support_arbitrary_current_ids_and_multiple_scenes(draft):
    for segment in draft.segments:
        segment.id = 'custom-' + segment.id
    targets = [{'code':'naturalness', 'segment_ids':['custom-1','custom-3']},
               {'code':'structure', 'segment_ids':['custom-3']}]
    raw = synthetic_wire(draft, targets)
    verdict = scripts.decode_registered_review(raw, targets, draft)
    assert [(check.criterion_index, check.segment_id) for check in verdict.editorial_checks] == [
        (0,'custom-1'),(0,'custom-3'),(1,'custom-3')]
    with pytest.raises(ValueError, match='absent from the current script'):
        scripts.editorial_review_generation_schema([{'segment_ids':['missing']}], draft, named_checks=True)


def test_structurally_positive_wire_is_not_an_independent_quality_receipt(draft, targets):
    raw = synthetic_wire(draft, targets)
    raw.update(passed=True, issues=[])
    for value in raw['editorial_checks'].values():
        value['satisfied'] = True
    verdict = scripts.decode_registered_review(raw, targets, draft)
    assert verdict.passed
    assert not {'human_review','independent_semantic_review','candidate_hash','request_digest'} & verdict.model_dump().keys()


def test_metadata_only_target_does_not_require_unrelated_scene_edits(execution):
    context = execution[2]
    context.artifacts['studio_script_recovery'] = {'origin':'studio_user','execution_scope':'script_only'}
    before = scripts.EditorialScript.model_validate(script_data('canonical'))
    after = before.model_copy(deep=True)
    after.title = 'A changed metadata-only title'
    scripts.validate_registered_repair_progress(context,before,after,
        [{'code':'structure','segment_ids':['1'],'fields':['title']}])


@pytest.mark.parametrize('code,changed_field,expected', [
    ('practical_share','visual_intent','3:text'), ('unsupported_visual_intent','text','3:visual_intent'),
    ('naturalness','type','3:text')])
@pytest.mark.parametrize('explicit_fields', [[], ['text','visual_intent']])
def test_unrelated_field_change_is_not_progress(execution, code, changed_field, expected, explicit_fields):
    context = execution[2]
    context.artifacts['studio_script_recovery'] = {'origin':'studio_user','execution_scope':'script_only'}
    before = scripts.EditorialScript.model_validate(script_data('canonical'))
    after = before.model_copy(deep=True)
    setattr(after.segments[3], changed_field, 'explanation' if changed_field == 'type' else 'Different unrelated content.')
    with pytest.raises(ValueError, match=expected):
        scripts.validate_registered_repair_progress(context,before,after,[{'code':code,'segment_ids':['3'],
                                                                          'fields':explicit_fields}])


def test_named_review_preserves_pinned_lane_and_strict_transport(execution, draft, targets, monkeypatch):
    task_store, job, context = execution
    lane = {'digest':'a'*64, 'model':'qwen3.5:9b', 'endpoint':'http://127.0.0.1:11436',
            'model_digest':'b'*64, 'execution_scope':'normal_produce_script_review'}
    context.artifacts['_active_script_review_model_lane'] = lane
    loads = []
    def load_lane(settings, job_id, *, verify_runtime):
        loads.append((job_id, verify_runtime))
        return lane
    monkeypatch.setattr('atnos.production.script_review_model_lane.load_script_review_model_lane', load_lane)
    seen = []
    async def transport(settings, prompt, **kwargs):
        seen.append((settings.ollama_base_url,kwargs))
        return synthetic_wire(draft,targets)
    monkeypatch.setattr(scripts,'ask_ollama_json',transport)
    raw = asyncio.run(scripts.ask_model(task_store.settings,context,'Offline named-wire probe',scripts.ScriptReview,
        reviewing=True, decoding_schema=scripts.editorial_review_generation_schema(targets,draft,named_checks=True),
        reject_duplicate_keys=True))
    assert raw['passed'] is False
    assert loads == [(job,True)]
    assert seen[0][0] == lane['endpoint']
    assert seen[0][1]['model'] == lane['model'] and seen[0][1]['reject_duplicate_keys'] is True
    assert seen[0][1]['options']['temperature'] == 0
    check_schema = seen[0][1]['schema']['properties']['editorial_checks']
    assert check_schema.get('type') == 'object' or '$ref' in check_schema
