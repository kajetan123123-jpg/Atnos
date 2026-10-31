"""Offline registered field-wire regression; fixture responses are never acceptance.

The v8 files are immutable observations from a rejected historical action. Any
projection below is labelled as a deterministic test transformation, not a new
model result, review receipt, or permission to continue the real order.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from atnos.production.steps import order_script as scripts
from atnos.production.steps.common import parse_json
from test_production_order_script import execution
from test_production_order_store import store


FIXTURES = Path(__file__).parent / 'fixtures' / 'wb_review_v8_20261016'
EXPECTED_MASK = {
    '1': ['text', 'on_screen'],
    '3': ['text', 'on_screen'],
    '4': ['text', 'on_screen', 'visual_intent'],
    '5': ['text', 'on_screen'],
}
CALLS = (
    '37c8ad5ca1bb4d308e8b4697e89e84e0',
    'f116d0e0b593453dbfbb801d8b625905',
    '27363b0216f44adfa80e596a4054e1db',
)


def fixture_json(name):
    return json.loads((FIXTURES / name).read_text('utf-8'))


@pytest.fixture
def registered_v8(execution):
    task_store, job, context = execution
    canonical = fixture_json('canonical.json')
    script = scripts.EditorialScript.model_validate({
        key: canonical[key] for key in ('title', 'description', 'segments', 'claims')
    })
    request = fixture_json('request.json')
    request.update(origin='studio_user', mode='hypothetical_review')
    request['issues'] = request.pop('editorial_issues')
    context.artifacts['studio_script_recovery'] = request
    # Use the real frozen intake and its digest-bound corrected brief; a bare
    # kind marker would bypass neither parser nor compiler and is not valid.
    context.artifacts['production_order'] = fixture_json('production-order.json')
    context.artifacts['script_calculations'] = {
        'claims': {claim.id: claim.model_dump(mode='json') for claim in script.claims}
    }
    context.artifacts['project_profile']['production']['word_budget'] = [96, 118]
    return task_store, job, context, script, deepcopy(request['issues'])


def changed_map():
    """Synthetic transport fixture, not an accepted script or model response."""
    return {'patches': {
        '1': {
            'text': 'Compare what spending gives you with the next best use of your money, then choose according to your needs.',
            'on_screen': 'COMPARE YOUR ALTERNATIVES',
        },
        '3': {
            'text': 'Count the enjoyment and needs this purchase serves. Those benefits can make spending a reasonable choice for you.',
            'on_screen': 'COUNT THE BENEFITS',
        },
        '4': {
            'text': 'Identify the benefit now and the best alternative use. Weigh the trade against your needs before you allocate the money.',
            'on_screen': 'WEIGH YOUR OPTIONS',
            'visual_intent': 'A static balance scale with two empty pans. Renderer reveals the complete still and holds it; all labels are separate overlays.',
        },
        '5': {
            'text': 'Follow Wealth Blueprint. Build wealth on purpose.',
            'on_screen': 'BUILD WEALTH ON PURPOSE',
        },
    }}


def get_mask(context, script, issues):
    return scripts.registered_editorial_field_mask(context, script, issues)


def expand(script, issues, raw, mask=None):
    return scripts.expand_editorial_patches(
        script, issues, raw, fields_by_segment=deepcopy(EXPECTED_MASK if mask is None else mask))


def test_historical_fixture_hashes_and_rejected_scope_are_preserved():
    provenance = fixture_json('provenance.json')
    assert provenance['scope'] == 'ACTUAL_REJECTED_V8_OFFLINE_REGRESSION_NOT_ACCEPTANCE'
    for name, binding in provenance['files'].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == binding['sha256']


def test_actual_v8_mask_uses_only_current_issue_targets(registered_v8):
    _, _, context, script, issues = registered_v8
    assert get_mask(context, script, issues) == EXPECTED_MASK
    # Global acceptance criteria mention every scene; that is not edit scope.
    assert set(get_mask(context, script, issues)) == {'1', '3', '4', '5'}
    assert get_mask(context, script, [issues[0]]) == {'1': ['text', 'on_screen']}
    assert get_mask(context, script, [issues[3]]) == {'4': ['visual_intent']}


@pytest.mark.parametrize('code', ['naturalness', 'cadence', 'practical_share', 'structure', 'repetition'])
def test_each_supported_narration_code_has_narrow_fields(registered_v8, code):
    _, _, context, script, _ = registered_v8
    issues = [{'code': code, 'segment_ids': ['5'], 'detail': 'An actual localized wording problem.'}]
    assert get_mask(context, script, issues) == {'5': ['text', 'on_screen']}


@pytest.mark.parametrize('key,value', [
    ('origin', 'autonomy'), ('mode', 'initial_generation'), ('execution_scope', 'production'),
])
def test_unregistered_or_different_scope_keeps_legacy_selection(registered_v8, key, value):
    _, _, context, script, issues = registered_v8
    context.artifacts['studio_script_recovery'][key] = value
    assert get_mask(context, script, issues) is None


def test_no_registered_request_keeps_legacy_selection(registered_v8):
    _, _, context, script, issues = registered_v8
    context.artifacts.pop('studio_script_recovery')
    assert get_mask(context, script, issues) is None


@pytest.mark.parametrize('unsupported', ['unverified_number', 'arithmetic', 'word_budget', 'not_a_known_issue'])
def test_unsupported_issue_does_not_silently_drop_to_partial_mask(registered_v8, unsupported):
    _, _, context, script, issues = registered_v8
    issues.append({'code': unsupported, 'segment_ids': ['1'], 'detail': 'Needs its original contract.'})
    assert get_mask(context, script, issues) is None


def test_explicit_metadata_keeps_full_repair_contract(registered_v8):
    _, _, context, script, issues = registered_v8
    assert scripts.registered_editorial_field_mask(
        context, script, issues, metadata_fields={'description'}) is None


@pytest.mark.parametrize('fields', [['text'], ['on_screen'], ['text', 'on_screen']])
def test_explicit_current_fields_cannot_be_broadened_by_code_or_global_criteria(registered_v8, fields):
    _, _, context, script, issues = registered_v8
    current = {**issues[0], 'fields': fields}
    assert get_mask(context, script, [current]) == {'1': fields}


def test_explicit_metadata_issue_uses_existing_full_fallback(registered_v8):
    _, _, context, script, _ = registered_v8
    current = [{'code': 'structure', 'segment_ids': ['5'], 'fields': ['description'],
                'detail': 'A metadata correction has its own explicit contract.'}]
    assert get_mask(context, script, current) is None


def test_map_schema_requires_each_id_and_exact_authorized_fields(registered_v8):
    _, _, context, script, issues = registered_v8
    mask = get_mask(context, script, issues)
    schema = scripts.editorial_field_map_generation_schema(mask)
    assert schema['type'] == 'object'
    assert schema['additionalProperties'] is False
    assert 'patches' in schema['required']
    patches = schema['properties']['patches']
    assert patches['type'] == 'object' and patches['additionalProperties'] is False
    assert set(patches['required']) == set(mask)
    assert set(patches['properties']) == set(mask)
    for sid, fields in mask.items():
        entry = patches['properties'][sid]
        assert entry['type'] == 'object' and entry['additionalProperties'] is False
        assert set(entry['required']) == set(fields)
        assert set(entry['properties']) == set(fields)
        assert all(entry['properties'][field]['type'] == 'string' for field in fields)


def test_expansion_preserves_frozen_fields_and_does_not_mutate_source(registered_v8):
    _, _, _, script, issues = registered_v8
    before = script.model_dump(mode='json')
    output = expand(script, issues, changed_map())
    replacements = {row['id']: row for row in output['replacements']}
    assert set(replacements) == set(EXPECTED_MASK)
    for original in before['segments']:
        if original['id'] in replacements:
            new = replacements[original['id']]
            for field in set(original) - set(EXPECTED_MASK[original['id']]):
                assert new[field] == original[field]
            assert all(new[field] == changed_map()['patches'][original['id']][field]
                       for field in EXPECTED_MASK[original['id']])
    fixed = scripts.apply_segment_repair(script, output['replacements'], set(EXPECTED_MASK))
    assert fixed.segments[0] == script.segments[0]
    assert fixed.segments[2] == script.segments[2]
    assert fixed.claims == script.claims
    assert fixed.title == script.title and fixed.description == script.description
    assert scripts.financial_fingerprint(fixed) == scripts.financial_fingerprint(script)
    assert script.model_dump(mode='json') == before


@pytest.mark.parametrize('fault', [
    'missing_id', 'unknown_id', 'locked_id', 'empty_patches', 'list_instead_of_map',
    'missing_field', 'empty_text', 'null_text', 'nonstring_text', 'extra_field',
    'type_field', 'claim_field', 'fact_field', 'unflagged_visual', 'root_claims',
])
def test_map_runtime_rejects_incomplete_out_of_scope_and_malformed_values(registered_v8, fault):
    _, _, _, script, issues = registered_v8
    before = script.model_dump(mode='json')
    raw = changed_map()
    if fault == 'missing_id':
        raw['patches'].pop('1')
    elif fault in {'unknown_id', 'locked_id'}:
        raw['patches']['missing' if fault == 'unknown_id' else '2'] = raw['patches'].pop('1')
    elif fault == 'empty_patches':
        raw['patches'] = {}
    elif fault == 'list_instead_of_map':
        raw['patches'] = [{'id': sid, **row} for sid, row in raw['patches'].items()]
    elif fault == 'missing_field':
        raw['patches']['4'].pop('visual_intent')
    elif fault in {'empty_text', 'null_text', 'nonstring_text'}:
        raw['patches']['1']['text'] = {'empty_text': '', 'null_text': None, 'nonstring_text': 123}[fault]
    elif fault in {'extra_field', 'type_field', 'claim_field', 'fact_field', 'unflagged_visual'}:
        field, value = {
            'extra_field': ('pronunciation_terms', []), 'type_field': ('type', 'explanation'),
            'claim_field': ('claim_ids', ['c1']), 'fact_field': ('fact_ids', ['invented']),
            'unflagged_visual': ('visual_intent', 'An unauthorized new scene direction.'),
        }[fault]
        raw['patches']['1'][field] = value
    else:
        raw['claims'] = []
    with pytest.raises(ValueError):
        expand(script, issues, raw)
    assert script.model_dump(mode='json') == before


def test_pure_visual_wire_does_not_grant_text_or_headline_editing(registered_v8):
    _, _, context, script, issues = registered_v8
    visual_issues = [issues[3]]
    mask = get_mask(context, script, visual_issues)
    raw = {'patches': {'4': {'visual_intent': changed_map()['patches']['4']['visual_intent']}}}
    output = expand(script, visual_issues, raw, mask=mask)
    row = output['replacements'][0]
    assert row['text'] == script.segments[4].text
    assert row['on_screen'] == script.segments[4].on_screen
    raw['patches']['4']['text'] = 'A sentence outside this visual-only scope.'
    with pytest.raises(ValueError):
        expand(script, visual_issues, raw, mask=mask)


@pytest.mark.parametrize('fault', ['locked_scene', 'extra_field', 'empty_mask', 'duplicate_field'])
def test_caller_cannot_forge_a_broader_or_invalid_mask(registered_v8, fault):
    _, _, _, script, issues = registered_v8
    mask = deepcopy(EXPECTED_MASK)
    raw = changed_map()
    if fault == 'locked_scene':
        mask['2'] = ['text', 'on_screen']
        raw['patches']['2'] = {'text': 'An unauthorized locked-scene rewrite.', 'on_screen': 'CHANGED'}
    elif fault == 'extra_field':
        mask['1'].append('visual_intent')
        raw['patches']['1']['visual_intent'] = 'An unrelated new illustration.'
    elif fault == 'empty_mask':
        mask = {}
    else:
        mask['1'].append('text')
    with pytest.raises(ValueError):
        expand(script, issues, raw, mask=mask)


def test_strict_json_parser_rejects_duplicate_ids_and_nested_fields():
    duplicates = [
        '{"patches":{"1":{"text":"An original sentence.","on_screen":"HEAD"},'
        '"1":{"text":"A second sentence.","on_screen":"HEAD"}}}',
        '{"patches":{"1":{"text":"Original.","text":"Replacement.","on_screen":"HEAD"}}}',
    ]
    for raw in duplicates:
        with pytest.raises(ValueError):
            parse_json(raw, reject_duplicate_keys=True)


@pytest.mark.parametrize('call', CALLS)
def test_real_rejected_v8_list_wire_is_not_mistaken_for_field_map(registered_v8, call):
    _, _, _, script, issues = registered_v8
    observation = fixture_json(call + '.json')
    assert observation['contract'] == 'AdaptedScriptRepair'
    assert observation['status'] == 'response_received'
    with pytest.raises(ValueError):
        expand(script, issues, observation['response'])


@pytest.mark.parametrize('call', CALLS[1:])
def test_offline_projection_preserves_actual_missing_id_and_is_rejected(registered_v8, call):
    _, _, _, script, issues = registered_v8
    observation = fixture_json(call + '.json')
    # Mechanical OFFLINE projection solely tests exact-ID validation. It is not
    # evidence that this historical model used or passed the new wire.
    rows = observation['response']['replacements']
    projection = {'patches': {
        row['id']: {field: row[field] for field in EXPECTED_MASK[row['id']]}
        for row in rows
    }}
    assert set(projection['patches']) == {'3', '4', '5'}
    with pytest.raises(ValueError):
        expand(script, issues, projection)


def test_actual_first_response_still_fails_current_target_progress_after_offline_projection(registered_v8):
    _, _, context, script, issues = registered_v8
    observation = fixture_json(CALLS[0] + '.json')
    # Complete keys do not imply editorial correction. Retain the real old text.
    projection = {'patches': {
        row['id']: {field: row[field] for field in EXPECTED_MASK[row['id']]}
        for row in observation['response']['replacements']
    }}
    output = expand(script, issues, projection)
    with pytest.raises(ValueError, match='unchanged current repair targets'):
        scripts.validate_repair_response(context, script, issues, output, defer_editorial_validation=True)


def capture_repair(monkeypatch, task_store, context, script, issues):
    captured = {}
    class StopBeforeInference(Exception):
        pass
    async def stop(_settings, _context, prompt, schema, **kwargs):
        captured.update(prompt=prompt, schema=schema, **kwargs)
        raise StopBeforeInference()
    monkeypatch.setattr(scripts, 'ask_model', stop)
    with pytest.raises(StopBeforeInference):
        asyncio.run(scripts.repair(task_store.settings, context, script, issues))
    return captured


def test_normal_repair_selects_exact_map_and_retains_current_semantic_context(registered_v8, monkeypatch):
    task_store, job, context, script, issues = registered_v8
    before = task_store.get(job)
    captured = capture_repair(monkeypatch, task_store, context, script, issues)
    assert captured['schema'] is scripts.EditorialFieldRepair
    assert captured['reject_duplicate_keys'] is True
    assert captured['decoding_schema'] == scripts.editorial_field_map_generation_schema(EXPECTED_MASK)
    for issue in issues:
        assert issue['detail'] in captured['prompt']
    assert script.segments[0].text in captured['prompt']
    assert script.segments[2].text in captured['prompt']
    assert '96' in captured['prompt'] and '118' in captured['prompt']
    assert task_store.get(job) == before


def test_normal_pure_visual_repair_uses_visual_only_mask(registered_v8, monkeypatch):
    task_store, _, context, script, issues = registered_v8
    captured = capture_repair(monkeypatch, task_store, context, script, [issues[3]])
    assert captured['schema'] is scripts.EditorialFieldRepair
    assert captured['reject_duplicate_keys'] is True
    patches = captured['decoding_schema']['properties']['patches']
    assert set(patches['properties']) == {'4'}
    assert set(patches['properties']['4']['properties']) == {'visual_intent'}


def test_normal_repair_merges_one_offline_field_response_without_mutating_order(registered_v8, monkeypatch):
    task_store, job, context, script, issues = registered_v8
    before_order = task_store.get(job)
    before_script = script.model_dump(mode='json')
    calls = []
    async def synthetic_transport(_settings, _context, prompt, schema, **kwargs):
        calls.append((schema, kwargs))
        return deepcopy(changed_map())
    monkeypatch.setattr(scripts, 'ask_model', synthetic_transport)
    fixed = asyncio.run(scripts.repair(task_store.settings, context, script, issues))
    assert len(calls) == 1
    assert calls[0][0] is scripts.EditorialFieldRepair
    assert calls[0][1]['reject_duplicate_keys'] is True
    assert fixed.segments[0] == script.segments[0]
    assert fixed.segments[2] == script.segments[2]
    for old, new in zip(script.segments, fixed.segments, strict=True):
        allowed = set(EXPECTED_MASK.get(old.id, ()))
        assert new.model_dump(exclude=allowed) == old.model_dump(exclude=allowed)
    assert fixed.title == script.title and fixed.description == script.description
    assert fixed.claims == script.claims
    assert scripts.financial_fingerprint(fixed) == scripts.financial_fingerprint(script)
    assert script.model_dump(mode='json') == before_script
    assert task_store.get(job) == before_order
    # Transport success is deliberately not represented as an accepted review.
    assert 'independent_assessment' not in context.artifacts


def test_legacy_list_expander_is_unchanged(registered_v8):
    _, _, _, script, issues = registered_v8
    raw = {'patches': [{'id': '1', **changed_map()['patches']['1']}]}
    output = scripts.expand_editorial_patches(script, [issues[0]], raw)
    assert output['replacements'][0]['text'] == raw['patches'][0]['text']
    assert output['replacements'][0]['visual_intent'] == script.segments[1].visual_intent
    raw['patches'][0]['visual_intent'] = 'Not permitted by the legacy contract.'
    with pytest.raises(ValueError):
        scripts.expand_editorial_patches(script, [issues[0]], raw)


def test_unregistered_full_scope_keeps_existing_full_wire(registered_v8, monkeypatch):
    task_store, _, context, script, issues = registered_v8
    context.artifacts.pop('studio_script_recovery')
    captured = capture_repair(monkeypatch, task_store, context, script, issues)
    assert captured['schema'] is scripts.AdaptedScriptRepair
    assert 'replacements' in captured['decoding_schema']['properties']
    assert 'patches' not in captured['decoding_schema']['properties']


def test_unsupported_registered_scope_keeps_existing_full_wire(registered_v8, monkeypatch):
    task_store, _, context, script, issues = registered_v8
    issues.append({'code': 'unverified_number', 'segment_ids': ['1'],
                   'detail': 'A financial safety issue must retain its full repair contract.'})
    captured = capture_repair(monkeypatch, task_store, context, script, issues)
    assert captured['schema'] is scripts.AdaptedScriptRepair
    assert 'replacements' in captured['decoding_schema']['properties']
