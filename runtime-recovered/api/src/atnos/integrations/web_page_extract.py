"""WEB_PAGE_EXTRACT capability: Atnos safe fetch + Scrapling structured extraction.

Agent-facing name is WEB_PAGE_EXTRACT. Scrapling is an implementation detail.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from atnos.integrations import scrapling_bridge
from atnos.integrations.safe_web_fetch import fetch_public_html

logger = logging.getLogger("atnos.web_page_extract")

EXTRACTION_VERSION = scrapling_bridge.PINNED_VERSION


def extract_url(
    url: str,
    *,
    root: Any = None,
    client: httpx.Client | None = None,
    freshness: str = "REFERENCE",
    css: str | None = None,
    text: str | None = None,
    xpath: str | None = None,
    include_raw_diagnostics: bool = False,
    use_cache: bool = True,
    now: float | None = None,
) -> dict[str, Any]:
    fetched = fetch_public_html(
        url,
        root=root,
        client=client,
        freshness=freshness,
        use_cache=use_cache,
        now=now,
    )
    if not fetched.get("ok"):
        _log_exec(
            status=str(fetched.get("status")),
            hostname=_host(fetched.get("final_url") or url),
            http_status=fetched.get("http_status"),
            cache=fetched.get("cache"),
            duration_ms=fetched.get("duration_ms"),
            content_size=None,
            block_count=None,
        )
        return {
            "ok": False,
            "status": fetched.get("status") or "FAILED",
            "detail": fetched.get("detail"),
            "output": None,
            "provenance": _provenance_from_fetch(fetched, content_hash=None),
            "diagnostics": {
                "http_status": fetched.get("http_status"),
                "content_type": fetched.get("content_type"),
                "cache": fetched.get("cache"),
                "duration_ms": fetched.get("duration_ms"),
                "redirect_chain": fetched.get("redirect_chain") or [],
            },
        }

    parsed = scrapling_bridge.extract_structured(
        str(fetched.get("html") or ""),
        base_url=str(fetched.get("final_url") or url),
        css=css,
        text_query=text,
        xpath=xpath,
        include_raw_diagnostics=include_raw_diagnostics,
    )
    output = dict(parsed.get("output") or {}) if isinstance(parsed.get("output"), dict) else {}
    output.update(
        {
            "url": fetched.get("requested_url") or url,
            "final_url": fetched.get("final_url"),
            "http_status": fetched.get("http_status"),
            "content_type": fetched.get("content_type"),
            "provider": "scrapling",
            "extractor": "scrapling",
            "cache": fetched.get("cache"),
            "truncated": bool(fetched.get("truncated") or output.get("truncated")),
            "redirect_chain": fetched.get("redirect_chain") or [],
            "cross_domain_redirect": bool(fetched.get("cross_domain_redirect")),
            "fetched_at": fetched.get("fetched_at"),
            "raw_bytes": fetched.get("raw_bytes"),
        }
    )
    # Prefer normalized main-content hash when present; keep raw hash separately.
    content_hash = output.get("content_hash") or fetched.get("content_hash")
    status = str(parsed.get("status") or "FAILED")
    if fetched.get("truncated") and status == "SUCCESS":
        status = "TRUNCATED"
    ok = bool(parsed.get("ok")) and status in {"SUCCESS", "TRUNCATED"}
    provenance = _provenance_from_fetch(fetched, content_hash=content_hash)
    provenance["extractor"] = "scrapling"
    provenance["extraction_version"] = EXTRACTION_VERSION
    provenance["provider"] = "scrapling"

    quality = output.get("extraction_quality") if isinstance(output.get("extraction_quality"), dict) else {}
    _log_exec(
        status=status,
        hostname=str(fetched.get("hostname") or _host(fetched.get("final_url"))),
        http_status=fetched.get("http_status"),
        cache=fetched.get("cache"),
        duration_ms=(fetched.get("duration_ms") or 0) + (output.get("duration_ms") or 0),
        content_size=quality.get("extracted_chars") or fetched.get("raw_bytes"),
        block_count=quality.get("block_count"),
    )
    return {
        "ok": ok,
        "status": status,
        "detail": parsed.get("detail") if not ok else None,
        "output": output,
        "provenance": provenance,
        "diagnostics": {
            "http_status": fetched.get("http_status"),
            "content_type": fetched.get("content_type"),
            "cache": fetched.get("cache"),
            "duration_ms": (fetched.get("duration_ms") or 0) + (output.get("duration_ms") or 0),
            "redirect_chain": fetched.get("redirect_chain") or [],
            "cross_domain_redirect": bool(fetched.get("cross_domain_redirect")),
            "extraction_quality": quality,
            "browser": "OFF",
            "stealth": "OFF",
        },
    }


def extract_html(
    html: str,
    *,
    url: str | None = None,
    css: str | None = None,
    text: str | None = None,
    xpath: str | None = None,
    include_raw_diagnostics: bool = False,
) -> dict[str, Any]:
    parsed = scrapling_bridge.extract_structured(
        html,
        base_url=url,
        css=css,
        text_query=text,
        xpath=xpath,
        include_raw_diagnostics=include_raw_diagnostics,
    )
    output = dict(parsed.get("output") or {}) if isinstance(parsed.get("output"), dict) else {}
    if url:
        output["url"] = url
        output["final_url"] = url
    output["provider"] = "scrapling"
    output["extractor"] = "scrapling"
    status = str(parsed.get("status") or "FAILED")
    ok = bool(parsed.get("ok")) and status in {"SUCCESS", "TRUNCATED"}
    return {
        "ok": ok,
        "status": status,
        "detail": parsed.get("detail") if not ok else None,
        "output": output,
        "provenance": {
            "provider": "scrapling",
            "extractor": "scrapling",
            "extraction_version": EXTRACTION_VERSION,
            "source": url,
            "content_hash": output.get("content_hash"),
            "fetched_at": output.get("extracted_at"),
        },
        "diagnostics": {
            "extraction_quality": output.get("extraction_quality"),
            "browser": "OFF",
            "stealth": "OFF",
            "cache": "n/a",
        },
    }


def find_relevant_blocks(
    page: dict[str, Any],
    claim: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Rank page blocks by simple token overlap with the claim. No invented verdict."""
    output = page.get("output") if isinstance(page.get("output"), dict) else page
    blocks = output.get("blocks") if isinstance(output, dict) else None
    if not isinstance(blocks, list) or not claim.strip():
        return []
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "")
        if block.get("type") == "a" and len(text) < 40:
            continue
        overlap = claim_tokens & _tokens(text)
        if not overlap:
            continue
        score = len(overlap) / max(len(claim_tokens), 1)
        if score >= 0.2 or len(overlap) >= 2:
            scored.append((score, block))
    scored.sort(key=lambda item: (-item[0], item[1].get("id") or ""))
    return [
        {
            "block_id": item["id"],
            "type": item.get("type"),
            "text": item.get("text"),
            "score": round(score, 3),
            "href": item.get("href"),
        }
        for score, item in scored[:limit]
    ]


def fact_check_claim_against_url(
    claim: str,
    url: str,
    *,
    root: Any = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Extract page and map claim → concrete blocks. Verdict only from evidence."""
    page = extract_url(url, root=root, client=client, freshness="REFERENCE")
    if not page.get("ok") and page.get("status") not in {"TRUNCATED"}:
        return {
            "status": page.get("status") or "FAILED",
            "claim_status": "UNVERIFIED",
            "verdict": "NOT_FOUND",
            "invented_value": False,
            "claim": claim,
            "url": url,
            "blocks": [],
            "provenance": page.get("provenance"),
            "detail": page.get("detail"),
            "brain_class": "TEMP_RESEARCH",
        }
    blocks = find_relevant_blocks(page, claim)
    claim_tokens = _tokens(claim)
    if not blocks:
        verdict = "NOT_FOUND"
        claim_status = "UNVERIFIED"
    else:
        best = blocks[0]["score"]
        joined = " ".join(str(b.get("text") or "") for b in blocks).casefold()
        best_text = str(blocks[0].get("text") or "").casefold()
        # Lexical overlap is retrieval, not entailment. Never treat unverified
        # negation or a number missing from the matching block as support.
        # This is conservative: it does not invent a contradiction verdict.
        claim_negation = bool(re.search(r"\b(?:not|never|no|cannot|can't|isn't|aren't|doesn't|don't|without)\b", claim.casefold()))
        claim_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)*\b", claim))
        source_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)*\b", best_text))
        unverified_assertion = claim_negation or not claim_numbers.issubset(source_numbers)
        # Require strong negation cues — bare "not" is too common in financial prose.
        contradicted = bool(
            re.search(
                r"\b(not true|false|incorrect|myth|no evidence|debunked|contrary to)\b",
                joined,
            )
            and best >= 0.35
        )
        if unverified_assertion:
            verdict = "NOT_FOUND"
            claim_status = "UNVERIFIED"
        elif contradicted:
            verdict = "CONTRADICTED"
            claim_status = "CONTRADICTED"
        elif best >= 0.55 and len(claim_tokens & _tokens(blocks[0].get("text") or "")) >= max(2, len(claim_tokens) // 3):
            verdict = "SUPPORTED"
            claim_status = "SUPPORTED"
        elif best >= 0.25:
            verdict = "PARTIALLY_SUPPORTED"
            claim_status = "PARTIALLY_SUPPORTED"
        else:
            verdict = "NOT_FOUND"
            claim_status = "UNVERIFIED"

    return {
        "status": "SUCCESS" if page.get("ok") or page.get("status") == "TRUNCATED" else page.get("status"),
        "claim_status": claim_status,
        "verdict": verdict,
        "invented_value": False,
        "claim": claim,
        "url": page.get("output", {}).get("url") if isinstance(page.get("output"), dict) else url,
        "final_url": page.get("output", {}).get("final_url") if isinstance(page.get("output"), dict) else url,
        "blocks": blocks,
        "block_ids": [b["block_id"] for b in blocks],
        "page_title": (page.get("output") or {}).get("title") if isinstance(page.get("output"), dict) else None,
        "content_hash": (page.get("provenance") or {}).get("content_hash"),
        "provenance": page.get("provenance"),
        "extraction_quality": (page.get("diagnostics") or {}).get("extraction_quality"),
        "source_quality": "UNSCORED",  # Authority is a research concern, not extraction.
        "brain_class": "EVIDENCE" if blocks and verdict in {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED"} else "TEMP_RESEARCH",
        "provider": "scrapling",
        "capability": "WEB_PAGE_EXTRACT",
    }


def brain_record_from_extract(
    result: dict[str, Any],
    *,
    project: str,
    brain_class: str | None = None,
    claim: str | None = None,
) -> dict[str, Any] | None:
    """Project Brain payload. Never store full page dump as reusable knowledge by default."""
    output = result.get("output") if isinstance(result.get("output"), dict) else None
    if not output:
        return None
    klass = brain_class or "TEMP_RESEARCH"
    if klass == "REUSABLE_KNOWLEDGE":
        # Only curated fragments, never full page_text.
        statement = {
            "title": output.get("title"),
            "selected_blocks": (output.get("blocks") or [])[:5],
            "content_hash": output.get("content_hash"),
        }
    elif klass == "EVIDENCE":
        statement = {
            "title": output.get("title"),
            "claim": claim,
            "block_ids": [b.get("id") for b in (output.get("blocks") or [])[:10] if isinstance(b, dict)],
            "snippets": [b.get("text") for b in (output.get("blocks") or [])[:5] if isinstance(b, dict)],
            "content_hash": output.get("content_hash"),
        }
    else:
        statement = {
            "title": output.get("title"),
            "final_url": output.get("final_url"),
            "content_hash": output.get("content_hash"),
            "block_count": (output.get("extraction_quality") or {}).get("block_count"),
            "note": "TEMP_RESEARCH — full page not persisted",
        }
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    return {
        "kind": "WEB_PAGE_EVIDENCE",
        "project": project,
        "use_class": klass,
        "source": "WEB_PAGE_EXTRACT",
        "statement": statement,
        "topic": "web_page",
        "provider": "scrapling",
        "capability": "WEB_PAGE_EXTRACT",
        "fetched_at": provenance.get("fetched_at") or output.get("fetched_at") or output.get("extracted_at"),
        "source_url": provenance.get("final_url") or provenance.get("source") or output.get("final_url"),
        "requested_url": provenance.get("requested_url") or output.get("url"),
        "content_hash": provenance.get("content_hash") or output.get("content_hash"),
        "extraction_version": provenance.get("extraction_version") or EXTRACTION_VERSION,
        "confidence": "source_snapshot",
        "invented_value": False,
        "full_page_stored": False,
    }


def _provenance_from_fetch(fetched: dict[str, Any], *, content_hash: str | None) -> dict[str, Any]:
    return {
        "provider": "scrapling",
        "extractor": "scrapling",
        "extraction_version": EXTRACTION_VERSION,
        "source": fetched.get("final_url") or fetched.get("requested_url"),
        "requested_url": fetched.get("requested_url"),
        "final_url": fetched.get("final_url"),
        "redirect_chain": fetched.get("redirect_chain") or [],
        "cross_domain_redirect": bool(fetched.get("cross_domain_redirect")),
        "fetched_at": fetched.get("fetched_at"),
        "http_status": fetched.get("http_status"),
        "content_type": fetched.get("content_type"),
        "content_hash": content_hash or fetched.get("content_hash"),
        "user_agent": fetched.get("user_agent"),
        "cache": fetched.get("cache"),
    }


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9%]{3,}", text.casefold()) if tok not in _STOP}


_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "with",
        "from",
        "this",
        "are",
        "was",
        "were",
        "have",
        "has",
        "you",
        "your",
        "our",
        "their",
        "about",
        "into",
        "over",
        "under",
        "than",
        "then",
        "also",
        "can",
        "may",
        "will",
        "http",
        "https",
        "www",
    }
)


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        return urlparse(str(url)).hostname
    except Exception:
        return None


def _log_exec(
    *,
    status: str,
    hostname: str | None,
    http_status: Any,
    cache: Any,
    duration_ms: Any,
    content_size: Any,
    block_count: Any,
) -> None:
    logger.info(
        "WEB_PAGE_EXTRACT status=%s host=%s http=%s cache=%s duration_ms=%s content_size=%s blocks=%s",
        status,
        hostname,
        http_status,
        cache,
        duration_ms,
        content_size,
        block_count,
    )
