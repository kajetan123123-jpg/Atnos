"""Claim Evidence Gate — classification, evidence, gate, isolation."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from atnos.integrations.claim_evidence_gate import (
    assert_project_isolation,
    build_ledger,
    classify_claim_text,
    evaluate_gate,
    extract_claims_from_script,
    propose_repairs,
    production_blocker_from_ledger,
    research_claim,
)


CFPB_HTML = """
<html><head><title>Emergency fund</title></head><body><main>
<h1>Emergency funds</h1>
<p>An emergency fund is a cash reserve that is specifically set aside for unplanned expenses or financial emergencies.</p>
<p>An emergency fund is money set aside to cover unexpected expenses.</p>
</main></body></html>
"""


def test_classification_matrix():
    cases = [
        ("Most Americans cannot cover a $400 emergency expense.", "NUMERIC", True),
        ("According to the CFPB, an emergency fund covers unexpected costs.", "SOURCE_ATTRIBUTED", True),
        ("You should automate savings every Friday.", "RECOMMENDATION", False),
        ("I think a notebook is better than an app.", "OPINION", False),
        ("What if you made saving automatic?", "RHETORICAL", False),
        ("Inflation may erode cash over time.", "HYPOTHESIS", False),
        (
            "For example, saving two hundred dollars each month for twelve months totals two thousand four hundred dollars.",
            "NARRATIVE",
            False,
        ),
        (
            "Another option is saving one hundred dollars every month for twenty-four months.",
            "NARRATIVE",
            False,
        ),
        ("That also totals two thousand four hundred dollars.", "NARRATIVE", False),
    ]
    for text, expected_type, required in cases:
        row = classify_claim_text(text)
        assert row["claim_type"] == expected_type, (text, row)
        assert row["source_required"] is required, (text, row)
        if not required and "dollars" in text.casefold():
            assert row["risk"] == "LOW", (text, row)


def test_illustrative_money_math_does_not_block_gate():
    claims = [
        {
            "claim_id": "claim_007",
            "exact_text": "For example, saving two hundred dollars each month for twelve months totals two thousand four hundred dollars.",
            "claim_type": "NUMERIC",
            "risk": "HIGH",
            "source_required": True,
            "fact_check_status": "NOT_FOUND",
        },
        {
            "claim_id": "claim_009",
            "exact_text": "Another option is saving one hundred dollars every month for twenty-four months.",
            "claim_type": "NUMERIC",
            "risk": "HIGH",
            "source_required": True,
            "fact_check_status": "NOT_FOUND",
        },
        {
            "claim_id": "claim_010",
            "exact_text": "That also totals two thousand four hundred dollars.",
            "claim_type": "NUMERIC",
            "risk": "HIGH",
            "source_required": True,
            "fact_check_status": "NOT_FOUND",
        },
    ]
    gate = evaluate_gate(claims)
    assert gate["decision"] == "PASS"
    assert gate["production_allowed"] is True
    assert gate["blocked_claims"] == []
    blocker = production_blocker_from_ledger({"claims": claims, "gate": {"decision": "FACT_CHECK_BLOCKED", "production_allowed": False}})
    assert blocker["blocked"] is False


def test_extract_preserves_original_and_normalizes():
    script = {
        "segments": [
            {"id": "s01", "type": "hook", "text": "Start today. Build an emergency fund."},
            {"id": "s02", "type": "cta", "text": "Move money every Friday."},
        ]
    }
    claims = extract_claims_from_script(script)
    assert len(claims) >= 2
    assert all(c["exact_text"] for c in claims)
    assert any(c["claim_type"] == "RECOMMENDATION" for c in claims)


def test_recommendation_does_not_block(tmp_path):
    script = {
        "title": "Friday habit",
        "segments": [
            {"id": "s01", "type": "practical", "text": "You should automate savings every Friday."},
            {"id": "s02", "type": "cta", "text": "Start this habit today."},
        ],
    }
    ledger = build_ledger(
        project="wealth_blueprint",
        production_id="wb-rec-only",
        script=script,
        root=tmp_path,
        live_research=False,
    )
    assert ledger["gate"]["decision"] == "PASS"
    assert ledger["gate"]["production_allowed"] is True
    assert all(c["fact_check_status"] == "NOT_REQUIRED" for c in ledger["claims"])


def test_high_risk_unsupported_blocks(tmp_path):
    script = {
        "segments": [
            {
                "id": "s01",
                "type": "explanation",
                "text": "Most Americans cannot cover a $400 emergency expense in cash.",
            }
        ]
    }
    ledger = build_ledger(
        project="wealth_blueprint",
        production_id="wb-block-numeric",
        script=script,
        root=tmp_path,
        live_research=False,
    )
    gate = ledger["gate"]
    assert gate["decision"] == "FACT_CHECK_BLOCKED"
    blocker = production_blocker_from_ledger(ledger)
    assert blocker["blocked"] is True
    assert blocker["code"] == "FACT_CHECK_BLOCKED"


def test_not_found_is_not_false(tmp_path):
    claim = {
        "claim_id": "claim_001",
        "exact_text": "Most Americans cannot cover a $400 emergency.",
        "normalized_claim": "Most Americans cannot cover a $400 emergency.",
        "claim_type": "NUMERIC",
        "risk": "HIGH",
        "source_required": True,
        "fact_check_status": "UNREVIEWED",
        "attributed_institution": None,
    }
    researched = research_claim(claim, root=tmp_path, live=False)
    # Without live candidates matching tightly enough for $400 poll, status stays missing/not found — not CONTRADICTED.
    assert researched["fact_check_status"] in {"NOT_FOUND", "UNVERIFIED"}
    assert researched["fact_check_status"] != "CONTRADICTED"


def test_supported_factual_via_web_extract(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=CFPB_HTML.encode())

    script = {
        "segments": [
            {
                "id": "s01",
                "type": "explanation",
                "text": "An emergency fund is money set aside to cover unexpected expenses.",
            }
        ]
    }
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ledger = build_ledger(
            project="wealth_blueprint",
            production_id="wb-cfpb-supported",
            script=script,
            root=tmp_path,
            client=client,
            live_research=True,
        )
    claim = ledger["claims"][0]
    assert claim["fact_check_status"] in {"SUPPORTED", "PARTIALLY_SUPPORTED"}
    assert claim["evidence"]
    assert claim["evidence"][0]["block_ids"]
    assert ledger["gate"]["production_allowed"] is True


def test_wrong_attribution_fails(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=CFPB_HTML.encode())

    claim = {
        "claim_id": "claim_attr",
        "exact_text": "According to the SEC, an emergency fund is money set aside for unexpected expenses.",
        "normalized_claim": "According to the SEC, an emergency fund is money set aside for unexpected expenses.",
        "claim_type": "SOURCE_ATTRIBUTED",
        "risk": "MEDIUM",
        "source_required": True,
        "fact_check_status": "UNREVIEWED",
        "attributed_institution": "SEC",
    }
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        # Force CFPB candidate by topic; attribution should fail vs SEC.
        from atnos.integrations import claim_evidence_gate as gate

        original = gate.discover_source_candidates

        def forced(c):
            return [
                {
                    "topics": ("emergency fund",),
                    "url": "https://www.consumerfinance.gov/an-essential-guide-to-building-an-emergency-fund/",
                    "authority": "PRIMARY_INSTITUTIONAL",
                    "institution": "CFPB",
                    "capability": "WEB_PAGE_EXTRACT",
                }
            ]

        gate.discover_source_candidates = forced  # type: ignore
        try:
            researched = research_claim(claim, root=tmp_path, client=client, live=True)
        finally:
            gate.discover_source_candidates = original  # type: ignore
    assert researched["fact_check_status"] == "UNVERIFIED"
    assert researched["decision"] == "WRONG_ATTRIBUTION"


def test_contradiction_repair_does_not_invent_number():
    claims = [
        {
            "claim_id": "claim_001",
            "exact_text": "Inflation is exactly 70 percent this year.",
            "source_required": True,
            "risk": "HIGH",
            "fact_check_status": "CONTRADICTED",
            "evidence": [{"blocks": [{"text": "CPI rose 3.2 percent last year."}]}],
        }
    ]
    repairs = propose_repairs(claims)
    assert repairs
    blob = json.dumps(repairs)
    assert "60%" not in blob
    assert "invented_number" in blob
    assert any(opt["action"] == "REMOVE_NUMBER" for opt in repairs[0]["options"])


def test_project_isolation(tmp_path):
    script = {"segments": [{"id": "s01", "type": "cta", "text": "Start saving today."}]}
    build_ledger(project="wealth_blueprint", production_id="iso-1", script=script, root=tmp_path, live_research=False)
    build_ledger(project="wonder_crew", production_id="iso-1", script=script, root=tmp_path, live_research=False)
    wb = json.loads((tmp_path / "claim_ledgers" / "wealth_blueprint" / "iso-1.json").read_text(encoding="utf-8"))
    wc = json.loads((tmp_path / "claim_ledgers" / "wonder_crew" / "iso-1.json").read_text(encoding="utf-8"))
    assert wb["project"] == "wealth_blueprint"
    assert wc["project"] == "wonder_crew"
    assert assert_project_isolation(tmp_path, "wealth_blueprint", "wonder_crew") is True


def test_evaluate_gate_partial_high_risk_warning_path():
    claims = [
        {
            "claim_id": "c1",
            "source_required": True,
            "risk": "HIGH",
            "fact_check_status": "PARTIALLY_SUPPORTED",
            "decision": "ALLOW_WITH_WARNING",
        }
    ]
    gate = evaluate_gate(claims)
    assert gate["production_allowed"] is True


def test_stale_current_metric_keeps_period():
    row = classify_claim_text("US inflation is 3.2% this year.")
    assert row["claim_type"] in {"NUMERIC", "HISTORICAL", "FACTUAL"}
    assert row["measurement"] is not None
    assert row["measurement"].get("freshness") == "CURRENT" or row["measurement"].get("year")


def test_media_gate_blocks_gpu_stage(tmp_path):
    from atnos.integrations.claim_evidence_gate import ensure_media_claim_gate

    script = {
        "segments": [
            {
                "id": "s01",
                "type": "explanation",
                "text": "Most Americans cannot cover a $400 emergency expense in cash.",
            }
        ]
    }
    blocked = ensure_media_claim_gate(
        project="wealth_blueprint",
        production_id="gpu-block-1",
        stage="rendering",
        script=script,
        root=tmp_path,
    )
    assert blocked["blocked"] is True
    assert blocked["code"] == "FACT_CHECK_BLOCKED"


def test_media_gate_skips_non_wb():
    from atnos.integrations.claim_evidence_gate import ensure_media_claim_gate

    result = ensure_media_claim_gate(
        project="wonder_crew",
        production_id="x",
        stage="rendering",
        script={"segments": []},
    )
    assert result.get("skipped") is True
    assert result.get("blocked") is False
