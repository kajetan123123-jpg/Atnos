from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

import imageio_ffmpeg
import requests

from atnos.core.config import Settings
from content.audio_postprocess import YOUTUBE_AUDIO_FILTER


class ElevenLabsTtsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ElevenLabsRuntimeAvailability:
    available: bool
    voice_id: str | None
    model_id: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "voice_id": self.voice_id,
            "model_id": self.model_id,
            "reason": self.reason,
        }


class ElevenLabsNarration:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def supports_expressive_tags(self) -> bool:
        return self.settings.elevenlabs_model_id.casefold().startswith("eleven_v3")

    def runtime_availability(self, *, voice_id: str | None = None) -> ElevenLabsRuntimeAvailability:
        resolved_voice_id = str(voice_id or self.settings.elevenlabs_voice_id or "").strip() or None
        if not self.settings.shorts_allow_cloud_tts:
            return ElevenLabsRuntimeAvailability(
                available=False,
                voice_id=resolved_voice_id,
                model_id=self.settings.elevenlabs_model_id,
                reason="Cloud TTS is disabled. Set SHORTS_ALLOW_CLOUD_TTS=true to use ElevenLabs.",
            )
        if not self.settings.elevenlabs_api_key:
            return ElevenLabsRuntimeAvailability(
                available=False,
                voice_id=resolved_voice_id,
                model_id=self.settings.elevenlabs_model_id,
                reason="ELEVENLABS_API_KEY is missing.",
            )
        if not resolved_voice_id:
            return ElevenLabsRuntimeAvailability(
                available=False,
                voice_id=None,
                model_id=self.settings.elevenlabs_model_id,
                reason="ELEVENLABS_VOICE_ID is missing.",
            )
        return ElevenLabsRuntimeAvailability(
            available=True,
            voice_id=resolved_voice_id,
            model_id=self.settings.elevenlabs_model_id,
            reason="ElevenLabs narration backend is configured.",
        )

    def build_payload(self, text: str) -> dict[str, object]:
        normalized = " ".join(text.split()).strip()
        if not normalized:
            raise ElevenLabsTtsError("Narration text is empty.")
        return {
            "text": normalized,
            "model_id": self.settings.elevenlabs_model_id,
            "language_code": self.settings.elevenlabs_language_code,
            "voice_settings": {
                "stability": self.settings.elevenlabs_stability,
                "similarity_boost": self.settings.elevenlabs_similarity_boost,
                "style": self.settings.elevenlabs_style,
                "speed": self.settings.elevenlabs_speed,
                "use_speaker_boost": self.settings.elevenlabs_use_speaker_boost,
            },
        }

    def generate(self, *, text: str, output_dir: Path) -> Path:
        runtime = self.runtime_availability()
        if not runtime.available:
            raise ElevenLabsTtsError(runtime.reason)

        output_dir.mkdir(parents=True, exist_ok=True)
        source_audio = output_dir / "narration_elevenlabs.mp3"
        target_wav = output_dir / "narration.wav"
        payload = self.build_payload(text)
        url = (
            f"{self.settings.elevenlabs_base_url}/v1/text-to-speech/"
            f"{self.settings.elevenlabs_voice_id}"
        )
        response = requests.post(
            url,
            params={"output_format": self.settings.elevenlabs_output_format},
            headers={
                "xi-api-key": self.settings.elevenlabs_api_key or "",
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=payload,
            timeout=180,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ElevenLabsTtsError(f"ElevenLabs TTS request failed: {exc}") from exc

        source_audio.write_bytes(response.content)
        if source_audio.stat().st_size <= 0:
            raise ElevenLabsTtsError("ElevenLabs returned an empty audio response.")
        self._convert_to_wav(source_audio=source_audio, target_wav=target_wav)
        return target_wav

    def _convert_to_wav(self, *, source_audio: Path, target_wav: Path) -> None:
        ffmpeg = self._resolve_ffmpeg()
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_audio),
            "-af",
            YOUTUBE_AUDIO_FILTER,
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(target_wav),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        if completed.returncode != 0:
            raise ElevenLabsTtsError(
                "FFmpeg could not convert ElevenLabs narration to WAV: "
                + (completed.stderr or completed.stdout or f"exit code {completed.returncode}")[-1200:]
            )
        if not target_wav.exists() or target_wav.stat().st_size <= 0:
            raise ElevenLabsTtsError("FFmpeg finished without producing narration.wav.")

    def _resolve_ffmpeg(self) -> str:
        configured = (self.settings.ffmpeg_exe or "").strip()
        if configured:
            return configured
        return imageio_ffmpeg.get_ffmpeg_exe()
