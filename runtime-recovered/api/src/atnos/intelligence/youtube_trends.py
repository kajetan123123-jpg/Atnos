from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import (
    connect,
    normalize_key,
    rate_limit_active,
    read_collection_run,
    read_intelligence_account_config,
    retry_blocked,
    rows_to_dicts,
    utc_midnight_tomorrow_iso,
    utc_now_iso,
    write_collection_run,
)
from atnos.intelligence.youtube_api import (
    YouTubeCollectorError,
    YouTubeQuotaExhausted,
    build_youtube_data_client,
    extract_best_thumbnail,
    parse_int,
    published_after_days,
    raise_for_youtube_error,
    split_niche_keywords,
)

logger = logging.getLogger(__name__)

_REGION_ALIAS = {
    "USA": "US", "UK": "GB", "GBR": "GB", "POL": "PL", "POLAND": "PL",
    "DEU": "DE", "GER": "DE", "GERMANY": "DE", "FRA": "FR", "FRANCE": "FR",
    "ESP": "ES", "SPAIN": "ES", "ITA": "IT", "ITALY": "IT",
    "CAN": "CA", "CANADA": "CA", "AUS": "AU", "AUSTRALIA": "AU",
    "JPN": "JP", "JAPAN": "JP", "IND": "IN", "INDIA": "IN",
    "BRA": "BR", "BRAZIL": "BR", "MEX": "MX", "MEXICO": "MX",
    "NLD": "NL", "NETHERLANDS": "NL", "SWE": "SE", "SWEDEN": "SE",
    "NOR": "NO", "NORWAY": "NO", "FIN": "FI", "FINLAND": "FI",
    "DNK": "DK", "DENMARK": "DK", "IRL": "IE", "IRELAND": "IE",
    "CZE": "CZ", "CZECHIA": "CZ", "SVK": "SK", "SLOVAKIA": "SK",
    "HUN": "HU", "HUNGARY": "HU", "ROU": "RO", "ROMANIA": "RO",
    "UKR": "UA", "UKRAINE": "UA", "PRT": "PT", "PORTUGAL": "PT",
}


def _normalize_region_code(region: str | None) -> str:
    raw = (region or "PL").strip().upper()
    if not raw:
        return "PL"
    mapped = _REGION_ALIAS.get(raw, raw)
    if len(mapped) != 2 or not mapped.isalpha():
        logger.warning("Invalid YouTube regionCode %r, falling back to PL", region)
        return "PL"
    return mapped


def _search_keyword_sync(
    *,
    session: Session,
    settings: Settings,
    account_id: str,
    niche: str,
    keyword: str,
    region: str,
    oauth_account_id: int | None = None,
) -> list[dict[str, Any]]:
    youtube = _build_data_client(session, settings, oauth_account_id=oauth_account_id)
    try:
        search_response = youtube.search().list(
            part="snippet",
            q=keyword,
            type="video",
            order="viewCount",
            publishedAfter=published_after_days(7),
            regionCode=region,
            maxResults=50,
        ).execute()
        video_ids = [
            str(((item.get("id") or {}).get("videoId")) or "").strip()
            for item in (search_response.get("items") or [])
        ]
        video_ids = list(dict.fromkeys(video_id for video_id in video_ids if video_id))
        if not video_ids:
            return []
        video_items: list[dict[str, Any]] = []
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start : start + 50]
            videos_response = youtube.videos().list(
                part="snippet,statistics",
                id=",".join(batch),
                maxResults=len(batch),
            ).execute()
            video_items.extend(videos_response.get("items") or [])
    except Exception as exc:
        raise_for_youtube_error(exc)

    collected_at = utc_now_iso()
    rows: list[dict[str, Any]] = []
    for item in video_items:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        video_id = str(item.get("id") or "").strip()
        if not video_id:
            continue
        rows.append(
            {
                "video_id": video_id,
                "account_id": account_id,
                "niche": niche,
                "region": region,
                "title": str(snippet.get("title") or "").strip(),
                "view_count": parse_int(stats.get("viewCount")),
                "like_count": parse_int(stats.get("likeCount")),
                "comment_count": parse_int(stats.get("commentCount")),
                "published_at": str(snippet.get("publishedAt") or "").strip() or None,
                "channel_id": str(snippet.get("channelId") or "").strip() or None,
                "channel_title": str(snippet.get("channelTitle") or "").strip() or None,
                "thumbnail_url": extract_best_thumbnail(snippet),
                "collected_at": collected_at,
            }
        )
    with connect(settings) as db:
        db.executemany(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                :video_id, :account_id, :niche, :region, :title, :view_count, :like_count,
                :comment_count, :published_at, :channel_id, :channel_title, :thumbnail_url, :collected_at
            )
            ON CONFLICT(video_id) DO UPDATE SET
                account_id=excluded.account_id,
                niche=excluded.niche,
                region=excluded.region,
                title=excluded.title,
                view_count=excluded.view_count,
                like_count=excluded.like_count,
                comment_count=excluded.comment_count,
                published_at=excluded.published_at,
                channel_id=excluded.channel_id,
                channel_title=excluded.channel_title,
                thumbnail_url=excluded.thumbnail_url,
                collected_at=excluded.collected_at
            """,
            rows,
        )
        db.commit()
    return rows


def _build_data_client(session: Session, settings: Settings, *, oauth_account_id: int | None = None) -> Any:
    try:
        return build_youtube_data_client(session, settings, oauth_account_id=oauth_account_id)
    except TypeError as exc:
        if "oauth_account_id" not in str(exc):
            raise
        return build_youtube_data_client(session, settings)


def _read_existing_sync(settings: Settings, *, account_id: str, niche: str, region: str) -> list[dict[str, Any]]:
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT * FROM yt_signals
            WHERE account_id = ? AND niche = ? AND region = ?
            ORDER BY view_count DESC
            LIMIT 100
            """,
            (account_id, niche, region),
        ).fetchall()
        return rows_to_dicts(rows)


async def collect_youtube_trends(
    *,
    session: Session,
    settings: Settings,
    account_id: str,
    niche: str,
    region: str = "PL",
    force: bool = False,
) -> dict[str, Any]:
    normalized_account = account_id.strip()
    normalized_niche = " ".join(niche.split())
    normalized_region = _normalize_region_code(region)
    keywords = split_niche_keywords(normalized_niche)
    if not normalized_account or not normalized_niche or not keywords:
        return {"status": "invalid_request", "count": 0, "items": []}
    account_config = read_intelligence_account_config(settings, account_id=normalized_account) or {}
    oauth_account_id = _optional_int(account_config.get("youtube_oauth_account_id"))

    run_key = f"youtube_trends:{normalized_account}:{normalize_key(normalized_niche)}:{normalized_region}"
    run = read_collection_run(settings, run_key)
    if retry_blocked(run):
        return {
            "status": "retry_scheduled",
            "next_retry_at": run.get("next_retry_at") if run else None,
            "items": await asyncio.to_thread(
                _read_existing_sync,
                settings,
                account_id=normalized_account,
                niche=normalized_niche,
                region=normalized_region,
            ),
        }
    if not force and rate_limit_active(run, interval=timedelta(minutes=15)):
        items = await asyncio.to_thread(
            _read_existing_sync,
            settings,
            account_id=normalized_account,
            niche=normalized_niche,
            region=normalized_region,
        )
        return {"status": "rate_limited", "count": len(items), "items": items}

    all_items: list[dict[str, Any]] = []
    try:
        for keyword in keywords:
            all_items.extend(
                await asyncio.to_thread(
                    _search_keyword_sync,
                    session=session,
                    settings=settings,
                    account_id=normalized_account,
                    niche=normalized_niche,
                    keyword=keyword,
                    region=normalized_region,
                    oauth_account_id=oauth_account_id,
                )
            )
    except YouTubeQuotaExhausted as exc:
        logger.warning("YouTube trends quota exhausted; retry scheduled at UTC midnight: %s", exc)
        retry_at = utc_midnight_tomorrow_iso()
        write_collection_run(
            settings,
            run_key=run_key,
            collector="youtube_trends",
            status="quota_exhausted",
            message=str(exc),
            next_retry_at=retry_at,
        )
        return {"status": "quota_exhausted", "next_retry_at": retry_at, "count": 0, "items": []}
    except YouTubeCollectorError as exc:
        message = str(exc)
        if _looks_like_configuration_error(message):
            write_collection_run(
                settings,
                run_key=run_key,
                collector="youtube_trends",
                status="needs_config",
                message=message,
            )
            return {
                "status": "needs_config",
                "count": 0,
                "items": [],
                "message": message,
                "next_actions": [
                    "Set YOUTUBE_DATA_API_KEY for public trend collection, or connect YouTube Studio OAuth.",
                    "Run /api/intelligence/trends again after credentials are configured.",
                ],
            }
        write_collection_run(
            settings,
            run_key=run_key,
            collector="youtube_trends",
            status="error",
            message=message,
        )
        return {"status": "error", "count": 0, "items": [], "message": message}

    write_collection_run(
        settings,
        run_key=run_key,
        collector="youtube_trends",
        status="collected",
        message=f"Collected {len(all_items)} YouTube trend signals.",
        success=True,
    )
    all_items.sort(key=lambda item: int(item.get("view_count") or 0), reverse=True)
    return {"status": "collected", "count": len(all_items), "items": all_items}


def _looks_like_configuration_error(message: str) -> bool:
    lowered = message.casefold()
    markers = (
        "oauth",
        "credential",
        "client secret",
        "client_secret",
        "token",
        "unauthorized",
        "connect youtube",
        "not configured",
        "missing",
    )
    return any(marker in lowered for marker in markers)


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
