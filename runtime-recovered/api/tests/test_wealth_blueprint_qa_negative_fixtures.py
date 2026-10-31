"""Production QA negative fixtures — video_qc precheck + hard fails."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atnos.production.channels.wealth_blueprint.video_qc import run_wealth_blueprint_qc
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.qa_gate import probe_media


def _ffmpeg() -> str:
    exe = resolve_ffmpeg()
    if not exe:
        pytest.skip("ffmpeg unavailable")
    return exe


def _black_mp4(path: Path, *, seconds: float = 2.0, size: str = "1080x1920") -> None:
    ffmpeg = _ffmpeg()
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={size}:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _silent_video_no_audio(path: Path, *, seconds: float = 2.0) -> None:
    ffmpeg = _ffmpeg()
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=green:s=1080x1920:d={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _wrong_resolution(path: Path) -> None:
    ffmpeg = _ffmpeg()
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=720x1280:d=1.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def test_black_video_fails_qc(tmp_path: Path):
    video = tmp_path / "black.mp4"
    _black_mp4(video)
    audio = tmp_path / "voice.wav"
    # Minimal valid-ish wav header + silence bytes so probe can run; QC must still fail black.
    _black_mp4(tmp_path / "with_audio.mp4", seconds=2.0)
    report = run_wealth_blueprint_qc(
        artifact_dir=tmp_path,
        video_path=video,
        audio_path=tmp_path / "with_audio.mp4",
        manifest_path=None,
        scene_plan_path=None,
    )
    assert report.get("ok") is False
    failed = {c["name"] for c in report.get("checks", []) if c.get("status") == "fail"}
    assert failed & {"not_entirely_black", "no_blank_or_black_frames", "frame_render_errors"}


def test_no_audio_probe_fails(tmp_path: Path):
    video = tmp_path / "silent.mp4"
    _silent_video_no_audio(video)
    probe = probe_media(video)
    assert not probe.get("audio_codec")


def test_wrong_resolution_fails_format(tmp_path: Path):
    video = tmp_path / "wrong.mp4"
    _wrong_resolution(video)
    probe = probe_media(video)
    assert not (probe.get("width") == 1080 and probe.get("height") == 1920)


def test_corrupted_mp4_fails_probe(tmp_path: Path):
    video = tmp_path / "corrupt.mp4"
    video.write_bytes(b"not an mp4 file at all")
    probe = probe_media(video)
    assert not probe.get("ok", True) or not probe.get("video_codec")


def test_missing_asset_path_is_hard_fail(tmp_path: Path):
    missing = tmp_path / "missing.mp4"
    report = run_wealth_blueprint_qc(
        artifact_dir=tmp_path,
        video_path=missing,
        audio_path=missing,
        manifest_path=None,
        scene_plan_path=None,
    )
    assert report.get("ok") is False
    failed = {c["name"] for c in report.get("checks", []) if c.get("status") == "fail"}
    assert "assets_exist" in failed or "final_mp4_valid" in failed or "audio_exists" in failed
