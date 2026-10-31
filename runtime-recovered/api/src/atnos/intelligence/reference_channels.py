from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Any

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.competitor import _parse_published_at
from atnos.intelligence.storage import connect, json_dumps, utc_midnight_tomorrow_iso, utc_now_iso, write_collection_run
from atnos.intelligence.youtube_api import (
    YouTubeCollectorError,
    YouTubeQuotaExhausted,
    build_youtube_data_client,
    fetch_recent_channel_videos,
    parse_duration_seconds,
    resolve_channel,
    video_record,
)


def _first_words(text: str, count: int = 10) -> str:
    return " ".join(text.split()[:count])


def _engagement_rate(videos: list[dict[str, Any]]) -> float:
    views = sum(int(video.get("views") or 0) for video in videos)
    interactions = sum(int(video.get("likes") or 0) + int(video.get("comments") or 0) for video in videos)
    return round(interactions / views, 6) if views > 0 else 0.0


def _posting_cadence(videos: list[dict[str, Any]]) -> float:
    dates = [
        published
        for video in videos
        if (published := _parse_published_at(video.get("published_at"))) is not None
    ]
    if len(dates) < 2:
        return 0.0
    window_days = max(1.0, (max(dates) - min(dates)).total_seconds() / 86400)
    return round(len(dates) / window_days * 7, 2)


def _avg_video_length_s(videos: list[dict[str, Any]]) -> float:
    durations = [
        parsed
        for video in videos
        if (parsed := parse_duration_seconds(str(video.get("duration") or ""))) is not None
    ]
    return round(sum(durations) / max(1, len(durations)), 2) if durations else 0.0


def _fallback_style_tags(titles: list[str]) -> list[str]:
    tokens = Counter()
    for title in titles:
        lowered = title.casefold()
        if "?" in title:
            tokens["question_hook"] += 1
        if any(char.isdigit() for char in title[:12]):
            tokens["numbered_list"] += 1
        if any(word in lowered for word in ("secret", "sekret", "never", "nigdy")):
            tokens["secret_gap"] += 1
        if any(word in lowered for word in ("rich", "bogaci", "million", "milion")):
            tokens["wealth_signal"] += 1
    return [name for name, _ in tokens.most_common(6)] or ["faceless_educational"]


async def _ollama_reference_summary(settings: Settings, *, titles: list[str], thumbnails: list[str | None]) -> dict[str, Any]:
    model = settings.ollama_fast_model or settings.ollama_chat_model or settings.ollama_main_model or "qwen3:8b"
    prompt = (
        "Analizujesz kanał referencyjny YouTube dla faceless Shorts. "
        "Użyj WYŁĄCZNIE podanych tytułów i adresów miniaturek jako sygnałów, bez wymyślania danych. "
        "Zwróć tylko JSON: {\"hook_pattern_summary\":\"...\","
        "\"thumbnail_style_tags\":[\"tag1\",\"tag2\"]}.\n\n"
        f"Tytuły:\n{json.dumps(titles[:20], ensure_ascii=False)}\n"
        f"Miniaturki:\n{json.dumps([item for item in thumbnails[:20] if item], ensure_ascii=False)}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            text = str((response.json() or {}).get("response") or "").strip()
    except Exception:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _store_reference_sync(
    *,
    session: Session,
    settings: Settings,
    channel_urls: list[str],
    analysis_by_channel: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    youtube = build_youtube_data_client(session, settings)
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
            videos = [video_record(item) for item in fetch_recent_channel_videos(youtube, channel_id, max_results=20)]
            titles = [str(video.get("title") or "") for video in videos]
            hook_patterns = [_first_words(title) for title in titles if title]
            llm_analysis = analysis_by_channel.get(channel_url) or {}
            thumbnail_tags = llm_analysis.get("thumbnail_style_tags")
            if not isinstance(thumbnail_tags, list) or not thumbnail_tags:
                thumbnail_tags = _fallback_style_tags(titles)
            channel_row = {
                "channel_id": channel_id,
                "source_url": channel_url,
                "channel_title": str(snippet.get("title") or "").strip() or None,
                "avg_video_length_s": _avg_video_length_s(videos),
                "avg_duration_sec": int(round(_avg_video_length_s(videos))),
                "posting_cadence": _posting_cadence(videos),
                "posting_cadence_per_week": _posting_cadence(videos),
                "engagement_rate": _engagement_rate(videos),
                "avg_engagement_rate": _engagement_rate(videos),
                "hook_patterns": hook_patterns[:20],
                "thumbnail_style_tags": [str(item) for item in thumbnail_tags[:12]],
                "hook_pattern_summary": str(llm_analysis.get("hook_pattern_summary") or "").strip() or None,
                "collected_at": collected_at,
                "last_refreshed": collected_at,
            }
            db.execute(
                """
                INSERT INTO reference_channels (
                    channel_id, source_url, channel_title, avg_video_length_s, posting_cadence,
                    engagement_rate, avg_duration_sec, posting_cadence_per_week, avg_engagement_rate,
                    hook_patterns, thumbnail_style_tags, hook_pattern_summary, collected_at, last_refreshed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    source_url=excluded.source_url,
                    channel_title=excluded.channel_title,
                    avg_video_length_s=excluded.avg_video_length_s,
                    avg_duration_sec=excluded.avg_duration_sec,
                    posting_cadence=excluded.posting_cadence,
                    posting_cadence_per_week=excluded.posting_cadence_per_week,
                    engagement_rate=excluded.engagement_rate,
                    avg_engagement_rate=excluded.avg_engagement_rate,
                    hook_patterns=excluded.hook_patterns,
                    thumbnail_style_tags=excluded.thumbnail_style_tags,
                    hook_pattern_summary=excluded.hook_pattern_summary,
                    collected_at=excluded.collected_at,
                    last_refreshed=excluded.last_refreshed
                """,
                (
                    channel_row["channel_id"],
                    channel_row["source_url"],
                    channel_row["channel_title"],
                    channel_row["avg_video_length_s"],
                    channel_row["posting_cadence"],
                    channel_row["engagement_rate"],
                    channel_row["avg_duration_sec"],
                    channel_row["posting_cadence_per_week"],
                    channel_row["avg_engagement_rate"],
                    json_dumps(channel_row["hook_patterns"]),
                    json_dumps(channel_row["thumbnail_style_tags"]),
                    channel_row["hook_pattern_summary"],
                    channel_row["collected_at"],
                    channel_row["last_refreshed"],
                ),
            )
            for video in videos:
                if not video["video_id"]:
                    continue
                video_row = {**video, "reference_channel_id": channel_id, "collected_at": collected_at}
                db.execute(
                    """
                    INSERT INTO reference_videos (
                        video_id, reference_channel_id, title, views, likes, comments,
                        published_at, duration, thumbnail_url, collected_at
                    )
                    VALUES (
                        :video_id, :reference_channel_id, :title, :views, :likes, :comments,
                        :published_at, :duration, :thumbnail_url, :collected_at
                    )
                    ON CONFLICT(video_id) DO UPDATE SET
                        reference_channel_id=excluded.reference_channel_id,
                        title=excluded.title,
                        views=excluded.views,
                        likes=excluded.likes,
                        comments=excluded.comments,
                        published_at=excluded.published_at,
                        duration=excluded.duration,
                        thumbnail_url=excluded.thumbnail_url,
                        collected_at=excluded.collected_at
                    """,
                    video_row,
                )
                videos_out.append(video_row)
            channels.append(channel_row)
        db.commit()
    return {"status": "collected", "channels": channels, "videos": videos_out}


async def collect_reference_channels(
    *,
    session: Session,
    settings: Settings,
    channel_urls: list[str],
) -> dict[str, Any]:
    normalized_urls = list(dict.fromkeys(url.strip() for url in channel_urls if url.strip()))
    if not normalized_urls:
        return {"status": "invalid_request", "channels": [], "videos": []}

    # Build LLM summaries after fetching raw metadata would mean two API passes. For v1,
    # run the LLM opportunistically from deterministic placeholders and update after store.
    analysis_by_channel = {url: {} for url in normalized_urls}
    run_key = "reference_channels"
    try:
        result = await asyncio.to_thread(
            _store_reference_sync,
            session=session,
            settings=settings,
            channel_urls=normalized_urls,
            analysis_by_channel=analysis_by_channel,
        )
    except YouTubeQuotaExhausted as exc:
        retry_at = utc_midnight_tomorrow_iso()
        write_collection_run(
            settings,
            run_key=run_key,
            collector="reference_channels",
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
            collector="reference_channels",
            status=status,
            message=message,
        )
        return {
            "status": status,
            "message": message,
            "channels": [],
            "videos": [],
            "next_actions": [
                "Set YOUTUBE_DATA_API_KEY or connect YouTube Studio OAuth before reference-channel benchmarking.",
                "Run /api/intelligence/reference again after credentials are configured.",
            ]
            if status == "needs_config"
            else [],
        }

    updated_channels: list[dict[str, Any]] = []
    for channel in result["channels"]:
        titles = [video["title"] for video in result["videos"] if video["reference_channel_id"] == channel["channel_id"]]
        thumbnails = [
            video.get("thumbnail_url")
            for video in result["videos"]
            if video["reference_channel_id"] == channel["channel_id"]
        ]
        llm_analysis = await _ollama_reference_summary(settings, titles=titles, thumbnails=thumbnails)
        if llm_analysis:
            channel["hook_pattern_summary"] = str(llm_analysis.get("hook_pattern_summary") or "").strip() or channel.get(
                "hook_pattern_summary"
            )
            tags = llm_analysis.get("thumbnail_style_tags")
            if isinstance(tags, list) and tags:
                channel["thumbnail_style_tags"] = [str(item) for item in tags[:12]]
            with connect(settings) as db:
                db.execute(
                    """
                    UPDATE reference_channels
                    SET hook_pattern_summary = ?, thumbnail_style_tags = ?
                    WHERE channel_id = ?
                    """,
                    (
                        channel["hook_pattern_summary"],
                        json_dumps(channel["thumbnail_style_tags"]),
                        channel["channel_id"],
                    ),
                )
                db.commit()
        updated_channels.append(channel)
    result["channels"] = updated_channels
    write_collection_run(
        settings,
        run_key=run_key,
        collector="reference_channels",
        status="collected",
        message=f"Collected {len(updated_channels)} reference channels.",
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
