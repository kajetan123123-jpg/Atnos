"""Bind a supplied hypothetical to its actual calculator receipt, never to a regex exemption."""
from __future__ import annotations

import math
import re

from atnos.production.hypothetical_script_intake import HypotheticalScriptIntake, compile_supplied_plan
from atnos.production.project_profile import content_hash
from atnos.production.spoken_numbers import numeric_mentions
from atnos.production.steps.order_script import EditorialScript, spoken_claim_value


_EXTERNAL_ASSERTION = re.compile(
    r"\b(average|averaged|historically|historical|past returns?|market (?:returns?|performance)|stock market|"
    r"s&p|nasdaq|wig20|dow jones|studies|research shows|survey|according to|federal reserve|"
    r"currently|current (?:rate|return)|last year|this year|guaranteed (?:return|profit)|"
    r"risk.free investment)\b", re.I,
)
_ILLUSTRATION = re.compile(r"\b(assumed|assuming|hypothetical|illustrative|if|could|might)\b", re.I)
_GUARANTEE = re.compile(r"\b(will (?:grow|become|earn|return)|guarantees?|ensures?|always grows?)\b", re.I)


def numeric_binding_error(text: str, attached: list[dict]) -> str | None:
    """Check actual spoken values even when a sentence also has a market assertion."""
    financial = [item for item in numeric_mentions(text)
                 if item["unit"] in {"dollars", "PLN", "percent", "years", "weeks"}]
    if not financial:
        return None
    if not attached:
        return "Financial number has no attached calculator claim"
    allowed, principals = [], []
    from atnos.production.steps.order_script import NumericClaim
    for raw in attached:
        parsed = NumericClaim.model_validate(raw)
        allowed.extend([parsed.result, spoken_claim_value(parsed), *parsed.operands])
        if parsed.operation in {"compound", "monthly_contribution"}:
            allowed.append(parsed.operands[1] * 100)
            principals.append(parsed.operands[0])
    wrong_value = any(not any(math.isclose(item["value"], value, rel_tol=0, abs_tol=.005)
                             for value in allowed) for item in financial)
    wrong_currency = any(item["unit"] in {"dollars", "PLN"}
                         and not any(raw["unit"] == item["unit"] for raw in attached) for item in financial)
    outcome = any(item["unit"] == "percent" or not any(math.isclose(item["value"], value, rel_tol=0, abs_tol=.005)
                  for value in principals) for item in financial)
    if wrong_value or wrong_currency or (outcome and not _ILLUSTRATION.search(text)):
        return "Wrong number/currency or an outcome not described as hypothetical"
    return None


def script_binding_issues(script: dict, intake: dict, receipt: dict) -> list[dict]:
    """Apply the media evidence rules before the writer/reviewer accepts narration.

    Only the precise average/assumed wording defect is eligible for selective
    repair. A matching calculator receipt does not excuse any changed number,
    external market claim or guaranteed return.
    """
    from atnos.integrations.claim_evidence_gate import extract_claims_from_script, evaluate_gate
    binding = verified_binding(script, intake, receipt)
    bound = bind_ledger_claims(extract_claims_from_script(script), script, binding)
    rejected = {row["claim_id"] for row in evaluate_gate(bound)["blocked_claims"]}
    segments = {row["id"]: row for row in script["segments"]}
    issues = []
    for claim in bound:
        if claim["claim_id"] not in rejected:
            continue
        segment_id = claim["script_location"]["segment_id"]
        segment = segments[segment_id]
        attached = [binding["claims"][key] for key in segment.get("claim_ids", [])]
        assertions = [match.group(0).casefold() for match in _EXTERNAL_ASSERTION.finditer(claim["exact_text"])]
        wording_only = (assertions == ["average"] and not _GUARANTEE.search(claim["exact_text"])
                        and numeric_binding_error(claim["exact_text"], attached) is None)
        issues.append({"code": "hypothetical_assumption_wording" if wording_only else "hypothetical_evidence",
                       "segment_ids": [segment_id], "fields": ["text"],
                       "detail": ("The compiled rate is an assumed constant illustrative rate, not an evidenced average. "
                                  "Rewrite the conditional sentence using the frozen assumptions; preserve all numbers and units."
                                  if wording_only else claim.get("calculator_binding_rejected") or
                                  "The current statement has no supporting hypothetical evidence.")})
    return issues


def editorial_script_payload(script: dict) -> dict:
    """Accept only the normal finalized-script envelope around strict content."""
    content_keys = set(EditorialScript.model_fields)
    if set(script) - content_keys - {"text", "word_count", "qa", "provenance"}:
        raise ValueError("Unexpected fields in the current hypothetical script")
    parsed = EditorialScript.model_validate({key: script[key] for key in content_keys if key in script})
    spoken = " ".join(segment.text for segment in parsed.segments)
    if "text" in script and script["text"] != spoken:
        raise ValueError("Finalized narration does not match its typed segments")
    if "word_count" in script and script["word_count"] != len(spoken.split()):
        raise ValueError("Finalized word count does not match its typed segments")
    return parsed.model_dump(mode="json")


def verified_binding(script: dict, intake: dict, receipt: dict | None) -> dict:
    contract = HypotheticalScriptIntake.model_validate(intake)
    expected = compile_supplied_plan(contract.model_dump(mode="json"))
    if receipt != expected:
        raise ValueError("Hypothetical calculation receipt is missing or disagrees with the frozen source assumptions")
    parsed = EditorialScript.model_validate(editorial_script_payload(script))
    if any(segment.fact_ids for segment in parsed.segments):
        raise ValueError("The supplied hypothetical brief has no reviewed external fact IDs")
    actual = {claim.id: claim.model_dump(mode="json") for claim in parsed.claims}
    if actual != expected["claims"]:
        raise ValueError("Current script claims disagree with the actual hypothetical calculator receipt")
    used = {key for segment in parsed.segments for key in segment.claim_ids}
    if used != set(actual):
        raise ValueError("Hypothetical script has missing, unknown or unused calculator claim IDs")
    return {"version": 1, "evidence_kind": "verified_hypothetical_calculation",
            "script_hash": content_hash(script), "typed_script_hash": content_hash(parsed.model_dump(mode="json")),
            "calculation_receipt_hash": content_hash(receipt),
            "source_scenario_sha256": contract.source_scenario_sha256,
            "reviewed_brief_sha256": contract.reviewed_brief_sha256,
            "claims": actual, "human_review": "not_performed"}


def bind_ledger_claims(claims: list[dict], script: dict, binding: dict) -> list[dict]:
    segments = {item["id"]: item for item in script["segments"]}
    compiled = binding["claims"]
    result = []
    for source_claim in claims:
        claim = dict(source_claim)
        text = claim["exact_text"]
        segment = segments.get(claim["script_location"]["segment_id"], {})
        attached = [compiled[key] for key in segment.get("claim_ids", []) if key in compiled]
        if _EXTERNAL_ASSERTION.search(text) or _GUARANTEE.search(text):
            # A calculator cannot prove a historical/current market fact or a guarantee.
            claim.update(source_required=True, risk="HIGH", fact_check_status="UNREVIEWED", decision="BLOCK")
            claim["calculator_binding_rejected"] = "External assertion or guaranteed outcome is not a hypothetical calculation"
            result.append(claim)
            continue
        mentions = numeric_mentions(text)
        financial = [item for item in mentions if item["unit"] in {"dollars", "PLN", "percent", "years", "weeks"}]
        if not financial:
            result.append(claim)
            continue
        defect = numeric_binding_error(text, attached)
        if defect:
            claim.update(source_required=True, risk="HIGH", fact_check_status="UNREVIEWED", decision="BLOCK")
            claim["calculator_binding_rejected"] = defect
        else:
            claim.update(source_required=False, risk="LOW", fact_check_status="NOT_REQUIRED", decision="ALLOW",
                         evidence_kind="verified_hypothetical_calculation", brain_claim_class="estimate")
            claim["evidence_ids"] = ["calculation:" + binding["calculation_receipt_hash"]]
            claim["calculator_evidence"] = {"receipt_hash": binding["calculation_receipt_hash"],
                "source_scenario_sha256": binding["source_scenario_sha256"],
                "reviewed_brief_sha256": binding["reviewed_brief_sha256"],
                "claim_ids": list(segment.get("claim_ids", [])),
                "assumptions": [raw["assumptions"] for raw in attached], "human_review": "not_performed"}
        result.append(claim)
    return result
