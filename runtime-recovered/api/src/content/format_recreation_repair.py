from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import shutil
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance

from atnos.core.config import Settings
from content.format_recreation_review import FormatRecreationReviewService
from content.render_ffmpeg import ShortRenderService
from content.render_qa import RenderQaService
from content.visual_quality_review import VisualQualityReviewService


@dataclass(frozen=True, slots=True)
class FormatRecreationRepairResult:
    status: str
    source_short_dir: str
    output_dir: str
    repaired_mp4: str | None
    repair_plan_path: str
    render_qa_path: str | None
    visual_quality_report_path: str | None
    followup_review_path: str | None
    followup_decision: str | None
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FormatRecreationRepairService:
    """Applies a safe visual repair pass from format-review instructions.

    This pass does not recreate source footage. It adjusts the generated
    storyboard frames to better match observed mechanics: lower saturation and
    stronger beat-to-beat frame changes.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.renderer = ShortRenderService(settings)
        self.render_qa = RenderQaService()
        self.visual_quality = VisualQualityReviewService(min_score=float(settings.shorts_min_quality_score))
        self.review_service = FormatRecreationReviewService(settings)

    def run(
        self,
        *,
        source_short_dir: str | Path,
        review_json_path: str | Path,
        output_dir: str | Path,
        reference_observation_json: str | Path | None = None,
    ) -> FormatRecreationRepairResult:
        source_dir = Path(source_short_dir).resolve()
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        review = json.loads(Path(review_json_path).read_text(encoding="utf-8"))
        instructions = tuple(str(item) for item in review.get("repair_instructions") or ())
        notes = [f"repair_instructions={len(instructions)}"]

        required = self._required_source_files(source_dir)
        missing = [name for name, path in required.items() if not path.exists()]
        if missing:
            result = FormatRecreationRepairResult(
                status="missing_source_artifacts",
                source_short_dir=str(source_dir),
                output_dir=str(output_path),
                repaired_mp4=None,
                repair_plan_path=str(output_path / "format_recreation_repair_plan.json"),
                render_qa_path=None,
                visual_quality_report_path=None,
                followup_review_path=None,
                followup_decision=None,
                notes=tuple([f"missing: {', '.join(missing)}"]),
            )
            self._write_result(result, output_path)
            return result

        frame_paths = self._frame_paths(required["visual_storyboard"])
        repaired_frames = self._repair_frames(
            frame_paths=frame_paths,
            output_dir=output_path / "visual_frames_repaired",
            review=review,
        )
        storyboard_path = self._write_repaired_storyboard(
            source_storyboard=required["visual_storyboard"],
            output_dir=output_path,
            frames=repaired_frames,
            instructions=instructions,
        )
        visual_report = self.visual_quality.review(
            output_dir=output_path,
            storyboard_manifest=storyboard_path,
            frames=repaired_frames,
        )
        shutil.copyfile(required["script"], output_path / "script.md")
        shutil.copyfile(required["script_json"], output_path / "script.json")
        shutil.copyfile(required["narration"], output_path / "narration.wav")
        shutil.copyfile(required["subtitles"], output_path / "subtitles.srt")

        final_mp4, render_metadata = self.renderer.render(
            frame_paths=repaired_frames,
            audio_path=output_path / "narration.wav",
            subtitles_path=output_path / "subtitles.srt",
            output_dir=output_path,
        )
        script_text = self._expected_voiceover(output_path / "script.json", fallback=output_path / "script.md")
        qa = self.render_qa.build(
            final_mp4=final_mp4,
            render_metadata=render_metadata,
            subtitles_path=output_path / "subtitles.srt",
            audio_path=output_path / "narration.wav",
            expected_text=script_text,
        )
        render_qa_path = output_path / "render_qa.json"
        render_qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

        followup_review_path = None
        followup_decision = None
        if reference_observation_json is not None:
            followup_dir = output_path / "followup_format_review"
            followup = self.review_service.review_from_paths(
                reference_observation_json=reference_observation_json,
                candidate_video_path=final_mp4,
                output_dir=followup_dir,
            )
            followup_review_path = followup_dir / "format_recreation_review.json"
            followup_decision = followup.decision
            notes.append(f"followup_score={followup.overall_score}")

        repair_plan_path = output_path / "format_recreation_repair_plan.json"
        repair_plan_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "source_short_dir": str(source_dir),
                    "source_review": str(Path(review_json_path).resolve()),
                    "instructions": list(instructions),
                    "operations": [
                        "lower_saturation",
                        "increase_beat_to_beat_visual_variance",
                        "preserve_vertical_1080x1920",
                        "render_with_existing_audio_and_subtitles",
                    ],
                    "safety": "Do not copy source footage, characters, audio, watermark, branding or script.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result = FormatRecreationRepairResult(
            status="repaired",
            source_short_dir=str(source_dir),
            output_dir=str(output_path),
            repaired_mp4=str(final_mp4),
            repair_plan_path=str(repair_plan_path),
            render_qa_path=str(render_qa_path),
            visual_quality_report_path=str(output_path / "visual_quality_report.json"),
            followup_review_path=str(followup_review_path) if followup_review_path is not None else None,
            followup_decision=followup_decision,
            notes=tuple(notes),
        )
        self._write_result(result, output_path)
        return result

    @staticmethod
    def _required_source_files(source_dir: Path) -> dict[str, Path]:
        return {
            "script": source_dir / "script.md",
            "script_json": source_dir / "script.json",
            "visual_storyboard": source_dir / "visual_storyboard.json",
            "narration": source_dir / "narration.wav",
            "subtitles": source_dir / "subtitles.srt",
        }

    @staticmethod
    def _frame_paths(storyboard_path: Path) -> list[Path]:
        payload = json.loads(storyboard_path.read_text(encoding="utf-8"))
        raw_frames = payload.get("frames")
        if not isinstance(raw_frames, list):
            raise ValueError("visual_storyboard.json has no frames list.")
        return [Path(str(path)).resolve() for path in raw_frames]

    def _repair_frames(
        self,
        *,
        frame_paths: list[Path],
        output_dir: Path,
        review: dict[str, Any],
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        saturation_issue = _score(review, "saturation_alignment") < 7.0
        pacing_issue = _score(review, "pacing_signal") < 7.0
        repaired: list[Path] = []
        for index, frame_path in enumerate(frame_paths, start=1):
            target = output_dir / f"scene_{index:02d}_repaired.png"
            with Image.open(frame_path) as image:
                rgb = image.convert("RGB")
                repaired_image = self._repair_image(
                    rgb,
                    index=index,
                    total=len(frame_paths),
                    saturation_issue=saturation_issue,
                    pacing_issue=pacing_issue,
                )
                repaired_image.save(target)
            repaired.append(target)
        return repaired

    @staticmethod
    def _repair_image(
        image: Image.Image,
        *,
        index: int,
        total: int,
        saturation_issue: bool,
        pacing_issue: bool,
    ) -> Image.Image:
        # The factors intentionally create a rhythm of dark/bright beats while
        # keeping colors muted. This is a format repair, not source copying.
        color_factors = (0.16, 0.24, 0.12, 0.30, 0.14, 0.22)
        brightness_factors = (0.58, 0.98, 0.46, 1.12, 0.66, 1.04)
        contrast_factors = (1.45, 1.85, 1.30, 1.95, 1.55, 1.80)
        offset = (index - 1) % len(color_factors)
        result = image
        if saturation_issue:
            result = ImageEnhance.Color(result).enhance(color_factors[offset])
        if pacing_issue:
            result = ImageEnhance.Brightness(result).enhance(brightness_factors[offset])
            result = ImageEnhance.Contrast(result).enhance(contrast_factors[offset])
            result = _draw_beat_repair_overlay(result, index=index, total=total)
        return result

    @staticmethod
    def _write_repaired_storyboard(
        *,
        source_storyboard: Path,
        output_dir: Path,
        frames: list[Path],
        instructions: tuple[str, ...],
    ) -> Path:
        payload = json.loads(source_storyboard.read_text(encoding="utf-8"))
        payload["engine"] = "pillow-editorial-storyboard-format-repair"
        payload["format_repair_applied"] = True
        payload["format_repair_instructions"] = list(instructions)
        payload["frames"] = [str(frame) for frame in frames]
        payload["frame_count"] = len(frames)
        storyboard_path = output_dir / "visual_storyboard.json"
        storyboard_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return storyboard_path

    @staticmethod
    def _expected_voiceover(script_json_path: Path, *, fallback: Path) -> str:
        try:
            payload = json.loads(script_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback.read_text(encoding="utf-8")
        if isinstance(payload, dict):
            voiceover = str(payload.get("voiceover") or "").strip()
            if voiceover:
                return voiceover
            parts = [
                str(payload.get("hook") or "").strip(),
                *(str(item).strip() for item in payload.get("body") or [] if str(item).strip()),
                str(payload.get("cta") or "").strip(),
            ]
            joined = " ".join(item for item in parts if item)
            if joined:
                return joined
        return fallback.read_text(encoding="utf-8")

    @staticmethod
    def _write_result(result: FormatRecreationRepairResult, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "format_recreation_repair_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "format_recreation_repair_report.md").write_text(
            _render_repair_report(result),
            encoding="utf-8",
        )


def _draw_beat_repair_overlay(image: Image.Image, *, index: int, total: int) -> Image.Image:
    result = image.copy()
    overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = result.size
    if index % 2 == 1:
        draw.rectangle((0, 0, w, h), fill=(0, 0, 0, 52))
        draw.rectangle((0, int(h * 0.70), w, h), fill=(0, 0, 0, 105))
        draw.line((80, int(h * 0.24), w - 80, int(h * 0.24)), fill=(245, 245, 240, 90), width=8)
    else:
        draw.rectangle((0, 0, w, int(h * 0.18)), fill=(245, 245, 235, 45))
        draw.rectangle((0, int(h * 0.70), w, h), fill=(0, 0, 0, 82))
        for step in range(5):
            y = int(h * (0.28 + step * 0.065))
            draw.line((100, y, w - 100, y + 18), fill=(220, 225, 220, 65), width=5)
    progress = int((w - 160) * min(1.0, index / max(total, 1)))
    draw.rounded_rectangle((80, h - 90, 80 + progress, h - 76), radius=7, fill=(245, 245, 235, 160))
    return Image.alpha_composite(result.convert("RGBA"), overlay).convert("RGB")


def _score(review: dict[str, Any], name: str) -> float:
    for item in review.get("signal_scores") or []:
        if isinstance(item, dict) and item.get("name") == name:
            try:
                return float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _render_repair_report(result: FormatRecreationRepairResult) -> str:
    notes = "\n".join(f"- {item}" for item in result.notes)
    return "\n".join(
        [
            "# Format Recreation Repair Report",
            "",
            f"- status: {result.status}",
            f"- repaired_mp4: {result.repaired_mp4 or 'n/a'}",
            f"- output_dir: {result.output_dir}",
            f"- followup_decision: {result.followup_decision or 'not_run'}",
            "",
            "## Notes",
            notes or "- none",
            "",
            "## Safety",
            "- This pass repairs format mechanics only.",
            "- Do not copy source footage, characters, audio, watermark, branding or script.",
            "",
        ]
    )
