from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from atnos.core.config import Settings
from content.format_recreation_repair import FormatRecreationRepairService


def test_format_recreation_repair_writes_repaired_frames_and_reuses_audio_subtitles() -> None:
    with TemporaryDirectory(prefix="atnos-format-repair-") as tmpdir:
        root = Path(tmpdir)
        source = root / "source"
        source.mkdir()
        frames_dir = source / "visual_frames"
        frames_dir.mkdir()
        frame_paths = []
        for index, color in enumerate(((220, 20, 20), (20, 220, 20), (20, 20, 220), (220, 220, 20)), start=1):
            frame = frames_dir / f"scene_{index:02d}.png"
            Image.new("RGB", (1080, 1920), color).save(frame)
            frame_paths.append(frame)
        _write_source_files(source, frame_paths)
        review = root / "review.json"
        review.write_text(
            json.dumps(
                {
                    "signal_scores": [
                        {"name": "saturation_alignment", "score": 4.0},
                        {"name": "pacing_signal", "score": 4.0},
                    ],
                    "repair_instructions": ["lower saturation", "increase beat variance"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = FormatRecreationRepairService(settings)

        fake_mp4 = root / "repaired" / "final.mp4"
        fake_mp4.parent.mkdir(parents=True, exist_ok=True)
        fake_mp4.write_bytes(b"fake-mp4")
        with patch.object(
            service.renderer,
            "render",
            return_value=(fake_mp4, {"qa": {"duration_sec": 30.0, "width": 1080, "height": 1920, "has_audio": True}}),
        ), patch.object(
            service.render_qa,
            "build",
            return_value={"ok": True, "youtube_shorts_ready": True},
        ):
            result = service.run(
                source_short_dir=source,
                review_json_path=review,
                output_dir=root / "repaired",
            )

        assert result.status == "repaired"
        assert (root / "repaired" / "visual_storyboard.json").exists()
        assert (root / "repaired" / "narration.wav").read_bytes() == b"RIFFfake"
        assert (root / "repaired" / "subtitles.srt").read_text(encoding="utf-8")
        repaired_frames = sorted((root / "repaired" / "visual_frames_repaired").glob("*.png"))
        assert len(repaired_frames) == 4
        assert result.repaired_mp4 == str(fake_mp4)


def test_format_recreation_repair_reports_missing_artifacts() -> None:
    with TemporaryDirectory(prefix="atnos-format-repair-missing-") as tmpdir:
        root = Path(tmpdir)
        source = root / "source"
        source.mkdir()
        review = root / "review.json"
        review.write_text("{}", encoding="utf-8")

        result = FormatRecreationRepairService(
            Settings(app_env="test", app_root=root, data_dir=root / "data")
        ).run(
            source_short_dir=source,
            review_json_path=review,
            output_dir=root / "repaired",
        )

        assert result.status == "missing_source_artifacts"
        assert "missing" in result.notes[0]


def test_format_recreation_repair_expected_voiceover_prefers_script_json() -> None:
    with TemporaryDirectory(prefix="atnos-format-repair-voiceover-") as tmpdir:
        root = Path(tmpdir)
        script_json = root / "script.json"
        script_md = root / "script.md"
        script_json.write_text(
            json.dumps({"voiceover": "Tylko właściwy voiceover."}, ensure_ascii=False),
            encoding="utf-8",
        )
        script_md.write_text("# Tytuł\n\n## Voiceover\nNie używaj całego markdowna.", encoding="utf-8")

        text = FormatRecreationRepairService._expected_voiceover(script_json, fallback=script_md)

        assert text == "Tylko właściwy voiceover."


def _write_source_files(source: Path, frame_paths: list[Path]) -> None:
    (source / "script.md").write_text("# Test\n\nVoiceover", encoding="utf-8")
    (source / "script.json").write_text("{}", encoding="utf-8")
    (source / "narration.wav").write_bytes(b"RIFFfake")
    (source / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
    (source / "visual_storyboard.json").write_text(
        json.dumps(
            {
                "engine": "test",
                "frame_count": len(frame_paths),
                "scenes": ["A", "B", "C", "D"],
                "frames": [str(path) for path in frame_paths],
                "subtitle_safe_zone_reserved": True,
                "subtitle_safe_zone_height_px": 460,
                "max_subtitle_words_per_cue": 4,
                "duplicated_scene_text_layers": False,
                "full_scene_text_on_frame": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
