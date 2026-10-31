from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from youtube.youtube_auth import build_youtube_analytics_service
from youtube.youtube_models import YouTubeAnalyticsResult


METRICS = (
    "views",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "likes",
    "shares",
    "subscribersGained",
    "subscribersLost",
)


def get_video_analytics(
    video_id: str,
    start_date: str,
    end_date: str,
    *,
    settings: Settings | None = None,
    analytics_service=None,
) -> YouTubeAnalyticsResult:
    resolved_settings = settings or Settings()
    service = analytics_service or build_youtube_analytics_service(resolved_settings)
    response = service.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics=",".join(METRICS),
        filters=f"video=={video_id}",
    ).execute()
    row = (response.get("rows") or [[None] * len(METRICS)])[0]
    metrics = {
        metric: row[index] if index < len(row) else None
        for index, metric in enumerate(METRICS)
    }
    result = YouTubeAnalyticsResult(
        video_id=video_id,
        start_date=start_date,
        end_date=end_date,
        metrics=metrics,
        status="ready" if response.get("rows") else "pending",
    )
    _write_jsonl(resolved_settings.data_dir / "logs" / "youtube_analytics.jsonl", result.to_dict())
    return result


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
