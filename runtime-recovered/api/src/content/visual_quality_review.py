from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


@dataclass(frozen=True, slots=True)
class VisualQualityReport:
    passed: bool
    overall_score: float
    frame_count: int
    frame_score: float
    contrast_score: float
    diversity_score: float
    text_quality_score: float
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "overall_score": self.overall_score,
            "frame_count": self.frame_count,
            "frame_score": self.frame_score,
            "contrast_score": self.contrast_score,
            "diversity_score": self.diversity_score,
            "text_quality_score": self.text_quality_score,
            "issues": list(self.issues),
        }


class VisualQualityReviewService:
    def __init__(self, *, min_score: float = 7.0) -> None:
        self.min_score = float(min_score)

    def review(self, *, output_dir: Path, storyboard_manifest: Path, frames: list[Path]) -> VisualQualityReport:
        manifest = self._load_manifest(storyboard_manifest)
        issues: list[str] = []
        if not storyboard_manifest.exists():
            issues.append("visual_storyboard.json missing")
        if len(frames) < 4:
            issues.append("storyboard has fewer than 4 visual scenes")

        frame_metrics = [self._frame_metrics(frame) for frame in frames]
        missing = [metric for metric in frame_metrics if not metric["exists"]]
        if missing:
            issues.append("one or more storyboard frames are missing")
        bad_size = [metric for metric in frame_metrics if metric["exists"] and metric["size"] != (1080, 1920)]
        if bad_size:
            issues.append("one or more storyboard frames are not 1080x1920")
        tiny = [metric for metric in frame_metrics if metric["exists"] and int(metric["bytes"]) < 48_000]
        if tiny:
            issues.append("one or more storyboard frames look too small to be a finished visual")

        frame_score = self._frame_score(frame_metrics)
        contrast_score = self._contrast_score(frame_metrics)
        diversity_score = self._diversity_score(frame_metrics)
        text_quality_score = self._text_quality_score(manifest, issues)
        overall = round((frame_score * 0.3) + (contrast_score * 0.25) + (diversity_score * 0.2) + (text_quality_score * 0.25), 2)
        passed = overall >= self.min_score and not self._has_critical_issue(issues)
        report = VisualQualityReport(
            passed=passed,
            overall_score=overall,
            frame_count=len(frames),
            frame_score=frame_score,
            contrast_score=contrast_score,
            diversity_score=diversity_score,
            text_quality_score=text_quality_score,
            issues=tuple(issues),
        )
        (output_dir / "visual_quality_report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"_invalid": True}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _frame_metrics(frame: Path) -> dict[str, object]:
        if not frame.exists():
            return {"path": str(frame), "exists": False, "size": None, "bytes": 0, "brightness": 0.0, "contrast": 0.0, "mean_rgb": (0, 0, 0)}
        with Image.open(frame) as image:
            rgb = image.convert("RGB")
            gray = rgb.convert("L")
            stat_gray = ImageStat.Stat(gray)
            stat_rgb = ImageStat.Stat(rgb)
            return {
                "path": str(frame),
                "exists": True,
                "size": image.size,
                "bytes": frame.stat().st_size,
                "brightness": float(stat_gray.mean[0]),
                "contrast": float(stat_gray.stddev[0]),
                "mean_rgb": tuple(float(value) for value in stat_rgb.mean[:3]),
            }

    @staticmethod
    def _frame_score(metrics: list[dict[str, object]]) -> float:
        if not metrics:
            return 0.0
        score = 10.0
        if len(metrics) < 4:
            score -= 2.5
        for metric in metrics:
            if not metric["exists"]:
                score -= 3.0
            if metric["exists"] and metric["size"] != (1080, 1920):
                score -= 2.0
            if metric["exists"] and int(metric["bytes"]) < 48_000:
                score -= 1.0
        return round(max(0.0, min(10.0, score)), 2)

    @staticmethod
    def _contrast_score(metrics: list[dict[str, object]]) -> float:
        valid = [float(metric["contrast"]) for metric in metrics if metric["exists"]]
        if not valid:
            return 0.0
        average = sum(valid) / len(valid)
        return round(max(0.0, min(10.0, average / 42.0 * 10.0)), 2)

    @staticmethod
    def _diversity_score(metrics: list[dict[str, object]]) -> float:
        colors = [metric["mean_rgb"] for metric in metrics if metric["exists"]]
        if len(colors) < 2:
            return 0.0
        distances: list[float] = []
        for index, left in enumerate(colors):
            for right in colors[index + 1 :]:
                distances.append(math.sqrt(sum((float(left[i]) - float(right[i])) ** 2 for i in range(3))))
        if not distances:
            return 0.0
        average = sum(distances) / len(distances)
        scene_count_bonus = min(2.0, max(0, len(colors) - 3) * 0.5)
        return round(max(0.0, min(10.0, average / 35.0 * 8.0 + scene_count_bonus)), 2)

    @staticmethod
    def _text_quality_score(manifest: dict[str, Any], issues: list[str]) -> float:
        if manifest.get("_invalid"):
            issues.append("visual_storyboard.json is invalid JSON")
            return 0.0
        scenes = manifest.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            issues.append("visual storyboard does not include scene text")
            return 6.5
        combined = " ".join(str(scene) for scene in scenes)
        suspicious = ("Ä", "Ĺ", "Ă", "â€", "â€“", "�")
        if any(token in combined for token in suspicious):
            issues.append("visual storyboard contains mojibake or broken encoding")
            return 3.0
        if manifest.get("duplicated_scene_text_layers") is True:
            issues.append("visual storyboard repeats the same scene text in multiple on-screen layers")
            return 4.0
        if manifest.get("full_scene_text_on_frame") is True:
            issues.append("visual storyboard places full narration text on the frame instead of reserving it for subtitles")
            return 5.5
        if manifest.get("subtitle_safe_zone_reserved") is False:
            issues.append("visual storyboard does not reserve a clean subtitle safe zone")
            return 6.0
        safe_zone_height = manifest.get("subtitle_safe_zone_height_px")
        if isinstance(safe_zone_height, int | float) and safe_zone_height < 420:
            issues.append("visual storyboard subtitle safe zone is too small for mobile captions")
            return 6.0
        max_words = manifest.get("max_subtitle_words_per_cue")
        if isinstance(max_words, int | float) and max_words > 4:
            issues.append("subtitle cues are too long and can cover the main visual")
            return 6.0
        long_scenes = [scene for scene in scenes if len(str(scene)) > 180]
        if long_scenes:
            issues.append("one or more visual scenes have too much text for a phone screen")
            return 6.5
        return 10.0

    @staticmethod
    def _has_critical_issue(issues: list[str]) -> bool:
        critical_fragments = (
            "missing",
            "not 1080x1920",
            "mojibake",
            "invalid JSON",
            "fewer than 4",
            "repeats the same scene text",
            "full narration text",
            "safe zone is too small",
            "subtitle cues are too long",
        )
        return any(any(fragment in issue for fragment in critical_fragments) for issue in issues)
