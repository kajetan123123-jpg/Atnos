from __future__ import annotations

import asyncio
from typing import Any

from atnos.intelligence.google_trends import import_pytrends_trend_req
from atnos.research.cache import get_cached, set_cached


async def get_trending_topics(
    *,
    geo: str = "PL",
    language: str = "pl",
    category: int = 0,
    timeframe: str = "now 7-d",
    count: int = 20,
) -> list[dict[str, object]]:
    _ = (category, timeframe)
    cache_key = f"trends:topics:{geo}:{language}:{count}"
    cached = await get_cached(cache_key)
    if cached and isinstance(cached.get("topics"), list):
        return list(cached["topics"])

    def _load() -> list[dict[str, object]]:
        try:
            TrendReq = import_pytrends_trend_req()
        except RuntimeError:
            return []
        pytrends = TrendReq(hl=language, tz=60)
        try:
            df = pytrends.trending_searches(pn=geo.lower())
        except Exception:
            return []
        output = []
        for idx, row in enumerate(df.head(count).itertuples(index=False), start=1):
            output.append({"topic": str(row[0]), "rank": idx, "source": "google_trends"})
        return output

    topics = await asyncio.to_thread(_load)
    await set_cached(cache_key, {"topics": topics}, ttl_hours=3)
    return topics


async def get_interest_over_time(
    keywords: list[str],
    *,
    geo: str = "PL",
    timeframe: str = "today 3-m",
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for batch_start in range(0, len(keywords), 5):
        batch = keywords[batch_start : batch_start + 5]
        cache_key = f"trends:interest:{geo}:{timeframe}:{'|'.join(batch).casefold()}"
        cached = await get_cached(cache_key)
        if cached and isinstance(cached.get("interest"), dict):
            output.update(cached["interest"])
            continue

        def _load() -> dict[str, list[dict[str, Any]]]:
            try:
                TrendReq = import_pytrends_trend_req()
            except RuntimeError:
                return {keyword: [] for keyword in batch}
            pytrends = TrendReq(hl="pl", tz=60)
            try:
                pytrends.build_payload(batch, geo=geo, timeframe=timeframe)
                df = pytrends.interest_over_time()
            except Exception:
                return {keyword: [] for keyword in batch}
            result: dict[str, list[dict[str, Any]]] = {}
            for keyword in batch:
                if keyword not in df:
                    result[keyword] = []
                    continue
                result[keyword] = [
                    {"date": str(index.date()), "value": int(value)}
                    for index, value in df[keyword].items()
                ]
            return result

        loaded = await asyncio.to_thread(_load)
        await set_cached(cache_key, {"interest": loaded}, ttl_hours=6)
        output.update(loaded)
    return output


async def get_related_queries(keyword: str, *, geo: str = "PL") -> dict[str, list[dict[str, object]]]:
    cache_key = f"trends:related:{geo}:{keyword.casefold()}"
    cached = await get_cached(cache_key)
    if cached:
        return {
            "rising": list(cached.get("rising") or []),
            "top": list(cached.get("top") or []),
        }

    def _load() -> dict[str, list[dict[str, object]]]:
        try:
            TrendReq = import_pytrends_trend_req()
        except RuntimeError:
            return {"rising": [], "top": []}
        pytrends = TrendReq(hl="pl", tz=60)
        try:
            pytrends.build_payload([keyword], geo=geo, timeframe="today 3-m")
            raw = pytrends.related_queries().get(keyword) or {}
        except Exception:
            return {"rising": [], "top": []}

        def _rows(key: str) -> list[dict[str, object]]:
            df = raw.get(key)
            if df is None:
                return []
            return [
                {"query": str(row.query), "value": int(row.value)}
                for row in df.head(10).itertuples(index=False)
            ]

        return {"rising": _rows("rising"), "top": _rows("top")}

    result = await asyncio.to_thread(_load)
    await set_cached(cache_key, result, ttl_hours=12)
    return result


async def score_topic_by_trends(topic: str, *, geo: str = "PL") -> float:
    try:
        interest = (await get_interest_over_time([topic], geo=geo, timeframe="now 7-d")).get(topic) or []
        values = [int(item.get("value") or 0) for item in interest]
        if not values:
            return 0.5
        avg = sum(values) / max(1, len(values))
        first = sum(values[:3]) / max(1, len(values[:3]))
        last = sum(values[-3:]) / max(1, len(values[-3:]))
        multiplier = 1.3 if last > first else 1.0
        return max(0.0, min(1.0, (avg / 100.0) * multiplier))
    except Exception:
        return 0.5
