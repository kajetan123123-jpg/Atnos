from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .channel_logging import ChannelOpsLogger
from .channel_models import ContentItem, ContentStatus
from .channel_ops_service import ChannelOpsService


REVIEW_STATUSES = {
    ContentStatus.MANUAL_UPLOAD_READY,
    ContentStatus.APPROVAL_PENDING,
    ContentStatus.QA_FAILED,
    ContentStatus.BLOCKED,
    ContentStatus.PRIVATE_UPLOADED,
}


@dataclass(frozen=True, slots=True)
class ReviewQueueItem:
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


@dataclass(frozen=True, slots=True)
class ReviewQueueWriteResult:
    json_path: Path
    markdown_path: Path
    item_count: int
    ready_count: int
    repair_count: int


class ChannelReviewQueue:
    def __init__(self, channel_ops: ChannelOpsService) -> None:
        self.channel_ops = channel_ops
        self.settings = channel_ops.settings
        self.root = self.settings.data_dir / "channel_ops"
        self.json_path = self.root / "review_queue.json"
        self.markdown_path = self.root / "review_queue.md"
        self.logger = ChannelOpsLogger(self.settings.data_dir / "logs" / "channel_ops.jsonl")

    def write(self) -> ReviewQueueWriteResult:
        items = self.collect()
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "item_count": len(items),
            "ready_count": len([item for item in items if item.status in {"manual_upload_ready", "approval_pending"}]),
            "repair_count": len([item for item in items if item.status in {"qa_failed", "blocked"}]),
            "items": [asdict(item) for item in items],
        }
        self.json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.markdown_path.write_text(self._markdown(items=items, payload=payload), encoding="utf-8")
        self.logger.log(
            "review_queue_written",
            {
                "json_path": str(self.json_path.resolve()),
                "markdown_path": str(self.markdown_path.resolve()),
                "item_count": payload["item_count"],
                "ready_count": payload["ready_count"],
                "repair_count": payload["repair_count"],
            },
        )
        return ReviewQueueWriteResult(
            json_path=self.json_path.resolve(),
            markdown_path=self.markdown_path.resolve(),
            item_count=int(payload["item_count"]),
            ready_count=int(payload["ready_count"]),
            repair_count=int(payload["repair_count"]),
        )

    def collect(self) -> list[ReviewQueueItem]:
        items: list[ReviewQueueItem] = []
        for channel in self.channel_ops.list_channels():
            for item in self.channel_ops.list_queue(channel.channel_id):
                status = ContentStatus(item.status)
                if status not in REVIEW_STATUSES:
                    continue
                items.append(self._queue_item(item))
        return sorted(items, key=lambda item: (_status_rank(item.status), item.updated_at, item.item_id))

    def _queue_item(self, item: ContentItem) -> ReviewQueueItem:
        output_dir = Path(item.output_dir) if item.output_dir else None
        package = _read_json(output_dir / "youtube_upload_package.json") if output_dir else {}
        checks = package.get("checks") if isinstance(package.get("checks"), dict) else {}
        review_path = self.channel_ops.registry.channel_dir(item.channel_id) / "items" / item.item_id / "review.md"
        return ReviewQueueItem(
            channel_id=item.channel_id,
            item_id=item.item_id,
            topic=item.topic,
            status=str(item.status),
            final_mp4=item.final_mp4,
            output_dir=item.output_dir,
            review_path=str(review_path.resolve()) if review_path.exists() else None,
            quality_score=item.quality_score,
            visual_quality_score=_float_or_none(checks.get("visual_quality_score")),
            script_quality_score=_float_or_none(checks.get("script_quality_score")),
            render_ok=_bool_or_none(checks.get("render_ok")),
            youtube_shorts_ready=_bool_or_none(checks.get("youtube_shorts_ready")),
            upload_approval_id=item.upload_approval_id,
            youtube_video_id=item.youtube_video_id,
            updated_at=item.updated_at,
        )

    def _markdown(self, *, items: list[ReviewQueueItem], payload: dict[str, Any]) -> str:
        ready = [item for item in items if item.status in {"manual_upload_ready", "approval_pending"}]
        repair = [item for item in items if item.status in {"qa_failed", "blocked"}]
        uploaded = [item for item in items if item.status == "private_uploaded"]
        lines = [
            "# Channel Review Queue",
            "",
            "## Summary",
            f"- total: `{payload['item_count']}`",
            f"- ready: `{payload['ready_count']}`",
            f"- needs_repair: `{payload['repair_count']}`",
            "",
            "## Ready For Review",
            *_item_lines(ready),
            "",
            "## Needs Repair",
            *_item_lines(repair),
            "",
            "## Private Uploaded",
            *_item_lines(uploaded),
            "",
            "## Next Actions",
            *_next_actions(ready=ready, repair=repair),
            "",
        ]
        return "\n".join(lines)


def _item_lines(items: list[ReviewQueueItem]) -> list[str]:
    if not items:
        return ["- none"]
    lines: list[str] = []
    for item in items:
        score = item.visual_quality_score if item.visual_quality_score is not None else item.quality_score
        lines.append(
            "- "
            f"`{item.status}` "
            f"{item.channel_id} / {item.topic} "
            f"(visual={score if score is not None else 'unknown'}, script={item.script_quality_score if item.script_quality_score is not None else 'unknown'}) "
            f"-> `{item.final_mp4 or item.output_dir or 'missing'}`"
        )
    return lines


def _next_actions(*, ready: list[ReviewQueueItem], repair: list[ReviewQueueItem]) -> list[str]:
    actions: list[str] = []
    if any(item.status == "manual_upload_ready" for item in ready):
        actions.append("- Finish YouTube OAuth or manually upload reviewed files as private.")
    if any(item.status == "approval_pending" for item in ready):
        actions.append("- Review pending approvals before private upload.")
    if repair:
        actions.append("- Repair QA failures before creating upload approvals.")
    if not actions:
        actions.append("- Generate the next daily channel plan.")
    return actions


def _status_rank(status: str) -> int:
    order = {
        "approval_pending": 0,
        "manual_upload_ready": 1,
        "qa_failed": 2,
        "blocked": 3,
        "private_uploaded": 4,
    }
    return order.get(status, 99)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
