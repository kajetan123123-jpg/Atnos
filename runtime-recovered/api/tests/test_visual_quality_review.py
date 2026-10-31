from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from content.visual_quality_review import VisualQualityReviewService


class VisualQualityReviewServiceTest(unittest.TestCase):
    def test_good_storyboard_passes_visual_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=5, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(
                json.dumps(
                    {
                        "engine": "test",
                        "scenes": [
                            "Mocny hook dla widza",
                            "Pierwszy konkret",
                            "Drugi konkret",
                            "Trzeci konkret",
                            "Naturalne CTA",
                        ],
                        "frames": [str(frame) for frame in frames],
                    }
                ),
                encoding="utf-8",
            )

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertTrue(report.passed)
            self.assertGreaterEqual(report.overall_score, 7)
            self.assertTrue((root / "visual_quality_report.json").exists())

    def test_mojibake_storyboard_fails_visual_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=5, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(
                json.dumps({"scenes": ["To wyglÄ…da jak uszkodzony tekst"], "frames": [str(frame) for frame in frames]}),
                encoding="utf-8",
            )

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertFalse(report.passed)
            self.assertTrue(any("mojibake" in issue for issue in report.issues))

    def test_too_few_frames_fail_visual_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=2, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(json.dumps({"scenes": ["Hook", "CTA"], "frames": [str(frame) for frame in frames]}), encoding="utf-8")

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertFalse(report.passed)
            self.assertTrue(any("fewer than 4" in issue for issue in report.issues))

    def test_repeated_scene_text_layers_fail_visual_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=5, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(
                json.dumps(
                    {
                        "engine": "test",
                        "duplicated_scene_text_layers": True,
                        "scenes": [
                            "Ten sam tekst jako tytul",
                            "Ten sam tekst jako podpis",
                            "Ten sam tekst jako overlay",
                            "Ten sam tekst jako caption",
                            "Ten sam tekst jako subtitles",
                        ],
                        "frames": [str(frame) for frame in frames],
                    }
                ),
                encoding="utf-8",
            )

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertFalse(report.passed)
            self.assertTrue(any("repeats the same scene text" in issue for issue in report.issues))

    def test_missing_subtitle_safe_zone_warns_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=5, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(
                json.dumps(
                    {
                        "engine": "test",
                        "subtitle_safe_zone_reserved": False,
                        "scenes": [
                            "Mocny hook",
                            "Pierwszy konkret",
                            "Drugi konkret",
                            "Trzeci konkret",
                            "Naturalne CTA",
                        ],
                        "frames": [str(frame) for frame in frames],
                    }
                ),
                encoding="utf-8",
            )

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertFalse(report.passed)
            self.assertTrue(any("subtitle safe zone" in issue for issue in report.issues))

    def test_full_scene_text_on_frame_fails_visual_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=5, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(
                json.dumps(
                    {
                        "engine": "test",
                        "full_scene_text_on_frame": True,
                        "scenes": [
                            "Dlugi tekst narracji umieszczony jako glowny tytul na kadrze.",
                            "Pierwszy konkret",
                            "Drugi konkret",
                            "Trzeci konkret",
                            "Naturalne CTA",
                        ],
                        "frames": [str(frame) for frame in frames],
                    }
                ),
                encoding="utf-8",
            )

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertFalse(report.passed)
            self.assertTrue(any("full narration text" in issue for issue in report.issues))

    def test_tiny_subtitle_safe_zone_fails_visual_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=5, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(
                json.dumps(
                    {
                        "engine": "test",
                        "subtitle_safe_zone_reserved": True,
                        "subtitle_safe_zone_height_px": 260,
                        "scenes": [
                            "Mocny hook",
                            "Pierwszy konkret",
                            "Drugi konkret",
                            "Trzeci konkret",
                            "Naturalne CTA",
                        ],
                        "frames": [str(frame) for frame in frames],
                    }
                ),
                encoding="utf-8",
            )

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertFalse(report.passed)
            self.assertTrue(any("safe zone is too small" in issue for issue in report.issues))

    def test_long_subtitle_cues_fail_visual_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = _create_frames(root, count=5, size=(1080, 1920))
            manifest = root / "visual_storyboard.json"
            manifest.write_text(
                json.dumps(
                    {
                        "engine": "test",
                        "subtitle_safe_zone_reserved": True,
                        "subtitle_safe_zone_height_px": 460,
                        "max_subtitle_words_per_cue": 9,
                        "scenes": [
                            "Mocny hook",
                            "Pierwszy konkret",
                            "Drugi konkret",
                            "Trzeci konkret",
                            "Naturalne CTA",
                        ],
                        "frames": [str(frame) for frame in frames],
                    }
                ),
                encoding="utf-8",
            )

            report = VisualQualityReviewService(min_score=7).review(
                output_dir=root,
                storyboard_manifest=manifest,
                frames=frames,
            )

            self.assertFalse(report.passed)
            self.assertTrue(any("subtitle cues are too long" in issue for issue in report.issues))


def _create_frames(root: Path, *, count: int, size: tuple[int, int]) -> list[Path]:
    frames: list[Path] = []
    for index in range(count):
        path = root / f"scene_{index + 1:02d}.png"
        image = Image.new("RGB", size, (20 + index * 30, 40 + index * 20, 80 + index * 10))
        draw = ImageDraw.Draw(image)
        draw.rectangle((120, 240, 960, 1560), outline=(255, 220 - index * 10, 120), width=12)
        draw.ellipse((280 + index * 20, 620, 820, 1160), fill=(180, 80 + index * 20, 70 + index * 18))
        image.save(path)
        frames.append(path)
    return frames


if __name__ == "__main__":
    unittest.main()
