from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile

from atnos.core.config import Settings
from content.motion_render_ffmpeg import MotionShortRenderService


def test_build_motion_command_uses_zoompan_concat_and_subtitles() -> None:
    command = MotionShortRenderService.build_motion_ffmpeg_command(
        ffmpeg_exe="ffmpeg",
        frame_paths=[Path("F:/media/scene1.png"), Path("F:/media/scene2.png")],
        audio_path=Path("F:/media/narration.wav"),
        subtitles_path=Path("F:/media/subtitles.srt"),
        output_path=Path("F:/media/final.mp4"),
    )

    joined = " ".join(command)

    assert "zoompan" in joined
    assert "concat=n=2:v=1:a=0" in joined
    assert "subtitles=filename=" in joined
    assert "-filter_complex" in command
    assert command[-1] == "F:\\media\\final.mp4"


def test_motion_render_returns_motion_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = MotionShortRenderService(settings)
        output_dir = root / "out"
        output_dir.mkdir()
        frames = [output_dir / "scene1.png", output_dir / "scene2.png"]
        for frame in frames:
            frame.write_bytes(b"png")
        audio = output_dir / "narration.wav"
        subtitles = output_dir / "subtitles.srt"
        audio.write_bytes(b"wav")
        subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\nTest\n", encoding="utf-8")

        def fake_run(*args, **kwargs):
            (output_dir / "final.mp4").write_bytes(b"mp4")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with patch("content.motion_render_ffmpeg.subprocess.run", side_effect=fake_run), patch(
            "content.motion_render_ffmpeg.ShortRenderService._inspect_output",
            return_value={
                "duration_sec": 30.0,
                "width": 1080,
                "height": 1920,
                "has_audio": True,
                "size_bytes": 3,
                "warnings": [],
            },
        ):
            final_mp4, metadata = service.render_motion(
                frame_paths=frames,
                audio_path=audio,
                subtitles_path=subtitles,
                output_dir=output_dir,
            )

        assert final_mp4 == output_dir / "final.mp4"
        assert metadata["engine"] == "ffmpeg-ken-burns-motion-subtitles"
        assert metadata["motion"]["scene_count"] == 2
        assert metadata["watchdog_recovered"] is False
