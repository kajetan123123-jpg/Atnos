from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import (
    connect,
    read_intelligence_account_config,
    rows_to_dicts,
    utc_midnight_tomorrow_iso,
    utc_now,
    utc_now_iso,
    write_collection_run,
)
from atnos.intelligence.youtube_api import (
    YouTubeCollectorError,
    YouTubeQuotaExhausted,
    build_youtube_data_client,
    fetch_recent_channel_videos,
    parse_duration_seconds,
    resolve_channel,
    video_record,
)


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _format_bucket(title: str, duration: str | None) -> str:
    duration_s = parse_duration_seconds(duration)
    lowered = title.casefold()
    if any(marker in lowered for marker in ("?", "dlaczego", "why", "how", "jak ")):
        return "question_hook"
    if any(char.isdigit() for char in title[:12]):
        return "numbered_list"
    if duration_s is not None and duration_s <= 75:
        return "short_explainer"
    return "narrative"


def compute_competitor_aggregates(videos: list[dict[str, Any]]) -> dict[str, Any]:
    now = utc_now()
    last_7d = [
        video
        for video in videos
        if (published := _parse_published_at(video.get("published_at"))) is not None
        and now - published <= timedelta(days=7)
    ]
    avg_views_7d = sum(int(video.get("views") or 0) for video in last_7d) / max(1, len(last_7d))
    publish_dates = [
        published
        for video in videos
        if (published := _parse_published_at(video.get("published_at"))) is not None
    ]
    if publish_dates:
        window_days = max(1.0, (max(publish_dates) - min(publish_dates)).total_seconds() / 86400)
        posting_frequency = len(publish_dates) / window_days * 7
    else:
        posting_frequency = 0.0
    formats = Counter(_format_bucket(str(video.get("title") or ""), video.get("duration")) for video in videos)
    return {
        "avg_views_7d": round(avg_views_7d, 2),
        "posting_frequency": round(posting_frequency, 2),
        "top_performing_format": formats.most_common(1)[0][0] if formats else None,
    }


def _collect_competitors_sync(
    *,
    session: Session,
    settings: Settings,
    account_id: str,
    channel_urls: list[str],
    oauth_account_id: int | None = None,
) -> dict[str, Any]:
    youtube = _build_data_client(session, settings, oauth_account_id=oauth_account_id)
    collected_at = utc_now_iso()
    channels: list[dict[str, Any]] = []
    videos_out: list[dict[str, Any]] = []
    with connect(settings) as db:
        for channel_url in channel_urls:
            channel = resolve_channel(youtube, channel_url)
            if not channel:
                continue
            channel_id = str(channel.get("id") or "").strip()
            snippet = channel.get("snippet") or {}
            raw_videos = fetch_recent_channel_videos(youtube, channel_id, max_results=20)
            videos = [video_record(item) for item in raw_videos]
            aggregates = compute_competitor_aggregates(videos)
            channel_row = {
                "account_id": account_id,
                "channel_id": channel_id,
                "source_url": channel_url,
                "channel_title": str(snippet.get("title") or "").strip() or None,
                **aggregates,
                "collected_at": collected_at,
                "last_refreshed": collected_at,
                "next_refresh_at": (utc_now() + timedelta(hours=24)).isoformat(),
            }
            db.execute(
                """
                INSERT INTO competitor_channels (
                    account_id, channel_id, source_url, channel_title, avg_views_7d,
                    posting_frequency, posting_frequency_per_week, top_performing_format,
                    top_format, collected_at, last_refreshed, next_refresh_at
                )
                VALUES (
                    :account_id, :channel_id, :source_url, :channel_title, :avg_views_7d,
                    :posting_frequency, :posting_frequency, :top_performing_format,
                    :top_performing_format, :collected_at, :last_refreshed, :next_refresh_at
                )
                ON CONFLICT(account_id, channel_id) DO UPDATE SET
                    source_url=excluded.source_url,
                    channel_title=excluded.channel_title,
                    avg_views_7d=excluded.avg_views_7d,
                    posting_frequency=excluded.posting_frequency,
                    posting_frequency_per_week=excluded.posting_frequency_per_week,
                    top_performing_format=excluded.top_performing_format,
                    top_format=excluded.top_format,
                    collected_at=excluded.collected_at,
                    last_refreshed=excluded.last_refreshed,
                    next_refresh_at=excluded.next_refresh_at
                """,
                channel_row,
            )
            for video in videos:
                if not video["video_id"]:
                    continue
                video_row = {
                    **video,
                    "account_id": account_id,
                    "competitor_channel_id": channel_id,
                    "collected_at": collected_at,
                }
                db.execute(
                    """
                    INSERT INTO competitor_videos (
                        video_id, account_id, channel_id, competitor_channel_id, title, views, likes,
                        comments, published_at, duration, duration_seconds, thumbnail_url, collected_at
                    )
                    VALUES (
                        :video_id, :account_id, :competitor_channel_id, :competitor_channel_id, :title, :views, :likes,
                        :comments, :published_at, :duration, :duration_s, :thumbnail_url, :collected_at
                    )
                    ON CONFLICT(video_id) DO UPDATE SET
                        account_id=excluded.account_id,
                        channel_id=excluded.channel_id,
                        competitor_channel_id=excluded.competitor_channel_id,
                        title=excluded.title,
                        views=excluded.views,
                        likes=excluded.likes,
                        comments=excluded.comments,
                        published_at=excluded.published_at,
                        duration=excluded.duration,
                        duration_seconds=excluded.duration_seconds,
                        thumbnail_url=excluded.thumbnail_url,
                        collected_at=excluded.collected_at
                    """,
                    video_row,
                )
                videos_out.append(video_row)
            channels.append(channel_row)
        db.commit()
    return {"status": "collected", "channels": channels, "videos": videos_out}


def _build_data_client(session: Session, settings: Settings, *, oauth_account_id: int | None = None) -> Any:
    try:
        return build_youtube_data_client(session, settings, oauth_account_id=oauth_account_id)
    except TypeError as exc:
        if "oauth_account_id" not in str(exc):
            raise
        return build_youtube_data_client(session, settings)


async def collect_competitors(
    *,
    session: Session,
    settings: Settings,
    account_id: str,
    channel_urls: list[str],
) -> dict[str, Any]:
    normalized_urls = list(dict.fromkeys(url.strip() for url in channel_urls if url.strip()))
    normalized_account = account_id.strip()
    if not normalized_account or not normalized_urls:
        return {"status": "invalid_request", "channels": [], "videos": []}
    account_config = read_intelligence_account_config(settings, account_id=normalized_account) or {}
    oauth_account_id = _optional_int(account_config.get("youtube_oauth_account_id"))
    run_key = f"competitor:{normalized_account}"
    try:
        result = await asyncio.to_thread(
            _collect_competitors_sync,
            session=session,
            settings=settings,
            account_id=normalized_account,
            channel_urls=normalized_urls,
            oauth_account_id=oauth_account_id,
        )
    except YouTubeQuotaExhausted as exc:
        retry_at = utc_midnight_tomorrow_iso()
        write_collection_run(
            settings,
            run_key=run_key,
            collector="competitor",
            status="quota_exhausted",
            message=str(exc),
            next_retry_at=retry_at,
        )
        return {"status": "quota_exhausted", "next_retry_at": retry_at, "channels": [], "videos": []}
    except YouTubeCollectorError as exc:
        message = str(exc)
        status = "needs_config" if _looks_like_configuration_error(message) else "error"
        write_collection_run(
            settings,
            run_key=run_key,
            collector="competitor",
            status=status,
            message=message,
        )
        return {
            "status": status,
            "message": message,
            "channels": [],
            "videos": [],
            "next_actions": [
                "Set YOUTUBE_DATA_API_KEY or connect YouTube Studio OAuth before competitor analysis.",
                "Run /api/intelligence/competitors again after credentials are configured.",
            ]
            if status == "needs_config"
            else [],
        }
    write_collection_run(
        settings,
        run_key=run_key,
        collector="competitor",
        status="collected",
        message=f"Collected {len(result.get('channels', []))} competitor channels.",
        success=True,
    )
    return result


def _looks_like_configuration_error(message: str) -> bool:
    lowered = message.casefold()
    return any(
        marker in lowered
        for marker in (
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
    )


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def read_due_competitor_groups(settings: Settings) -> list[dict[str, Any]]:
    now = utc_now_iso()
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT account_id, source_url
            FROM competitor_channels
            WHERE next_refresh_at IS NULL OR next_refresh_at <= ?
            ORDER BY account_id
            """,
            (now,),
        ).fetchall()
        return rows_to_dicts(rows)
