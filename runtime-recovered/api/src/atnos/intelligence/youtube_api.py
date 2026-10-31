from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService


class YouTubeQuotaExhausted(RuntimeError):
    pass


class YouTubeCollectorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class YouTubeClients:
    data: Any
    analytics: Any


def build_youtube_data_client(session: Session, settings: Settings, *, oauth_account_id: int | None = None) -> Any:
    try:
        if settings.youtube_data_api_key:
            return build("youtube", "v3", developerKey=settings.youtube_data_api_key, cache_discovery=False)
        return YouTubeConnectorService(session, settings, account_id=oauth_account_id).build_data_client()
    except YouTubeConnectorError as exc:
        raise YouTubeCollectorError(str(exc)) from exc


def youtube_data_client_mode(settings: Settings) -> str:
    return "api_key" if settings.youtube_data_api_key else "oauth"


def build_youtube_analytics_client(session: Session, settings: Settings, *, oauth_account_id: int | None = None) -> Any:
    try:
        return YouTubeConnectorService(session, settings, account_id=oauth_account_id).build_analytics_client()
    except YouTubeConnectorError as exc:
        raise YouTubeCollectorError(str(exc)) from exc


def published_after_days(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def parse_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return None
    parts = {key: parse_int(raw) for key, raw in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def is_quota_error(exc: BaseException) -> bool:
    if not isinstance(exc, HttpError):
        return False
    if getattr(exc.resp, "status", None) != 403:
        return False
    body = ""
    try:
        body = exc.content.decode("utf-8", errors="ignore")
    except Exception:
        body = str(exc)
    lowered = body.casefold()
    return any(marker in lowered for marker in ("quotaexceeded", "dailylimitexceeded", "quota exceeded"))


def raise_for_youtube_error(exc: BaseException) -> None:
    if is_quota_error(exc):
        raise YouTubeQuotaExhausted(str(exc)) from exc
    raise YouTubeCollectorError(str(exc)) from exc


def extract_best_thumbnail(snippet: dict[str, Any]) -> str | None:
    thumbnails = snippet.get("thumbnails") or {}
    if not isinstance(thumbnails, dict):
        return None
    for key in ("maxres", "standard", "high", "medium", "default"):
        candidate = thumbnails.get(key)
        if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
            return candidate["url"]
    return None


def split_niche_keywords(niche: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[,;\n]+", niche) if part.strip()]
    if parts:
        return list(dict.fromkeys(parts))
    cleaned = " ".join(niche.split())
    return [cleaned] if cleaned else []


def extract_channel_hint(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return cleaned
    match = re.search(r"/channel/([^/?#]+)", cleaned)
    if match:
        return match.group(1)
    match = re.search(r"/@([^/?#]+)", cleaned)
    if match:
        return f"@{match.group(1)}"
    tail = cleaned.rstrip("/").split("/")[-1]
    return tail or cleaned


def resolve_channel(youtube: Any, channel_url: str) -> dict[str, Any] | None:
    hint = extract_channel_hint(channel_url)
    if not hint:
        return None
    try:
        if hint.startswith("UC"):
            response = youtube.channels().list(part="id,snippet,contentDetails", id=hint).execute()
        elif hint.startswith("@"):
            response = youtube.channels().list(part="id,snippet,contentDetails", forHandle=hint).execute()
        else:
            response = youtube.search().list(part="snippet", q=hint, type="channel", maxResults=1).execute()
            items = response.get("items") or []
            if not items:
                return None
            channel_id = ((items[0].get("snippet") or {}).get("channelId")) or ((items[0].get("id") or {}).get("channelId"))
            if not channel_id:
                return None
            response = youtube.channels().list(part="id,snippet,contentDetails", id=channel_id).execute()
    except Exception as exc:
        raise_for_youtube_error(exc)
    items = response.get("items") or []
    return items[0] if items else None


def fetch_recent_channel_videos(youtube: Any, channel_id: str, *, max_results: int = 20) -> list[dict[str, Any]]:
    try:
        search = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            order="date",
            type="video",
            maxResults=max_results,
        ).execute()
        video_ids = [
            str(((item.get("id") or {}).get("videoId")) or "").strip()
            for item in (search.get("items") or [])
        ]
        video_ids = [video_id for video_id in video_ids if video_id]
        if not video_ids:
            return []
        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids),
            maxResults=max_results,
        ).execute()
    except Exception as exc:
        raise_for_youtube_error(exc)
    return list(response.get("items") or [])


def video_record(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    details = item.get("contentDetails") or {}
    return {
        "video_id": str(item.get("id") or "").strip(),
        "title": str(snippet.get("title") or "").strip(),
        "views": parse_int(stats.get("viewCount")),
        "likes": parse_int(stats.get("likeCount")),
        "comments": parse_int(stats.get("commentCount")),
        "published_at": str(snippet.get("publishedAt") or "").strip() or None,
        "duration": str(details.get("duration") or "").strip() or None,
        "duration_s": parse_duration_seconds(str(details.get("duration") or "")),
        "thumbnail_url": extract_best_thumbnail(snippet),
    }
