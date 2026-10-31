from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import math
import re
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Iterable
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen

from atnos.core.config import Settings
from content.video_frame_observation import FrameObservationPackage, VideoFrameObservationService
from content.visual_recreation_planner import (
    VisualRecreationPlan,
    build_visual_recreation_plans,
    render_visual_recreation_plan_markdown,
)


DEFAULT_FACELESS_AI_QUERIES: tuple[str, ...] = (
    "hashtag:aicatstory",
    "hashtag:aicat",
    "hashtag:catshorts",
    "hashtag:aistory",
    '"#aicat" "#shorts"',
    '"#catshorts" "#ai"',
    '"#aistory" "#shorts"',
    '"#facelessshorts" "#ai"',
    '"#ai" "#shorts" "story"',
    '"#shorts" "AI generated"',
)


@dataclass(frozen=True, slots=True)
class ShortsBenchmarkCandidate:
    title: str
    channel: str
    url: str
    video_id: str
    view_count: int
    duration_s: int | None
    query: str
    pattern: str
    format_score: float
    rank_score: float
    source_kind: str = "short"
    duration_anomaly: bool = False
    channel_url: str | None = None
    thumbnail_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ShortsPatternInsight:
    pattern: str
    sample_count: int
    total_views: int
    top_views: int
    median_views: int
    top_channels: tuple[str, ...]
    winning_signals: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ChannelBenchmarkInsight:
    channel: str
    channel_url: str | None
    sample_count: int
    total_views: int
    top_views: int
    median_views: int
    dominant_patterns: tuple[str, ...]
    top_video_url: str
    top_titles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VisualBenchmarkObservation:
    video_id: str
    title: str
    channel: str
    thumbnail_url: str | None
    thumbnail_path: str | None
    width: int | None
    height: int | None
    aspect_ratio: float | None
    contrast_score: float | None
    saturation_score: float | None
    visual_notes: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FormatReplicationBrief:
    brief_id: str
    source_pattern: str
    source_title: str
    source_channel: str
    source_url: str
    source_video_id: str
    source_views: int
    legal_recreation_topic: str
    hook_template: str
    beat_timeline: tuple[str, ...]
    format_recipe: tuple[str, ...]
    source_observation: str
    recreation_prompt: str
    visual_rules: tuple[str, ...]
    qa_rules: tuple[str, ...]
    forbidden_copying: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ShortsBenchmarkRun:
    run_id: str
    created_at: str
    output_dir: str
    query_count: int
    candidate_count: int
    top_candidates: tuple[ShortsBenchmarkCandidate, ...]
    pattern_insights: tuple[ShortsPatternInsight, ...]
    channel_insights: tuple[ChannelBenchmarkInsight, ...]
    visual_observations: tuple[VisualBenchmarkObservation, ...]
    frame_observations: tuple[FrameObservationPackage, ...]
    replication_briefs: tuple[FormatReplicationBrief, ...]
    visual_recreation_plans: tuple[VisualRecreationPlan, ...]
    markdown_path: str
    json_path: str
    briefs_path: str
    visual_recreation_plans_path: str
    frame_observations_path: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


EntryFetcher = Callable[[str, int], Iterable[dict[str, Any]]]


class FacelessShortsBenchmarkService:
    """Collects public YouTube metadata and turns it into safe format briefs.

    The service does not download videos, copy scripts, or bypass platform login.
    It learns format-level signals from public metadata and produces original
    recreation briefs for Atnos' local shorts pipeline.
    """

    def __init__(self, settings: Settings, *, fetcher: EntryFetcher | None = None) -> None:
        self.settings = settings
        self.fetcher = fetcher or self._fetch_with_yt_dlp

    def run(
        self,
        *,
        queries: Iterable[str] = DEFAULT_FACELESS_AI_QUERIES,
        per_query_limit: int = 8,
        top_limit: int = 12,
        brief_limit: int = 3,
        allow_duration_anomalies: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ShortsBenchmarkRun:
        created_at = datetime.now(UTC)
        run_id = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_faceless_ai_shorts_benchmark"
        output_dir = self.settings.data_dir / "learning" / "faceless-ai-shorts" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        query_list = [query.strip() for query in queries if query.strip()]
        candidates: list[ShortsBenchmarkCandidate] = []
        for query_index, query in enumerate(query_list, start=1):
            _emit_progress(
                progress_callback,
                phase="benchmark_query",
                message="Fetching public YouTube metadata for a faceless Shorts query.",
                query=query,
                query_index=query_index,
                query_total=len(query_list),
                candidates_so_far=len(candidates),
            )
            for entry in self.fetcher(query, per_query_limit):
                candidate = self._candidate_from_entry(
                    entry,
                    query=query,
                    allow_duration_anomalies=allow_duration_anomalies,
                )
                if candidate is not None:
                    candidates.append(candidate)
            _emit_progress(
                progress_callback,
                phase="benchmark_query_complete",
                message="Finished one public Shorts query.",
                query=query,
                query_index=query_index,
                query_total=len(query_list),
                candidates_so_far=len(candidates),
            )

        ranked = tuple(sorted(_dedupe_candidates(candidates), key=lambda item: item.rank_score, reverse=True))
        _emit_progress(
            progress_callback,
            phase="benchmark_rank",
            message="Ranking and deduplicating public Shorts candidates.",
            candidate_count=len(ranked),
        )
        top_candidates = ranked[:top_limit]
        insights = tuple(self._build_pattern_insights(ranked))
        channel_insights = tuple(self._build_channel_insights(ranked))
        visual_observations = tuple(self._build_visual_observations(top_candidates, output_dir=output_dir))
        frame_observations = tuple(
            self._build_frame_observations(visual_observations, output_dir=output_dir)
        )
        briefs = tuple(self._build_replication_briefs(top_candidates[:brief_limit]))

        json_path = output_dir / "benchmark_run.json"
        markdown_path = output_dir / "benchmark_report.md"
        briefs_path = output_dir / "replication_briefs.json"
        visual_plans_path = output_dir / "visual_recreation_plans.json"
        frame_observations_path = output_dir / "frame_observations.json"
        visual_recreation_plans = tuple(
            build_visual_recreation_plans(
                briefs=[brief.to_dict() for brief in briefs],
                observations=[item.to_dict() for item in visual_observations],
                frame_observations=[item.to_dict() for item in frame_observations],
            )
        )

        run = ShortsBenchmarkRun(
            run_id=run_id,
            created_at=created_at.isoformat(),
            output_dir=str(output_dir),
            query_count=len(query_list),
            candidate_count=len(ranked),
            top_candidates=top_candidates,
            pattern_insights=insights,
            channel_insights=channel_insights,
            visual_observations=visual_observations,
            frame_observations=frame_observations,
            replication_briefs=briefs,
            visual_recreation_plans=visual_recreation_plans,
            markdown_path=str(markdown_path),
            json_path=str(json_path),
            briefs_path=str(briefs_path),
            visual_recreation_plans_path=str(visual_plans_path),
            frame_observations_path=str(frame_observations_path),
        )

        json_path.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        briefs_path.write_text(
            json.dumps([brief.to_dict() for brief in briefs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        visual_plans_path.write_text(
            json.dumps([plan.to_dict() for plan in visual_recreation_plans], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        frame_observations_path.write_text(
            json.dumps([item.to_dict() for item in frame_observations], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_visual_recreation_plan_markdown(visual_recreation_plans, output_dir=output_dir)
        markdown_path.write_text(self._render_markdown(run), encoding="utf-8")
        self._write_obsidian_note(run)
        self._append_log(run)
        _emit_progress(
            progress_callback,
            phase="benchmark_written",
            message="Benchmark artifacts written.",
            json_path=str(json_path),
            markdown_path=str(markdown_path),
            candidate_count=len(ranked),
        )
        return run

    def _candidate_from_entry(
        self,
        entry: dict[str, Any],
        *,
        query: str,
        allow_duration_anomalies: bool = False,
    ) -> ShortsBenchmarkCandidate | None:
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        video_id = str(entry.get("id") or "").strip()
        if not title or not video_id:
            return None
        view_count = _safe_int(entry.get("view_count"))
        duration_s = _safe_optional_int(entry.get("duration"))
        channel = str(entry.get("channel") or entry.get("uploader") or "unknown").strip() or "unknown"
        channel_url = str(entry.get("channel_url") or entry.get("uploader_url") or "").strip() or None
        thumbnail_url = _thumbnail_url_from_entry(entry)
        pattern = classify_faceless_pattern(title)
        if pattern == "creator_tutorial":
            return None
        duration_anomaly = False
        source_kind = "short"
        if duration_s is None:
            return None
        if duration_s is not None and duration_s > 90:
            if not allow_duration_anomalies:
                return None
            normalized_title = _normalize(title)
            if pattern == "creator_tutorial" or _has_any(
                normalized_title,
                (
                    "tutorial",
                    "guide",
                    "how to",
                    "how i make",
                    "how i made",
                    "full course",
                    "step-by-step",
                    "full guide",
                    "ultimate guide",
                ),
            ):
                return None
            if pattern == "money_or_monetization" and not _has_any(normalized_title, ("#short", "shorts")):
                return None
            if not _has_any(normalized_title, ("#short", "shorts", "catshorts", "aicat", "catstory")):
                return None
            duration_anomaly = True
            source_kind = "hashtag_short_reference"
        format_score = score_faceless_format(title=title, duration_s=duration_s, query=query, pattern=pattern)
        rank_score = score_candidate_rank(
            view_count=view_count,
            duration_s=duration_s,
            format_score=format_score,
            pattern=pattern,
        )
        if duration_anomaly:
            rank_score = round(rank_score * 0.88, 3)
        return ShortsBenchmarkCandidate(
            title=title,
            channel=channel,
            url=url,
            video_id=video_id,
            view_count=view_count,
            duration_s=duration_s,
            query=query,
            pattern=pattern,
            format_score=format_score,
            rank_score=rank_score,
            source_kind=source_kind,
            duration_anomaly=duration_anomaly,
            channel_url=channel_url,
            thumbnail_url=thumbnail_url,
        )

    def _fetch_with_yt_dlp(self, query: str, limit: int) -> Iterable[dict[str, Any]]:
        try:
            import yt_dlp  # noqa: F401
        except Exception as exc:  # pragma: no cover - covered by integration/manual run
            raise RuntimeError("yt-dlp is required for public YouTube metadata benchmark.") from exc

        fast_entries = _fetch_with_youtube_results_page(query=query, limit=limit)
        if fast_entries:
            return fast_entries

        source, is_playlist_source = build_youtube_discovery_source(query, limit)
        try:
            entries = _fetch_with_yt_dlp_subprocess(
                source=source,
                limit=limit,
                is_playlist_source=is_playlist_source,
                timeout_s=45,
            )
        except (RuntimeError, subprocess.TimeoutExpired):
            entries = []
        if entries:
            return entries
        fallback_source = build_youtube_search_fallback_source(query, limit)
        try:
            return _fetch_with_yt_dlp_subprocess(
                source=fallback_source,
                limit=limit,
                is_playlist_source=False,
                timeout_s=45,
            )
        except (RuntimeError, subprocess.TimeoutExpired):
            return []

    def _build_pattern_insights(self, candidates: tuple[ShortsBenchmarkCandidate, ...]) -> list[ShortsPatternInsight]:
        grouped: dict[str, list[ShortsBenchmarkCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.pattern, []).append(candidate)

        insights: list[ShortsPatternInsight] = []
        for pattern, items in grouped.items():
            views = sorted((item.view_count for item in items), reverse=True)
            channels = []
            for item in sorted(items, key=lambda item: item.view_count, reverse=True):
                if item.channel not in channels:
                    channels.append(item.channel)
                if len(channels) >= 5:
                    break
            insights.append(
                ShortsPatternInsight(
                    pattern=pattern,
                    sample_count=len(items),
                    total_views=sum(views),
                    top_views=views[0] if views else 0,
                    median_views=views[len(views) // 2] if views else 0,
                    top_channels=tuple(channels),
                    winning_signals=_winning_signals_for_pattern(pattern),
                )
            )
        return sorted(insights, key=lambda item: (item.top_views, item.total_views), reverse=True)

    def _build_channel_insights(self, candidates: tuple[ShortsBenchmarkCandidate, ...]) -> list[ChannelBenchmarkInsight]:
        grouped: dict[str, list[ShortsBenchmarkCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.channel, []).append(candidate)

        insights: list[ChannelBenchmarkInsight] = []
        for channel, items in grouped.items():
            sorted_items = sorted(items, key=lambda item: item.view_count, reverse=True)
            views = sorted((item.view_count for item in items), reverse=True)
            patterns: list[str] = []
            for item in sorted_items:
                if item.pattern not in patterns:
                    patterns.append(item.pattern)
            top = sorted_items[0]
            insights.append(
                ChannelBenchmarkInsight(
                    channel=channel,
                    channel_url=top.channel_url,
                    sample_count=len(items),
                    total_views=sum(views),
                    top_views=views[0] if views else 0,
                    median_views=views[len(views) // 2] if views else 0,
                    dominant_patterns=tuple(patterns[:3]),
                    top_video_url=top.url,
                    top_titles=tuple(item.title for item in sorted_items[:3]),
                )
            )
        return sorted(insights, key=lambda item: (item.top_views, item.total_views), reverse=True)

    def _build_visual_observations(
        self,
        candidates: tuple[ShortsBenchmarkCandidate, ...],
        *,
        output_dir: Path,
    ) -> list[VisualBenchmarkObservation]:
        thumbnail_dir = output_dir / "thumbnails"
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        observations: list[VisualBenchmarkObservation] = []
        for candidate in candidates[:8]:
            observations.append(self._observe_candidate_thumbnail(candidate, thumbnail_dir=thumbnail_dir))
        return observations

    def _observe_candidate_thumbnail(
        self,
        candidate: ShortsBenchmarkCandidate,
        *,
        thumbnail_dir: Path,
    ) -> VisualBenchmarkObservation:
        if not candidate.thumbnail_url:
            return VisualBenchmarkObservation(
                video_id=candidate.video_id,
                title=candidate.title,
                channel=candidate.channel,
                thumbnail_url=None,
                thumbnail_path=None,
                width=None,
                height=None,
                aspect_ratio=None,
                contrast_score=None,
                saturation_score=None,
                visual_notes=("No public thumbnail URL found in metadata.",),
                status="missing_thumbnail",
            )

        thumbnail_path = thumbnail_dir / f"{_safe_filename(candidate.video_id)}.jpg"
        try:
            _download_url(candidate.thumbnail_url, thumbnail_path)
            return _analyze_thumbnail(candidate, thumbnail_path)
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            return VisualBenchmarkObservation(
                video_id=candidate.video_id,
                title=candidate.title,
                channel=candidate.channel,
                thumbnail_url=candidate.thumbnail_url,
                thumbnail_path=str(thumbnail_path),
                width=None,
                height=None,
                aspect_ratio=None,
                contrast_score=None,
                saturation_score=None,
                visual_notes=(f"Thumbnail observation failed: {exc}",),
                status="error",
            )

    def _build_frame_observations(
        self,
        visual_observations: tuple[VisualBenchmarkObservation, ...],
        *,
        output_dir: Path,
    ) -> list[FrameObservationPackage]:
        observer = VideoFrameObservationService(self.settings)
        observation_root = output_dir / "frame_observations"
        packages: list[FrameObservationPackage] = []
        for item in visual_observations:
            target_dir = observation_root / _safe_filename(item.video_id)
            if item.thumbnail_path and Path(item.thumbnail_path).exists():
                packages.append(
                    observer.observe_image_reference(
                        image_path=item.thumbnail_path,
                        output_dir=target_dir,
                        source_ref=item.video_id,
                        source_title=item.title,
                        source_channel=item.channel,
                        source_kind="public_thumbnail_reference",
                    )
                )
            else:
                packages.append(
                    observer.missing_reference(
                        output_dir=target_dir,
                        source_ref=item.video_id,
                        source_title=item.title,
                        source_channel=item.channel,
                        reason="No public thumbnail/frame reference available for this benchmark candidate.",
                    )
                )
        return packages

    def _build_replication_briefs(
        self,
        candidates: tuple[ShortsBenchmarkCandidate, ...],
    ) -> list[FormatReplicationBrief]:
        briefs: list[FormatReplicationBrief] = []
        used_patterns: set[str] = set()
        for candidate in candidates:
            if candidate.pattern in used_patterns and len(briefs) >= 2:
                continue
            used_patterns.add(candidate.pattern)
            brief = build_replication_brief(candidate, index=len(briefs) + 1)
            briefs.append(brief)
            if len(briefs) >= 3:
                break
        return briefs

    def _render_markdown(self, run: ShortsBenchmarkRun) -> str:
        lines = [
            "# Faceless AI Shorts Benchmark",
            "",
            f"- run_id: `{run.run_id}`",
            f"- created_at: `{run.created_at}`",
            f"- candidates: `{run.candidate_count}`",
            "",
            "## Top public candidates by benchmark score",
            "",
            "| rank | views | score | kind | pattern | channel | title |",
            "|---:|---:|---:|---|---|---|---|",
        ]
        for index, item in enumerate(run.top_candidates, start=1):
            safe_title = item.title.replace("|", " ")
            safe_channel = item.channel.replace("|", " ")
            lines.append(
                f"| {index} | {item.view_count} | {item.rank_score:.3f} | {item.source_kind} | "
                f"{item.pattern} | {safe_channel} | [{safe_title}]({item.url}) |"
            )
        lines.extend(["", "## Pattern insights", ""])
        for insight in run.pattern_insights:
            lines.extend(
                [
                    f"### {insight.pattern}",
                    f"- sample_count: `{insight.sample_count}`",
                    f"- top_views: `{insight.top_views}`",
                    f"- median_views: `{insight.median_views}`",
                    f"- top_channels: {', '.join(insight.top_channels) or 'unknown'}",
                    "- winning signals:",
                    *[f"  - {signal}" for signal in insight.winning_signals],
                    "",
                ]
            )
        lines.extend(["## Channel signals", ""])
        for insight in run.channel_insights[:10]:
            titles = "; ".join(insight.top_titles)
            channel_link = f"[{insight.channel}]({insight.channel_url})" if insight.channel_url else insight.channel
            lines.extend(
                [
                    f"### {channel_link}",
                    f"- sample_count: `{insight.sample_count}`",
                    f"- top_views: `{insight.top_views}`",
                    f"- median_views: `{insight.median_views}`",
                    f"- dominant_patterns: {', '.join(insight.dominant_patterns)}",
                    f"- top_video: {insight.top_video_url}",
                    f"- observed titles: {titles}",
                    "",
                ]
            )
        lines.extend(["## Visual thumbnail observations", ""])
        for item in run.visual_observations:
            lines.extend(
                [
                    f"### {item.channel} / {item.video_id}",
                    f"- status: `{item.status}`",
                    f"- thumbnail_path: `{item.thumbnail_path}`",
                    f"- size: `{item.width}x{item.height}`",
                    f"- contrast_score: `{item.contrast_score}`",
                    f"- saturation_score: `{item.saturation_score}`",
                    "- notes:",
                    *[f"  - {note}" for note in item.visual_notes],
                    "",
                ]
            )
        lines.extend(["## Frame-level observations", ""])
        for package in run.frame_observations:
            lines.extend(
                [
                    f"### {package.source_channel} / {package.source_ref}",
                    f"- status: `{package.status}`",
                    f"- mode: `{package.observation_mode}`",
                    f"- frame_count: `{package.frame_count}`",
                    f"- source_kind: `{package.source_kind}`",
                    f"- output_dir: `{package.output_dir}`",
                    f"- pacing hypothesis: {package.motion_pacing_hypothesis}",
                    f"- safe learning summary: {package.safe_learning_summary}",
                    "- aggregate notes:",
                    *[f"  - {note}" for note in package.aggregate_notes],
                    "",
                ]
            )
        lines.extend(["## Safe replication briefs", ""])
        for brief in run.replication_briefs:
            lines.extend(
                [
                    f"### {brief.brief_id}",
                    f"- source pattern: `{brief.source_pattern}`",
                    f"- source signal: [{brief.source_title}]({brief.source_url}) by {brief.source_channel}, {brief.source_views} views",
                    f"- legal recreation topic: {brief.legal_recreation_topic}",
                    f"- hook template: {brief.hook_template}",
                    "",
                    "Format recipe:",
                    *[f"- {rule}" for rule in brief.format_recipe],
                    "",
                    f"Source observation: {brief.source_observation}",
                    "",
                    f"Recreation prompt: {brief.recreation_prompt}",
                    "",
                    "Beat timeline:",
                    *[f"- {beat}" for beat in brief.beat_timeline],
                    "",
                    "Visual rules:",
                    *[f"- {rule}" for rule in brief.visual_rules],
                    "",
                    "QA rules:",
                    *[f"- {rule}" for rule in brief.qa_rules],
                    "",
                    "Forbidden copying:",
                    *[f"- {rule}" for rule in brief.forbidden_copying],
                    "",
                ]
            )
        lines.extend(["## AI visual recreation plans", ""])
        for plan in run.visual_recreation_plans:
            lines.extend(
                [
                    f"### {plan.plan_id}",
                    f"- brief_id: `{plan.brief_id}`",
                    f"- source_thumbnail_path: `{plan.source_thumbnail_path}`",
                    f"- reference_visual_summary: {plan.reference_visual_summary}",
                    f"- safe_similarity_target: {plan.safe_similarity_target}",
                    f"- character_sheet_prompt: {plan.character_sheet_prompt}",
                    f"- thumbnail_prompt: {plan.thumbnail_prompt}",
                    "- scene prompts:",
                    *[
                        f"  - {scene.scene_id} ({scene.timing}): {scene.image_prompt}"
                        for scene in plan.scene_prompts
                    ],
                    "",
                ]
            )
        lines.extend(
            [
                "## Operational rule",
                "",
                "Use these briefs to recreate format mechanics only: hook shape, pacing, scene rhythm, caption density and payoff. Do not copy exact scripts, footage, characters, audio, watermarks or creator identity.",
                "",
            ]
        )
        return "\n".join(lines)

    def _write_visual_recreation_plan_markdown(
        self,
        plans: tuple[VisualRecreationPlan, ...],
        *,
        output_dir: Path,
    ) -> None:
        plan_dir = output_dir / "visual_recreation_plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        for plan in plans:
            path = plan_dir / f"{_safe_filename(plan.plan_id)}.md"
            path.write_text(render_visual_recreation_plan_markdown(plan), encoding="utf-8")

    def _write_obsidian_note(self, run: ShortsBenchmarkRun) -> None:
        vault_path = getattr(self.settings, "obsidian_vault_path", None)
        export_enabled = bool(getattr(self.settings, "obsidian_export_enabled", False))
        if not export_enabled or not vault_path:
            return
        vault = Path(vault_path)
        if not vault.exists():
            return
        target = vault / "Atnos" / "Shorts" / f"{run.run_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(Path(run.markdown_path).read_text(encoding="utf-8"), encoding="utf-8")

    def _append_log(self, run: ShortsBenchmarkRun) -> None:
        log_path = self.settings.data_dir / "logs" / "faceless_shorts_learning.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "event": "faceless_ai_shorts_benchmark_recorded",
                        "run_id": run.run_id,
                        "candidate_count": run.candidate_count,
                        "channel_count": len(run.channel_insights),
                        "visual_observation_count": len(run.visual_observations),
                        "frame_observation_count": len(run.frame_observations),
                        "visual_recreation_plan_count": len(run.visual_recreation_plans),
                        "markdown_path": run.markdown_path,
                        "json_path": run.json_path,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def classify_faceless_pattern(title: str) -> str:
    text = _normalize(title)
    if _has_any(text, ("cat", "kitten", "kitty", "meow", "animal", "aicat", "catstory")):
        return "ai_animal_microstory"
    if _has_any(text, ("motivation", "motivational", "inspirational", "quotes", "life advice")):
        return "motivational_faceless"
    if _has_any(text, ("history", "ancient", "what if", "pov")):
        return "what_if_history_pov"
    if _has_any(text, ("finance", "money", "$", "earn", "income", "month")):
        return "money_or_monetization"
    if _has_any(text, ("tutorial", "guide", "how to", "create", "make", "faceless")):
        return "creator_tutorial"
    return "general_ai_faceless"


def score_faceless_format(*, title: str, duration_s: int | None, query: str, pattern: str) -> float:
    text = _normalize(" ".join((title, query)))
    score = 0.0
    if _has_any(text, ("short", "shorts", "#short", "reels")):
        score += 0.22
    if _has_any(text, ("ai", "sora", "generated", "faceless", "automation")):
        score += 0.22
    if pattern in {"ai_animal_microstory", "motivational_faceless", "what_if_history_pov"}:
        score += 0.18
    if _has_any(text, ("story", "pov", "what if", "poor", "became", "secret", "viral")):
        score += 0.16
    if duration_s is not None:
        if duration_s <= 75:
            score += 0.18
        elif duration_s <= 180:
            score += 0.08
        elif "short" in text or "#short" in text:
            score += 0.04
    return round(min(score, 1.0), 3)


def score_candidate_rank(*, view_count: int, duration_s: int | None, format_score: float, pattern: str) -> float:
    views_score = min(math.log10(max(view_count, 1)) / 7.0, 1.0)
    short_bonus = 0.12 if duration_s is not None and duration_s <= 75 else 0.0
    pattern_bonus = 0.08 if pattern == "ai_animal_microstory" else 0.04 if pattern != "creator_tutorial" else 0.0
    return round(min(views_score * 0.62 + format_score * 0.30 + short_bonus + pattern_bonus, 1.0), 3)


def build_youtube_discovery_source(query: str, limit: int) -> tuple[str, bool]:
    """Map a benchmark query to a yt-dlp source.

    `ytsearch` is useful for breadth, but YouTube hashtag pages often surface
    actual Shorts channels better than broad search terms. The boolean tells
    the fetcher whether yt-dlp should treat the source as a playlist.
    """

    clean = str(query or "").strip()
    tag = _hashtag_slug_from_query(clean)
    if tag:
        return f"https://www.youtube.com/hashtag/{quote(tag)}", True
    return f"ytsearch{max(1, int(limit))}:{clean}", False


def build_youtube_search_fallback_source(query: str, limit: int) -> str:
    clean = str(query or "").strip()
    tag = _hashtag_slug_from_query(clean)
    if tag:
        return f"ytsearch{max(1, int(limit))}:#{tag} #shorts AI"
    return f"ytsearch{max(1, int(limit))}:{clean}"


def build_replication_brief(candidate: ShortsBenchmarkCandidate, *, index: int) -> FormatReplicationBrief:
    if candidate.pattern == "ai_animal_microstory":
        topic = "Oryginalny kot-agent odkrywa błąd w automatycznym short pipeline i naprawia go przez QA."
        hook = "Ten kot zatrzymał słabego shorta."
        recipe = (
            "Zwierzę jest natychmiastowym protagonistą i zajmuje pierwszy kadr.",
            "Historia ma mieć jasny problem, eskalację i prosty payoff zamiast tutorialowego tłumaczenia.",
            "Każda scena musi zmieniać sytuację; nie wolno mielić jednego tekstu przez cały film.",
            "Mechanika ma przypominać źródłowy format: emocjonalna mikrohistoria, ale z oryginalnym bohaterem i treścią.",
        )
        beats = (
            "0-2s: pokaż absurdalną sytuację z oryginalnym zwierzęciem i jednym mocnym tekstem.",
            "2-8s: pokaż problem widoczny na ekranie, bez tłumaczenia tła.",
            "8-20s: eskaluj przez 2-3 krótkie sceny z innym kadrem.",
            "20-35s: pokaż payoff: zwierzę uruchamia proces/QA i wynik się poprawia.",
            "35-45s: domknij lekcją i loopiem do kolejnego odcinka.",
        )
        visuals = (
            "Oryginalna postać zwierzęcia; zero kopiowania designu z kanałów źródłowych.",
            "Cinematic close-up, szybkie zmiany scen, czytelny kontrast przed/po.",
            "Jedna warstwa napisów, max 4 słowa na cue, safe zone na dole.",
        )
    elif candidate.pattern == "motivational_faceless":
        topic = "Faceless lekcja o tym, dlaczego automatyzacja bez kontroli jakości marnuje czas."
        hook = "Automatyzacja bez QA kosztuje najwięcej."
        recipe = (
            "Zacznij od mocnego kontrastu: zła automatyzacja kontra kontrolowany system.",
            "Użyj rytmicznych, dużych napisów jako głównego nośnika, nie dekoracji.",
            "Daj jedną praktyczną lekcję, bez obietnic zarobku i bez fake luksusu.",
            "Mechanika ma przypominać viralowy motywacyjny short: teza, napięcie, prosta zasada, domknięcie.",
        )
        beats = (
            "0-2s: kontrastowa teza na czarnym tle.",
            "2-10s: pokaż złe zachowanie jako prosty symbol.",
            "10-25s: pokaż jedną zasadę i jedną konsekwencję.",
            "25-40s: pokaż checklistę naprawy zamiast pustej motywacji.",
        )
        visuals = (
            "Minimalistyczne karty, mocny kontrast, żadnych fake luksusowych obietnic.",
            "Ruch typografii pod rytm narracji.",
            "Brak stockowych uśmiechniętych ludzi i brak fałszywych wyników finansowych.",
        )
    else:
        topic = "Faceless AI workflow pokazany jako przed/po: chaos kontra plan, storyboard i QA."
        hook = "Nie kopiuj narzędzi. Kopiuj proces."
        recipe = (
            "Zbuduj short jako demonstrację procesu, nie listę narzędzi.",
            "Pokaż konkretny before/after i jeden artefakt dowodu.",
            "Zamień abstrakcyjne hasła AI na widoczne kroki: hook, asset plan, voice, render, QA.",
            "Mechanika ma przypominać działający faceless explainer: szybko, jasno, z dowodem na ekranie.",
        )
        beats = (
            "0-2s: pokaż decyzję lub błąd.",
            "2-10s: pokaż chaos bez procesu.",
            "10-25s: pokaż plan: hook, assety, voice, render.",
            "25-40s: pokaż QA i finalną paczkę prywatną do review.",
        )
        visuals = (
            "Pipeline cards, foldery artefaktów, dashboard QA.",
            "Wyraźny before/after, bez ściany tekstu.",
            "Kolory wysokiego kontrastu i duże napisy.",
        )

    return FormatReplicationBrief(
        brief_id=f"benchmark-brief-{index:02d}-{candidate.pattern}",
        source_pattern=candidate.pattern,
        source_title=candidate.title,
        source_channel=candidate.channel,
        source_url=candidate.url,
        source_video_id=candidate.video_id,
        source_views=candidate.view_count,
        legal_recreation_topic=topic,
        hook_template=hook,
        beat_timeline=beats,
        format_recipe=recipe,
        source_observation=(
            f"Source had {candidate.view_count} public views, duration={candidate.duration_s}s, "
            f"pattern={candidate.pattern}, kind={candidate.source_kind}, channel={candidate.channel}. "
            "Use this as format signal only; duration anomalies are not proof of a Shorts-native upload."
        ),
        recreation_prompt=(
            f"Odtwórz mechanikę formatu '{candidate.pattern}' z filmu '{candidate.title}' "
            f"bez kopiowania skryptu, audio, kadrów, postaci ani brandingu. Zrób oryginalny short: {topic} "
            f"Hook: {hook}"
        ),
        visual_rules=visuals,
        qa_rules=(
            "Hook musi być widoczny i słyszalny w pierwszych 2 sekundach.",
            "Nie używaj pełnego tytułu jako powtarzanej warstwy tekstu w każdej scenie.",
            "Final MP4 musi mieć 1080x1920, audio i napisy bez nachodzenia na główny kadr.",
            "Upload zostaje prywatny/manualny do zatwierdzenia.",
        ),
        forbidden_copying=(
            "Nie kopiuj exact script, audio, footage, miniatury, watermarków ani nazwy kanału.",
            "Nie używaj tej samej postaci/charakteru co źródłowy kanał.",
            "Nie przedstawiaj metadanych źródła jako własnych wyników.",
        ),
    )


def _dedupe_candidates(candidates: Iterable[ShortsBenchmarkCandidate]) -> list[ShortsBenchmarkCandidate]:
    deduped: dict[str, ShortsBenchmarkCandidate] = {}
    for candidate in candidates:
        key = candidate.video_id or candidate.url or candidate.title.casefold()
        existing = deduped.get(key)
        if existing is None or candidate.rank_score > existing.rank_score:
            deduped[key] = candidate
    return list(deduped.values())


def _emit_progress(callback: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if callback is None:
        return
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }
    try:
        callback(event)
    except Exception:
        return


def _fetch_with_yt_dlp_subprocess(
    *,
    source: str,
    limit: int,
    is_playlist_source: bool,
    timeout_s: int,
) -> list[dict[str, Any]]:
    args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--dump-json",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--socket-timeout",
        "15",
        "--extractor-retries",
        "1",
        "--retries",
        "1",
    ]
    if is_playlist_source:
        bounded_limit = max(1, int(limit))
        args.extend(["--playlist-end", str(bounded_limit), "--playlist-items", f"1-{bounded_limit}"])
    else:
        args.append("--no-playlist")
    args.append(source)
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    entries: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    if completed.returncode != 0 and not entries:
        raise RuntimeError(completed.stderr.strip() or f"yt-dlp failed for {source}")
    return entries


def _fetch_with_youtube_results_page(*, query: str, limit: int) -> list[dict[str, Any]]:
    search_query = _youtube_search_query_from_benchmark_query(query)
    url = f"https://www.youtube.com/results?search_query={quote(search_query)}&ucbcb=1"
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            html = response.read(2_500_000).decode("utf-8", "replace")
    except (OSError, URLError):
        return []
    entries = _entries_from_youtube_results_html(
        html,
        limit=max(40, int(limit) * 8),
        query=query,
        default_duration_s=45 if "#short" in search_query.lower() or "shorts" in search_query.lower() else None,
    )
    return _prioritize_short_like_entries(entries, limit=max(int(limit) * 4, int(limit)))


def _youtube_search_query_from_benchmark_query(query: str) -> str:
    clean = str(query or "").strip()
    tag = _hashtag_slug_from_query(clean)
    if tag:
        return f"#{tag} #shorts AI"
    return clean


def _entries_from_youtube_results_html(
    html: str,
    *,
    limit: int,
    query: str,
    default_duration_s: int | None = None,
) -> list[dict[str, Any]]:
    payload = _extract_yt_initial_data(html)
    if not payload:
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for renderer_kind, renderer in _walk_youtube_renderers(payload):
        video_id = str(renderer.get("videoId") or "").strip()
        if not video_id or video_id in seen:
            continue
        title = _text_from_youtube_obj(renderer.get("title") or renderer.get("headline"))
        if not title:
            continue
        view_count = _parse_youtube_view_count(
            _text_from_youtube_obj(renderer.get("viewCountText"))
            or _text_from_youtube_obj(renderer.get("shortViewCountText"))
            or _text_from_youtube_obj(renderer.get("shortViewCount"))
        )
        duration_s = _parse_youtube_duration(_duration_text_from_renderer(renderer))
        if duration_s is None and renderer_kind == "reelItemRenderer":
            duration_s = default_duration_s
        channel = (
            _text_from_youtube_obj(renderer.get("ownerText"))
            or _text_from_youtube_obj(renderer.get("longBylineText"))
            or _text_from_youtube_obj(renderer.get("shortBylineText"))
            or "unknown"
        )
        thumbnail_url = _thumbnail_from_renderer(renderer)
        entries.append(
            {
                "title": title,
                "channel": channel,
                "uploader": channel,
                "view_count": view_count,
                "duration": duration_s,
                "id": video_id,
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": thumbnail_url,
                "query": query,
            }
        )
        seen.add(video_id)
        if len(entries) >= max(1, int(limit)):
            break
    return entries


def _prioritize_short_like_entries(entries: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    def _score(entry: dict[str, Any]) -> tuple[int, int, int]:
        duration = _safe_optional_int(entry.get("duration"))
        title = _normalize(str(entry.get("title") or ""))
        short_duration = 1 if duration is not None and duration <= 90 else 0
        short_text = 1 if _has_any(title, ("#short", "shorts", "catshorts", "aicat", "aicatstory")) else 0
        views = _safe_int(entry.get("view_count"))
        return short_duration, short_text, views

    return sorted(entries, key=_score, reverse=True)[: max(1, int(limit))]


def _extract_yt_initial_data(html: str) -> dict[str, Any] | None:
    marker = "var ytInitialData ="
    start = html.find(marker)
    if start < 0:
        marker = "window[\"ytInitialData\"] ="
        start = html.find(marker)
    if start < 0:
        return None
    brace_start = html.find("{", start)
    if brace_start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(brace_start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(html[brace_start : index + 1])
                except json.JSONDecodeError:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


def _walk_youtube_renderers(payload: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(payload, dict):
        for key in ("videoRenderer", "reelItemRenderer"):
            value = payload.get(key)
            if isinstance(value, dict):
                yield key, value
        for value in payload.values():
            yield from _walk_youtube_renderers(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_youtube_renderers(item)


def _text_from_youtube_obj(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        simple = value.get("simpleText")
        if isinstance(simple, str):
            return simple.strip()
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(str(item.get("text") or "") for item in runs if isinstance(item, dict)).strip()
        accessibility = value.get("accessibility")
        if isinstance(accessibility, dict):
            label = (accessibility.get("accessibilityData") or {}).get("label")
            if isinstance(label, str):
                return label.strip()
    return ""


def _duration_text_from_renderer(renderer: dict[str, Any]) -> str:
    direct = _text_from_youtube_obj(renderer.get("lengthText"))
    if direct:
        return direct
    overlays = renderer.get("thumbnailOverlays")
    if isinstance(overlays, list):
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            text = _text_from_youtube_obj(
                (overlay.get("thumbnailOverlayTimeStatusRenderer") or {}).get("text")
            )
            if text:
                return text
    return ""


def _parse_youtube_duration(text: str) -> int | None:
    parts = [part for part in re.split(r"[:：]", text.strip()) if part.isdigit()]
    if not parts:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds if seconds > 0 else None


def _parse_youtube_view_count(text: str) -> int:
    normalized = text.replace("\xa0", " ").strip().lower()
    match = re.search(r"([\d,.]+)\s*([kmb]|tys\.?|mln|mld)?", normalized)
    if not match:
        return 0
    number_text = match.group(1)
    suffix = match.group(2) or ""
    if "," in number_text and "." not in number_text:
        number_text = number_text.replace(",", ".") if len(number_text.split(",")[-1]) <= 2 else number_text.replace(",", "")
    else:
        number_text = number_text.replace(",", "")
    try:
        value = float(number_text)
    except ValueError:
        return 0
    multiplier = 1
    if suffix in {"k", "tys", "tys."}:
        multiplier = 1_000
    elif suffix in {"m", "mln"}:
        multiplier = 1_000_000
    elif suffix in {"b", "mld"}:
        multiplier = 1_000_000_000
    return int(value * multiplier)


def _thumbnail_from_renderer(renderer: dict[str, Any]) -> str | None:
    thumbnails = (renderer.get("thumbnail") or {}).get("thumbnails")
    if not isinstance(thumbnails, list):
        return None
    urls = [str(item.get("url") or "").strip() for item in thumbnails if isinstance(item, dict)]
    urls = [url for url in urls if url]
    return urls[-1] if urls else None


def _thumbnail_url_from_entry(entry: dict[str, Any]) -> str | None:
    direct = str(entry.get("thumbnail") or "").strip()
    if direct:
        return direct
    thumbnails = entry.get("thumbnails")
    if not isinstance(thumbnails, list):
        return None
    urls: list[tuple[int, str]] = []
    for item in thumbnails:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        width = _safe_int(item.get("width"))
        height = _safe_int(item.get("height"))
        urls.append((width * height, url))
    if not urls:
        return None
    return sorted(urls, reverse=True)[0][1]


def _download_url(url: str, target: Path) -> None:
    request = Request(url, headers={"User-Agent": "Atnos faceless-shorts-benchmark/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = response.read(6 * 1024 * 1024)
    if not payload:
        raise ValueError("empty thumbnail response")
    target.write_bytes(payload)


def _analyze_thumbnail(candidate: ShortsBenchmarkCandidate, thumbnail_path: Path) -> VisualBenchmarkObservation:
    try:
        from PIL import Image, ImageStat
    except Exception as exc:  # pragma: no cover - Pillow is present in normal runtime
        raise RuntimeError("Pillow is required for thumbnail observation.") from exc

    with Image.open(thumbnail_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        stat = ImageStat.Stat(rgb)
        contrast_score = round(min(sum(stat.stddev) / 3.0 / 80.0, 1.0), 3)
        hsv = rgb.convert("HSV")
        saturation_score = round(min(ImageStat.Stat(hsv).mean[1] / 255.0, 1.0), 3)
    aspect_ratio = round(width / height, 3) if height else None
    notes = ["Thumbnail saved as visual reference; do not copy it directly."]
    if contrast_score >= 0.45:
        notes.append("High contrast visual signal; likely readable in feed.")
    if saturation_score >= 0.35:
        notes.append("Strong color saturation; useful for scroll-stopping style.")
    if width and height and width > height:
        notes.append("YouTube thumbnail is landscape; recreate Shorts frames vertically instead of copying crop.")
    return VisualBenchmarkObservation(
        video_id=candidate.video_id,
        title=candidate.title,
        channel=candidate.channel,
        thumbnail_url=candidate.thumbnail_url,
        thumbnail_path=str(thumbnail_path),
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        contrast_score=contrast_score,
        saturation_score=saturation_score,
        visual_notes=tuple(notes),
        status="observed",
    )


def _winning_signals_for_pattern(pattern: str) -> tuple[str, ...]:
    if pattern == "ai_animal_microstory":
        return (
            "animal/cat subject is instantly readable in feed",
            "story escalation beats create retention without a human host",
            "absurd/emotional payoff increases shareability",
        )
    if pattern == "motivational_faceless":
        return (
            "simple contrast claim works without showing a face",
            "large kinetic captions carry the video",
            "useful takeaway beats generic inspirational filler",
        )
    if pattern == "money_or_monetization":
        return (
            "money framing attracts clicks, but needs no fake revenue promise",
            "comparison and workflow framing is safer than get-rich claims",
        )
    return (
        "strong hook and clear before/after structure",
        "short scenes with one idea per beat",
        "visible proof or artifact beats abstract explanation",
    )


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _hashtag_slug_from_query(query: str) -> str | None:
    clean = str(query or "").strip()
    if not clean:
        return None
    match = re.match(r"^https?://(?:www\.)?youtube\.com/hashtag/([^/?#]+)", clean, flags=re.IGNORECASE)
    if match:
        return _sanitize_hashtag_slug(match.group(1))
    if clean.casefold().startswith("hashtag:"):
        return _sanitize_hashtag_slug(clean.split(":", maxsplit=1)[1])
    return None


def _sanitize_hashtag_slug(value: str) -> str | None:
    slug = str(value or "").strip().lstrip("#")
    slug = re.sub(r"[^0-9A-Za-z_]+", "", slug)
    return slug or None


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-")
    return safe[:80] or "thumbnail"
