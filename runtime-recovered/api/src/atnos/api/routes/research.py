from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from atnos.research.cache import cache_stats, clear_all
from atnos.research.research_aggregator import find_best_topics, research_topic
from atnos.research.search_client import web_search
from atnos.research.trends_client import get_trending_topics
from atnos.research.wikipedia_client import get_article_summary

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/trends")
async def trending_topics(geo: str = "PL", count: int = Query(default=20, ge=1, le=50)) -> list[dict[str, object]]:
    return await get_trending_topics(geo=geo, count=count)


@router.get("/topics/suggest")
async def suggest_topics(
    niche: str,
    geo: str = "PL",
    language: str = "pl",
    count: int = Query(default=5, ge=1, le=20),
) -> list[dict[str, Any]]:
    return await find_best_topics(niche=niche, geo=geo, language=language, count=count)


@router.get("/topic/{topic}/research")
async def research_topic_endpoint(topic: str, depth: str = "standard", language: str = "pl") -> dict[str, Any]:
    return await research_topic(topic, language=language, depth=depth)


@router.get("/search")
async def search(q: str, count: int = Query(default=10, ge=1, le=20), type: str = "web") -> list[dict[str, str | None]]:
    return await web_search(q, count=count, search_type=type)


@router.get("/wikipedia/{title}")
async def wikipedia_summary(title: str, language: str = "pl") -> dict[str, object] | None:
    return await get_article_summary(title, language=language)


@router.get("/cache/stats")
async def research_cache_stats() -> dict[str, Any]:
    return await cache_stats()


@router.delete("/cache")
async def clear_research_cache() -> dict[str, int]:
    return {"deleted": await clear_all()}
