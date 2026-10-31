from __future__ import annotations

import os
import shutil
from pathlib import Path

from atnos.core.paths import atnos_state_dir


def resolve_ffmpeg() -> str | None:
    """Resolve FFmpeg from PATH, explicit env, ATNOS_TOOLS_DIR, or imageio."""
    explicit = _existing_file(
        os.getenv("FFMPEG_EXE")
        or os.getenv("ATNOS_FFMPEG_EXE")
        or os.getenv("ATNOS_FFMPEG")
    )
    if explicit:
        return explicit

    path = shutil.which("ffmpeg")
    if path:
        return path

    for candidate in _common_ffmpeg_candidates("ffmpeg.exe"):
        if candidate.exists():
            return str(candidate)

    try:
        import imageio_ffmpeg

        imageio_path = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if imageio_path.exists():
            return str(imageio_path)
    except Exception:
        return None
    return None


def resolve_ffprobe(ffmpeg_path: str | None = None) -> str | None:
    """Resolve FFprobe; required for strict Shorts QA media probing."""
    explicit = _existing_file(os.getenv("FFPROBE_EXE") or os.getenv("ATNOS_FFPROBE_EXE"))
    if explicit:
        return explicit

    path = shutil.which("ffprobe")
    if path:
        return path

    if ffmpeg_path:
        sibling = Path(ffmpeg_path).with_name("ffprobe.exe")
        if sibling.exists():
            return str(sibling)

    for candidate in _common_ffmpeg_candidates("ffprobe.exe"):
        if candidate.exists():
            return str(candidate)
    return None


def _existing_file(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path) if path.exists() else None


def _common_ffmpeg_candidates(binary_name: str) -> list[Path]:
    """Candidates from ATNOS_TOOLS_DIR / state tree — Atnos tools live on E:."""
    candidates: list[Path] = []
    tools = os.getenv("ATNOS_TOOLS_DIR")
    if tools:
        root = Path(tools)
        candidates.extend(
            [
                root / "FFmpeg" / "bin" / binary_name,
                root / "ffmpeg" / "bin" / binary_name,
                root / "FFmpegShared" / "bin" / binary_name,
            ]
        )
    # Canonical Atnos tools root (E:-only policy).
    candidates.extend(
        [
            Path(r"E:\Atnos\Tools\FFmpeg\bin") / binary_name,
            Path(r"E:\Atnos\Tools\ffmpeg\bin") / binary_name,
        ]
    )
    runtime_root = atnos_state_dir().parent
    candidates.extend(
        [
            runtime_root / "tools" / "ffmpeg" / "bin" / binary_name,
            runtime_root / "tools" / "ffmpeg-shared" / "bin" / binary_name,
            Path.home() / "ffmpeg" / "bin" / binary_name,
        ]
    )
    return candidates
