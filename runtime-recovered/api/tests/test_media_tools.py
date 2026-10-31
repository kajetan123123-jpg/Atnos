from __future__ import annotations

import sys
from pathlib import Path

from atnos.production import media_tools


def test_common_ffmpeg_candidates_include_canonical_e_drive_installation() -> None:
    candidates = media_tools._common_ffmpeg_candidates("ffmpeg.exe")

    assert Path("E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe") in candidates


def test_resolve_ffmpeg_prefers_explicit_existing_binary(monkeypatch, tmp_path: Path) -> None:
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"test")
    monkeypatch.setenv("FFMPEG_EXE", str(binary))
    monkeypatch.setattr(media_tools.shutil, "which", lambda _name: None)

    assert media_tools.resolve_ffmpeg() == str(binary)


def test_resolve_ffmpeg_ignores_missing_explicit_binary(monkeypatch) -> None:
    monkeypatch.setenv("FFMPEG_EXE", "Z:/missing/ffmpeg.exe")
    monkeypatch.setattr(media_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(media_tools, "_common_ffmpeg_candidates", lambda _name: [])
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)

    assert media_tools.resolve_ffmpeg() is None


def test_resolve_ffprobe_uses_ffmpeg_sibling(monkeypatch, tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")
    monkeypatch.delenv("FFPROBE_EXE", raising=False)
    monkeypatch.delenv("ATNOS_FFPROBE_EXE", raising=False)
    monkeypatch.setattr(media_tools.shutil, "which", lambda _name: None)

    assert media_tools.resolve_ffprobe(str(ffmpeg)) == str(ffprobe)
