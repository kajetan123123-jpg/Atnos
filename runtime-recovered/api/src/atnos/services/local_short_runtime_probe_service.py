from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import requests

from atnos.core.config import Settings
from atnos.services.ollama_model_roles_service import OllamaModelRolesService
from atnos.services.piper_tts_service import PiperTtsService
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService
from content.elevenlabs_tts import ElevenLabsNarration


@dataclass(frozen=True, slots=True)
class LocalShortRuntimeComponent:
    key: str
    label: str
    ready: bool
    path: str | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "ready": self.ready,
            "path": self.path,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LocalShortRuntimeSnapshot:
    enabled: bool
    ready: bool
    reason: str
    components: tuple[LocalShortRuntimeComponent, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "reason": self.reason,
            "components": [item.to_dict() for item in self.components],
        }


class LocalShortRuntimeProbeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.piper = PiperTtsService(settings)
        self.elevenlabs = ElevenLabsNarration(settings)
        self.whisper = WhisperCppSubtitleService(settings)

    def snapshot(self) -> LocalShortRuntimeSnapshot:
        ffmpeg_component = self._probe_ffmpeg()
        ollama_component = self._probe_ollama()
        tts_component = self._probe_tts_backend()
        whisper_runtime = self.whisper.runtime_availability()
        components = (
            ffmpeg_component,
            ollama_component,
            tts_component,
            LocalShortRuntimeComponent(
                key="whisper_cpp",
                label="whisper.cpp",
                ready=whisper_runtime.available,
                path=whisper_runtime.executable_path or whisper_runtime.model_path,
                reason=whisper_runtime.reason,
            ),
        )

        if not self.settings.atnos_local_shorts_enabled:
            return LocalShortRuntimeSnapshot(
                enabled=False,
                ready=False,
                reason="Local shorts are disabled in runtime settings.",
                components=components,
            )

        missing = [item.label for item in components if not item.ready]
        if missing:
            return LocalShortRuntimeSnapshot(
                enabled=True,
                ready=False,
                reason=f"Brakuje gotowych runtime'ow: {', '.join(missing)}.",
                components=components,
            )
        return LocalShortRuntimeSnapshot(
            enabled=True,
            ready=True,
            reason="Local short pipeline is ready on this Windows runtime.",
            components=components,
        )

    def _probe_tts_backend(self) -> LocalShortRuntimeComponent:
        if self.settings.shorts_tts_backend == "elevenlabs":
            configured_voice_id = next(
                (
                    str(value).strip()
                    for value in (
                        self.settings.elevenlabs_voice_id,
                        self.settings.elevenlabs_voice_id_wealth_blueprint,
                        self.settings.elevenlabs_voice_id_dark_chronicles,
                        self.settings.elevenlabs_voice_id_sigma_squad,
                    )
                    if value and str(value).strip()
                ),
                None,
            )
            runtime = self.elevenlabs.runtime_availability(voice_id=configured_voice_id)
            return LocalShortRuntimeComponent(
                key="elevenlabs_tts",
                label="ElevenLabs TTS",
                ready=runtime.available,
                path=runtime.voice_id,
                reason=runtime.reason,
            )

        piper_runtime = self.piper.runtime_availability()
        return LocalShortRuntimeComponent(
            key="piper",
            label="Piper",
            ready=piper_runtime.available,
            path=piper_runtime.executable_path or piper_runtime.model_path,
            reason=piper_runtime.reason,
        )

    def _probe_ffmpeg(self) -> LocalShortRuntimeComponent:
        try:
            executable = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
        except Exception as exc:
            return LocalShortRuntimeComponent(
                key="ffmpeg",
                label="FFmpeg",
                ready=False,
                path=None,
                reason=f"FFmpeg executable was not resolved: {exc}",
            )
        if not executable.exists():
            return LocalShortRuntimeComponent(
                key="ffmpeg",
                label="FFmpeg",
                ready=False,
                path=executable.as_posix(),
                reason="FFmpeg executable path does not exist.",
            )
        return LocalShortRuntimeComponent(
            key="ffmpeg",
            label="FFmpeg",
            ready=True,
            path=executable.as_posix(),
            reason="FFmpeg renderer is ready.",
        )

    def _probe_ollama(self) -> LocalShortRuntimeComponent:
        endpoint = self.settings.ollama_base_url.rstrip("/")
        try:
            response = requests.get(
                f"{endpoint}/api/tags",
                timeout=min(float(self.settings.ollama_health_timeout_s), 1.5),
            )
        except Exception as exc:
            return LocalShortRuntimeComponent(
                key="ollama",
                label="Ollama",
                ready=False,
                path=endpoint,
                reason=f"Ollama local endpoint is not reachable: {exc}",
            )
        if response.status_code >= 400:
            return LocalShortRuntimeComponent(
                key="ollama",
                label="Ollama",
                ready=False,
                path=endpoint,
                reason=f"Ollama returned HTTP {response.status_code}.",
            )
        configured_model = self.settings.ollama_synthesis_model or self.settings.ollama_chat_model
        if configured_model:
            try:
                payload: dict[str, Any] = response.json()
            except Exception as exc:
                return LocalShortRuntimeComponent(
                    key="ollama",
                    label="Ollama",
                    ready=False,
                    path=endpoint,
                    reason=f"Ollama tags endpoint returned invalid JSON: {exc}",
                )
            models = payload.get("models")
            available = {
                str(item.get("name") or item.get("model") or "").strip()
                for item in models
                if isinstance(item, dict)
            } if isinstance(models, list) else set()
            if configured_model not in available:
                fallback_model = (
                    (self.settings.ollama_fast_model or "").strip()
                    or OllamaModelRolesService.DEFAULT_FAST_TEXT_MODEL
                )
                if fallback_model in available:
                    return LocalShortRuntimeComponent(
                        key="ollama",
                        label="Ollama",
                        ready=True,
                        path=endpoint,
                        reason=(
                            f"Configured Ollama synthesis model '{configured_model}' is missing, "
                            f"using fast fallback '{fallback_model}'. "
                            f"Available models: {', '.join(sorted(available)) or 'none'}."
                        ),
                    )
                return LocalShortRuntimeComponent(
                    key="ollama",
                    label="Ollama",
                    ready=False,
                    path=endpoint,
                    reason=(
                        f"Configured Ollama synthesis model '{configured_model}' is missing. "
                        f"Available models: {', '.join(sorted(available)) or 'none'}."
                    ),
                )
        return LocalShortRuntimeComponent(
            key="ollama",
            label="Ollama",
            ready=True,
            path=endpoint,
            reason="Ollama local endpoint is reachable.",
        )
