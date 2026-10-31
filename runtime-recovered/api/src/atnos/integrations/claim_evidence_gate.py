"""Claim Evidence Gate — classify, research, ledger, and block unsafe WB claims.

Extends existing Project Content Brain claim classification and Capability Runtime.
Does not invent numbers or silently rewrite scripts.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from atnos.integrations.public_data import data_root

logger = logging.getLogger("atnos.claim_evidence_gate")

ClaimType = Literal[
    "FACTUAL",
    "NUMERIC",
    "HISTORICAL",
    "FINANCIAL_RULE",
    "SOURCE_ATTRIBUTED",
    "OPINION",
    "RECOMMENDATION",
    "HYPOTHESIS",
    "RHETORICAL",
    "NARRATIVE",
]
ClaimRisk = Literal["LOW", "MEDIUM", "HIGH"]
ClaimStatus = Literal[
    "UNREVIEWED",
    "RESEARCHING",
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "CONTRADICTED",
    "NOT_FOUND",
    "UNVERIFIED",
    "NOT_REQUIRED",
    "CONFLICTING_EVIDENCE",
    "SOURCE_CHANGED",
]
SourceAuthority = Literal[
    "PRIMARY_OFFICIAL",
    "PRIMARY_INSTITUTIONAL",
    "SECONDARY_REPUTABLE",
    "ENCYCLOPEDIC_REFERENCE",
    "COMMUNITY",
    "BLOG",
    "UNKNOWN",
]
GateDecision = Literal["PASS", "WARN", "FACT_CHECK_BLOCKED", "REPAIR_REQUIRED"]

# Map evidence-gate types onto existing Project Content Brain ClaimClass values.
BRAIN_CLAIM_CLASS = {
    "FACTUAL": "needs_source",
    "NUMERIC": "needs_source",
    "HISTORICAL": "needs_source",
    "FINANCIAL_RULE": "needs_source",
    "SOURCE_ATTRIBUTED": "needs_source",
    "OPINION": "opinion",
    "RECOMMENDATION": "opinion",
    "HYPOTHESIS": "estimate",
    "RHETORICAL": "opinion",
    "NARRATIVE": "opinion",
}

# Curated institutional candidates — not domain-rank heuristics.
# Used only when claim topic matches; authority still recorded separately.
TOPIC_SOURCE_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "topics": ("emergency fund", "emergency savings", "financial cushion", "unexpected expenses"),
        "url": "https://www.consumerfinance.gov/an-essential-guide-to-building-an-emergency-fund/",
        "authority": "PRIMARY_INSTITUTIONAL",
        "institution": "CFPB",
        "capability": "WEB_PAGE_EXTRACT",
    },
    {
        "topics": ("inflation", "consumer price", "cpi"),
        "capability": "MACRO_INDICATORS",
        "authority": "PRIMARY_INSTITUTIONAL",
        "institution": "World Bank",
        "params": {"country": "US", "indicator": "FP.CPI.TOTL.ZG"},
    },
    {
        "topics": ("gdp", "gross domestic product"),
        "capability": "MACRO_INDICATORS",
        "authority": "PRIMARY_INSTITUTIONAL",
        "institution": "World Bank",
        "params": {"country": "US", "indicator": "NY.GDP.MKTP.CD"},
    },
    {
        "topics": ("exchange rate", "fx rate", "usd/eur", "eur/usd"),
        "capability": "FX_RATES",
        "authority": "PRIMARY_INSTITUTIONAL",
        "institution": "Frankfurter",
        "params": {"base": "USD", "quote": "EUR"},
    },
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMERIC = re.compile(
    r"\b\d+(\.\d+)?\s*(%|percent|dollars?|usd|eur|pln)?\b|"
    r"[$€£]\s?\d[\d,]*(?:\.\d+)?|"
    r"\b(hundred|thousand|million|billion)\b",
    re.I,
)
_ATTRIBUTION = re.compile(
    r"\b(according to|per the|as reported by|cfbp|cfpb|federal reserve|sec|world bank|imf|bureau)\b",
    re.I,
)
_RECOMMEND = re.compile(
    r"\b(should|ought|start|build|set up|track|move|allocate|save|keep|make sure|try to|you need)\b",
    re.I,
)
_HYPOTHESIS = re.compile(r"\b(may|might|could|can help|helps you|over time)\b", re.I)
_OPINION = re.compile(r"\b(i think|better|best|real difference|deserves|thank you)\b", re.I)
_HISTORICAL = re.compile(r"\b(in 20\d{2}|last year|this year|historically|since 20\d{2})\b", re.I)
_FIN_RULE = re.compile(
    r"\b(tax|ike|ikze|ira|401k|interest rate|apr|compound interest|withdrawal|penalty)\b",
    re.I,
)
_FACTUAL_MARKERS = re.compile(
    r"\b(most americans|majority|on average|is money set aside|emergency fund is|"
    r"inflation is|gdp is|studies show|research shows|keeps your emergency fund)\b",
    re.I,
)
_HIGH_RISK = re.compile(
    r"\b(guaranteed|always beat|risk[- ]free|tax|lawsuit|illegal|return of \d|"
    r"will double|never lose|\$\d|percent|\d+\s*%)\b",
    re.I,
)
# Local WB shorts often use toy arithmetic ("$100/mo × 24 = $2400"). That is not an
# external statistic and must not block media stages waiting on paid research APIs.
_ILLUSTRATIVE_MONEY_MATH = re.compile(
    r"\b(for example|another option|for instance|say you|imagine you)\b|"
    r"\b(totals?|adds? up to|equals?|comes to|works out to)\b|"
    r"\b(each|every|per)\s+month\s+for\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|twenty|twenty-four|thirty|thirty-six|forty|forty-eight|sixty)\s+months?\b|"
    r"\b\d+\s*(dollars?|usd)\s+(each|every|a|per)\s+month\b|"
    r"\b(two|one|three|four|five)\s+hundred\s+dollars?\s+(each|every|a|per)\s+month\b",
    re.I,
)
_EXTERNAL_STAT_MARKERS = re.compile(
    r"\b(most|majority|americans|households|on average|survey|studies?|research|"
    r"inflation|gdp|unemployment|according to|reported|bureau|federal reserve|"
    r"cfbp|cfpb|sec|imf|world bank)\b",
    re.I,
)
_BLOCKING_STATUSES = frozenset(
    {"UNREVIEWED", "RESEARCHING", "CONTRADICTED", "UNVERIFIED", "NOT_FOUND", "CONFLICTING_EVIDENCE", "SOURCE_CHANGED"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ledger_dir(root: Path | None, project: str) -> Path:
    safe = re.sub(r"[^a-z0-9_-]+", "_", project.casefold())
    path = data_root(root) / "claim_ledgers" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path(root: Path | None, project: str, production_id: str) -> Path:
    safe_prod = re.sub(r"[^a-zA-Z0-9_-]+", "_", production_id)[:80] or "unknown"
    return ledger_dir(root, project) / f"{safe_prod}.json"


def normalize_claim_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"^[\-\*\u2022]\s*", "", cleaned)
    return cleaned


def _is_illustrative_money_math(text: str) -> bool:
    """True for self-contained example arithmetic (no external statistic to verify)."""
    raw = normalize_claim_text(text)
    if not raw or not _NUMERIC.search(raw):
        return False
    if _EXTERNAL_STAT_MARKERS.search(raw) or _ATTRIBUTION.search(raw):
        return False
    if _HISTORICAL.search(raw) and re.search(r"\b(20\d{2}|last year|this year)\b", raw, re.I):
        # Year-bound macro/history claims still need sources.
        if re.search(r"\b(inflation|gdp|rate|return|index|market)\b", raw, re.I):
            return False
    return bool(_ILLUSTRATIVE_MONEY_MATH.search(raw))


def classify_claim_text(text: str, *, segment_type: str | None = None) -> dict[str, Any]:
    """Deterministic claim typing. Extends brain ClaimClass; does not invent sources."""
    raw = normalize_claim_text(text)
    lowered = raw.casefold()
    claim_type: ClaimType = "NARRATIVE"
    if _ATTRIBUTION.search(raw):
        claim_type = "SOURCE_ATTRIBUTED"
    elif _NUMERIC.search(raw) and not _RECOMMEND.search(raw[:24]):
        claim_type = "NUMERIC"
    elif _FIN_RULE.search(raw) and (_NUMERIC.search(raw) or "must" in lowered or "required" in lowered):
        claim_type = "FINANCIAL_RULE"
    elif _HISTORICAL.search(raw) and (_NUMERIC.search(raw) or _FACTUAL_MARKERS.search(raw)):
        claim_type = "HISTORICAL"
    elif _FACTUAL_MARKERS.search(raw):
        claim_type = "FACTUAL"
    elif _HYPOTHESIS.search(raw) and (
        _RECOMMEND.search(raw) or re.search(r"\b(may|might|could)\b", lowered)
    ):
        claim_type = "HYPOTHESIS"
    elif segment_type == "cta" or (_RECOMMEND.search(raw) and len(raw.split()) <= 28):
        claim_type = "RECOMMENDATION"
    elif _OPINION.search(raw) and not _FACTUAL_MARKERS.search(raw):
        claim_type = "OPINION"
    elif _RECOMMEND.search(raw):
        claim_type = "RECOMMENDATION"
    elif segment_type == "hook" or raw.endswith("?"):
        claim_type = "RHETORICAL"
    elif len(raw.split()) <= 12 and not _NUMERIC.search(raw):
        claim_type = "RHETORICAL"

    risk: ClaimRisk = "LOW"
    if claim_type in {"NUMERIC", "FINANCIAL_RULE", "HISTORICAL"} or _HIGH_RISK.search(raw):
        risk = "HIGH"
    elif claim_type in {"FACTUAL", "SOURCE_ATTRIBUTED"}:
        risk = "MEDIUM"
    elif claim_type == "HYPOTHESIS":
        risk = "LOW"

    source_required = claim_type in {
        "FACTUAL",
        "NUMERIC",
        "HISTORICAL",
        "FINANCIAL_RULE",
        "SOURCE_ATTRIBUTED",
    } or (risk == "HIGH" and claim_type not in {"RECOMMENDATION", "OPINION", "RHETORICAL", "NARRATIVE"})

    # Soft recommendations that look like guarantees escalate.
    if claim_type == "RECOMMENDATION" and re.search(r"\b(guaranteed|always|never fail)\b", lowered):
        risk = "HIGH"
        source_required = True

    # Local solo runtime: toy savings arithmetic does not need paid evidence APIs.
    if _is_illustrative_money_math(raw):
        risk = "LOW"
        source_required = False
        if claim_type == "NUMERIC":
            claim_type = "NARRATIVE"

    period = None
    year = re.search(r"\b(20\d{2})\b", raw)
    if year:
        period = {"year": year.group(1)}
    if re.search(r"\b(current|today|now|this year)\b", lowered) and claim_type in {"NUMERIC", "FACTUAL", "HISTORICAL"}:
        period = {**(period or {}), "freshness": "CURRENT"}

    return {
        "claim_type": claim_type,
        "risk": risk,
        "source_required": source_required,
        "normalized_claim": raw,
        "brain_claim_class": BRAIN_CLAIM_CLASS.get(claim_type, "opinion"),
        "measurement": period,
        "attributed_institution": _extract_attribution(raw),
    }


def _extract_attribution(text: str) -> str | None:
    patterns = (
        (r"\bcfpb\b|\bconsumer financial protection bureau\b", "CFPB"),
        (r"\bfederal reserve\b|\bfed\b", "Federal Reserve"),
        (r"\bsec\b|\bedgar\b", "SEC"),
        (r"\bworld bank\b", "World Bank"),
        (r"\bimf\b", "IMF"),
    )
    lowered = text.casefold()
    for pattern, name in patterns:
        if re.search(pattern, lowered):
            return name
    return None


def extract_claims_from_script(script: dict[str, Any] | str) -> list[dict[str, Any]]:
    """Split script into claim records. Preserves original sentence text."""
    if isinstance(script, str):
        payload = {"text": script, "segments": [{"id": "body", "text": script, "type": "practical"}]}
    else:
        payload = script
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    if not segments and payload.get("text"):
        segments = [{"id": "body", "text": payload.get("text"), "type": "practical"}]

    claims: list[dict[str, Any]] = []
    index = 0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
        if not parts:
            parts = [text]
        for part in parts:
            if len(part.split()) < 3:
                continue
            index += 1
            classified = classify_claim_text(part, segment_type=str(seg.get("type") or ""))
            claim_id = f"claim_{index:03d}"
            claims.append(
                {
                    "claim_id": claim_id,
                    "exact_text": part,
                    "normalized_claim": classified["normalized_claim"],
                    "script_location": {
                        "segment_id": seg.get("id"),
                        "segment_type": seg.get("type"),
                    },
                    "claim_type": classified["claim_type"],
                    "risk": classified["risk"],
                    "source_required": classified["source_required"],
                    "source_status": "NONE",
                    "fact_check_status": "NOT_REQUIRED" if not classified["source_required"] else "UNREVIEWED",
                    "evidence_ids": [],
                    "evidence": [],
                    "decision": "PENDING",
                    "brain_claim_class": classified["brain_claim_class"],
                    "measurement": classified.get("measurement"),
                    "attributed_institution": classified.get("attributed_institution"),
                    "updated_at": _now(),
                }
            )
    return claims


def classify_source_authority(url: str | None, *, institution: str | None = None, declared: str | None = None) -> SourceAuthority:
    if declared in {
        "PRIMARY_OFFICIAL",
        "PRIMARY_INSTITUTIONAL",
        "SECONDARY_REPUTABLE",
        "ENCYCLOPEDIC_REFERENCE",
        "COMMUNITY",
        "BLOG",
        "UNKNOWN",
    }:
        return declared  # type: ignore[return-value]
    host = ""
    if url:
        try:
            from urllib.parse import urlparse

            host = (urlparse(url).hostname or "").casefold()
        except Exception:
            host = ""
    if institution and institution.upper() in {"CFPB", "SEC", "FEDERAL RESERVE", "WORLD BANK", "IMF"}:
        return "PRIMARY_INSTITUTIONAL"
    if host.endswith(".gov") or host.endswith(".gov.uk"):
        return "PRIMARY_OFFICIAL"
    if host in {"data.worldbank.org", "api.worldbank.org", "api.frankfurter.dev", "data.sec.gov"}:
        return "PRIMARY_INSTITUTIONAL"
    if "wikipedia.org" in host:
        return "ENCYCLOPEDIC_REFERENCE"
    if host.endswith(".edu"):
        return "SECONDARY_REPUTABLE"
    if any(token in host for token in ("medium.com", "blogspot.", "wordpress.", "substack.com")):
        return "BLOG"
    return "UNKNOWN"


def discover_source_candidates(claim: dict[str, Any]) -> list[dict[str, Any]]:
    text = f"{claim.get('normalized_claim') or ''} {claim.get('exact_text') or ''}".casefold()
    attributed = (claim.get("attributed_institution") or "").casefold()
    out: list[dict[str, Any]] = []
    for item in TOPIC_SOURCE_CANDIDATES:
        if not any(topic in text for topic in item["topics"]):
            continue
        institution = str(item.get("institution") or "").casefold()
        if attributed:
            # Attribution check: candidates must match the named institution.
            aliases = {
                "cfpb": {"cfpb", "consumer financial protection bureau"},
                "world bank": {"world bank"},
                "sec": {"sec"},
                "frankfurter": {"frankfurter"},
                "federal reserve": {"federal reserve", "fed"},
            }
            allowed = aliases.get(attributed, {attributed})
            if institution and institution not in allowed and not any(a in institution for a in allowed):
                continue
        out.append(dict(item))
    return out


def research_claim(
    claim: dict[str, Any],
    *,
    root: Path | None = None,
    client: Any = None,
    live: bool = True,
) -> dict[str, Any]:
    """Research a single claim via structured API first, else WEB_PAGE_EXTRACT."""
    updated = dict(claim)
    updated["fact_check_status"] = "RESEARCHING"
    updated["updated_at"] = _now()
    if not claim.get("source_required"):
        updated["fact_check_status"] = "NOT_REQUIRED"
        updated["decision"] = "ALLOW"
        return updated

    evidence_rows: list[dict[str, Any]] = []
    candidates = discover_source_candidates(claim)

    # Prefer structured capabilities when candidates declare them.
    for candidate in candidates:
        capability = candidate.get("capability")
        if capability in {"FX_RATES", "MACRO_INDICATORS", "SEC_FILINGS", "COMPANY_FILINGS"}:
            from atnos.integrations.capability_router import route_data_need

            result = route_data_need(
                intent=str(claim.get("normalized_claim") or claim.get("exact_text")),
                capability=str(capability),
                parameters=dict(candidate.get("params") or {}),
                root=root,
                client=client,
            )
            evid = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:10]}",
                "kind": "structured_api",
                "capability": capability,
                "provider": result.get("provider") or candidate.get("institution"),
                "query": candidate.get("params"),
                "observation": result.get("value"),
                "observation_date": (result.get("provenance") or {}).get("observation_date"),
                "fetched_at": (result.get("provenance") or {}).get("fetched_at"),
                "status": result.get("status"),
                "source_authority": classify_source_authority(
                    (result.get("provenance") or {}).get("source"),
                    institution=candidate.get("institution"),
                    declared=candidate.get("authority"),
                ),
                "provenance": result.get("provenance"),
            }
            evidence_rows.append(evid)
            if result.get("status") == "HEALTHY" and result.get("value") is not None:
                updated["fact_check_status"] = _status_from_structured(claim, result)
            elif result.get("status") in {"REQUIRES_SETUP", "DISABLED"}:
                updated["fact_check_status"] = "UNVERIFIED"
            else:
                updated["fact_check_status"] = "NOT_FOUND"
            break

    if not evidence_rows and live:
        from atnos.integrations.web_page_extract import fact_check_claim_against_url

        url = None
        authority = "UNKNOWN"
        institution = None
        for candidate in candidates:
            if candidate.get("url"):
                url = candidate["url"]
                authority = candidate.get("authority") or "UNKNOWN"
                institution = candidate.get("institution")
                break
        # Explicit URL on claim (attribution / brief)
        if not url:
            url_match = re.search(r"https?://[^\s)\]\"']+", str(claim.get("exact_text") or ""))
            if url_match:
                url = url_match.group(0)

        if url:
            page_check = fact_check_claim_against_url(
                str(claim.get("normalized_claim") or claim.get("exact_text")),
                url,
                root=root,
                client=client,
            )
            # Attribution: if claim names an institution, source must match.
            attributed = claim.get("attributed_institution")
            authority_ok = True
            if attributed and institution and attributed.upper() != str(institution).upper():
                authority_ok = False
            if attributed and not institution:
                final = str(page_check.get("final_url") or url)
                if attributed.upper() == "CFPB" and "consumerfinance.gov" not in final:
                    authority_ok = False

            evid = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:10]}",
                "kind": "web_page_blocks",
                "capability": "WEB_PAGE_EXTRACT",
                "url": page_check.get("url") or url,
                "final_url": page_check.get("final_url"),
                "content_hash": page_check.get("content_hash"),
                "block_ids": page_check.get("block_ids") or [],
                "blocks": page_check.get("blocks") or [],
                "fetched_at": (page_check.get("provenance") or {}).get("fetched_at"),
                "source_authority": classify_source_authority(
                    page_check.get("final_url") or url,
                    institution=institution,
                    declared=authority,
                ),
                "extraction_quality": page_check.get("extraction_quality"),
                "source_quality": authority,
                "attribution_ok": authority_ok,
                "verdict": page_check.get("verdict"),
                "provenance": page_check.get("provenance"),
            }
            evidence_rows.append(evid)
            if not authority_ok:
                updated["fact_check_status"] = "UNVERIFIED"
                updated["decision"] = "WRONG_ATTRIBUTION"
            else:
                verdict = str(page_check.get("verdict") or "NOT_FOUND")
                updated["fact_check_status"] = {
                    "SUPPORTED": "SUPPORTED",
                    "PARTIALLY_SUPPORTED": "PARTIALLY_SUPPORTED",
                    "CONTRADICTED": "CONTRADICTED",
                    "NOT_FOUND": "NOT_FOUND",
                }.get(verdict, "UNVERIFIED")
        else:
            updated["fact_check_status"] = "NOT_FOUND"
            updated["decision"] = "NO_SOURCE_CANDIDATE"
    elif not evidence_rows:
        updated["fact_check_status"] = "NOT_FOUND"
        updated["decision"] = "NO_LIVE_RESEARCH"

    # Conflict detection across multiple evidence rows.
    statuses = {str(row.get("verdict") or row.get("status") or "") for row in evidence_rows}
    if "SUPPORTED" in statuses and "CONTRADICTED" in statuses:
        updated["fact_check_status"] = "CONFLICTING_EVIDENCE"
        updated["decision"] = "ESCALATE_REVIEW"
        updated["conflict"] = {
            "sources": [
                {"evidence_id": row.get("evidence_id"), "verdict": row.get("verdict") or row.get("status")}
                for row in evidence_rows
            ],
            "possible_causes": ["different_year", "different_population", "different_definition", "update_lag"],
        }

    updated["evidence"] = evidence_rows
    updated["evidence_ids"] = [row["evidence_id"] for row in evidence_rows if row.get("evidence_id")]
    updated["source_status"] = "ATTACHED" if evidence_rows else "MISSING"
    if updated.get("decision") in {None, "PENDING"}:
        updated["decision"] = _decision_for_status(
            str(updated.get("fact_check_status")),
            risk=str(updated.get("risk")),
            source_required=bool(updated.get("source_required")),
        )
    updated["updated_at"] = _now()
    logger.info(
        "claim_research claim_id=%s status=%s capability=%s",
        updated.get("claim_id"),
        updated.get("fact_check_status"),
        (evidence_rows[0].get("capability") if evidence_rows else None),
    )
    return updated


def _status_from_structured(claim: dict[str, Any], result: dict[str, Any]) -> ClaimStatus:
    value = result.get("value")
    if value is None:
        return "NOT_FOUND"
    # Structured hit confirms a queryable metric exists; does not invent script numbers.
    text = str(claim.get("normalized_claim") or "")
    if _NUMERIC.search(text):
        # Without exact figure match we only partially support.
        amount = None
        if isinstance(value, dict):
            amount = value.get("amount") or value.get("rate")
        if amount is None:
            return "PARTIALLY_SUPPORTED"
        return "PARTIALLY_SUPPORTED"
    return "SUPPORTED"


def _decision_for_status(status: str, *, risk: str, source_required: bool) -> str:
    if not source_required or status == "NOT_REQUIRED":
        return "ALLOW"
    if status in {"SUPPORTED", "PARTIALLY_SUPPORTED"}:
        return "ALLOW" if status == "SUPPORTED" or risk != "HIGH" else "ALLOW_WITH_WARNING"
    if status == "CONTRADICTED":
        return "REPAIR_OR_REMOVE"
    if status == "CONFLICTING_EVIDENCE":
        return "ESCALATE_REVIEW"
    if status in {"NOT_FOUND", "UNVERIFIED", "UNREVIEWED", "RESEARCHING"}:
        return "BLOCK" if risk == "HIGH" else ("WARN" if risk == "MEDIUM" else "ALLOW_WITH_WARNING")
    return "REVIEW"


def _relax_illustrative_money_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Downgrade toy savings arithmetic so existing ledgers unlock without paid research."""
    relaxed: list[dict[str, Any]] = []
    for raw_claim in claims:
        claim = dict(raw_claim)
        text = str(claim.get("exact_text") or claim.get("normalized_claim") or "")
        if claim.get("calculator_binding_rejected"):
            # Exact typed-calculator rejection cannot be overridden by a generic
            # "for example"/"equals" heuristic in an old ledger.
            relaxed.append(claim)
            continue
        if _is_illustrative_money_math(text):
            claim["source_required"] = False
            claim["risk"] = "LOW"
            if str(claim.get("claim_type") or "") == "NUMERIC":
                claim["claim_type"] = "NARRATIVE"
            status = str(claim.get("fact_check_status") or "")
            if status in _BLOCKING_STATUSES or status in {"", "UNREVIEWED"}:
                claim["fact_check_status"] = "NOT_REQUIRED"
            claim["decision"] = "ALLOW"
            claim["illustrative_money_math"] = True
        relaxed.append(claim)
    return relaxed


def evaluate_gate(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Production write gate from ledger claims."""
    claims = _relax_illustrative_money_claims(claims)
    required = [c for c in claims if c.get("source_required")]
    supported = [c for c in required if c.get("fact_check_status") in {"SUPPORTED", "PARTIALLY_SUPPORTED"}]
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for claim in claims:
        status = str(claim.get("fact_check_status") or "UNREVIEWED")
        risk = str(claim.get("risk") or "LOW")
        if not claim.get("source_required"):
            continue
        if status in _BLOCKING_STATUSES:
            blocked.append(
                {
                    "claim_id": claim.get("claim_id"),
                    "reason": f"Required claim lacks valid support: {status}",
                    "status": status,
                    "exact_text": claim.get("exact_text"),
                }
            )
        elif status in _BLOCKING_STATUSES and risk == "MEDIUM":
            warnings.append(
                {
                    "claim_id": claim.get("claim_id"),
                    "reason": f"Medium-risk required claim is {status}",
                    "status": status,
                }
            )
        elif status == "CONTRADICTED":
            blocked.append(
                {
                    "claim_id": claim.get("claim_id"),
                    "reason": "Claim contradicted by evidence",
                    "status": status,
                    "exact_text": claim.get("exact_text"),
                }
            )

    if blocked:
        decision: GateDecision = "FACT_CHECK_BLOCKED"
    elif any(c.get("decision") == "REPAIR_OR_REMOVE" for c in claims):
        decision = "REPAIR_REQUIRED"
    elif warnings:
        decision = "WARN"
    else:
        decision = "PASS"

    coverage = {
        "required_total": len(required),
        "required_supported": len(supported),
        "required_verified_ratio": (f"{len(supported)}/{len(required)}" if required else "0/0"),
        "not_required": sum(1 for c in claims if not c.get("source_required")),
        "blocked": len(blocked),
        "warnings": len(warnings),
    }
    return {
        "decision": decision,
        "production_allowed": decision in {"PASS", "WARN"},
        "code": "FACT_CHECK_BLOCKED" if decision == "FACT_CHECK_BLOCKED" else decision,
        "blocked_claims": blocked,
        "warnings": warnings,
        "claim_coverage": coverage,
        "ready_for_production": decision == "PASS" or (decision == "WARN" and not blocked),
    }


def propose_repairs(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggest script repairs. Never invents replacement numbers."""
    repairs: list[dict[str, Any]] = []
    for claim in claims:
        status = claim.get("fact_check_status")
        if status not in {"NOT_FOUND", "CONTRADICTED", "UNVERIFIED"}:
            continue
        if not claim.get("source_required"):
            continue
        exact = str(claim.get("exact_text") or "")
        options: list[dict[str, Any]] = []
        if _NUMERIC.search(exact):
            options.append(
                {
                    "action": "REMOVE_NUMBER",
                    "detail": "Strip the unverified figure; do not substitute another number without evidence.",
                }
            )
            options.append(
                {
                    "action": "WEAKEN_CLAIM",
                    "detail": "Rephrase as recommendation without a specific figure.",
                }
            )
        elif status == "CONTRADICTED":
            options.append({"action": "REMOVE_CLAIM", "detail": "Evidence contradicts the claim; remove or rewrite after new evidence."})
            supported_snippet = None
            for evid in claim.get("evidence") or []:
                blocks = evid.get("blocks") or []
                if blocks:
                    supported_snippet = blocks[0].get("text")
                    break
            if supported_snippet:
                options.append(
                    {
                        "action": "ALIGN_TO_EVIDENCE",
                        "detail": "Paraphrase only what evidence supports; do not copy long quotes.",
                        "evidence_snippet": str(supported_snippet)[:240],
                    }
                )
        else:
            options.append({"action": "WEAKEN_CLAIM", "detail": "Convert to recommendation or remove factual framing."})
            options.append({"action": "REMOVE_CLAIM", "detail": "Delete the sentence from the script."})
        repairs.append(
            {
                "claim_id": claim.get("claim_id"),
                "before": exact,
                "after": None,
                "why": f"fact_check_status={status}",
                "options": options,
                "invented_number": False,
            }
        )
    return repairs


def brain_writes_from_ledger(claims: list[dict[str, Any]], *, project: str) -> list[dict[str, Any]]:
    """Only SUPPORTED claims with provenance may become verified observations."""
    rows: list[dict[str, Any]] = []
    for claim in claims:
        status = claim.get("fact_check_status")
        if status == "SUPPORTED":
            evid = (claim.get("evidence") or [None])[0] or {}
            rows.append(
                {
                    "kind": "VERIFIED_OBSERVATION",
                    "knowledge_type": "EVIDENCE",
                    "project": project,
                    "claim_id": claim.get("claim_id"),
                    "statement": claim.get("normalized_claim"),
                    "exact_text": claim.get("exact_text"),
                    "provenance": evid.get("provenance") or {},
                    "evidence_ids": claim.get("evidence_ids") or [],
                    "content_hash": evid.get("content_hash"),
                    "invented_value": False,
                }
            )
        elif claim.get("claim_type") == "HYPOTHESIS":
            rows.append(
                {
                    "kind": "HYPOTHESIS",
                    "knowledge_type": "HYPOTHESIS",
                    "project": project,
                    "claim_id": claim.get("claim_id"),
                    "statement": claim.get("normalized_claim"),
                    "invented_value": False,
                    "note": "Remains hypothesis — not project fact.",
                }
            )
        elif status in {"UNVERIFIED", "NOT_FOUND", "UNREVIEWED"}:
            # Explicitly do not promote.
            continue
    return rows


def build_ledger(
    *,
    project: str,
    production_id: str,
    script: dict[str, Any] | str,
    root: Path | None = None,
    client: Any = None,
    live_research: bool = True,
    extra_claims: list[dict[str, Any]] | None = None,
    hypothetical_intake: dict[str, Any] | None = None,
    calculation_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claims = extract_claims_from_script(script)
    if extra_claims:
        for item in extra_claims:
            if not isinstance(item, dict) or not item.get("exact_text"):
                continue
            classified = classify_claim_text(str(item["exact_text"]))
            claims.append(
                {
                    "claim_id": item.get("claim_id") or f"claim_x_{len(claims)+1:03d}",
                    "exact_text": item["exact_text"],
                    "normalized_claim": classified["normalized_claim"],
                    "script_location": item.get("script_location") or {"segment_id": "extra", "segment_type": "research"},
                    "claim_type": item.get("claim_type") or classified["claim_type"],
                    "risk": item.get("risk") or classified["risk"],
                    "source_required": item.get("source_required", classified["source_required"]),
                    "source_status": "NONE",
                    "fact_check_status": "UNREVIEWED" if classified["source_required"] or item.get("source_required") else "NOT_REQUIRED",
                    "evidence_ids": [],
                    "evidence": [],
                    "decision": "PENDING",
                    "brain_claim_class": classified["brain_claim_class"],
                    "measurement": classified.get("measurement"),
                    "attributed_institution": item.get("attributed_institution") or classified.get("attributed_institution"),
                    "updated_at": _now(),
                }
            )

    calculator_binding = None
    if hypothetical_intake is not None:
        from atnos.production.hypothetical_claim_evidence import verified_binding, bind_ledger_claims
        if not isinstance(script, dict):
            raise ValueError("A hypothetical ledger requires the current typed script")
        calculator_binding = verified_binding(script, hypothetical_intake, calculation_receipt)
        claims = bind_ledger_claims(claims, script, calculator_binding)

    researched: list[dict[str, Any]] = []
    for claim in claims:
        if claim.get("source_required"):
            if calculator_binding is not None:
                # This narrow intake provides mathematical assumptions, no external
                # sources. A current/historical assertion requires a different brief;
                # never call even a free research API or invent evidence here.
                claim = dict(claim)
                claim.update(fact_check_status="UNVERIFIED", decision="BLOCK", risk="HIGH")
                claim.setdefault("calculator_binding_rejected", "This hypothetical brief supplies no evidence for external facts")
                researched.append(claim)
            else:
                researched.append(research_claim(claim, root=root, client=client, live=live_research))
        else:
            claim = dict(claim)
            claim["fact_check_status"] = "NOT_REQUIRED"
            claim["decision"] = "ALLOW"
            researched.append(claim)

    gate = evaluate_gate(researched)
    repairs = propose_repairs(researched)
    brain = brain_writes_from_ledger(researched, project=project)
    ledger = {
        "project": project,
        "production_id": production_id,
        "created_at": _now(),
        "updated_at": _now(),
        "claims": researched,
        "gate": gate,
        "repairs": repairs,
        "brain_writes": brain,
        "claim_coverage": gate.get("claim_coverage"),
        "script_title": script.get("title") if isinstance(script, dict) else None,
    }
    from atnos.production.project_profile import content_hash
    ledger['script_payload_hash'] = content_hash(script)
    if calculator_binding is not None:
        ledger["calculator_binding"] = calculator_binding
    path = ledger_path(root, project, production_id)
    path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    ledger["ledger_path"] = str(path)
    return ledger


def load_ledger(root: Path | None, project: str, production_id: str) -> dict[str, Any] | None:
    path = ledger_path(root, project, production_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def assert_project_isolation(root: Path | None, project: str, foreign_project: str) -> bool:
    """True when foreign project ledger dir does not contain this project's files."""
    own = ledger_dir(root, project)
    foreign = ledger_dir(root, foreign_project)
    if not own.exists():
        return True
    for path in own.glob("*.json"):
        if (foreign / path.name).exists():
            # Same filename across projects is OK only if content project field differs — check.
            try:
                payload = json.loads((foreign / path.name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("project") == project:
                return False
    return True


def production_blocker_from_ledger(ledger: dict[str, Any] | None) -> dict[str, Any]:
    if not ledger:
        return {"blocked": False, "code": None, "detail": "No claim ledger yet."}
    claims = ledger.get("claims") if isinstance(ledger.get("claims"), list) else []
    # Re-evaluate so illustrative money-math unlocks without rewriting old ledgers.
    if claims:
        gate = evaluate_gate(claims)
    else:
        gate = ledger.get("gate") if isinstance(ledger.get("gate"), dict) else {}
    if gate.get("decision") == "FACT_CHECK_BLOCKED" or not gate.get("production_allowed", True):
        return {
            "blocked": True,
            "code": "FACT_CHECK_BLOCKED",
            "detail": "Required high-risk claims lack supporting evidence.",
            "blocked_claims": gate.get("blocked_claims") or [],
            "claim_coverage": gate.get("claim_coverage"),
        }
    return {
        "blocked": False,
        "code": gate.get("code"),
        "detail": "Claim evidence gate allows production to proceed.",
        "warnings": gate.get("warnings") or [],
        "claim_coverage": gate.get("claim_coverage"),
    }


MEDIA_STAGES_REQUIRING_CLAIM_GATE = frozenset(
    {
        "voice_generation",
        "captions",
        "rendering",
        "covers",
        "asset_planning",
        "package",
        "qa",
    }
)


def ensure_media_claim_gate(
    *,
    project: str,
    production_id: str,
    stage: str,
    script: dict[str, Any] | None = None,
    root: Path | None = None,
    hypothetical_intake: dict[str, Any] | None = None,
    calculation_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Block GPU/media stages when Wealth Blueprint claim gate fails.

    Returns {"blocked": bool, ...}. Does nothing for non-WB or non-media stages.
    """
    if project != "wealth_blueprint":
        return {"blocked": False, "skipped": True, "reason": "not_wealth_blueprint"}
    stage_key = stage.split("/")[0]
    if stage_key not in MEDIA_STAGES_REQUIRING_CLAIM_GATE and not stage_key.startswith(
        ("voice", "render", "remotion", "visual", "asset", "caption", "cover", "package")
    ):
        return {"blocked": False, "skipped": True, "reason": "not_media_stage"}

    # A supplied hypothetical always binds CURRENT script bytes to CURRENT frozen
    # assumptions. A stale ledger or a broad text heuristic cannot stand in for it.
    if hypothetical_intake is not None:
        try:
            if script is None:
                raise ValueError("Current script is missing for the supplied hypothetical example")
            ledger = build_ledger(project=project, production_id=production_id, script=script, root=root,
                live_research=False, hypothetical_intake=hypothetical_intake, calculation_receipt=calculation_receipt)
        except (ValueError, KeyError, TypeError) as exc:
            return {"blocked": True, "code": "FACT_CHECK_BLOCKED", "detail": str(exc),
                    "blocked_claims": [], "evidence_kind": "hypothetical_binding_failed"}
        return production_blocker_from_ledger(ledger)
    ledger = load_ledger(root, project, production_id)
    if ledger is None and script is not None:
        # Offline classify/gate only — research belongs to capability workflow, not GPU begin().
        ledger = build_ledger(
            project=project,
            production_id=production_id,
            script=script,
            root=root,
            live_research=False,
        )
    elif ledger is None:
        return {
            "blocked": True,
            "code": "FACT_CHECK_BLOCKED",
            "detail": "No claim ledger for this production; run claim evidence gate before media stages.",
            "blocked_claims": [],
        }
    blocker = production_blocker_from_ledger(ledger)
    if blocker.get("blocked"):
        logger.info(
            "media_blocked_by_claim_gate production_id=%s stage=%s claims=%s",
            production_id,
            stage,
            [item.get("claim_id") for item in blocker.get("blocked_claims") or []],
        )
    return blocker
