from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import (
    connect,
    rows_to_dicts,
    read_intelligence_account_config,
    utc_midnight_tomorrow_iso,
    utc_now,
    utc_now_iso,
    write_collection_run,
)
from atnos.intelligence.youtube_api import (
    YouTubeCollectorError,
    YouTubeQuotaExhausted,
    build_youtube_analytics_client,
    build_youtube_data_client,
    parse_int,
    raise_for_youtube_error,
)

FULL_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
    "subscribersGained,likes,comments,shares,annotationClickThroughRate,cardClickRate"
)
CORE_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,likes,comments,shares"
DATA_API_FALLBACK_MISSING_METRICS = [
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "subscribersGained",
    "shares",
    "annotationClickThroughRate",
    "cardClickRate",
]


def _fetch_video_titles(youtube: Any, video_ids: list[str]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start : start + 50]
        if not batch:
            continue
        try:
            response = youtube.videos().list(part="snippet", id=",".join(batch), maxResults=len(batch)).execute()
        except Exception as exc:
            raise_for_youtube_error(exc)
        for item in response.get("items") or []:
            snippet = item.get("snippet") or {}
            titles[str(item.get("id") or "")] = str(snippet.get("title") or "")
    return titles


def _query_own_performance(analytics: Any, *, start_date: date, end_date: date) -> dict[str, Any]:
    try:
        return analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date.isoformat(),
            endDate=end_date.isoformat(),
            metrics=FULL_METRICS,
            dimensions="video",
            sort="-views",
            maxResults=200,
        ).execute()
    except HttpError as exc:
        try:
            return analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date.isoformat(),
                endDate=end_date.isoformat(),
                metrics=CORE_METRICS,
                dimensions="video",
                sort="-views",
                maxResults=200,
            ).execute()
        except Exception as fallback_exc:
            raise_for_youtube_error(fallback_exc)
    except Exception as exc:
        raise_for_youtube_error(exc)


def _query_own_performance_data_api_fallback(youtube: Any, *, max_results: int = 200) -> dict[str, Any]:
    """Pobierz podstawowe metryki własnego kanału przez YouTube Data API.

    Ten fallback jest celowo zdegradowany: Data API nie zwraca retencji, watch-time
    ani CTR, ale pozwala utrzymać pętlę feedbacku na views/likes/comments, gdy
    YouTube Analytics API nie jest jeszcze włączone w Google Cloud.
    """
    try:
        channel_response = youtube.channels().list(part="contentDetails", mine=True).execute()
        channel_items = channel_response.get("items") or []
        uploads_playlist_id = (
            ((channel_items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
            if channel_items
            else None
        )
        if not uploads_playlist_id:
            return _data_api_fallback_response([])

        video_ids: list[str] = []
        page_token: str | None = None
        while len(video_ids) < max_results:
            playlist_response = youtube.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=min(50, max_results - len(video_ids)),
                pageToken=page_token,
            ).execute()
            for item in playlist_response.get("items") or []:
                video_id = str(((item.get("contentDetails") or {}).get("videoId")) or "").strip()
                if video_id:
                    video_ids.append(video_id)
            page_token = playlist_response.get("nextPageToken")
            if not page_token:
                break

        videos: list[dict[str, Any]] = []
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start : start + 50]
            if not batch:
                continue
            videos_response = youtube.videos().list(
                part="snippet,statistics",
                id=",".join(batch),
                maxResults=len(batch),
            ).execute()
            videos.extend(videos_response.get("items") or [])
        return _data_api_fallback_response(videos)
    except Exception as exc:
        raise_for_youtube_error(exc)


def _data_api_fallback_response(videos: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    titles: dict[str, str] = {}
    for item in videos:
        video_id = str(item.get("id") or "").strip()
        if not video_id:
            continue
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        titles[video_id] = str(snippet.get("title") or "").strip()
        rows.append(
            [
                video_id,
                parse_int(stats.get("viewCount")),
                0.0,
                0.0,
                0.0,
                0,
                parse_int(stats.get("likeCount")),
                parse_int(stats.get("commentCount")),
                0,
            ]
        )
    return {
        "columnHeaders": [
            {"name": "video"},
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
            {"name": "averageViewDuration"},
            {"name": "averageViewPercentage"},
            {"name": "subscribersGained"},
            {"name": "likes"},
            {"name": "comments"},
            {"name": "shares"},
        ],
        "rows": rows,
        "titles": titles,
        "degraded": True,
        "fallback_source": "youtube_data_api_v3",
        "metrics_missing": DATA_API_FALLBACK_MISSING_METRICS,
    }


def _collect_own_channel_sync(
    *,
    session: Session,
    settings: Settings,
    account_id: str,
    oauth_account_id: int | None = None,
) -> dict[str, Any]:
    analytics = _build_analytics_client(session, settings, oauth_account_id=oauth_account_id)
    youtube = _build_data_client(session, settings, oauth_account_id=oauth_account_id)
    end_date = date.today()
    start_date = end_date - timedelta(days=90)
    fallback_message: str | None = None
    try:
        response = _query_own_performance(analytics, start_date=start_date, end_date=end_date)
    except YouTubeCollectorError as exc:
        if not _looks_like_youtube_analytics_access_error(str(exc)):
            raise
        fallback_message = str(exc)
        response = _query_own_performance_data_api_fallback(youtube, max_results=200)
    headers = [item.get("name") for item in response.get("columnHeaders") or []]
    rows = response.get("rows") or []
    video_ids = [str(row[0]) for row in rows if row]
    titles = dict(response.get("titles") or {}) or _fetch_video_titles(youtube, video_ids)
    collected_at = utc_now_iso()
    items: list[dict[str, Any]] = []
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO own_channel_accounts (account_id, last_requested_at, next_refresh_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                last_requested_at=excluded.last_requested_at,
                next_refresh_at=excluded.next_refresh_at
            """,
            (account_id, collected_at, (utc_now() + timedelta(hours=6)).isoformat()),
        )
        for raw in rows:
            payload = dict(zip(headers, raw, strict=False))
            video_id = str(payload.get("video") or raw[0]).strip()
            ctr_values = [
                payload.get("annotationClickThroughRate"),
                payload.get("cardClickRate"),
            ]
            ctr = next((float(value) for value in ctr_values if isinstance(value, (int, float))), None)
            item = {
                "account_id": account_id,
                "video_id": video_id,
                "title": titles.get(video_id),
                "views": int(payload.get("views") or 0),
                "watch_time_min": float(payload.get("estimatedMinutesWatched") or 0),
                "avg_view_duration_s": float(payload.get("averageViewDuration") or 0),
                "avg_view_pct": float(payload.get("averageViewPercentage") or 0),
                "ctr": ctr,
                "subs_gained": int(payload.get("subscribersGained") or 0),
                "likes": int(payload.get("likes") or 0),
                "comments": int(payload.get("comments") or 0),
                "shares": int(payload.get("shares") or 0),
                "collected_at": collected_at,
            }
            db.execute(
                """
                INSERT INTO own_performance (
                    account_id, video_id, title, views, watch_time_min, avg_view_duration_s,
                    avg_view_pct, ctr, subs_gained, likes, comments, shares, collected_at
                )
                VALUES (
                    :account_id, :video_id, :title, :views, :watch_time_min, :avg_view_duration_s,
                    :avg_view_pct, :ctr, :subs_gained, :likes, :comments, :shares, :collected_at
                )
                ON CONFLICT(account_id, video_id, collected_at) DO UPDATE SET
                    title=excluded.title,
                    views=excluded.views,
                    watch_time_min=excluded.watch_time_min,
                    avg_view_duration_s=excluded.avg_view_duration_s,
                    avg_view_pct=excluded.avg_view_pct,
                    ctr=excluded.ctr,
                    subs_gained=excluded.subs_gained,
                    likes=excluded.likes,
                    comments=excluded.comments,
                    shares=excluded.shares,
                    collected_at=excluded.collected_at
                """,
                item,
            )
            items.append(item)
        db.commit()
    result: dict[str, Any] = {"status": "collected", "count": len(items), "items": items}
    if response.get("degraded"):
        result.update(
            {
                "degraded": True,
                "fallback_source": response.get("fallback_source") or "youtube_data_api_v3",
                "metrics_missing": list(response.get("metrics_missing") or DATA_API_FALLBACK_MISSING_METRICS),
                "fallback_reason": fallback_message,
            }
        )
    return result


def _build_analytics_client(session: Session, settings: Settings, *, oauth_account_id: int | None = None) -> Any:
    try:
        return build_youtube_analytics_client(session, settings, oauth_account_id=oauth_account_id)
    except TypeError as exc:
        if "oauth_account_id" not in str(exc):
            raise
        return build_youtube_analytics_client(session, settings)


def _build_data_client(session: Session, settings: Settings, *, oauth_account_id: int | None = None) -> Any:
    try:
        return build_youtube_data_client(session, settings, oauth_account_id=oauth_account_id)
    except TypeError as exc:
        if "oauth_account_id" not in str(exc):
            raise
        return build_youtube_data_client(session, settings)


async def collect_own_channel(
    *,
    session: Session,
    settings: Settings,
    account_id: str,
) -> dict[str, Any]:
    normalized_account = account_id.strip()
    if not normalized_account:
        return {"status": "invalid_request", "count": 0, "items": []}
    account_config = read_intelligence_account_config(settings, account_id=normalized_account) or {}
    oauth_account_id = _optional_int(account_config.get("youtube_oauth_account_id"))
    _register_own_channel_request(settings, account_id=normalized_account)
    run_key = f"own_channel:{normalized_account}"
    try:
        result = await asyncio.to_thread(
            _collect_own_channel_sync,
            session=session,
            settings=settings,
            account_id=normalized_account,
            oauth_account_id=oauth_account_id,
        )
    except YouTubeQuotaExhausted as exc:
        retry_at = utc_midnight_tomorrow_iso()
        write_collection_run(
            settings,
            run_key=run_key,
            collector="own_channel",
            status="quota_exhausted",
            message=str(exc),
            next_retry_at=retry_at,
        )
        _schedule_own_channel_retry(settings, account_id=normalized_account, next_refresh_at=retry_at)
        return {"status": "quota_exhausted", "next_retry_at": retry_at, "count": 0, "items": []}
    except YouTubeCollectorError as exc:
        return _own_channel_error_payload(settings, account_id=normalized_account, run_key=run_key, message=str(exc))
    except Exception as exc:
        return _own_channel_error_payload(settings, account_id=normalized_account, run_key=run_key, message=str(exc))
    degraded = bool(result.get("degraded"))
    write_collection_run(
        settings,
        run_key=run_key,
        collector="own_channel",
        status="collected_degraded" if degraded else "collected",
        message=(
            f"Collected {result.get('count', 0)} own-channel performance rows via "
            f"{result.get('fallback_source', 'fallback')} with limited metrics."
            if degraded
            else f"Collected {result.get('count', 0)} own-channel performance rows."
        ),
        success=True,
    )
    return result


def read_due_own_accounts(settings: Settings) -> list[dict[str, Any]]:
    now = utc_now_iso()
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT account_id FROM own_channel_accounts
            WHERE next_refresh_at IS NULL OR next_refresh_at <= ?
            """,
            (now,),
        ).fetchall()
        return rows_to_dicts(rows)


def _register_own_channel_request(settings: Settings, *, account_id: str) -> None:
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO own_channel_accounts (account_id, last_requested_at, next_refresh_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                last_requested_at=excluded.last_requested_at
            """,
            (account_id, utc_now_iso(), None),
        )
        db.commit()


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _schedule_own_channel_retry(settings: Settings, *, account_id: str, next_refresh_at: str) -> None:
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO own_channel_accounts (account_id, last_requested_at, next_refresh_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                last_requested_at=excluded.last_requested_at,
                next_refresh_at=excluded.next_refresh_at
            """,
            (account_id, utc_now_iso(), next_refresh_at),
        )
        db.commit()


def _own_channel_error_payload(
    settings: Settings,
    *,
    account_id: str,
    run_key: str,
    message: str,
) -> dict[str, Any]:
    status = "needs_config" if _looks_like_configuration_error(message) else "error"
    next_retry_at = (utc_now() + timedelta(hours=6)).isoformat()
    write_collection_run(
        settings,
        run_key=run_key,
        collector="own_channel",
        status=status,
        message=message,
        next_retry_at=next_retry_at,
    )
    _schedule_own_channel_retry(settings, account_id=account_id, next_refresh_at=next_retry_at)
    return {
        "status": status,
        "message": message,
        "next_retry_at": next_retry_at,
        "count": 0,
        "items": [],
        "next_actions": [
            "Connect YouTube Studio OAuth with yt-analytics.readonly.",
            "Run /api/intelligence/own again after credentials are configured.",
        ]
        if status == "needs_config"
        else [],
    }


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


def _looks_like_youtube_analytics_access_error(message: str) -> bool:
    lowered = message.casefold()
    return (
        "youtube analytics api" in lowered
        and any(marker in lowered for marker in ("accessnotconfigured", "has not been used", "disabled", "enable it"))
    )
