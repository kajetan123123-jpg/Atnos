from __future__ import annotations

import asyncio
import html
import re
import subprocess
import sys
from urllib.parse import urlparse

import httpx

from atnos.research.cache import get_cached, set_cached
from atnos.research.config import load_research_config


def _source_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except ValueError:
        return ""


def _clean_html(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<.*?>", " ", value)).split())


async def web_search(
    query: str,
    *,
    count: int = 10,
    country: str = "pl",
    language: str = "pl",
    search_type: str = "web",
) -> list[dict[str, str | None]]:
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return []
    cache_key = f"search:{search_type}:{country}:{language}:{count}:{normalized_query.casefold()}"
    ttl = 2 if search_type == "news" else 6
    cached = await get_cached(cache_key)
    if cached and isinstance(cached.get("results"), list) and cached["results"]:
        return list(cached["results"])

    config = load_research_config()
    results: list[dict[str, str | None]] = []
    if config.get("serpapi_key"):
        try:
            results = await _search_serpapi(
                normalized_query,
                count=count,
                country=country,
                language=language,
                search_type=search_type,
                api_key=str(config["serpapi_key"]),
            )
        except httpx.HTTPError:
            results = []
    if not results and config.get("brave_search_key"):
        try:
            results = await _search_brave(
                normalized_query,
                count=count,
                country=country,
                language=language,
                search_type=search_type,
                api_key=str(config["brave_search_key"]),
            )
        except httpx.HTTPError:
            results = []
    if not results:
        results = await _search_duckduckgo_html(normalized_query, count=count)

    await set_cached(cache_key, {"results": results}, ttl_hours=ttl)
    return results


async def _search_serpapi(
    query: str,
    *,
    count: int,
    country: str,
    language: str,
    search_type: str,
    api_key: str,
) -> list[dict[str, str | None]]:
    params = {
        "engine": "google",
        "q": query,
        "num": count,
        "gl": country,
        "hl": language,
        "api_key": api_key,
    }
    if search_type == "news":
        params["tbm"] = "nws"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("https://serpapi.com/search", params=params)
        response.raise_for_status()
    data = response.json()
    raw = data.get("news_results") if search_type == "news" else data.get("organic_results")
    if not isinstance(raw, list):
        raw = []
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("link") or ""),
            "snippet": str(item.get("snippet") or ""),
            "source": str(item.get("source") or _source_from_url(str(item.get("link") or ""))),
            "date": item.get("date") if isinstance(item.get("date"), str) else None,
        }
        for item in raw[:count]
        if isinstance(item, dict)
    ]


async def _search_brave(
    query: str,
    *,
    count: int,
    country: str,
    language: str,
    search_type: str,
    api_key: str,
) -> list[dict[str, str | None]]:
    endpoint = "https://api.search.brave.com/res/v1/news/search" if search_type == "news" else "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    params = {"q": query, "count": count, "country": country.upper(), "search_lang": language}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(endpoint, headers=headers, params=params)
        response.raise_for_status()
    data = response.json()
    raw = data.get("results") if search_type == "news" else data.get("web", {}).get("results")
    if not isinstance(raw, list):
        raw = []
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or ""),
            "source": str(item.get("meta_url", {}).get("hostname") or _source_from_url(str(item.get("url") or ""))),
            "date": item.get("age") if isinstance(item.get("age"), str) else None,
        }
        for item in raw[:count]
        if isinstance(item, dict)
    ]


async def _search_duckduckgo_html(query: str, *, count: int) -> list[dict[str, str | None]]:
    library_results = await _search_duckduckgo_library(query, count=count)
    if library_results:
        return library_results

    url = "https://duckduckgo.com/html/"
    params = {"q": query}
    try:
        async with httpx.AsyncClient(timeout=10, headers={"user-agent": "AtnosResearch/1.0"}) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
    except httpx.HTTPError:
        return []
    results: list[dict[str, str | None]] = []
    blocks = re.findall(r'<a rel="nofollow" class="result__a" href="(.*?)">(.*?)</a>.*?<a class="result__snippet".*?>(.*?)</a>', response.text, flags=re.DOTALL | re.IGNORECASE)
    for href, title, snippet in blocks[:count]:
        clean_url = html.unescape(href)
        results.append(
            {
                "title": _clean_html(title),
                "url": clean_url,
                "snippet": _clean_html(snippet),
                "source": _source_from_url(clean_url),
                "date": None,
            }
        )
    return results


async def _search_duckduckgo_library(query: str, *, count: int) -> list[dict[str, str | None]]:
    """Fallback przez bibliotekę DDG, gdy HTML endpoint jest zablokowany lub zmieni markup."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "duckduckgo-search", "--break-system-packages", "-q"],
            )
            from duckduckgo_search import DDGS
        except Exception:
            return []

    def _sync_search() -> list[dict[str, str | None]]:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=count))
        return [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("href") or ""),
                "snippet": str(item.get("body") or ""),
                "source": _source_from_url(str(item.get("href") or "")),
                "date": None,
            }
            for item in raw[:count]
            if isinstance(item, dict)
        ]

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _sync_search)
    except Exception:
        return []


async def get_trending_news(
    topic: str,
    *,
    language: str = "pl",
    hours_back: int = 48,
    count: int = 5,
) -> list[dict[str, str | None]]:
    _ = hours_back
    return await web_search(topic, count=count, language=language, search_type="news")
