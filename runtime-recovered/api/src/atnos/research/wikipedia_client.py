from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from atnos.research.cache import get_cached, set_cached


def _api_base(language: str) -> str:
    return f"https://{language or 'pl'}.wikipedia.org"


def _strip_html(value: str) -> str:
    return " ".join(re.sub(r"<.*?>", " ", value).split())


async def search_wikipedia(query: str, *, limit: int = 5, language: str = "pl") -> list[dict[str, object]]:
    cache_key = f"wiki:search:{language}:{limit}:{query.casefold()}"
    cached = await get_cached(cache_key)
    if cached and isinstance(cached.get("results"), list):
        return list(cached["results"])
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": limit,
        "utf8": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{_api_base(language)}/w/api.php", params=params)
            response.raise_for_status()
    except httpx.HTTPError:
        return []
    raw = response.json().get("query", {}).get("search", [])
    results = [
        {
            "title": str(item.get("title") or ""),
            "snippet": _strip_html(str(item.get("snippet") or "")),
            "pageid": int(item.get("pageid") or 0),
        }
        for item in raw
        if isinstance(item, dict)
    ]
    await set_cached(cache_key, {"results": results}, ttl_hours=24)
    return results


async def get_article_summary(title: str, *, language: str = "pl") -> dict[str, object] | None:
    cache_key = f"wiki:summary:{language}:{title.casefold()}"
    cached = await get_cached(cache_key)
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{_api_base(language)}/api/rest_v1/page/summary/{quote(title)}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
    except httpx.HTTPError:
        return None
    data = response.json()
    result = {
        "title": str(data.get("title") or title),
        "description": str(data.get("description") or ""),
        "extract": str(data.get("extract") or "")[:700],
        "thumbnail_url": data.get("thumbnail", {}).get("source") if isinstance(data.get("thumbnail"), dict) else None,
        "url": data.get("content_urls", {}).get("desktop", {}).get("page") if isinstance(data.get("content_urls"), dict) else "",
    }
    await set_cached(cache_key, result, ttl_hours=24)
    return result


async def get_article_sections(
    title: str,
    *,
    language: str = "pl",
    max_sections: int = 5,
) -> list[dict[str, str]]:
    cache_key = f"wiki:sections:{language}:{max_sections}:{title.casefold()}"
    cached = await get_cached(cache_key)
    if cached and isinstance(cached.get("sections"), list):
        return list(cached["sections"])
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{_api_base(language)}/api/rest_v1/page/mobile-sections/{quote(title)}")
            if response.status_code == 404:
                return []
            response.raise_for_status()
    except httpx.HTTPError:
        return []
    data = response.json()
    sections_raw = data.get("remaining", {}).get("sections", []) if isinstance(data.get("remaining"), dict) else []
    sections: list[dict[str, str]] = []
    lead = data.get("lead", {})
    if isinstance(lead, dict) and lead.get("displaytitle"):
        sections.append({"title": _strip_html(str(lead.get("displaytitle"))), "text": _strip_html(str(lead.get("description") or ""))[:300]})
    for item in sections_raw:
        if not isinstance(item, dict):
            continue
        line = _strip_html(str(item.get("line") or ""))
        text = _strip_html(str(item.get("text") or ""))[:300]
        if line and text:
            sections.append({"title": line, "text": text})
        if len(sections) >= max_sections:
            break
    await set_cached(cache_key, {"sections": sections}, ttl_hours=24)
    return sections


async def get_facts_for_topic(topic: str, *, language: str = "pl", max_facts: int = 10) -> list[str]:
    results = await search_wikipedia(topic, limit=3, language=language)
    if not results:
        return []
    title = str(results[0].get("title") or topic)
    sections = await get_article_sections(title, language=language, max_sections=8)
    text = " ".join(section["text"] for section in sections if section.get("text"))
    sentences = re.split(r"(?<=[.!?])\s+", text)
    facts: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        cleaned = " ".join(sentence.split())
        key = cleaned.casefold()
        if len(cleaned) < 50 or len(cleaned) > 220 or key in seen:
            continue
        if not (re.search(r"\d", cleaned) or any(word in cleaned.casefold() for word in ("pierws", "naj", "wazn", "ważn", "klucz", "mili", "procent"))):
            continue
        seen.add(key)
        facts.append(cleaned)
        if len(facts) >= max_facts:
            break
    return facts
