"""Deterministic capability routing. Provider choice does not ask an LLM."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Callable

from atnos.integrations.public_data import (
    CAPABILITY_ALIASES,
    last_health,
    list_public_data,
    load_public_state,
    query_capability,
    record_event,
    remember_use,
)
from atnos.integrations.wikimedia_identity import read_identity

_FRESH_OK = {
    "CURRENT": {"realtime", "delayed"},
    "RECENT": {"realtime", "delayed", "historical"},
    "HISTORICAL": {"historical", "delayed", "reference"},
    "REFERENCE": {"reference", "historical"},
}
_KNOWN_CIKS = {"apple": "320193"}
Invoke = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def canonical_capability(capability: str) -> str:
    return CAPABILITY_ALIASES.get(capability, capability)


def classify_intent(text: str) -> tuple[str, dict[str, Any], str | None]:
    """Map a plain request to a capability. Returns capability, params, freshness."""
    raw = text.strip()
    lowered = raw.lower()
    if re.search(r"\b(10-k|10k|filing|filings|sec)\b", lowered):
        params: dict[str, Any] = {}
        for name, cik in _KNOWN_CIKS.items():
            if name in lowered:
                params["cik"] = cik
                break
        return "SEC_FILINGS", params, "HISTORICAL"
    pair = re.search(r"\b([A-Za-z]{3})\s*/\s*([A-Za-z]{3})\b", raw)
    if pair and re.search(r"\b(kurs|exchange|rate|fx)\b", lowered):
        return "FX_RATES", {"base": pair.group(1).upper(), "quote": pair.group(2).upper()}, "CURRENT"
    if re.search(r"\b(gdp|pkb|gross domestic product|inflation|inflacja|cpi)\b", lowered):
        indicator = "FP.CPI.TOTL.ZG" if re.search(r"\b(inflation|inflacja|cpi)\b", lowered) else "NY.GDP.MKTP.CD"
        country = ""
        if re.search(r"\b(poland|polska|polski|polskie)\b", lowered):
            country = "PL"
        elif re.search(r"\b(united states|usa|u\.s\.|\bus\b)\b", lowered):
            country = "US"
        params = {"indicator": indicator}
        if country:
            params["country"] = country
        year = re.search(r"\b(?:19|20)\d{2}\b", raw)
        if year:
            params["year"] = year.group(0)
        return "MACRO_INDICATORS", params, "HISTORICAL"
    if re.search(r"\b(what is|what's|czym jest|encykloped)\b", lowered):
        title = re.sub(r"(?i)^(what is|what's|czym jest)\s+", "", raw).strip(" ?.!")
        return "ENCYCLOPEDIC_KNOWLEDGE", {"title": title}, "REFERENCE"
    if re.search(
        r"\b(awesome|find (a )?(library|skill|repo|framework|tool)|discover (resource|library|tool)|katalog (narz|skill|repo))\b",
        lowered,
    ):
        query = re.sub(
            r"(?i)\b(find|discover|awesome|a|an|the|library|skill|repo|framework|tool|resource|katalog|narzędzi|skills?|repozytori\w*)\b",
            " ",
            raw,
        )
        query = re.sub(r"\s+", " ", query).strip(" ?.!")
        return "RESOURCE_DISCOVERY", {"query": query or raw}, "REFERENCE"
    if re.search(r"\b(scrape|scraping|extract (from )?(html|page|url)|parse html|web page extract)\b", lowered):
        return "WEB_PAGE_EXTRACT", {}, "CURRENT"
    return "", {}, None


def _awesome_provider() -> dict[str, Any]:
    """Provider descriptor for select_providers when Awesome is injected explicitly."""
    return {
        "id": "awesome",
        "name": "Awesome",
        "enabled": True,
        "capabilities": ["RESOURCE_DISCOVERY"],
        "cost": "FREE",
        "freshness": "reference",
        "priority": 20,
    }


def _providers(root: Path | None, extra: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = list_public_data(root)
    if extra:
        rows.extend(extra)
    return rows


def _needs_setup(provider: dict[str, Any], root: Path | None) -> bool:
    if provider.get("id") != "wikimedia":
        return False
    identity = read_identity(load_public_state(root))
    return not identity["contact"]


def select_providers(
    capability: str,
    *,
    root: Path | None = None,
    project_id: str | None = None,
    freshness: str | None = None,
    preferred_provider: str | None = None,
    health: dict[str, str] | None = None,
    extra_providers: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    wanted = canonical_capability(capability)
    state = load_public_state(root)
    allow = None
    project_map = state.get("project_providers") if isinstance(state.get("project_providers"), dict) else {}
    if project_id and project_id in project_map and isinstance(project_map[project_id], list):
        allow = {str(item) for item in project_map[project_id]}
    fresh_ok = _FRESH_OK.get(freshness or "", None)
    setup_blocked: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for provider in _providers(root, extra_providers):
        names = {canonical_capability(item) for item in provider.get("capabilities") or []}
        if wanted not in names and capability not in (provider.get("capabilities") or []):
            continue
        if allow is not None and provider["id"] not in allow:
            continue
        if not provider.get("enabled", True):
            continue
        if _needs_setup(provider, root):
            setup_blocked.append(provider)
            continue
        status = (health or {}).get(provider["id"]) or last_health(provider["id"])
        if status in {"DISABLED", "REQUIRES_SETUP"}:
            continue
        eligible.append(provider)
    if not eligible:
        return [], setup_blocked, "no_eligible_provider"

    def sort_key(provider: dict[str, Any]) -> tuple:
        status = (health or {}).get(provider["id"]) or last_health(provider["id"]) or ""
        offline = 1 if status == "OFFLINE" else 0
        preferred = 0 if preferred_provider and provider["id"] == preferred_provider else 1
        paid = 0 if provider.get("cost") == "FREE" else 1 if provider.get("cost") == "FREE_TIER" else 2
        fit = 0
        if fresh_ok is not None and provider.get("freshness") not in fresh_ok:
            fit = 1
        return (offline, preferred, paid, fit, int(provider.get("priority") or 100), provider["id"])

    ranked = sorted(eligible, key=sort_key)
    healthy = [item for item in ranked if ((health or {}).get(item["id"]) or last_health(item["id"])) != "OFFLINE"]
    chosen = healthy or ranked
    reason = "capability_match,enabled,free_before_paid"
    if freshness:
        reason += f",freshness_{freshness.lower()}"
    if preferred_provider and chosen and chosen[0]["id"] == preferred_provider:
        reason += ",preferred_provider"
    return chosen, setup_blocked, reason


def brain_source(result: dict[str, Any]) -> dict[str, Any]:
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    return {
        "source_type": "public_data",
        "provider": result.get("provider"),
        "capability": result.get("capability"),
        "observation": result.get("value"),
        "fetched_at": provenance.get("fetched_at"),
        "source": provenance.get("source"),
        "observation_date": provenance.get("observation_date"),
        "confidence": "source_data",
    }


def _retryable(result: dict[str, Any]) -> bool:
    if result.get("status") == "OFFLINE":
        return True
    http_status = (result.get("diagnostics") or {}).get("http_status")
    return result.get("status") == "DEGRADED" and isinstance(http_status, int) and http_status >= 500


def route_data_need(
    *,
    intent: str | None = None,
    capability: str | None = None,
    parameters: dict[str, Any] | None = None,
    project_id: str | None = None,
    freshness: str | None = None,
    preferred_provider: str | None = None,
    fallback_allowed: bool = True,
    root: Path | None = None,
    client: Any = None,
    health: dict[str, str] | None = None,
    extra_providers: list[dict[str, Any]] | None = None,
    invoke: Invoke | None = None,
    execute: bool = True,
) -> dict[str, Any]:
    params = dict(parameters or {})
    resolved = capability
    inferred_freshness = freshness
    if not resolved and intent:
        resolved, inferred, inferred_need = classify_intent(intent)
        inferred_freshness = inferred_freshness or inferred_need
        for key, value in inferred.items():
            params.setdefault(key, value)
    if not resolved:
        return {"status": "ERROR", "detail": "Nie rozpoznano capability.", "routing": {"events": []}}

    if resolved == "RESOURCE_DISCOVERY":
        from atnos.integrations.external_repositories import discover_resources

        request_id = uuid.uuid4().hex[:12]
        routing = {
            "request_id": request_id,
            "requested_capability": resolved,
            "candidates": ["awesome"],
            "selected_provider": "awesome",
            "reason": "discovery_catalog",
            "fallback": None,
            "fallback_used": False,
            "events": [],
            "freshness_requirement": inferred_freshness,
        }
        if not execute:
            return {"status": "SELECTED", "capability": resolved, "routing": routing}
        discovered = discover_resources(str(params.get("query") or ""), root=root)
        result = {
            **discovered,
            "provider": "awesome",
            "routing": routing,
            "brain_provenance": {
                "source_type": "discovery_catalog",
                "provider": "awesome",
                "capability": "RESOURCE_DISCOVERY",
                "observation": discovered.get("items"),
                "confidence": "discovered_not_trusted",
                "trust": "DISCOVERED",
            },
        }
        record_event(
            root,
            {
                "event": "route",
                "request_id": request_id,
                "capability": resolved,
                "selected": "awesome",
                "health": discovered.get("status"),
                "reason": "discovery_catalog",
                "fallback": False,
            },
        )
        return result

    ranked, setup_blocked, reason = select_providers(
        resolved,
        root=root,
        project_id=project_id,
        freshness=inferred_freshness,
        preferred_provider=preferred_provider,
        health=health,
        extra_providers=extra_providers,
    )
    request_id = uuid.uuid4().hex[:12]
    routing = {
        "request_id": request_id,
        "requested_capability": resolved,
        "candidates": [item["id"] for item in ranked],
        "selected_provider": ranked[0]["id"] if ranked else None,
        "reason": reason,
        "fallback": None,
        "fallback_used": False,
        "events": [],
        "freshness_requirement": inferred_freshness,
    }
    if not ranked:
        status_name = "NO_AVAILABLE_PROVIDER"
        detail = "Wikimedia requires setup." if setup_blocked else "Żaden włączony provider nie obsługuje tej capability."
        record_event(root, {"event": "route", "request_id": request_id, "capability": resolved, "selected": None, "reason": status_name, "fallback": False})
        return {"status": status_name, "capability": resolved, "detail": detail, "routing": routing}
    if not execute:
        record_event(root, {"event": "route_preview", "request_id": request_id, "capability": resolved, "selected": ranked[0]["id"], "reason": reason, "fallback": False})
        return {"status": "SELECTED", "capability": resolved, "routing": routing}

    def default_invoke(provider_id: str, cap: str, call_params: dict[str, Any]) -> dict[str, Any]:
        return query_capability(cap, call_params, client=client, root=root)

    call = invoke or default_invoke
    result = call(ranked[0]["id"], resolved, params)
    result = dict(result)
    result["routing"] = routing
    result["brain_provenance"] = brain_source(result)
    if fallback_allowed and _retryable(result) and len(ranked) > 1:
        routing["events"].append("PRIMARY_PROVIDER_FAILED")
        fallback = call(ranked[1]["id"], resolved, params)
        routing["events"].append("FALLBACK_PROVIDER_USED")
        routing["fallback"] = ranked[1]["id"]
        routing["fallback_used"] = True
        fallback = dict(fallback)
        fallback["routing"] = routing
        fallback["brain_provenance"] = brain_source(fallback)
        fallback["brain_provenance"]["confidence"] = "fallback_source"
        fallback["brain_provenance"]["fallback_used"] = True
        record_event(
            root,
            {
                "event": "route",
                "request_id": request_id,
                "capability": resolved,
                "selected": ranked[1]["id"],
                "health": fallback.get("status"),
                "cache": fallback.get("cache"),
                "reason": "FALLBACK_PROVIDER_USED",
                "fallback": True,
                "primary": ranked[0]["id"],
            },
        )
        remember_use(root, ranked[1]["id"], str(fallback.get("status") or ""))
        return fallback
    remember_use(root, ranked[0]["id"], str(result.get("status") or ""))
    record_event(
        root,
        {
            "event": "route",
            "request_id": request_id,
            "capability": resolved,
            "selected": ranked[0]["id"],
            "health": result.get("status"),
            "cache": result.get("cache"),
            "reason": reason,
            "fallback": False,
        },
    )
    return result
