from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Protocol

from atnos.core.config import Settings
from atnos.services.agent_quality_runtime_service import AgentQualityRuntimeService, QualityCheck
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeProbeService, LocalShortRuntimeSnapshot

from content.elevenlabs_tts import ElevenLabsNarration
from content.expressive_voice_director import ExpressiveVoiceDirector
from content.hook_variants import build_hook_variants
from content.render_ffmpeg import ShortRenderService
from content.render_qa import RenderQaService
from content.script_generator import ScriptGenerator, ShortScript
from content.script_quality_gate import ScriptQualityGate, ScriptQualityReport
from content.script_repair import ScriptRepair
from content.short_asset_plan import (
    build_short_asset_plan,
    render_pre_render_quality_report,
    render_visual_direction_markdown,
)
from content.subtitles import ShortSubtitleService
from content.thumbnail import ThumbnailService
from content.tts_piper import PiperNarration
from content.visual_quality_review import VisualQualityReviewService
from content.visual_storyboard import VisualStoryboardService
from content.youtube_package import YouTubeReleasePackage, build_youtube_release_package


class ShortRuntimeProbe(Protocol):
    def snapshot(self) -> LocalShortRuntimeSnapshot:
        ...


class NarrationService(Protocol):
    supports_expressive_tags: bool

    def generate(self, *, text: str, output_dir: Path) -> Path:
        ...


@dataclass(frozen=True, slots=True)
class ShortAgentResult:
    status: str
    topic: str
    output_dir: Path
    final_mp4: Path | None
    quality_report: dict[str, object]
    artifacts: dict[str, str]
    repair_attempts: int
    quality_run: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "topic": self.topic,
            "output_dir": str(self.output_dir),
            "final_mp4": str(self.final_mp4) if self.final_mp4 is not None else None,
            "quality_report": self.quality_report,
            "artifacts": dict(self.artifacts),
            "repair_attempts": self.repair_attempts,
            "quality_run": self.quality_run,
        }


class ShortAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        generator: ScriptGenerator | None = None,
        tts: NarrationService | None = None,
        subtitles: ShortSubtitleService | None = None,
        thumbnail: ThumbnailService | None = None,
        visuals: VisualStoryboardService | None = None,
        visual_quality: VisualQualityReviewService | None = None,
        renderer: ShortRenderService | None = None,
        runtime_probe: ShortRuntimeProbe | None = None,
    ) -> None:
        self.settings = settings
        self.generator = generator or ScriptGenerator(settings)
        self.quality_gate = ScriptQualityGate(min_score=float(settings.shorts_min_quality_score))
        self.repair = ScriptRepair(self.generator)
        self.tts = tts or self._build_tts(settings)
        self.voice_director = ExpressiveVoiceDirector()
        self.subtitles = subtitles or ShortSubtitleService(settings)
        thumbnail_brand = settings.shorts_watermark_text if settings.shorts_show_watermark else ""
        self.thumbnail = thumbnail or ThumbnailService(brand_label=thumbnail_brand)
        self.visuals = visuals or VisualStoryboardService(
            brand_color=settings.shorts_brand_color,
            brand_color_dark=settings.shorts_brand_color_dark,
            show_watermark=settings.shorts_show_watermark,
            watermark_text=settings.shorts_watermark_text,
        )
        self.visual_quality = visual_quality or VisualQualityReviewService(
            min_score=float(settings.shorts_min_quality_score)
        )
        self.renderer = renderer or ShortRenderService(settings)
        self.render_qa = RenderQaService()
        self.runtime_probe = runtime_probe or LocalShortRuntimeProbeService(settings)
        self.quality_runtime = AgentQualityRuntimeService(settings)

    def run(self, topic: str, *, force_repair: bool = False) -> ShortAgentResult:
        normalized_topic = " ".join(topic.split()).strip()
        if not normalized_topic:
            raise ValueError("Short topic cannot be empty.")
        output_dir = self._create_output_dir(normalized_topic)
        self._log("started", {"topic": normalized_topic, "output_dir": str(output_dir)})

        readiness = self.runtime_probe.snapshot()
        (output_dir / "runtime_readiness.json").write_text(
            json.dumps(readiness.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not readiness.ready:
            result = self._runtime_not_ready_result(
                topic=normalized_topic,
                output_dir=output_dir,
                readiness=readiness,
            )
            self._write_runtime_not_ready_report(result=result, readiness=readiness)
            result = self._attach_quality_run(result, readiness=readiness)
            self._log("runtime_not_ready", result.to_dict())
            return result

        brief = self.generator.build_brief(normalized_topic)
        (output_dir / "brief.md").write_text(brief, encoding="utf-8")
        script = self.generator.generate(topic=normalized_topic, brief=brief)
        report = self.quality_gate.evaluate(script=script, topic=normalized_topic)
        repair_attempts = 0
        while (force_repair or not report.passed) and repair_attempts < 3:
            force_repair = False
            repair_attempts += 1
            script = self.repair.repair(topic=normalized_topic, brief=brief, script=script, report=report)
            report = self.quality_gate.evaluate(script=script, topic=normalized_topic)

        if not report.passed:
            fallback_script = ScriptGenerator._automation_fallback_script(normalized_topic)
            fallback_report = self.quality_gate.evaluate(script=fallback_script, topic=normalized_topic)
            if fallback_report.passed:
                self._log(
                    "script_fallback_after_failed_repairs",
                    {
                        "topic": normalized_topic,
                        "repair_attempts": repair_attempts,
                        "previous_issues": list(report.issues),
                        "fallback_score": fallback_report.overall_score,
                    },
                )
                script = fallback_script
                report = fallback_report

        self._write_script(output_dir, script)
        (output_dir / "script_quality.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not report.passed:
            result = self._needs_review_result(
                topic=normalized_topic,
                output_dir=output_dir,
                report=report,
                repair_attempts=repair_attempts,
            )
            self._write_production_report(result=result, script=script, report=report)
            result = self._attach_quality_run(result, script_report=report)
            self._log("needs_human_review", result.to_dict())
            return result

        hook_variants = build_hook_variants(script=script, topic=normalized_topic)
        asset_plan = build_short_asset_plan(
            script=script,
            topic=normalized_topic,
            hook_variants=hook_variants,
        )
        (output_dir / "hook_variants.json").write_text(
            json.dumps(hook_variants.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "short_asset_plan.json").write_text(
            json.dumps(asset_plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "visual_direction.md").write_text(
            render_visual_direction_markdown(
                topic=normalized_topic,
                hook_variants=hook_variants,
                asset_plan=asset_plan,
            ),
            encoding="utf-8",
        )
        (output_dir / "quality_report.md").write_text(
            render_pre_render_quality_report(
                hook_variants=hook_variants,
                asset_plan=asset_plan,
                script_score=float(report.overall_score),
            ),
            encoding="utf-8",
        )

        voice_plan = self.voice_director.build(script)
        (output_dir / "voice_direction.json").write_text(
            json.dumps(voice_plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        narration_text = voice_plan.expressive_text if getattr(self.tts, "supports_expressive_tags", False) else voice_plan.plain_text
        narration = self.tts.generate(text=narration_text, output_dir=output_dir)
        subtitles = self.subtitles.generate(audio_path=narration, output_dir=output_dir, fallback_text=script.voiceover)
        thumbnail = self.thumbnail.generate(title=script.title, topic=normalized_topic, output_dir=output_dir)
        storyboard = self.visuals.generate(
            script=script,
            topic=normalized_topic,
            output_dir=output_dir,
            asset_plan=asset_plan.to_dict(),
        )
        visual_report = self.visual_quality.review(
            output_dir=output_dir,
            storyboard_manifest=storyboard.manifest_path,
            frames=list(storyboard.frames),
        )
        if not visual_report.passed:
            result = self._visual_repair_result(
                topic=normalized_topic,
                output_dir=output_dir,
                report=report,
                repair_attempts=repair_attempts,
            )
            self._write_production_report(result=result, script=script, report=report)
            result = self._attach_quality_run(result, script_report=report)
            self._log("needs_visual_repair", result.to_dict())
            return result
        final_mp4, render_metadata = self.renderer.render(
            frame_paths=list(storyboard.frames) or [thumbnail],
            audio_path=narration,
            subtitles_path=subtitles,
            output_dir=output_dir,
        )
        qa = self.render_qa.build(
            final_mp4=final_mp4,
            render_metadata=render_metadata,
            subtitles_path=subtitles,
            audio_path=narration,
            expected_text=script.voiceover,
        )
        (output_dir / "render_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        youtube_package = build_youtube_release_package(
            topic=normalized_topic,
            script=script,
            output_dir=output_dir,
            render_qa=qa,
            quality_report=report,
            privacy=self.settings.youtube_upload_privacy or "private",
            requires_upload_approval=self.settings.require_upload_approval,
        )
        (output_dir / "youtube_upload_package.json").write_text(
            json.dumps(youtube_package.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        render_ready = bool(qa.get("ok")) and qa.get("youtube_shorts_ready") is not False
        result_status = "manual_upload_ready" if render_ready else "render_qa_failed"
        result = ShortAgentResult(
            status=result_status,
            topic=normalized_topic,
            output_dir=output_dir,
            final_mp4=final_mp4,
            quality_report=report.to_dict(),
            artifacts={
                "script": str(output_dir / "script.md"),
                "hook_variants": str(output_dir / "hook_variants.json"),
                "short_asset_plan": str(output_dir / "short_asset_plan.json"),
                "visual_direction": str(output_dir / "visual_direction.md"),
                "pre_render_quality_report": str(output_dir / "quality_report.md"),
                "voice_direction": str(output_dir / "voice_direction.json"),
                "narration": str(narration),
                "subtitles": str(subtitles),
                "thumbnail": str(thumbnail),
                "visual_storyboard": str(storyboard.manifest_path),
                "visual_quality_report": str(output_dir / "visual_quality_report.json"),
                "final_mp4": str(final_mp4),
                "render_qa": str(output_dir / "render_qa.json"),
                "youtube_upload_package": str(output_dir / "youtube_upload_package.json"),
                "production_report": str(output_dir / "production_report.md"),
            },
            repair_attempts=repair_attempts,
        )
        self._write_production_report(result=result, script=script, report=report, youtube_package=youtube_package)
        result = self._attach_quality_run(result, script_report=report, render_qa=qa)
        self._log("completed" if render_ready else "render_qa_failed", result.to_dict())
        return result

    @staticmethod
    def _build_tts(settings: Settings) -> NarrationService:
        if settings.shorts_tts_backend == "elevenlabs":
            return ElevenLabsNarration(settings)
        return PiperNarration(settings)

    def _visual_repair_result(
        self,
        *,
        topic: str,
        output_dir: Path,
        report: ScriptQualityReport,
        repair_attempts: int,
    ) -> ShortAgentResult:
        return ShortAgentResult(
            status="NEEDS_VISUAL_REPAIR",
            topic=topic,
            output_dir=output_dir,
            final_mp4=None,
            quality_report=report.to_dict(),
            artifacts={
                "script": str(output_dir / "script.md"),
                "hook_variants": str(output_dir / "hook_variants.json"),
                "short_asset_plan": str(output_dir / "short_asset_plan.json"),
                "visual_direction": str(output_dir / "visual_direction.md"),
                "pre_render_quality_report": str(output_dir / "quality_report.md"),
                "script_quality": str(output_dir / "script_quality.json"),
                "thumbnail": str(output_dir / "thumbnail.png"),
                "visual_storyboard": str(output_dir / "visual_storyboard.json"),
                "visual_quality_report": str(output_dir / "visual_quality_report.json"),
                "production_report": str(output_dir / "production_report.md"),
            },
            repair_attempts=repair_attempts,
        )

    def _create_output_dir(self, topic: str) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.casefold().encode("ascii", "ignore").decode("ascii")).strip("-")
        if not slug:
            slug = "short"
        base_name = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{slug[:48]}"
        path = self.settings.shorts_output_dir / base_name
        suffix = 2
        while path.exists():
            path = self.settings.shorts_output_dir / f"{base_name}-{suffix}"
            suffix += 1
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _write_script(output_dir: Path, script: ShortScript) -> None:
        (output_dir / "script.md").write_text(script.to_markdown(), encoding="utf-8")
        (output_dir / "script.json").write_text(json.dumps(script.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _needs_review_result(
        self,
        *,
        topic: str,
        output_dir: Path,
        report: ScriptQualityReport,
        repair_attempts: int,
    ) -> ShortAgentResult:
        return ShortAgentResult(
            status="NEEDS_HUMAN_REVIEW",
            topic=topic,
            output_dir=output_dir,
            final_mp4=None,
            quality_report=report.to_dict(),
            artifacts={
                "script": str(output_dir / "script.md"),
                "script_quality": str(output_dir / "script_quality.json"),
                "production_report": str(output_dir / "production_report.md"),
            },
            repair_attempts=repair_attempts,
        )

    def _runtime_not_ready_result(
        self,
        *,
        topic: str,
        output_dir: Path,
        readiness: LocalShortRuntimeSnapshot,
    ) -> ShortAgentResult:
        return ShortAgentResult(
            status="runtime_not_ready",
            topic=topic,
            output_dir=output_dir,
            final_mp4=None,
            quality_report={
                "overall_score": 0.0,
                "passed": False,
                "runtime": readiness.to_dict(),
            },
            artifacts={
                "runtime_readiness": str(output_dir / "runtime_readiness.json"),
                "production_report": str(output_dir / "production_report.md"),
            },
            repair_attempts=0,
        )

    def _write_production_report(
        self,
        *,
        result: ShortAgentResult,
        script: ShortScript,
        report: ScriptQualityReport,
        youtube_package: YouTubeReleasePackage | None = None,
    ) -> None:
        lines = [
            "# Short Production Report",
            "",
            f"- status: {result.status}",
            f"- topic: {result.topic}",
            f"- output_dir: {result.output_dir}",
            f"- final_mp4: {result.final_mp4 or 'not_rendered'}",
            f"- repair_attempts: {result.repair_attempts}",
            f"- overall_score: {report.overall_score}",
            "",
            "## Quality",
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            "",
        ]
        if youtube_package is not None:
            lines.extend(
                [
                    "## YouTube upload package",
                    f"- package_status: {youtube_package.status}",
                    f"- privacy: {youtube_package.privacy}",
                    f"- requires_upload_approval: {youtube_package.requires_upload_approval}",
                    f"- title: {youtube_package.title}",
                    f"- package_path: {result.output_dir / 'youtube_upload_package.json'}",
                    "",
                    "### Upload checklist",
                    "- review final.mp4 manually before upload",
                    "- keep privacy=private until approved",
                    "- verify subtitles and thumbnail before publication",
                    "",
                ]
            )
        lines.extend(
            [
            "## Script",
            script.to_markdown(),
            ]
        )
        (result.output_dir / "production_report.md").write_text("\n".join(lines), encoding="utf-8")

    def _attach_quality_run(
        self,
        result: ShortAgentResult,
        *,
        script_report: ScriptQualityReport | None = None,
        render_qa: dict[str, object] | None = None,
        readiness: LocalShortRuntimeSnapshot | None = None,
    ) -> ShortAgentResult:
        checks = self._build_quality_checks(result=result, script_report=script_report, render_qa=render_qa, readiness=readiness)
        quality_run = self.quality_runtime.record_run(
            task=f"Local short: {result.topic}",
            area="short_pipeline",
            checks=checks,
            artifacts=list(result.artifacts.values()),
            notes=f"ShortAgent status: {result.status}; repair_attempts={result.repair_attempts}",
            min_score=float(self.settings.shorts_min_quality_score),
        )
        self._append_quality_run_to_report(result.output_dir / "production_report.md", quality_run)
        return replace(result, quality_run=quality_run)

    def _build_quality_checks(
        self,
        *,
        result: ShortAgentResult,
        script_report: ScriptQualityReport | None,
        render_qa: dict[str, object] | None,
        readiness: LocalShortRuntimeSnapshot | None,
    ) -> list[QualityCheck]:
        checks: list[QualityCheck] = []
        if readiness is not None:
            checks.append(
                QualityCheck(
                    name="runtime_readiness",
                    status="pass" if readiness.ready else "fail",
                    evidence=readiness.reason,
                    score=10.0 if readiness.ready else 0.0,
                    required=True,
                )
            )
        if script_report is not None:
            checks.append(
                QualityCheck(
                    name="script_quality",
                    status="pass" if script_report.passed else "fail",
                    evidence=(
                        "Script quality gate passed."
                        if script_report.passed
                        else "Script quality gate failed: " + "; ".join(script_report.issues[:6])
                    ),
                    score=float(script_report.overall_score),
                    required=True,
                )
            )
        if render_qa is not None:
            warnings = [str(item) for item in (render_qa.get("warnings") or [])]
            ok = bool(render_qa.get("ok")) and not warnings
            checks.append(
                QualityCheck(
                    name="render_quality",
                    status="pass" if ok else "warn",
                    evidence="Render QA passed." if ok else "Render QA warnings: " + "; ".join(warnings[:6]),
                    score=10.0 if ok else 6.5,
                    required=False,
                )
            )
        raw_visual_report_path = result.artifacts.get("visual_quality_report")
        visual_report_path = Path(raw_visual_report_path) if raw_visual_report_path else None
        if visual_report_path is not None and visual_report_path.exists():
            try:
                visual_report = json.loads(visual_report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                visual_report = {"passed": False, "overall_score": 0, "issues": ["invalid visual report JSON"]}
            visual_issues = [str(item) for item in (visual_report.get("issues") or [])]
            visual_passed = bool(visual_report.get("passed"))
            checks.append(
                QualityCheck(
                    name="visual_quality",
                    status="pass" if visual_passed else "fail",
                    evidence="Visual storyboard gate passed." if visual_passed else "Visual issues: " + "; ".join(visual_issues[:6]),
                    score=float(visual_report.get("overall_score") or 0),
                    required=True,
                )
            )
        artifact_names = tuple(result.artifacts.keys())
        missing = [
            name
            for name, raw_path in result.artifacts.items()
            if name != "production_report" and (not Path(raw_path).exists() or Path(raw_path).stat().st_size <= 0)
        ]
        checks.append(
            QualityCheck(
                name="artifact_package",
                status="pass" if not missing else "fail",
                evidence=(
                    "Artifacts present: " + ", ".join(artifact_names)
                    if not missing
                    else "Missing or empty artifacts: " + ", ".join(missing)
                ),
                score=10.0 if not missing else 0.0,
                required=result.status == "manual_upload_ready",
            )
        )
        return checks

    @staticmethod
    def _append_quality_run_to_report(report_path: Path, quality_run: dict[str, object]) -> None:
        if not report_path.exists():
            return
        existing = report_path.read_text(encoding="utf-8")
        appendix = [
            "",
            "## Quality runtime",
            f"- status: {quality_run.get('status')}",
            f"- score: {quality_run.get('score')}",
            f"- report_path: {quality_run.get('report_path')}",
        ]
        report_path.write_text(existing.rstrip() + "\n" + "\n".join(appendix) + "\n", encoding="utf-8")

    @staticmethod
    def _write_runtime_not_ready_report(
        *,
        result: ShortAgentResult,
        readiness: LocalShortRuntimeSnapshot,
    ) -> None:
        missing = [component for component in readiness.components if not component.ready]
        lines = [
            "# Short Production Report",
            "",
            f"- status: {result.status}",
            f"- topic: {result.topic}",
            f"- output_dir: {result.output_dir}",
            "- final_mp4: not_rendered",
            "- repair_attempts: 0",
            f"- runtime_reason: {readiness.reason}",
            "",
            "## Runtime readiness",
        ]
        if missing:
            lines.extend(
                f"- {component.label}: {component.reason} ({component.path or 'not configured'})"
                for component in missing
            )
        else:
            lines.append("- no missing components reported")
        lines.extend(
            [
                "",
                "## Next step",
                "Configure missing local short runtime components and rerun the pipeline.",
            ]
        )
        (result.output_dir / "production_report.md").write_text("\n".join(lines), encoding="utf-8")

    def _log(self, event: str, payload: dict[str, Any]) -> None:
        path = self.settings.data_dir / "logs" / "short_pipeline.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now(UTC).isoformat(), "event": event, **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
