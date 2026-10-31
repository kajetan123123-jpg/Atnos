from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from atnos.core.config import Settings
from content.render_ffmpeg import ShortRenderService


def make_settings(root: Path) -> Settings:
    env_path = root / ".env"
    env_path.write_text(
        "\n".join(
            (
                f"SHORTS_OUTPUT_DIR={root / 'data' / 'shorts'}",
                "FFMPEG_EXE=C:/Tools/ffmpeg/bin/ffmpeg.exe",
            )
        ),
        encoding="utf-8",
    )
    return Settings(app_env="test", app_root=root, data_dir=root / "data", _env_file=env_path)


class ShortRenderServiceTest(unittest.TestCase):
    def test_build_fast_ffmpeg_command_uses_vertical_short_settings_and_subtitles(self) -> None:
        command = ShortRenderService.build_fast_ffmpeg_command(
            ffmpeg_exe="ffmpeg",
            image_path=Path("F:/media/thumb.png"),
            audio_path=Path("F:/media/narration.wav"),
            subtitles_path=Path("F:/media/subtitles.srt"),
            output_path=Path("F:/media/final.mp4"),
        )

        joined = " ".join(command)

        self.assertIn("-loop", command)
        self.assertIn("-shortest", command)
        self.assertIn("scale=1080:1920", joined)
        self.assertIn("crop=1080:1920", joined)
        self.assertIn("subtitles=filename=", joined)
        self.assertIn("force_style=", joined)
        self.assertIn("FontName=Montserrat ExtraBold", joined)
        self.assertIn("Outline=4", joined)
        self.assertIn("Shadow=2", joined)
        self.assertIn("Alignment=2", joined)
        self.assertIn("MarginV=120", joined)
        self.assertIn("-b:a 192k", joined)
        self.assertEqual(command[-1], "F:\\media\\final.mp4")

    def test_build_single_frame_motion_command_uses_ken_burns_and_ass_subtitles(self) -> None:
        command = ShortRenderService.build_single_frame_motion_ffmpeg_command(
            ffmpeg_exe="ffmpeg",
            image_path=Path("F:/media/thumb.png"),
            audio_path=Path("F:/media/narration.wav"),
            subtitles_path=Path("F:/media/subtitles.ass"),
            output_path=Path("F:/media/final.mp4"),
            duration_s=10.0,
        )

        joined = " ".join(command)

        self.assertIn("zoompan", joined)
        self.assertIn("fade=t=in", joined)
        self.assertIn("fade=t=out", joined)
        self.assertIn("ass=filename=", joined)
        self.assertIn("fontsdir=", joined)
        self.assertIn("-b:a 192k", joined)

    def test_build_ken_burns_slideshow_command_uses_xfade(self) -> None:
        command = ShortRenderService.build_ken_burns_slideshow_ffmpeg_command(
            ffmpeg_exe="ffmpeg",
            frame_paths=[Path("F:/media/scene1.png"), Path("F:/media/scene2.png")],
            frame_durations=[4.0, 4.0],
            audio_path=Path("F:/media/narration.wav"),
            subtitles_path=Path("F:/media/subtitles.ass"),
            output_path=Path("F:/media/final.mp4"),
        )

        joined = " ".join(command)

        self.assertIn("zoompan", joined)
        self.assertIn("xfade=transition=fade", joined)
        self.assertIn("ass=filename=", joined)
        self.assertIn("-filter_complex", command)

    def test_build_fast_slideshow_command_uses_concat_frames(self) -> None:
        command = ShortRenderService.build_fast_slideshow_ffmpeg_command(
            ffmpeg_exe="ffmpeg",
            concat_file=Path("F:/media/frames_concat.txt"),
            audio_path=Path("F:/media/narration.wav"),
            subtitles_path=Path("F:/media/subtitles.srt"),
            output_path=Path("F:/media/final.mp4"),
        )

        joined = " ".join(command)

        self.assertIn("-f", command)
        self.assertIn("concat", command)
        self.assertIn("F:\\media\\frames_concat.txt", command)
        self.assertIn("scale=1080:1920", joined)
        self.assertIn("subtitles=filename=", joined)
        self.assertEqual(command[-1], "F:\\media\\final.mp4")

    def test_fast_render_returns_metadata_without_moviepy_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            service = ShortRenderService(settings)
            output_dir = root / "out"
            output_dir.mkdir()
            image = output_dir / "thumbnail.png"
            audio = output_dir / "narration.wav"
            subtitles = output_dir / "subtitles.srt"
            image.write_bytes(b"png")
            audio.write_bytes(b"wav")
            subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\nTest\n", encoding="utf-8")

            def fake_run(*args, **kwargs):
                (output_dir / "final.mp4").write_bytes(b"mp4")
                return SimpleNamespace(returncode=0, stderr="", stdout="")

            with patch("content.render_ffmpeg.subprocess.run", side_effect=fake_run), patch.object(
                ShortRenderService,
                "_inspect_output",
                return_value={
                    "duration_sec": 30.0,
                    "width": 1080,
                    "height": 1920,
                    "has_audio": True,
                    "size_bytes": 3,
                    "warnings": [],
                },
            ):
                final_mp4, metadata = service._render_fast_ffmpeg(
                    frame_paths=[image],
                    audio_path=audio,
                    subtitles_path=subtitles,
                    output_dir=output_dir,
                )

            self.assertEqual(final_mp4, output_dir / "final.mp4")
            self.assertEqual(metadata["engine"], "ffmpeg-ken-burns-ass-subtitles")
            self.assertFalse(metadata["watchdog_recovered"])
            self.assertEqual(metadata["watchdog_timeout_s"], 0)
            self.assertEqual(metadata["qa"]["warnings"], [])
            self.assertEqual(metadata["captions"]["format"], "ass")


if __name__ == "__main__":
    unittest.main()
