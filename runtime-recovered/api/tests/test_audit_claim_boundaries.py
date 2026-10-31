"""Regression tests for actual claim-gate failures seen in the system audit."""
import httpx
import pytest
from atnos.integrations.claim_evidence_gate import evaluate_gate
from atnos.integrations.web_page_extract import fact_check_claim_against_url


@pytest.mark.parametrize("status", ["CONTRADICTED", "NOT_FOUND", "UNVERIFIED", "SOURCE_CHANGED"])
def test_required_medium_risk_claim_cannot_pass_as_warning(status):
    gate = evaluate_gate([dict(claim_id="audit", exact_text="An emergency fund is not money set aside for unexpected expenses.", source_required=True, risk="MEDIUM", fact_check_status=status)])
    assert gate["production_allowed"] is False


def test_negated_definition_is_not_supported_by_positive_source(tmp_path):
    html=b"<html><main><p>An emergency fund is money set aside for unexpected expenses.</p></main></html>"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,headers={"content-type":"text/html"},content=html))) as client:
        result = fact_check_claim_against_url("An emergency fund is not money set aside for unexpected expenses.", "https://www.consumerfinance.gov/audit-fixture", root=tmp_path, client=client)
    assert result["verdict"] not in {"SUPPORTED", "PARTIALLY_SUPPORTED"}


def test_unsupported_number_is_not_supported_by_token_overlap(tmp_path):
    html=b"<html><main><p>The annual rate was 2 percent in the example.</p></main></html>"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,headers={"content-type":"text/html"},content=html))) as client:
        result = fact_check_claim_against_url("The annual rate was 99 percent in the example.", "https://example.com/audit-fixture", root=tmp_path, client=client)
    assert result["verdict"] not in {"SUPPORTED", "PARTIALLY_SUPPORTED"}
