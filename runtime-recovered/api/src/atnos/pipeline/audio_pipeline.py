from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleError, WhisperCppSubtitleService

logger = logging.getLogger(__name__)


class AudioPipeline:
    """Generate narration and subtitles for a first-pass E2E Short."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def generate_narration(self, script_text: str, job_dir: Path, voice_config: dict[str, Any]) -> Path:
        """Generate narration.mp3 with Edge TTS, pyttsx3, then silent fallback."""
        job_dir.mkdir(parents=True, exist_ok=True)
        narration_path = job_dir / "narration.mp3"
        text = _shorten_for_duration(str(script_text or "").strip())
        voice = str(voice_config.get("tts_voice") or "pl-PL-MarekNeural")
        if self._try_edge_tts(text, voice=voice, output_path=narration_path):
            if self.get_duration(narration_path) <= 62:
                return narration_path
            text = _drop_last_paragraph(text)
            if self._try_edge_tts(text, voice=voice, output_path=narration_path):
                return narration_path
        if self._try_pyttsx3(text, narration_path):
            return narration_path
        self._write_silent_mp3(narration_path, duration_s=30)
        logger.critical("TTS failed; generated silent narration fallback at %s.", narration_path)
        return narration_path

    def generate_subtitles(self, narration_path: Path, job_dir: Path) -> Path | None:
        """Generate narration.srt through whisper.cpp first, then openai-whisper fallback."""
        output_path = job_dir / "narration.srt"
        if self.settings is not None:
            try:
                descriptor = WhisperCppSubtitleService(self.settings).generate(audio_path=narration_path, conversation_id=None)
                shutil.copyfile(descriptor.absolute_path, output_path)
                return output_path
            except (WhisperCppSubtitleError, Exception) as exc:
                logger.warning("whisper.cpp subtitles failed: %s", exc)
        if self._try_openai_whisper(narration_path, output_path):
            return output_path
        logger.warning("Subtitle generation unavailable for %s.", narration_path)
        return None

    def get_duration(self, audio_path: Path) -> float:
        """Return media duration in seconds through ffprobe."""
        ffprobe = resolve_ffprobe(resolve_ffmpeg())
        if not ffprobe or not audio_path.exists():
            return 0.0
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            return float(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else 0.0
        except (OSError, subprocess.SubprocessError, ValueError):
            return 0.0

    def _try_edge_tts(self, text: str, *, voice: str, output_path: Path) -> bool:
        if importlib.util.find_spec("edge_tts") is None and not _pip_install("edge-tts>=7.2.3,<8.0.0"):
            return False
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "edge_tts",
                    "--voice",
                    voice,
                    "--text",
                    text,
                    "--write-media",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            ok = proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024
            if not ok:
                logger.warning("edge-tts failed: %s", (proc.stderr or proc.stdout)[-500:])
            return ok
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("edge-tts execution failed: %s", exc)
            return False

    def _try_pyttsx3(self, text: str, output_path: Path) -> bool:
        if importlib.util.find_spec("pyttsx3") is None and not _pip_install("pyttsx3>=2.99,<3.0.0"):
            return False

        wav_path = output_path.with_suffix(".pyttsx3.wav")
        try:
            import pyttsx3  # type: ignore

            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.save_to_file(text, str(wav_path))
            engine.runAndWait()
        except Exception as exc:
            logger.warning("pyttsx3 failed: %s", exc)
            return False
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            return False
        return _transcode_audio(wav_path, output_path)

    def _write_silent_mp3(self, output_path: Path, *, duration_s: float) -> None:
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            output_path.write_bytes(b"")
            return
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                str(max(1, duration_s)),
                "-q:a",
                "9",
                "-acodec",
                "libmp3lame",
                str(output_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def _try_openai_whisper(self, audio_path: Path, output_path: Path) -> bool:
        if importlib.util.find_spec("whisper") is None and not _pip_install("openai-whisper"):
            return False
        try:
            import whisper  # type: ignore

            model = whisper.load_model("base")
            result = model.transcribe(str(audio_path), language="pl")
            segments = result.get("segments") if isinstance(result, dict) else []
            if not isinstance(segments, list) or not segments:
                return False
            output_path.write_text(_segments_to_srt(segments), encoding="utf-8")
            return output_path.exists() and output_path.stat().st_size > 0
        except Exception as exc:
            logger.warning("openai-whisper fallback failed: %s", exc)
            return False


def _transcode_audio(input_path: Path, output_path: Path) -> bool:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(input_path), "-ar", "44100", "-ac", "2", str(output_path)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024
    except (OSError, subprocess.SubprocessError):
        return False


def _segments_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{_srt_ts(float(segment.get('start') or 0))} --> {_srt_ts(float(segment.get('end') or 0))}\n"
            f"{str(segment.get('text') or '').strip()}\n"
        )
    return "\n".join(blocks)


def _srt_ts(seconds: float) -> str:
    total_ms = int(max(0.0, seconds) * 1000)
    millis = total_ms % 1000
    total_s = total_ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def _pip_install(package: str) -> bool:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--break-system-packages", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=300,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _shorten_for_duration(text: str) -> str:
    words = text.split()
    if len(words) <= 145:
        return text
    return " ".join(words[:145])


def _drop_last_paragraph(text: str) -> str:
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    if len(parts) > 1:
        return "\n\n".join(parts[:-1])
    words = text.split()
    return " ".join(words[: max(30, len(words) - 35)])
