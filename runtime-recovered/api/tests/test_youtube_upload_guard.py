from __future__ import annotations

from pathlib import Path

from atnos.core.config import Settings
from youtube import upload_guard
from youtube.upload_guard import validate_upload_video_file
from youtube.youtube_upload import find_latest_short


def test_upload_guard_rejects_moviepy_temp_name_without_probe(tmp_path: Path) -> None:
    temp_video = tmp_path / "abcTEMP_MPY_wvf_snd.mp4"
    temp_video.write_bytes(b"fake-video")

    result = validate_upload_video_file(temp_video, strict_probe=False)

    assert result.valid is False
    assert "temporary_or_intermediate_video_name" in result.issues


def test_upload_guard_rejects_tiny_placeholder_in_strict_mode(tmp_path: Path) -> None:
    placeholder = tmp_path / "final.mp4"
    placeholder.write_bytes(b"mp4")

    result = validate_upload_video_file(placeholder, strict_probe=True)

    assert result.valid is False
    assert any(issue.startswith("video_too_small") for issue in result.issues)


def test_upload_guard_accepts_probeable_vertical_short_with_gate_artifact(tmp_path: Path, monkeypatch) -> None:
    final = tmp_path / "run" / "final.mp4"
    final.parent.mkdir()
    final.write_bytes(b"0" * (80 * 1024))
    (final.parent / "render_qa.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        upload_guard,
        "_probe_upload_media",
        lambda _path: {
            "available": True,
            "duration_s": 31.0,
            "size_bytes": final.stat().st_size,
            "width": 1080,
            "height": 1920,
            "video_codec": "h264",
            "audio_codec": "aac",
        },
    )

    result = validate_upload_video_file(final, require_run_artifacts=True, strict_probe=True)

    assert result.valid is True
    assert result.issues == ()


def test_find_latest_short_skips_newer_placeholder_and_returns_upload_ready_render(tmp_path: Path, monkeypatch) -> None:
    shorts_dir = tmp_path / "data" / "shorts"
    bad_run = shorts_dir / "20260605-bad"
    good_run = shorts_dir / "20260604-good"
    bad_run.mkdir(parents=True)
    good_run.mkdir(parents=True)
    bad = bad_run / "final.mp4"
    good = good_run / "final.mp4"
    bad.write_bytes(b"mp4")
    good.write_bytes(b"fake-video")
    (good_run / "render_qa.json").write_text("{}", encoding="utf-8")
    bad.touch()
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data", shorts_output_dir=shorts_dir)

    def fake_validate(path, **_kwargs):
        return upload_guard.UploadVideoValidation(
            path=Path(path),
            valid=Path(path) == good,
            issues=() if Path(path) == good else ("video_too_small:3_bytes",),
            strict_probe=False,
            probe={},
        )

    monkeypatch.setattr("youtube.youtube_upload.validate_upload_video_file", fake_validate)

    assert find_latest_short(settings) == good
