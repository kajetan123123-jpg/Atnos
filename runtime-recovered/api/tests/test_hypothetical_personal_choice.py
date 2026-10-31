"""Pure claim-binding regression; no research, DB, models or production files."""
import pytest

from atnos.integrations.claim_evidence_gate import extract_claims_from_script
from atnos.production.hypothetical_claim_evidence import (
    bind_ledger_claims, script_binding_issues, verified_binding,
)
from atnos.production.hypothetical_script_intake import compile_supplied_plan
from test_hypothetical_claim_evidence import supplied_script
from test_hypothetical_script_intake import contract
from test_production_order_script import script


ACTUAL_REPAIR_SENTENCE = (
    'Compare what spending gives you with the next best use of your money, '
    'then choose according to your needs.'
)


def _bind(supplied_script, text):
    supplied_script['segments'][1].update(text=text, claim_ids=[])
    intake = contract().model_dump(mode='json')
    receipt = compile_supplied_plan(intake)
    binding = verified_binding(supplied_script, intake, receipt)
    claims = bind_ledger_claims(extract_claims_from_script(supplied_script), supplied_script, binding)
    selected = [row for row in claims if row['script_location']['segment_id'] == supplied_script['segments'][1]['id']]
    return selected, script_binding_issues(supplied_script, intake, receipt)


@pytest.mark.parametrize('text', [
    ACTUAL_REPAIR_SENTENCE,
    'Choose according to your needs.',
    'Decide according to your current needs.',
])
def test_personal_choice_instruction_is_not_an_external_source_or_verified_fact(supplied_script, text):
    claims, issues = _bind(supplied_script, text)
    assert issues == []
    assert len(claims) == 1
    claim = claims[0]
    assert claim['exact_text'] == text
    assert claim['claim_type'] != 'SOURCE_ATTRIBUTED'
    assert claim['source_required'] is False
    assert claim['fact_check_status'] == 'NOT_REQUIRED'  # not SUPPORTED/verified
    assert claim['evidence'] == []
    assert 'calculator_evidence' not in claim


@pytest.mark.parametrize('text', [
    'According to the Fed, investing produces gains; choose according to your needs.',
    'According to the Federal Reserve, the rate is eight percent; choose according to your needs.',
    'According to the report, investing produces gains; choose according to your needs.',
    'The report predicts gains, so choose according to your needs.',
    'The Fed expects gains, so choose according to your needs.',
    'On average, money doubles; choose according to your needs.',
    'The stock market historically returns eight percent; choose according to your needs.',
    'Your money will grow; choose according to your needs.',
    'This investment guarantees profit; choose according to your needs.',
    'Compare the cost of fifty dollars, then choose according to your needs.',
    'Choose according to your needs and according to the report.',
    'Choose according to your needs and the report that guarantees profit.',
    'According to your needs, the market guarantees profit.',
])
def test_personal_phrase_never_exempts_an_additional_assertion_or_unbound_number(supplied_script, text):
    claims, issues = _bind(supplied_script, text)
    assert issues and {row['code'] for row in issues} == {'hypothetical_evidence'}
    assert any(row['source_required'] and row.get('calculator_binding_rejected') for row in claims)
    assert all(row['fact_check_status'] != 'SUPPORTED' for row in claims)
