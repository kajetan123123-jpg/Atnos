"""Use routed public data in research and fact checks without inventing numbers."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from atnos.integrations.capability_router import brain_source, classify_intent, route_data_need

TRANSIENT = "transient_lookup"
RESEARCH = "research_evidence"
REUSABLE = "reusable_project_knowledge"


def lookup_class(capability: str) -> str:
    if capability == "FX_RATES":
        return TRANSIENT
    if capability == "MACRO_INDICATORS":
        return REUSABLE
    if capability in {"SEC_FILINGS", "COMPANY_FILINGS", "PUBLIC_COMPANY_FILINGS"}:
        return RESEARCH
    if capability == "ENCYCLOPEDIC_KNOWLEDGE":
        return RESEARCH
    if capability == "WEB_PAGE_EXTRACT":
        return RESEARCH
    return TRANSIENT


def evidence_record(result: dict[str, Any], *, project: str, use_class: str) -> dict[str, Any] | None:
    """Return a Project Brain insight, or nothing for a transient lookup or a result without a source."""
    if use_class == TRANSIENT:
        return None
    if result.get("capability") == "WEB_PAGE_EXTRACT":
        from atnos.integrations.web_page_extract import brain_record_from_extract

        brain_class = str(result.get("brain_class") or "TEMP_RESEARCH")
        # Never auto-promote full pages to reusable knowledge.
        if brain_class == "REUSABLE_KNOWLEDGE" and not result.get("curated"):
            brain_class = "TEMP_RESEARCH"
        return brain_record_from_extract(
            result if result.get("output") else {"output": result.get("value"), "provenance": result.get("provenance")},
            project=project,
            brain_class=brain_class,
            claim=result.get("claim"),
        )
    if result.get("status") != "HEALTHY":
        return None
    provenance = brain_source(result)
    if not provenance.get("provider") or not provenance.get("source") or not provenance.get("fetched_at"):
        return None
    value = result.get("value") if isinstance(result.get("value"), dict) else {}
    return {
        "kind": "PUBLIC_DATA_EVIDENCE",
        "project": project,
        "use_class": use_class,
        "source": "PUBLIC_DATA",
        "statement": value,
        "topic": result.get("capability"),
        "provider": provenance["provider"],
        "capability": result.get("capability"),
        "fetched_at": provenance["fetched_at"],
        "source_url": provenance["source"],
        "observation_date": provenance.get("observation_date"),
        "confidence": provenance.get("confidence") or "source_data",
        "fallback_used": bool((result.get("routing") or {}).get("fallback_used")),
        "invented_value": False,
    }


def persist_evidence(store: Any, project: str, result: dict[str, Any]) -> dict[str, Any]:
    use_class = lookup_class(str(result.get("capability") or ""))
    record = evidence_record(result, project=project, use_class=use_class)
    if record is None:
        return {"stored": False, "use_class": use_class, "reason": "transient_or_unsourced"}
    insight_id = store.save_insight(project, record)
    return {"stored": True, "use_class": use_class, "insight_id": insight_id, "brain_class": record.get("use_class")}


def check_claim(text: str, *, root: Path | None = None, client: Any = None) -> dict[str, Any]:
    """Fact check asks for a capability. The router picks the provider."""
    url_match = re.search(r"https?://[^\s)\]\"']+", text or "")
    # Prefer structured public APIs over scraping when intent matches them.
    capability, _params, _freshness = classify_intent(text)
    if capability in {"FX_RATES", "MACRO_INDICATORS", "SEC_FILINGS", "COMPANY_FILINGS", "PUBLIC_COMPANY_FILINGS", "ENCYCLOPEDIC_KNOWLEDGE"}:
        result = route_data_need(intent=text, root=root, client=client)
        if result.get("status") != "HEALTHY":
            return {
                "status": result.get("status"),
                "capability": capability,
                "provider": (result.get("routing") or {}).get("selected_provider"),
                "invented_value": False,
                "value": None,
                "detail": result.get("detail"),
            }
        return {
            "status": "HEALTHY",
            "capability": result.get("capability") or capability,
            "provider": result.get("provider"),
            "value": result.get("value"),
            "provenance": result.get("provenance"),
            "brain_provenance": result.get("brain_provenance"),
            "freshness": result.get("freshness"),
            "invented_value": False,
        }
    if url_match:
        from atnos.integrations.web_page_extract import fact_check_claim_against_url

        claim = re.sub(r"https?://[^\s)\]\"']+", " ", text).strip() or text
        page_check = fact_check_claim_against_url(claim, url_match.group(0), root=root, client=client)
        return {
            "status": "HEALTHY" if page_check.get("verdict") in {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED"} else page_check.get("status"),
            "capability": "WEB_PAGE_EXTRACT",
            "provider": "scrapling",
            "invented_value": False,
            "value": {
                "verdict": page_check.get("verdict"),
                "claim_status": page_check.get("claim_status"),
                "blocks": page_check.get("blocks"),
                "block_ids": page_check.get("block_ids"),
                "page_title": page_check.get("page_title"),
            },
            "claim": claim,
            "brain_class": page_check.get("brain_class"),
            "provenance": page_check.get("provenance"),
            "detail": page_check.get("detail"),
            "claim_status": page_check.get("claim_status"),
            "verdict": page_check.get("verdict"),
        }
    if not capability:
        return {"status": "NO_CAPABILITY", "invented_value": False, "value": None, "capability": None}
    result = route_data_need(intent=text, root=root, client=client)
    if result.get("status") != "HEALTHY":
        return {
            "status": result.get("status"),
            "capability": capability,
            "provider": (result.get("routing") or {}).get("selected_provider"),
            "invented_value": False,
            "value": None,
            "detail": result.get("detail"),
        }
    return {
        "status": "HEALTHY",
        "capability": result.get("capability") or capability,
        "provider": result.get("provider"),
        "value": result.get("value"),
        "provenance": result.get("provenance"),
        "brain_provenance": result.get("brain_provenance"),
        "freshness": result.get("freshness"),
        "invented_value": False,
    }


def wealth_blueprint_inflation_evidence(*, root: Path | None = None, client: Any = None) -> dict[str, Any]:
    """Research evidence for a savings-and-inflation film. Missing numbers stay missing."""
    inflation = route_data_need(
        intent="US inflation",
        parameters={"country": "US", "indicator": "FP.CPI.TOTL.ZG"},
        freshness="HISTORICAL",
        project_id="wealth_blueprint",
        root=root,
        client=client,
    )
    background = route_data_need(intent="What is inflation?", project_id="wealth_blueprint", root=root, client=client)
    numbers = []
    if inflation.get("status") == "HEALTHY" and (inflation.get("value") or {}).get("amount") is not None:
        numbers.append(
            {
                "capability": "MACRO_INDICATORS",
                "provider": inflation.get("provider"),
                "value": inflation.get("value"),
                "provenance": inflation.get("provenance"),
                "freshness": inflation.get("freshness"),
            }
        )
    return {
        "project": "wealth_blueprint",
        "task": "inflation and the real value of savings",
        "numbers": numbers,
        "background_status": background.get("status"),
        "background_provider": (background.get("routing") or {}).get("selected_provider"),
        "background_provenance": background.get("provenance") if background.get("status") == "HEALTHY" else None,
        "invented_value": False,
        "missing": [] if numbers else ["US inflation rate"],
    }
