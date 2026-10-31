from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter(prefix="/channel-ops", tags=["channel-ops"])


class ChannelReviewQueueItemResponse(BaseModel):
    channel_id: str
    item_id: str
    topic: str
    status: str
    final_mp4: str | None
    output_dir: str | None
    review_path: str | None
    quality_score: float | None
    visual_quality_score: float | None
    script_quality_score: float | None
    render_ok: bool | None
    youtube_shorts_ready: bool | None
    upload_approval_id: str | None
    youtube_video_id: str | None
    updated_at: str


class ChannelReviewQueueResponse(BaseModel):
    item_count: int
    ready_count: int
    repair_count: int
    json_path: str
    markdown_path: str
    items: list[ChannelReviewQueueItemResponse]


@router.get("/review-queue", response_model=ChannelReviewQueueResponse)
def get_channel_review_queue(request: Request) -> ChannelReviewQueueResponse:
    ChannelOpsService, ChannelReviewQueue = _load_channel_review_queue_dependencies()
    queue = ChannelReviewQueue(ChannelOpsService(request.app.state.settings))

    try:
        write_result = queue.write()
        payload = _read_json(write_result.json_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build channel review queue: {exc}",
        ) from exc

    return ChannelReviewQueueResponse(
        item_count=int(payload.get("item_count", 0)),
        ready_count=int(payload.get("ready_count", 0)),
        repair_count=int(payload.get("repair_count", 0)),
        json_path=str(write_result.json_path),
        markdown_path=str(write_result.markdown_path),
        items=[
            ChannelReviewQueueItemResponse(**item)
            for item in payload.get("items", [])
            if isinstance(item, dict)
        ],
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Cannot read {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"Invalid review queue payload in {path}")
    return raw


def _load_channel_review_queue_dependencies() -> tuple[type[Any], type[Any]]:
    from channel_ops import ChannelOpsService, ChannelReviewQueue

    return ChannelOpsService, ChannelReviewQueue
