from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import re
from typing import Any, Callable, Literal, Protocol

from atnos.core.config import Settings
from content.short_agent import ShortAgent, ShortAgentResult
from youtube.youtube_readiness import YouTubeReadinessService

from .channel_logging import ChannelOpsLogger
from .channel_models import ChannelProfile, ContentItem, ContentStatus, utc_now
from .channel_ops_service import ChannelOpsService
from .review_queue import ChannelReviewQueue
from .review_package import write_daily_review_summary, write_item_review_package


DailyChannelItemStatus = Literal[
    "planned",
    "rendering",
    "qa_failed",
    "approval_pending",
    "manual_upload_ready",
    "private_uploaded",
    "blocked",
]


class ShortAgentLike(Protocol):
    def run(self, topic: str, *, force_repair: bool = False) -> ShortAgentResult:
        ...


class YouTubeReadinessLike(Protocol):
    status: str


@dataclass(frozen=True, slots=True)
class DailyChannelItemResult:
    channel_id: str
    item_id: str
    topic: str
    status: DailyChannelItemStatus
    output_dir: str | None = None
    final_mp4: str | None = None
    quality_score: float | None = None
    upload_approval_id: str | None = None
    youtube_readiness_status: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DailyChannelRunResult:
    timestamp: str
    processed_channels: int
    processed_items: int
    ready_for_review: list[DailyChannelItemResult]
    results: list[DailyChannelItemResult]
    daily_log_path: str
    daily_review_path: str | None = None
    review_queue_path: str | None = None
    review_queue_markdown_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "processed_channels": self.processed_channels,
            "processed_items": self.processed_items,
            "ready_for_review": [asdict(item) for item in self.ready_for_review],
            "results": [asdict(item) for item in self.results],
            "daily_log_path": self.daily_log_path,
            "daily_review_path": self.daily_review_path,
            "review_queue_path": self.review_queue_path,
            "review_queue_markdown_path": self.review_queue_markdown_path,
        }


class DailyChannelRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        channel_ops: ChannelOpsService | None = None,
        short_agent_factory: Callable[[Settings], ShortAgentLike] | None = None,
        youtube_readiness_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.channel_ops = channel_ops or ChannelOpsService(settings)
        self.short_agent_factory = short_agent_factory or (lambda short_settings: ShortAgent(short_settings))
        self.youtube_readiness_factory = youtube_readiness_factory or (
            lambda readiness_settings: YouTubeReadinessService(readiness_settings)
        )
        self.logger = ChannelOpsLogger(settings.data_dir / "logs" / "channel_ops.jsonl")
        self.daily_runs_dir = settings.data_dir / "channel_ops" / "daily_runs"
        self.daily_runs_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        *,
        max_per_channel: int | None = None,
        force_repair: bool = False,
        run_youtube_probe: bool = False,
        channel_ids: tuple[str, ...] | None = None,
    ) -> DailyChannelRunResult:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        daily_log_path = self.daily_runs_dir / f"{timestamp}.json"
        enabled_channels = [channel for channel in self.channel_ops.list_channels() if channel.enabled]
        if channel_ids:
            requested = {channel_id.strip().lower() for channel_id in channel_ids if channel_id.strip()}
            enabled_channels = [
                channel
                for channel in enabled_channels
                if channel.channel_id in requested
            ]
        self.logger.log(
            "daily_channel_run_started",
            {
                "timestamp": timestamp,
                "enabled_channels": [channel.channel_id for channel in enabled_channels],
                "max_per_channel": max_per_channel,
                "channel_ids": list(channel_ids or ()),
            },
        )

        results: list[DailyChannelItemResult] = []
        for channel in enabled_channels:
            target_count = self._target_count(channel=channel, max_per_channel=max_per_channel)
            if target_count <= 0:
                continue
            results.extend(
                self._run_channel(
                    channel=channel,
                    count=target_count,
                    force_repair=force_repair,
                    run_youtube_probe=run_youtube_probe,
                )
            )

        ready_for_review = [
            result
            for result in results
            if result.status in {"approval_pending", "manual_upload_ready", "private_uploaded"}
        ]
        daily_review_path = write_daily_review_summary(
            daily_log_path=daily_log_path,
            results=results,
            ready_for_review=ready_for_review,
        )
        review_queue = ChannelReviewQueue(self.channel_ops).write()
        payload = DailyChannelRunResult(
            timestamp=utc_now(),
            processed_channels=len(enabled_channels),
            processed_items=len(results),
            ready_for_review=ready_for_review,
            results=results,
            daily_log_path=str(daily_log_path.resolve()),
            daily_review_path=str(daily_review_path.resolve()),
            review_queue_path=str(review_queue.json_path),
            review_queue_markdown_path=str(review_queue.markdown_path),
        )
        daily_log_path.write_text(json.dumps(payload.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.log(
            "daily_channel_run_completed",
            {
                "processed_channels": payload.processed_channels,
                "processed_items": payload.processed_items,
                "ready_for_review": len(payload.ready_for_review),
                "daily_log_path": payload.daily_log_path,
                "daily_review_path": payload.daily_review_path,
                "review_queue_path": payload.review_queue_path,
                "review_queue_markdown_path": payload.review_queue_markdown_path,
            },
        )
        return payload

    def _run_channel(
        self,
        *,
        channel: ChannelProfile,
        count: int,
        force_repair: bool,
        run_youtube_probe: bool,
    ) -> list[DailyChannelItemResult]:
        plan = self.channel_ops.create_daily_plan(channel.channel_id, count)
        planned = plan[:count]
        channel_results: list[DailyChannelItemResult] = []
        seen_topics = self._existing_non_failed_topics(channel.channel_id)
        processed_topics: set[str] = set()

        for item in planned:
            topic_key = self._topic_key(item.topic)
            is_duplicate = topic_key in processed_topics or self._is_duplicate_topic(item, seen_topics)
            if is_duplicate:
                blocked = self._update_item_status(item, ContentStatus.BLOCKED)
                result = self._result_from_item(
                    blocked,
                    status="blocked",
                    error="duplicate_topic",
                )
                self._write_item_manifest(blocked, result)
                self.logger.log("daily_channel_item_blocked", asdict(result))
                channel_results.append(result)
                continue
            processed_topics.add(topic_key)

            item = self._update_item_status(item, ContentStatus.PLANNED)
            self.logger.log("daily_channel_item_planned", {"channel_id": item.channel_id, "item_id": item.item_id})
            item = self._update_item_status(item, ContentStatus.RENDERING)
            self.logger.log("daily_channel_item_rendering", {"channel_id": item.channel_id, "item_id": item.item_id})

            try:
                result = self._render_item(
                    channel=channel,
                    item=item,
                    force_repair=force_repair,
                    run_youtube_probe=run_youtube_probe,
                )
            except Exception as exc:
                blocked = self._update_item_status(item, ContentStatus.BLOCKED)
                result = self._result_from_item(
                    blocked,
                    status="blocked",
                    error=f"{type(exc).__name__}: {exc}",
                )

            self._write_item_manifest(self.channel_ops.calendar.find_item(item.item_id), result)
            self.logger.log("daily_channel_item_completed", asdict(result))
            channel_results.append(result)

        return channel_results

    def _render_item(
        self,
        *,
        channel: ChannelProfile,
        item: ContentItem,
        force_repair: bool,
        run_youtube_probe: bool,
    ) -> DailyChannelItemResult:
        short_settings = self._settings_for_channel(channel.channel_id)
        short_result = self.short_agent_factory(short_settings).run(item.topic, force_repair=force_repair)
        if short_result.status == "runtime_not_ready":
            blocked = self._update_item_status(item, ContentStatus.BLOCKED)
            return self._result_from_item(
                blocked,
                status="blocked",
                short_result=short_result,
                error="short_runtime_not_ready",
            )
        if short_result.status == "NEEDS_HUMAN_REVIEW":
            failed = self._update_item_status(item, ContentStatus.QA_FAILED)
            return self._result_from_item(
                failed,
                status="qa_failed",
                short_result=short_result,
                error="script_quality_gate_failed",
            )
        if short_result.status == "NEEDS_VISUAL_REPAIR":
            failed = self._update_item_status(item, ContentStatus.QA_FAILED)
            return self._result_from_item(
                failed,
                status="qa_failed",
                short_result=short_result,
                error="visual_quality_gate_failed",
            )
        if short_result.final_mp4 is None or not short_result.final_mp4.exists():
            blocked = self._update_item_status(item, ContentStatus.BLOCKED)
            return self._result_from_item(
                blocked,
                status="blocked",
                short_result=short_result,
                error="final_mp4_missing",
            )

        ready_item = self.channel_ops.mark_render_ready(item.item_id, short_result.output_dir)
        if ContentStatus(ready_item.status) != ContentStatus.QA_PASSED:
            failed = self._update_item_status(ready_item, ContentStatus.QA_FAILED)
            return self._result_from_item(
                failed,
                status="qa_failed",
                short_result=short_result,
                error="render_qa_failed",
            )

        upload_quality = self.channel_ops.validate_upload_quality(ready_item.item_id)
        if not upload_quality["ready"]:
            failed = self._update_item_status(ready_item, ContentStatus.QA_FAILED)
            return self._result_from_item(
                failed,
                status="qa_failed",
                short_result=short_result,
                error="upload_quality_gate_failed: " + "; ".join(str(issue) for issue in upload_quality["issues"]),
            )

        readiness = self.youtube_readiness_factory(self.settings).check(run_probe=run_youtube_probe)
        readiness_status = str(readiness.status)
        if readiness_status not in {"configured", "ready"}:
            manual = self._update_item_status(ready_item, ContentStatus.MANUAL_UPLOAD_READY)
            return self._result_from_item(
                manual,
                status="manual_upload_ready",
                short_result=short_result,
                youtube_readiness_status=readiness_status,
            )

        pending = self.channel_ops.request_upload_approval(ready_item.item_id)
        return self._result_from_item(
            pending,
            status="approval_pending",
            short_result=short_result,
            youtube_readiness_status=readiness_status,
        )

    def _settings_for_channel(self, channel_id: str) -> Settings:
        channel_root = self.channel_ops.registry.channel_dir(channel_id)
        shorts_output_dir = channel_root / "renders"
        shorts_output_dir.mkdir(parents=True, exist_ok=True)
        copied = self.settings.model_copy(deep=True, update={"shorts_output_dir": shorts_output_dir.resolve()})
        copied.shorts_output_dir.mkdir(parents=True, exist_ok=True)
        return copied

    def _write_item_manifest(self, item: ContentItem, result: DailyChannelItemResult) -> None:
        item_dir = self.channel_ops.registry.channel_dir(item.channel_id) / "items" / item.item_id
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "manifest.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (item_dir / "topic.md").write_text(f"# Topic\n\n{item.topic}\n", encoding="utf-8")
        review_path = write_item_review_package(item=item, result=result, item_dir=item_dir)
        self.logger.log(
            "daily_channel_item_review_package_written",
            {
                "channel_id": item.channel_id,
                "item_id": item.item_id,
                "review_path": str(review_path.resolve()),
            },
        )

    def _update_item_status(self, item: ContentItem, status: ContentStatus) -> ContentItem:
        updated = item.with_update(status=status)
        return self.channel_ops.calendar.update_item(updated)

    def _result_from_item(
        self,
        item: ContentItem,
        *,
        status: DailyChannelItemStatus,
        short_result: ShortAgentResult | None = None,
        youtube_readiness_status: str | None = None,
        error: str | None = None,
    ) -> DailyChannelItemResult:
        artifacts = dict(short_result.artifacts) if short_result is not None else {}
        return DailyChannelItemResult(
            channel_id=item.channel_id,
            item_id=item.item_id,
            topic=item.topic,
            status=status,
            output_dir=item.output_dir or (str(short_result.output_dir) if short_result is not None else None),
            final_mp4=item.final_mp4 or (str(short_result.final_mp4) if short_result and short_result.final_mp4 else None),
            quality_score=item.quality_score,
            upload_approval_id=item.upload_approval_id,
            youtube_readiness_status=youtube_readiness_status,
            artifacts=artifacts,
            error=error,
        )

    def _target_count(self, *, channel: ChannelProfile, max_per_channel: int | None) -> int:
        daily_target = max(0, int(channel.daily_target_count))
        if max_per_channel is None:
            return daily_target
        return max(0, min(daily_target, int(max_per_channel)))

    def _existing_non_failed_topics(self, channel_id: str) -> dict[str, str]:
        topics: dict[str, str] = {}
        ignored = {ContentStatus.FAILED, ContentStatus.BLOCKED}
        for item in self.channel_ops.list_queue(channel_id):
            if ContentStatus(item.status) in ignored:
                continue
            topics.setdefault(self._topic_key(item.topic), item.item_id)
        return topics

    def _is_duplicate_topic(self, item: ContentItem, existing_topics: dict[str, str]) -> bool:
        existing_item_id = existing_topics.get(self._topic_key(item.topic))
        return existing_item_id is not None and existing_item_id != item.item_id

    def _topic_key(self, topic: str) -> str:
        return re.sub(r"\s+", " ", topic.casefold()).strip()
