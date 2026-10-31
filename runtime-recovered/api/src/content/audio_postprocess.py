from __future__ import annotations

import logging
import subprocess
from pathlib import Path


logger = logging.getLogger(__name__)


YOUTUBE_AUDIO_FILTER = (
    "aresample=44100,"
    "pan=stereo|c0=c0|c1=c0,"
    "loudnorm=I=-16:TP=-1.5:LRA=11,"
    "aecho=0.8:0.88:20:0.2"
)


def postprocess_audio_for_youtube(
    *,
    input_path: Path,
    output_path: Path,
    ffmpeg_exe: str,
    strict: bool = True,
) -> Path:
    """Normalize narration to 44.1 kHz stereo WAV before final MP4 encoding."""
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not input_path.exists() or input_path.stat().st_size <= 0:
        message = f"Audio post-process input is missing or empty: {input_path}"
        if strict:
            raise RuntimeError(message)
        logger.warning(message)
        return input_path

    target = output_path
    temp_target = output_path
    replace_original = input_path == output_path
    if replace_original:
        temp_target = output_path.with_name(f"{output_path.stem}.youtube-ready.tmp.wav")
        temp_target.unlink(missing_ok=True)

    command = [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-af",
        YOUTUBE_AUDIO_FILTER,
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(temp_target),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode != 0:
        message = (
            "Audio post-process failed: "
            + (completed.stderr or completed.stdout or f"exit code {completed.returncode}")[-1200:]
        )
        temp_target.unlink(missing_ok=True)
        if strict:
            raise RuntimeError(message)
        logger.warning(message)
        return input_path
    if not temp_target.exists() or temp_target.stat().st_size <= 0:
        message = "Audio post-process finished without producing output."
        if strict:
            raise RuntimeError(message)
        logger.warning(message)
        return input_path
    if replace_original:
        temp_target.replace(target)
    return target
