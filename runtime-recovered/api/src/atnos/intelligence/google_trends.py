from __future__ import annotations

import asyncio
import importlib
import logging
import os
import subprocess
import sys
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import (
    connect,
    json_dumps,
    rows_to_dicts,
    utc_now_iso,
)

logger = logging.getLogger(__name__)
PYTRENDS_PACKAGE = "pytrends>=4.9.2"


def _is_google_rate_limit(exc: BaseException) -> bool:
    text = str(exc).casefold()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status == 429 or "429" in text or "rate limit" in text or "too many requests" in text


def _df_rows_to_points(df: Any, keyword: str) -> list[dict[str, Any]]:
    if df is None or keyword not in df:
        return []
    return [{"date": str(index.date()), "value": int(value)} for index, value in df[keyword].items()]


def _frame_to_records(df: Any, *, query_key: str = "query") -> list[dict[str, Any]]:
    if df is None:
        return []
    records: list[dict[str, Any]] = []
    for row in df.head(20).itertuples(index=False):
        data = row._asdict() if hasattr(row, "_asdict") else {}
        if not data:
            continue
        label = data.get(query_key) or data.get("topic_title") or data.get("topic_mid") or data.get("title")
        value = data.get("value")
        if label:
            records.append({"query": str(label), "value": int(value) if isinstance(value, int) else value})
    return records


def _collect_google_trends_sync(
    *,
    settings: Settings,
    keywords: list[str],
    timeframe: str,
    geo: str,
) -> list[dict[str, Any]]:
    TrendReq = import_pytrends_trend_req()

    pytrends = TrendReq(hl="pl", tz=60)
    collected_at = utc_now_iso()
    results: list[dict[str, Any]] = []
    with connect(settings) as db:
        for batch_start in range(0, len(keywords), 5):
            batch = keywords[batch_start : batch_start + 5]
            pytrends.build_payload(batch, geo=geo, timeframe=timeframe)
            interest_df = pytrends.interest_over_time()
            related_queries = pytrends.related_queries()
            related_topics = pytrends.related_topics()
            for keyword in batch:
                points = _df_rows_to_points(interest_df, keyword)
                values = [int(point["value"]) for point in points]
                interest_score = int(round(sum(values) / max(1, len(values)))) if values else 0
                query_payload = related_queries.get(keyword) or {}
                topic_payload = related_topics.get(keyword) or {}
                rising = _frame_to_records(query_payload.get("rising"))
                topic_rising = _frame_to_records(topic_payload.get("rising"), query_key="topic_title")
                row = {
                    "keyword": keyword,
                    "timeframe": timeframe,
                    "geo": geo,
                    "interest_score": interest_score,
                    "related_rising": rising,
                    "related_topics": topic_rising,
                    "status": "ready",
                    "collected_at": collected_at,
                }
                db.execute(
                    """
                    INSERT INTO gtrends_signals (
                        keyword, timeframe, geo, interest_score, related_rising,
                        related_topics, status, collected_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(keyword, timeframe, geo) DO UPDATE SET
                        interest_score=excluded.interest_score,
                        related_rising=excluded.related_rising,
                        related_topics=excluded.related_topics,
                        status=excluded.status,
                        collected_at=excluded.collected_at
                    """,
                    (
                        keyword,
                        timeframe,
                        geo,
                        interest_score,
                        json_dumps(rising),
                        json_dumps(topic_rising),
                        "ready",
                        collected_at,
                    ),
                )
                results.append(row)
        db.commit()
    return results


def _mark_rate_limited_sync(settings: Settings, *, keywords: list[str], timeframe: str, geo: str) -> list[dict[str, Any]]:
    collected_at = utc_now_iso()
    rows = []
    with connect(settings) as db:
        for keyword in keywords:
            row = {
                "keyword": keyword,
                "timeframe": timeframe,
                "geo": geo,
                "interest_score": 0.0,
                "related_rising": [],
                "related_topics": [],
                "status": "rate_limited",
                "collected_at": collected_at,
            }
            db.execute(
                """
                INSERT INTO gtrends_signals (
                    keyword, timeframe, geo, interest_score, related_rising,
                    related_topics, status, collected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword, timeframe, geo) DO UPDATE SET
                    status=excluded.status,
                    collected_at=excluded.collected_at
                """,
                (keyword, timeframe, geo, 0.0, "[]", "[]", "rate_limited", collected_at),
            )
            rows.append(row)
        db.commit()
    return rows


def _read_gtrends_sync(settings: Settings, *, keywords: list[str], timeframe: str, geo: str) -> list[dict[str, Any]]:
    with connect(settings) as db:
        rows = db.execute(
            f"""
            SELECT * FROM gtrends_signals
            WHERE timeframe = ? AND geo = ? AND keyword IN ({','.join('?' for _ in keywords)})
            ORDER BY interest_score DESC
            """,
            (timeframe, geo, *keywords),
        ).fetchall()
        return rows_to_dicts(rows)


async def collect_google_trends(
    *,
    settings: Settings,
    keywords: list[str],
    timeframe: str = "now 7-d",
    geo: str = "PL",
) -> dict[str, Any]:
    normalized_keywords = list(dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip()))
    normalized_geo = geo.strip().upper() or "PL"
    normalized_timeframe = " ".join(timeframe.replace("+", " ").split()) or "now 7-d"
    if not normalized_keywords:
        return {"status": "invalid_request", "count": 0, "items": []}

    delay_s = 1.0
    for attempt in range(1, 6):
        try:
            items = await asyncio.to_thread(
                _collect_google_trends_sync,
                settings=settings,
                keywords=normalized_keywords,
                timeframe=normalized_timeframe,
                geo=normalized_geo,
            )
            return {"status": "collected", "count": len(items), "items": items}
        except Exception as exc:
            if not _is_google_rate_limit(exc):
                logger.warning("Google Trends collector failed: %s", exc)
                items = await asyncio.to_thread(
                    _read_gtrends_sync,
                    settings,
                    keywords=normalized_keywords,
                    timeframe=normalized_timeframe,
                    geo=normalized_geo,
                )
                return {"status": "error", "message": str(exc), "count": len(items), "items": items}
            if attempt == 5:
                logger.warning("Google Trends rate limited after retries.")
                items = await asyncio.to_thread(
                    _mark_rate_limited_sync,
                    settings,
                    keywords=normalized_keywords,
                    timeframe=normalized_timeframe,
                    geo=normalized_geo,
                )
                return {"status": "rate_limited", "count": len(items), "items": items}
            await asyncio.sleep(delay_s)
            delay_s *= 2


def import_pytrends_trend_req() -> Any:
    """Import pytrends, installing it once at runtime when the environment is incomplete."""
    try:
        return importlib.import_module("pytrends.request").TrendReq
    except ImportError as exc:
        if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("ATNOS_DISABLE_AUTO_INSTALL", "").lower() in {"1", "true", "yes"}:
            raise RuntimeError("pytrends is not installed and auto-install is disabled in this runtime.") from exc
        if not _install_package(PYTRENDS_PACKAGE):
            raise RuntimeError("pytrends is not installed and automatic installation failed.") from exc
        try:
            return importlib.import_module("pytrends.request").TrendReq
        except ImportError as retry_exc:
            raise RuntimeError("pytrends installation completed but the package is still not importable.") from retry_exc


def _install_package(package: str) -> bool:
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                package,
                "--break-system-packages",
                "-q",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False
