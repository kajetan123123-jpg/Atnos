from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import textwrap
import unicodedata
from uuid import uuid4

import imageio_ffmpeg

from atnos.core.config import Settings
from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService


class VoiceSynthesisError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceRenderProfile:
    alias: str
    edge_voice: str
    edge_rate: str = "+0%"
    edge_pitch: str = "+0Hz"
    edge_volume: str = "+0%"
    kokoro_voice: str | None = None
    kokoro_speed: float = 1.0


class VoiceSynthesisService:
    DEFAULT_POLISH_VOICE = "okropek"
    DEFAULT_EDGE_POLISH_VOICE = "pl-PL-ZofiaNeural"
    EDGE_TTS_VOICES = (
        DEFAULT_EDGE_POLISH_VOICE,
        "pl-PL-MarekNeural",
    )
    VOICE_RENDER_PROFILES: dict[str, VoiceRenderProfile] = {
        # Conversational assistant persona — warmer, less “processed” than pl_maja.
        "okropek": VoiceRenderProfile(
            alias="okropek",
            edge_voice=DEFAULT_EDGE_POLISH_VOICE,
            edge_rate="-6%",
            edge_pitch="-2Hz",
            edge_volume="+2%",
            kokoro_speed=0.96,
        ),
        "pl_maja": VoiceRenderProfile(
            alias="pl_maja",
            edge_voice=DEFAULT_EDGE_POLISH_VOICE,
            edge_rate="-8%",
            edge_pitch="-4Hz",
            edge_volume="+0%",
            kokoro_speed=0.94,
        ),
        "pl_piotr": VoiceRenderProfile(
            alias="pl_piotr",
            edge_voice="pl-PL-MarekNeural",
            edge_rate="-4%",
            edge_pitch="-2Hz",
            edge_volume="+0%",
            kokoro_speed=1.0,
        ),
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)
        self.kokoro = KokoroLocalRuntimeService(settings)

    def synthesize(
        self,
        *,
        text: str,
        conversation_id: int | None,
        voice_name: str | None = None,
        speed: float = 1.0,
    ) -> ManagedArtifactDescriptor:
        normalized = self._prepare_tts_text(text)
        if not normalized:
            raise VoiceSynthesisError("Narration text is empty.")

        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".wav")
        temp_root = self.settings.workspace_staging_dir / "tts"
        temp_root.mkdir(parents=True, exist_ok=True)
        mp3_path = (temp_root / f"{uuid4().hex}.mp3").resolve()
        input_path = (temp_root / f"{uuid4().hex}.txt").resolve()
        script_path = (temp_root / f"{uuid4().hex}.ps1").resolve()
        script = textwrap.dedent(
            """
            param([string]$InputFile, [string]$OutputFile)
            Add-Type -AssemblyName System.Speech
            $text = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8
            $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $voices = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo })
            $voice = $voices |
              Where-Object { $_.Culture.Name -like 'pl-*' -and $_.Gender -eq 'Female' } |
              Select-Object -First 1
            if (-not $voice) {
              $voice = $voices |
                Where-Object { $_.Culture.Name -like 'pl-*' } |
                Select-Object -First 1
            }
            if ($voice) { $synth.SelectVoice($voice.Name) }
            # Slightly slower than default — Windows SAPI sounds less clipped.
            $synth.Rate = -1
            $synth.Volume = 90
            $synth.SetOutputToWaveFile($OutputFile)
            $synth.Speak($text)
            $synth.Dispose()
            """
        ).strip()

        input_path.write_text(normalized, encoding="utf-8")
        script_path.write_text(script, encoding="utf-8")
        errors: list[str] = []

        try:
            for backend in self._resolve_backend_order(text=normalized, voice_name=voice_name):
                if backend == "edge" and self._try_edge_tts(
                    text=normalized,
                    mp3_path=mp3_path,
                    output_path=output_path,
                    preferred_voice=voice_name,
                ):
                    return self.artifacts.finalize_output(
                        absolute_path=output_path,
                        filename="atnos-narration.wav",
                        media_type="audio/wav",
                    )
                if backend == "kokoro" and self._try_kokoro_local(
                    text=normalized,
                    output_path=output_path,
                    voice_name=voice_name,
                    speed=speed,
                ):
                    return self.artifacts.finalize_output(
                        absolute_path=output_path,
                        filename="atnos-narration.wav",
                        media_type="audio/wav",
                    )
            try:
                result = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script_path),
                        str(input_path),
                        str(output_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                if output_path.exists() and output_path.stat().st_size > 1024:
                    return self.artifacts.finalize_output(
                        absolute_path=output_path,
                        filename="atnos-narration.wav",
                        media_type="audio/wav",
                    )
                errors.append("PowerShell speech synthesis timed out before any WAV output was produced.")
                raise VoiceSynthesisError(errors[-1]) from exc
        finally:
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                mp3_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass

        if result.returncode != 0:
            errors.append(result.stderr.strip() or result.stdout.strip() or "Local speech synthesis failed.")
            raise VoiceSynthesisError(
                " | ".join(item for item in errors if item)
            )

        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-narration.wav",
            media_type="audio/wav",
        )

    @classmethod
    def _prepare_tts_text(cls, text: str) -> str:
        """Normalize assistant text into spoken Polish that sounds less robotic."""
        normalized = (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("—", ", ")
            .replace("–", ", ")
            .replace("→", " potem ")
            .replace("•", ". ")
            .replace("…", ". ")
            .replace("...", ". ")
        )
        # Drop markdown / code fences so TTS does not read asterisks or backticks.
        normalized = re.sub(r"```[\s\S]*?```", " ", normalized)
        normalized = re.sub(r"`([^`]+)`", r"\1", normalized)
        normalized = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", normalized)
        normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", normalized)
        normalized = re.sub(r"[*_~#>]{1,}", " ", normalized)
        normalized = re.sub(r"https?://\S+", " link ", normalized)
        normalized = re.sub(r"\bE:\\[^\s]+", " ścieżka ", normalized, flags=re.I)
        # Expand a few common Polish/tech abbreviations for spoken delivery.
        replacements = (
            (r"\bnp\.", "na przykład"),
            (r"\btzn\.", "to znaczy"),
            (r"\btj\.", "to jest"),
            (r"\bitp\.", "i tak dalej"),
            (r"\bitd\.", "i tak dalej"),
            (r"\bOK\b", "okej"),
            (r"\bok\b", "okej"),
            (r"\bAPI\b", "A P I"),
            (r"\bTTS\b", "te te es"),
        )
        for pattern, repl in replacements:
            normalized = re.sub(pattern, repl, normalized)
        cleaned: list[str] = []
        for char in normalized:
            category = unicodedata.category(char)
            if char in {"\n", "\t"}:
                cleaned.append(" ")
                continue
            if category in {"So", "Cs"}:
                continue
            cleaned.append(char)
        collapsed = re.sub(r"\s+", " ", "".join(cleaned)).strip()
        # Soft sentence pacing: ensure a breath after clause separators.
        collapsed = re.sub(r"\s*,\s*", ", ", collapsed)
        collapsed = re.sub(r"\s*;\s*", ". ", collapsed)
        collapsed = re.sub(r"\s*:\s*", ": ", collapsed)
        collapsed = re.sub(r"([.!?])\s*", r"\1 ", collapsed)
        collapsed = re.sub(r"\s+", " ", collapsed).strip()
        return collapsed

    @classmethod
    def resolve_persona_voice(cls, *, wake_word: str | None = None, voice_name: str | None = None) -> str:
        """Map ambient persona (e.g. Okropek) to a human-sounding render profile."""
        if voice_name and str(voice_name).strip():
            return str(voice_name).strip()
        wake = re.sub(r"[^\w\s]", " ", (wake_word or "").strip().casefold())
        wake = " ".join(wake.split())
        if wake in {"okropek", "okropku", "hej okropek", "hej okropku"}:
            return "okropek"
        return cls.DEFAULT_POLISH_VOICE

    def runtime_availability(self) -> dict[str, object]:
        kokoro_ready = self.kokoro.resolve_assets() is not None
        edge_ready = self._has_edge_tts_runtime()
        windows_speech_ready = self._has_windows_speech_runtime()
        available = kokoro_ready or edge_ready or windows_speech_ready
        if edge_ready:
            reason = "Edge TTS Polish neural voices are available."
        elif kokoro_ready:
            reason = "Kokoro local runtime is available."
        elif windows_speech_ready:
            reason = "Windows Speech fallback is available."
        else:
            reason = "No local speech synthesis backend is ready."
        return {
            "available": available,
            "preferred_voice": self.DEFAULT_POLISH_VOICE if edge_ready else self.settings.kokoro_voice_name,
            "kokoro_ready": kokoro_ready,
            "edge_ready": edge_ready,
            "windows_speech_ready": windows_speech_ready,
            "reason": reason,
        }

    def _resolve_backend_order(self, *, text: str, voice_name: str | None) -> tuple[str, ...]:
        normalized_voice = (voice_name or "").strip()
        if normalized_voice in self.VOICE_RENDER_PROFILES or normalized_voice in self.EDGE_TTS_VOICES:
            return ("edge", "kokoro", "windows")
        if normalized_voice and normalized_voice != self.settings.kokoro_voice_name:
            return ("kokoro", "edge", "windows")
        if self._looks_like_polish(text):
            return ("edge", "kokoro", "windows")
        return ("kokoro", "edge", "windows")

    def _try_edge_tts(
        self,
        *,
        text: str,
        mp3_path: Path,
        output_path: Path,
        preferred_voice: str | None,
    ) -> bool:
        if not self._has_edge_tts_runtime():
            return False

        render_profile = self._resolve_voice_render_profile(preferred_voice)
        voices = list(self.EDGE_TTS_VOICES)
        rate = "+0%"
        pitch = "+0Hz"
        volume = "+0%"
        normalized_voice = (preferred_voice or "").strip()
        if render_profile is not None:
            normalized_voice = render_profile.edge_voice
            rate = render_profile.edge_rate
            pitch = render_profile.edge_pitch
            volume = render_profile.edge_volume
        if normalized_voice in voices:
            voices.remove(normalized_voice)
            voices.insert(0, normalized_voice)

        for voice in voices:
            if self._render_edge_tts_voice(
                text=text,
                voice=voice,
                mp3_path=mp3_path,
                output_path=output_path,
                rate=rate if voice == normalized_voice else "+0%",
                pitch=pitch if voice == normalized_voice else "+0Hz",
                volume=volume if voice == normalized_voice else "+0%",
            ):
                return True
        return False

    def _render_edge_tts_voice(
        self,
        *,
        text: str,
        voice: str,
        mp3_path: Path,
        output_path: Path,
        rate: str,
        pitch: str,
        volume: str,
    ) -> bool:
        python_code = textwrap.dedent(
            """
            import asyncio
            import sys
            import edge_tts

            async def main() -> None:
                communicate = edge_tts.Communicate(
                    sys.argv[1],
                    sys.argv[2],
                    rate=sys.argv[3],
                    pitch=sys.argv[4],
                    volume=sys.argv[5],
                )
                await communicate.save(sys.argv[6])

            asyncio.run(main())
            """
        ).strip()
        try:
            synth_result = subprocess.run(
                [
                    os.fspath(Path(os.sys.executable).resolve()),
                    "-c",
                    python_code,
                    text,
                    voice,
                    rate,
                    pitch,
                    volume,
                    str(mp3_path),
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False

        if synth_result.returncode != 0 or not mp3_path.exists() or mp3_path.stat().st_size <= 0:
            return False

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        convert = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(mp3_path),
                "-ac",
                "1",
                "-ar",
                "24000",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        return convert.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024

    def _try_kokoro_local(
        self,
        *,
        text: str,
        output_path: Path,
        voice_name: str | None,
        speed: float,
    ) -> bool:
        render_profile = self._resolve_voice_render_profile(voice_name)
        try:
            self.kokoro.synthesize_to_wav(
                text=text,
                output_path=output_path,
                voice_name=(
                    render_profile.kokoro_voice
                    if render_profile is not None and render_profile.kokoro_voice
                    else self.settings.kokoro_voice_name
                ),
                lang="pl",
                speed=speed * (render_profile.kokoro_speed if render_profile is not None else 1.0),
            )
        except Exception:
            return False
        return output_path.exists() and output_path.stat().st_size > 1024

    @classmethod
    def _resolve_voice_render_profile(cls, voice_name: str | None) -> VoiceRenderProfile | None:
        normalized = (voice_name or "").strip().lower()
        if not normalized:
            return cls.VOICE_RENDER_PROFILES.get(cls.DEFAULT_POLISH_VOICE)
        return cls.VOICE_RENDER_PROFILES.get(normalized)

    @staticmethod
    def _has_edge_tts_runtime() -> bool:
        try:
            return importlib.util.find_spec("edge_tts") is not None
        except Exception:
            return False

    @staticmethod
    def _has_windows_speech_runtime() -> bool:
        return bool(shutil.which("powershell") or shutil.which("pwsh"))

    @staticmethod
    def _looks_like_polish(text: str) -> bool:
        lowered = text.lower()
        if any(char in lowered for char in "ąćęłńóśźż"):
            return True
        return bool(
            re.search(
                r"\b(czy|jest|trasa|wyjazd|dojazd|podroz|pogoda|plan|moge|zrobie|sprawdze|lodz|lodzk|warszaw)\b",
                lowered,
            )
        )
