"""Plan required capabilities from a user task — never from repo names."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CapabilityRequirement:
    capability: str
    required: bool = True
    optional: bool = False
    required_data: list[str] = field(default_factory=list)
    fallback_allowed: bool = False
    approval_required: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_required_capabilities(
    *,
    task_type: str,
    prompt: str,
    context: dict[str, Any] | None = None,
) -> list[CapabilityRequirement]:
    """Infer capability needs from the task. Repo/skill names are never planned."""
    context = context or {}
    text = f"{task_type}\n{prompt}\n{context.get('intent') or ''}".casefold()
    requirements: list[CapabilityRequirement] = []

    wants_script_qa = any(
        token in text
        for token in (
            "script qa",
            "script review",
            "review the current",
            "review script",
            "production brief",
            "packaging",
            "improve packaging",
            "qa skrypt",
        )
    )
    wants_packaging = any(
        token in text
        for token in (
            "packaging",
            "thumbnail",
            "title concept",
            "tytuł",
            "production brief",
            "hook alignment",
        )
    )
    wants_retention = any(token in text for token in ("retention", "retencj", "drop-off", "audience retention"))
    wants_social = any(
        token in text
        for token in ("social performance", "channel analytics", "social read", "konto social", "social data")
    )
    wants_discovery = any(
        token in text
        for token in (
            "find tool",
            "discover resource",
            "external library",
            "awesome",
            "potrzebuję narzędzia",
            "find a library",
        )
    )
    wants_animation = any(token in text for token in ("opentoonz", "2d animation", "animation_2d", "render 2d scene"))
    wants_scrape = any(
        token in text
        for token in (
            "scrape",
            "scraping",
            "extract from html",
            "extract from url",
            "parse html",
            "web page extract",
            "wyciągnij z strony",
            "pobierz html",
        )
    )

    if wants_script_qa or wants_packaging:
        requirements.append(
            CapabilityRequirement(
                capability="YOUTUBE_SCRIPT_QA",
                required=True,
                optional=False,
                required_data=["script", "script_path"],
                fallback_allowed=False,
                reason="Script quality must be inspected before packaging or brief updates.",
            )
        )
        requirements.append(
            CapabilityRequirement(
                capability="YOUTUBE_PACKAGING",
                required=True,
                optional=False,
                required_data=["script", "script_path"],
                fallback_allowed=False,
                reason="Packaging candidates are part of the production brief.",
            )
        )

    # Optional performance signals — never invent when missing.
    if wants_retention or (wants_script_qa and "retention" in text):
        requirements.append(
            CapabilityRequirement(
                capability="YOUTUBE_RETENTION_ANALYSIS",
                required=False,
                optional=True,
                required_data=["retention_export", "rows"],
                fallback_allowed=False,
                reason="Observed retention improves pacing recommendations when available.",
            )
        )
    elif wants_packaging or wants_script_qa:
        # Packaging/brief tasks benefit from retention if present, but must not fail without it.
        requirements.append(
            CapabilityRequirement(
                capability="YOUTUBE_RETENTION_ANALYSIS",
                required=False,
                optional=True,
                required_data=["retention_export", "rows"],
                fallback_allowed=False,
                reason="Optional: skip when no observed retention export exists.",
            )
        )

    if wants_social or wants_packaging or wants_script_qa:
        requirements.append(
            CapabilityRequirement(
                capability="SOCIAL_READ",
                required=False,
                optional=True,
                required_data=["connected_account"],
                fallback_allowed=False,
                reason="Optional live channel context; REQUIRES_SETUP must not invent metrics.",
            )
        )

    if wants_discovery:
        requirements.append(
            CapabilityRequirement(
                capability="RESOURCE_DISCOVERY",
                required=False,
                optional=True,
                required_data=["query"],
                fallback_allowed=False,
                reason="External resources only when the task explicitly needs a tool/library.",
            )
        )

    if wants_animation:
        requirements.append(
            CapabilityRequirement(
                capability="ANIMATION_2D_RENDER",
                required=False,
                optional=True,
                required_data=["scene"],
                fallback_allowed=False,
                reason="2D render only when explicitly requested; NOT_INSTALLED is not remapped to Remotion.",
            )
        )

    if wants_scrape:
        requirements.append(
            CapabilityRequirement(
                capability="WEB_PAGE_EXTRACT",
                required=True,
                optional=False,
                required_data=["html", "url"],
                fallback_allowed=False,
                reason="Public HTML extract via Scrapling parser; no stealth fetchers.",
            )
        )

    # Explicit capability list from context (already capability IDs, not repos).
    explicit = context.get("required_capabilities")
    if isinstance(explicit, list):
        known = {item.capability for item in requirements}
        for item in explicit:
            if isinstance(item, str) and item and item not in known:
                requirements.append(
                    CapabilityRequirement(
                        capability=item,
                        required=True,
                        optional=False,
                        reason="Explicitly requested by planner context.",
                    )
                )
            elif isinstance(item, dict) and item.get("capability"):
                cap = str(item["capability"])
                if cap not in known:
                    requirements.append(
                        CapabilityRequirement(
                            capability=cap,
                            required=bool(item.get("required", True)),
                            optional=bool(item.get("optional", not item.get("required", True))),
                            required_data=list(item.get("required_data") or []),
                            fallback_allowed=bool(item.get("fallback_allowed", False)),
                            approval_required=bool(item.get("approval_required", False)),
                            reason=str(item.get("reason") or "Explicit requirement."),
                        )
                    )

    return _dedupe(requirements)


def plan_fact_check_followups(script_qa_output: dict[str, Any] | None) -> list[CapabilityRequirement]:
    """Bridge unsupported claims to public-data capabilities without naming providers."""
    if not isinstance(script_qa_output, dict):
        return []
    claims = script_qa_output.get("unsupported_claims") or []
    followups: list[CapabilityRequirement] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text") or claim.get("claim") or "")
        lowered = text.casefold()
        if not lowered:
            continue
        source_url = claim.get("source_url") or claim.get("url")
        if isinstance(source_url, str) and source_url.startswith(("http://", "https://")):
            followups.append(
                CapabilityRequirement(
                    capability="WEB_PAGE_EXTRACT",
                    required=False,
                    optional=True,
                    required_data=["url"],
                    fallback_allowed=False,
                    reason=f"Claim needs page evidence from URL: {text[:120]} | {source_url}",
                )
            )
            continue
        url_in_text = re.search(r"https?://[^\s)\]\"']+", text)
        if url_in_text:
            followups.append(
                CapabilityRequirement(
                    capability="WEB_PAGE_EXTRACT",
                    required=False,
                    optional=True,
                    required_data=["url"],
                    fallback_allowed=False,
                    reason=f"Claim needs page evidence: {text[:160]}",
                )
            )
            continue
        if re.search(r"\b(gdp|inflation|cpi|unemployment|indicator)\b", lowered):
            followups.append(
                CapabilityRequirement(
                    capability="MACRO_INDICATORS",
                    required=False,
                    optional=True,
                    required_data=["indicator"],
                    fallback_allowed=False,
                    reason=f"Claim needs macro confirmation: {text[:120]}",
                )
            )
        elif re.search(r"\b(fx|exchange rate|usd|eur|currency|kurs)\b", lowered):
            followups.append(
                CapabilityRequirement(
                    capability="FX_RATES",
                    required=False,
                    optional=True,
                    required_data=["base", "quote"],
                    fallback_allowed=False,
                    reason=f"Claim needs FX confirmation: {text[:120]}",
                )
            )
        elif re.search(r"\b(sec|10-k|10-q|filing|edgar)\b", lowered):
            followups.append(
                CapabilityRequirement(
                    capability="SEC_FILINGS",
                    required=False,
                    optional=True,
                    required_data=["ticker"],
                    fallback_allowed=False,
                    reason=f"Claim needs SEC confirmation: {text[:120]}",
                )
            )
    return _dedupe(followups)


def _dedupe(items: list[CapabilityRequirement]) -> list[CapabilityRequirement]:
    seen: set[str] = set()
    out: list[CapabilityRequirement] = []
    for item in items:
        if item.capability in seen:
            continue
        seen.add(item.capability)
        out.append(item)
    return out
