from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .channel_logging import ChannelOpsLogger
from .channel_models import ContentItem
from .channel_registry import ChannelOpsError, ChannelRegistry
from .content_calendar import ContentCalendar


class FacelessBenchmarkBridge:
    def __init__(
        self,
        *,
        registry: ChannelRegistry,
        calendar: ContentCalendar,
        logger: ChannelOpsLogger,
    ) -> None:
        self.registry = registry
        self.calendar = calendar
        self.logger = logger

    def enqueue_benchmark_briefs(
        self,
        *,
        channel_id: str,
        benchmark_json_path: str | Path,
        limit: int = 3,
    ) -> list[ContentItem]:
        channel = self.registry.get_channel(channel_id)
        benchmark_path = Path(benchmark_json_path).resolve()
        payload = self._load_benchmark_payload(benchmark_path)
        briefs = payload.get("replication_briefs")
        if not isinstance(briefs, list):
            raise ChannelOpsError(f"Benchmark payload has no replication_briefs list: {benchmark_path}")
        visual_plans = payload.get("visual_recreation_plans")
        visual_plan_items = visual_plans if isinstance(visual_plans, list) else []
        plan_by_brief_id = {
            str(plan.get("brief_id") or ""): plan
            for plan in visual_plan_items
            if isinstance(plan, dict)
        }

        queue = self.calendar._read_queue(channel.channel_id)
        existing_keys = {_topic_key(item.topic) for item in queue}
        run_id = str(payload.get("run_id") or benchmark_path.parent.name or "benchmark")
        brief_dir = self.registry.channel_dir(channel.channel_id) / "benchmark_briefs" / _safe_slug(run_id)
        brief_dir.mkdir(parents=True, exist_ok=True)

        enqueued: list[ContentItem] = []
        for raw in briefs:
            if len(enqueued) >= max(0, int(limit)):
                break
            if not isinstance(raw, dict):
                continue
            topic = self._topic_from_brief(raw)
            topic_key = _topic_key(topic)
            if not topic or topic_key in existing_keys:
                continue

            brief_id = str(raw.get("brief_id") or f"brief-{len(enqueued) + 1}")
            brief_path = brief_dir / f"{_safe_slug(brief_id)}.json"
            brief_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            markdown_path = brief_dir / f"{_safe_slug(brief_id)}.md"
            markdown_path.write_text(self._render_brief_markdown(raw), encoding="utf-8")
            plan = plan_by_brief_id.get(brief_id)
            visual_plan_path: Path | None = None
            visual_plan_markdown_path: Path | None = None
            if plan is not None:
                visual_plan_path = brief_dir / f"{_safe_slug(brief_id)}-visual-plan.json"
                visual_plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
                visual_plan_markdown_path = brief_dir / f"{_safe_slug(brief_id)}-visual-plan.md"
                visual_plan_markdown_path.write_text(self._render_visual_plan_markdown(plan), encoding="utf-8")

            item = ContentItem(
                channel_id=channel.channel_id,
                topic=topic,
                source="faceless_shorts_benchmark",
                source_ref=brief_id,
                brief_path=str(brief_path),
                metadata={
                    "benchmark_run_id": run_id,
                    "benchmark_json_path": str(benchmark_path),
                    "source_pattern": raw.get("source_pattern"),
                    "source_channel": raw.get("source_channel"),
                    "source_url": raw.get("source_url"),
                    "source_views": raw.get("source_views"),
                    "markdown_brief_path": str(markdown_path),
                    "visual_recreation_plan_path": str(visual_plan_path) if visual_plan_path else None,
                    "visual_recreation_plan_markdown_path": str(visual_plan_markdown_path)
                    if visual_plan_markdown_path
                    else None,
                    "safety": "format_mechanics_only_no_copying",
                },
            )
            queue.append(item)
            existing_keys.add(topic_key)
            enqueued.append(item)

        self.calendar._write_queue(channel.channel_id, queue)
        self.logger.log(
            "benchmark_briefs_enqueued",
            {
                "channel_id": channel.channel_id,
                "benchmark_json_path": str(benchmark_path),
                "count": len(enqueued),
                "item_ids": [item.item_id for item in enqueued],
            },
        )
        return enqueued

    @staticmethod
    def _load_benchmark_payload(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise ChannelOpsError(f"Benchmark JSON does not exist: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ChannelOpsError(f"Invalid benchmark JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise ChannelOpsError(f"Invalid benchmark payload shape: {path}")
        return payload

    @staticmethod
    def _topic_from_brief(brief: dict[str, Any]) -> str:
        recreation_prompt = " ".join(str(brief.get("recreation_prompt") or "").split()).strip()
        if recreation_prompt:
            source_channel = " ".join(str(brief.get("source_channel") or "").split()).strip()
            source_views = brief.get("source_views") or 0
            suffix = "Nie kopiuj zrodla, tylko mechanike formatu."
            source_signal = f" Sygnał: {source_channel}, {source_views} views." if source_channel else ""
            return f"{recreation_prompt}{source_signal} {suffix}".strip()
        hook = " ".join(str(brief.get("hook_template") or "").split()).strip()
        topic = " ".join(str(brief.get("legal_recreation_topic") or "").split()).strip()
        pattern = " ".join(str(brief.get("source_pattern") or "faceless").split()).strip()
        if hook and topic:
            return f"Odtworz format {pattern}: {topic} Hook: {hook}. Nie kopiuj zrodla, tylko mechanike formatu."
        if topic:
            return f"Odtworz format {pattern}: {topic}. Nie kopiuj zrodla, tylko mechanike formatu."
        return ""

    @staticmethod
    def _render_brief_markdown(brief: dict[str, Any]) -> str:
        lines = [
            f"# {brief.get('brief_id') or 'benchmark brief'}",
            "",
            f"- source_pattern: `{brief.get('source_pattern') or ''}`",
            f"- source_channel: {brief.get('source_channel') or ''}",
            f"- source_url: {brief.get('source_url') or ''}",
            f"- source_views: {brief.get('source_views') or 0}",
            "",
            "## Legal recreation topic",
            "",
            str(brief.get("legal_recreation_topic") or ""),
            "",
            "## Hook",
            "",
            str(brief.get("hook_template") or ""),
            "",
        ]
        for key, title in (
            ("format_recipe", "Format recipe"),
            ("source_observation", "Source observation"),
            ("recreation_prompt", "Recreation prompt"),
            ("beat_timeline", "Beat timeline"),
            ("visual_rules", "Visual rules"),
            ("qa_rules", "QA rules"),
            ("forbidden_copying", "Forbidden copying"),
        ):
            values = brief.get(key)
            lines.extend([f"## {title}", ""])
            if isinstance(values, list):
                lines.extend(f"- {item}" for item in values)
            elif values:
                lines.append(str(values))
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _render_visual_plan_markdown(plan: dict[str, Any]) -> str:
        lines = [
            f"# {plan.get('plan_id') or 'visual recreation plan'}",
            "",
            f"- brief_id: `{plan.get('brief_id') or ''}`",
            f"- source_pattern: `{plan.get('source_pattern') or ''}`",
            f"- source_channel: {plan.get('source_channel') or ''}",
            f"- source_url: {plan.get('source_url') or ''}",
            f"- source_thumbnail_path: `{plan.get('source_thumbnail_path') or ''}`",
            "",
            "## Reference visual summary",
            "",
            str(plan.get("reference_visual_summary") or ""),
            "",
            "## Safe similarity target",
            "",
            str(plan.get("safe_similarity_target") or ""),
            "",
            "## Character sheet prompt",
            "",
            str(plan.get("character_sheet_prompt") or ""),
            "",
            "## Scene prompts",
            "",
        ]
        scenes = plan.get("scene_prompts")
        if isinstance(scenes, list):
            for scene in scenes:
                if not isinstance(scene, dict):
                    continue
                lines.extend(
                    [
                        f"### {scene.get('scene_id') or ''} / {scene.get('timing') or ''}",
                        f"- goal: {scene.get('goal') or ''}",
                        f"- image_prompt: {scene.get('image_prompt') or ''}",
                        f"- motion_prompt: {scene.get('motion_prompt') or ''}",
                        f"- overlay_text: {scene.get('overlay_text') or ''}",
                        f"- camera: {scene.get('camera') or ''}",
                        f"- negative_prompt: {scene.get('negative_prompt') or ''}",
                        "",
                    ]
                )
        lines.extend(
            [
                "## Thumbnail prompt",
                "",
                str(plan.get("thumbnail_prompt") or ""),
                "",
                "## Repair checklist",
                "",
            ]
        )
        repairs = plan.get("repair_checklist")
        if isinstance(repairs, list):
            lines.extend(f"- {item}" for item in repairs)
        return "\n".join(lines).strip() + "\n"


def _topic_key(topic: str) -> str:
    return re.sub(r"\s+", " ", topic.casefold()).strip()


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return slug or "brief"
