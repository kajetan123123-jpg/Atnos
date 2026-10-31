from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher
from threading import Thread
from typing import Any

from atnos.research.cache import get_cached, set_cached
from atnos.research.search_client import get_trending_news, web_search
from atnos.research.trends_client import get_related_queries, get_trending_topics, score_topic_by_trends
from atnos.research.wikipedia_client import get_article_summary, get_facts_for_topic, search_wikipedia


def _dedupe_key(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).replace("_", " ").strip()


def _is_similar_to_history(topic: str, history: list[str]) -> bool:
    key = _dedupe_key(topic)
    for item in history:
        other = _dedupe_key(item)
        if not other:
            continue
        if key in other or other in key:
            return True
        if SequenceMatcher(None, key, other).ratio() >= 0.7:
            return True
    return False


def _topic_related_to_niche(topic: str, niche: str) -> bool:
    topic_tokens = set(_dedupe_key(topic).split())
    niche_tokens = {token for token in _dedupe_key(niche).split() if len(token) >= 4}
    if len(topic_tokens) >= 3 and not niche_tokens:
        return True
    return bool(topic_tokens & niche_tokens) or len(topic_tokens) >= 4


async def find_best_topics(
    niche: str,
    *,
    geo: str = "PL",
    language: str = "pl",
    topic_history: list[str] | None = None,
    count: int = 5,
) -> list[dict[str, Any]]:
    history = topic_history or []
    trends_task = get_trending_topics(geo=geo, language=language, count=20)
    news_task = web_search(f"{niche} trendy tematy shorts", count=8, country=geo.lower(), language=language, search_type="news")
    search_task = web_search(f"{niche} YouTube Shorts tematy", count=8, country=geo.lower(), language=language)
    trends, news, search_results = await asyncio.gather(trends_task, news_task, search_task)

    candidates: list[dict[str, Any]] = []
    for item in trends:
        topic = str(item.get("topic") or "").strip()
        if topic:
            candidates.append({"topic": topic, "source": "trending", "snippet": None})
    for source, rows in (("news", news), ("search", search_results)):
        for item in rows:
            title = str(item.get("title") or "").strip()
            if title:
                candidates.append(
                    {
                        "topic": title,
                        "source": source,
                        "snippet": item.get("snippet"),
                    }
                )

    seen: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for item in candidates:
        topic = str(item["topic"]).strip()
        key = _dedupe_key(topic)
        if not key or key in seen:
            continue
        if _is_similar_to_history(topic, history):
            continue
        if not _topic_related_to_niche(topic, niche):
            continue
        seen.add(key)
        filtered.append(item)
        if len(filtered) >= max(10, count * 2):
            break

    score_tasks = [score_topic_by_trends(str(item["topic"]), geo=geo) for item in filtered]
    scores = await asyncio.gather(*score_tasks) if score_tasks else []
    for item, score in zip(filtered, scores, strict=False):
        item["trends_score"] = float(score)
    ranked = sorted(filtered, key=lambda item: float(item.get("trends_score") or 0.0), reverse=True)
    return ranked[:count]


def find_best_topics_sync(
    niche: str,
    *,
    geo: str = "PL",
    language: str = "pl",
    topic_history: list[str] | None = None,
    count: int = 5,
) -> list[dict[str, Any]]:
    coro = find_best_topics(
        niche,
        geo=geo,
        language=language,
        topic_history=topic_history,
        count=count,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[dict[str, Any]] | Exception | None = None

    def _runner() -> None:
        nonlocal result
        try:
            result = asyncio.run(coro)
        except Exception as exc:  # pragma: no cover - defensive sync bridge
            result = exc

    thread = Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=20)
    if isinstance(result, Exception) or result is None:
        return []
    return result


async def research_topic(topic: str, *, language: str = "pl", depth: str = "standard") -> dict[str, Any]:
    cache_key = f"research:topic:{language}:{depth}:{topic.casefold()}"
    if depth != "quick":
        cached = await get_cached(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    news_count = 3 if depth == "quick" else 10 if depth == "deep" else 5
    facts_task = get_facts_for_topic(topic, language=language, max_facts=10)
    news_task = get_trending_news(topic, language=language, count=news_count)
    related_task = get_related_queries(topic)
    search_task = web_search(topic, count=8, language=language) if depth == "deep" else None
    wiki_results = await search_wikipedia(topic, limit=1, language=language)
    wiki_title = str(wiki_results[0].get("title") or topic) if wiki_results else topic
    summary_task = get_article_summary(wiki_title, language=language)

    facts, news, related, summary = await asyncio.gather(facts_task, news_task, related_task, summary_task)
    web_results = await search_task if search_task is not None else []
    related_queries = [
        str(item.get("query"))
        for item in [*(related.get("rising") or []), *(related.get("top") or [])]
        if isinstance(item, dict) and item.get("query")
    ][:10]
    result = {
        "topic": topic,
        "wikipedia_summary": summary.get("extract") if isinstance(summary, dict) else None,
        "key_facts": facts,
        "recent_news": news,
        "related_queries": related_queries,
        "web_results": web_results,
        "research_depth": depth,
        "cached": False,
    }
    if depth != "quick":
        await set_cached(cache_key, result, ttl_hours=6 if depth == "deep" else 12)
    return result


async def get_script_context(topic: str, script_segment: str, *, language: str = "pl") -> str:
    tokens = [token for token in _dedupe_key(script_segment).split() if len(token) >= 4][:5]
    query = " ".join(tokens) or topic
    results = await search_wikipedia(query, limit=1, language=language)
    if not results:
        return ""
    summary = await get_article_summary(str(results[0].get("title") or topic), language=language)
    if not summary:
        return ""
    return str(summary.get("extract") or "")[:240]
