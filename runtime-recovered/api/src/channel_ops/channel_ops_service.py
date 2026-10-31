from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import Settings

from .channel_logging import ChannelOpsLogger
from .channel_models import ChannelProfile, ContentItem, ContentStatus, path_to_str
from .channel_registry import ChannelOpsError, ChannelRegistry
from .content_calendar import ContentCalendar
from .faceless_benchmark_bridge import FacelessBenchmarkBridge


class ChannelOpsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = ChannelOpsLogger(settings.data_dir / "logs" / "channel_ops.jsonl")
        self.registry = ChannelRegistry(settings=settings, logger=self.logger)
        self.calendar = ContentCalendar(registry=self.registry, logger=self.logger)
        self.benchmark_bridge = FacelessBenchmarkBridge(
            registry=self.registry,
            calendar=self.calendar,
            logger=self.logger,
        )

    def register_channel(self, **kwargs: Any) -> ChannelProfile:
        return self.registry.register_channel(**kwargs)

    def list_channels(self) -> list[ChannelProfile]:
        return self.registry.list_channels()

    def enqueue_topic(self, channel_id: str, topic: str) -> ContentItem:
        return self.calendar.enqueue_topic(channel_id=channel_id, topic=topic)

    def enqueue_benchmark_briefs(
        self,
        channel_id: str,
        benchmark_json_path: str | Path,
        *,
        limit: int = 3,
    ) -> list[ContentItem]:
        return self.benchmark_bridge.enqueue_benchmark_briefs(
            channel_id=channel_id,
            benchmark_json_path=benchmark_json_path,
            limit=limit,
        )

    def list_queue(self, channel_id: str) -> list[ContentItem]:
        return self.calendar.list_queue(channel_id)

    def create_daily_plan(self, channel_id: str, count: int) -> list[ContentItem]:
        return self.calendar.create_daily_plan(channel_id=channel_id, count=count)

    def mark_render_ready(self, item_id: str, output_dir: str | Path) -> ContentItem:
        item = self.calendar.find_item(item_id)
        output_path = Path(output_dir).resolve()
        final_mp4 = output_path / "final.mp4"
        qa = self._load_render_qa(output_path / "render_qa.json")
        qa_passed = self._render_qa_passed(qa)
        status = ContentStatus.QA_PASSED if qa_passed else ContentStatus.RENDERED
        updated = item.with_update(
            output_dir=path_to_str(output_path),
            final_mp4=path_to_str(final_mp4),
            status=status,
            quality_score=self._quality_score(qa, qa_passed),
        )
        self.calendar.update_item(updated)
        self.logger.log(
            "content_render_ready",
            {
                "channel_id": updated.channel_id,
                "item_id": updated.item_id,
                "status": updated.status,
                "output_dir": updated.output_dir,
                "final_mp4": updated.final_mp4,
                "quality_score": updated.quality_score,
            },
        )
        return updated

    def request_upload_approval(self, item_id: str) -> ContentItem:
        item = self.calendar.find_item(item_id)
        if ContentStatus(item.status) != ContentStatus.QA_PASSED:
            raise ChannelOpsError("Upload approval requires content status qa_passed.")
        self._assert_upload_quality_gate(item)

        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        short_id = item.item_id[-6:]
        approval_id = f"channelops-{timestamp}-{short_id}"
        updated = item.with_update(
            status=ContentStatus.APPROVAL_PENDING,
            upload_approval_id=approval_id,
        )
        self.calendar.update_item(updated)
        self.logger.log(
            "upload_approval_requested",
            {
                "channel_id": updated.channel_id,
                "item_id": updated.item_id,
                "upload_approval_id": approval_id,
            },
        )
        return updated

    def validate_upload_quality(self, item_id: str) -> dict[str, Any]:
        item = self.calendar.find_item(item_id)
        issues = self._upload_quality_issues(item)
        return {
            "ready": not issues,
            "issues": issues,
            "item_id": item.item_id,
            "channel_id": item.channel_id,
            "status": item.status,
        }

    def _assert_upload_quality_gate(self, item: ContentItem) -> None:
        issues = self._upload_quality_issues(item)
        if issues:
            raise ChannelOpsError("Upload approval blocked by quality gate: " + "; ".join(issues))

    def _upload_quality_issues(self, item: ContentItem) -> list[str]:
        issues: list[str] = []
        if not item.output_dir:
            issues.append("output_dir is missing")
            return issues

        output_path = Path(item.output_dir)
        final_mp4 = Path(item.final_mp4) if item.final_mp4 else output_path / "final.mp4"
        if not final_mp4.exists():
            issues.append(f"final.mp4 is missing: {final_mp4}")

        try:
            from monetization.shorts_contract import validate_short_pre_render_contract

            contract = validate_short_pre_render_contract(output_path)
        except Exception as exc:
            issues.append(f"short pre-render contract validation failed: {type(exc).__name__}: {exc}")
        else:
            if not contract.valid:
                issues.extend(f"pre-render contract: {issue}" for issue in contract.issues)

        qa = self._load_render_qa(output_path / "render_qa.json")
        if not qa:
            issues.append("render_qa.json is missing")
        else:
            if qa.get("ok") is False:
                issues.append("render_qa.ok is false")
            warnings = qa.get("warnings")
            if isinstance(warnings, list) and warnings:
                issues.append("render has warnings: " + "; ".join(str(item) for item in warnings[:3]))
            if qa.get("youtube_shorts_ready") is False:
                issues.append("youtube_shorts_ready is false")

        visual_report = self._load_optional_json(output_path / "visual_quality_report.json")
        if not visual_report:
            issues.append("visual_quality_report.json is missing")
        else:
            if visual_report.get("passed") is False:
                issues.append("visual quality gate failed")
            visual_score = self._float_or_none(visual_report.get("overall_score"))
            if visual_score is not None and visual_score < float(self.settings.shorts_min_quality_score):
                issues.append(
                    f"visual quality score {visual_score:g} is below minimum {self.settings.shorts_min_quality_score:g}"
                )
            visual_issues = visual_report.get("issues")
            if isinstance(visual_issues, list) and visual_issues:
                issues.append("visual quality issues: " + "; ".join(str(item) for item in visual_issues[:3]))

        package = self._load_optional_json(output_path / "youtube_upload_package.json")
        if not package:
            issues.append("youtube_upload_package.json is missing")
        else:
            package_status = str(package.get("status") or "")
            if package_status and package_status != "manual_upload_ready":
                issues.append(f"youtube_upload_package status is {package_status}")
            checks = package.get("checks")
            if isinstance(checks, dict):
                if checks.get("script_quality_passed") is False:
                    issues.append("script quality gate failed")
                script_score = self._float_or_none(checks.get("script_quality_score"))
                if script_score is not None and script_score < float(self.settings.shorts_min_quality_score):
                    issues.append(
                        f"script quality score {script_score:g} is below minimum {self.settings.shorts_min_quality_score:g}"
                    )
                if checks.get("render_ok") is False:
                    issues.append("youtube package render_ok is false")
                if checks.get("youtube_shorts_ready") is False:
                    issues.append("youtube package youtube_shorts_ready is false")
                if checks.get("visual_quality_passed") is False:
                    issues.append("youtube package visual_quality_passed is false")
                visual_package_score = self._float_or_none(checks.get("visual_quality_score"))
                if (
                    visual_package_score is not None
                    and visual_package_score < float(self.settings.shorts_min_quality_score)
                ):
                    issues.append(
                        f"youtube package visual quality score {visual_package_score:g} is below minimum {self.settings.shorts_min_quality_score:g}"
                    )

        if item.quality_score is not None and item.quality_score < float(self.settings.shorts_min_quality_score):
            issues.append(
                f"item quality score {item.quality_score:g} is below minimum {self.settings.shorts_min_quality_score:g}"
            )

        return issues

    def mark_uploaded_private(self, item_id: str, video_id: str) -> ContentItem:
        normalized_video_id = video_id.strip()
        if not normalized_video_id:
            raise ChannelOpsError("video_id cannot be empty.")
        item = self.calendar.find_item(item_id)
        updated = item.with_update(
            status=ContentStatus.PRIVATE_UPLOADED,
            youtube_video_id=normalized_video_id,
        )
        self.calendar.update_item(updated)
        self.logger.log(
            "content_uploaded_private",
            {
                "channel_id": updated.channel_id,
                "item_id": updated.item_id,
                "youtube_video_id": updated.youtube_video_id,
            },
        )
        return updated

    def update_analytics(self, item_id: str, metrics: dict[str, Any]) -> ContentItem:
        item = self.calendar.find_item(item_id)
        updated = item.with_update(analytics_summary=dict(metrics))
        self.calendar.update_item(updated)
        self.logger.log(
            "content_analytics_updated",
            {
                "channel_id": updated.channel_id,
                "item_id": updated.item_id,
                "metrics": metrics,
            },
        )
        return updated

    def recommend_next_actions(self, channel_id: str) -> list[str]:
        queue = self.calendar.list_queue(channel_id)
        actions: list[str] = []
        statuses = [ContentStatus(item.status) for item in queue]

        if any(
            status in {ContentStatus.IDEA, ContentStatus.PLANNED, ContentStatus.SCRIPTED, ContentStatus.RENDERING}
            for status in statuses
        ):
            actions.append("render_pending_items")
        if any(status in {ContentStatus.RENDERED, ContentStatus.QA_FAILED, ContentStatus.FAILED} for status in statuses):
            actions.append("repair_failed_qa")
        if ContentStatus.APPROVAL_PENDING in statuses or ContentStatus.MANUAL_UPLOAD_READY in statuses:
            actions.append("review_upload_approvals")
        if any(
            ContentStatus(item.status) == ContentStatus.PRIVATE_UPLOADED and not item.analytics_summary
            for item in queue
        ):
            actions.append("sync_private_upload_analytics")
        if not actions:
            actions.append("create_daily_plan")

        self.logger.log("next_actions_recommended", {"channel_id": channel_id, "actions": actions})
        return actions

    def _load_render_qa(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ChannelOpsError(f"Invalid render QA JSON: {path}") from exc
        if not isinstance(raw, dict):
            raise ChannelOpsError(f"Invalid render QA shape: {path}")
        return raw

    def _load_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ChannelOpsError(f"Invalid JSON: {path}") from exc
        if not isinstance(raw, dict):
            raise ChannelOpsError(f"Invalid JSON object shape: {path}")
        return raw

    def _render_qa_passed(self, qa: dict[str, Any] | None) -> bool:
        if not qa:
            return False
        if qa.get("youtube_shorts_ready") is True:
            return True

        checks = qa.get("youtube_short_checks")
        if not isinstance(checks, dict):
            checks = qa

        has_audio = checks.get("has_audio") is True
        width = self._float_or_none(checks.get("width"))
        height = self._float_or_none(checks.get("height"))
        duration = self._float_or_none(checks.get("duration_seconds") or checks.get("duration"))
        vertical_or_square = width is not None and height is not None and height >= width
        sensible_duration = duration is not None and 8 <= duration <= 180
        return has_audio and vertical_or_square and sensible_duration

    def _quality_score(self, qa: dict[str, Any] | None, qa_passed: bool) -> float | None:
        if not qa:
            return None
        for key in ("overall_score", "quality_score"):
            value = self._float_or_none(qa.get(key))
            if value is not None:
                return value
        if qa_passed:
            return 100.0
        return None

    def _float_or_none(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
