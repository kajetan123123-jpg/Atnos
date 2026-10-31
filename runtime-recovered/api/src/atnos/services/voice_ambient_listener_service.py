from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from io import BytesIO
import re
import threading
import time
from typing import TYPE_CHECKING
import wave

from atnos.core.config import Settings

if TYPE_CHECKING:
    from atnos.plugins.registry import PluginRegistry
    from atnos.providers.registry import ProviderRegistry
    from atnos.services.chat_orchestration_service import ChatOrchestrationService as _ChatOrchestrationService
    from atnos.services.conversation_service import ConversationTurnRequest as _ConversationTurnRequest

ChatOrchestrationService: "type[_ChatOrchestrationService] | None" = None
ConversationTurnRequest: "type[_ConversationTurnRequest] | None" = None


class _LazyProactiveBriefingService:
    """Patchable lazy proxy for tests and rare wake-word-only briefing dispatches."""

    def __init__(self, *args, **kwargs) -> None:
        self._args = args
        self._kwargs = kwargs

    def create_briefing_message(self, *args, **kwargs):
        from atnos.services.proactive_briefing_service import ProactiveBriefingService

        return ProactiveBriefingService(*self._args, **self._kwargs).create_briefing_message(
            *args,
            **kwargs,
        )


ProactiveBriefingService = _LazyProactiveBriefingService


def _load_chat_turn_dependencies() -> tuple[
    "type[_ChatOrchestrationService]",
    "type[_ConversationTurnRequest]",
]:
    """Load heavy chat orchestration code only when ambient voice dispatches a chat turn."""
    global ChatOrchestrationService, ConversationTurnRequest
    if ChatOrchestrationService is None:
        from atnos.services.chat_orchestration_service import (
            ChatOrchestrationService as resolved_chat_service,
        )

        ChatOrchestrationService = resolved_chat_service
    if ConversationTurnRequest is None:
        from atnos.services.conversation_service import (
            ConversationTurnRequest as resolved_turn_request,
        )

        ConversationTurnRequest = resolved_turn_request
    return ChatOrchestrationService, ConversationTurnRequest


def _load_briefing_dependency():
    """Load proactive briefing only when the ambient wake-word actually needs it."""
    return ProactiveBriefingService


@dataclass(frozen=True, slots=True)
class AmbientListenerReadiness:
    available: bool
    enabled: bool
    reason: str | None


class VoiceAmbientListenerService:
    SAMPLE_RATE = 16_000
    CHANNELS = 1
    SAMPLE_WIDTH = 2
    CHUNK_SIZE = 1280
    PRE_WAKE_SECONDS = 4
    MAX_COMMAND_SECONDS = 6
    MIN_COMMAND_SECONDS = 1
    TRAILING_SILENCE_SECONDS = 0.9
    SILENCE_RMS_THRESHOLD = 260.0
    FALLBACK_TRIGGER_RMS_THRESHOLD = 1400.0
    FALLBACK_TRANSCRIPTION_COOLDOWN_SECONDS = 1.8
    WAKE_MODEL_SCORE_THRESHOLD = 0.5

    def __init__(
        self,
        database,
        *,
        settings: Settings,
        provider_registry: "ProviderRegistry",
        plugin_registry: "PluginRegistry | None" = None,
        summary_writeback_factory=None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.provider_registry = provider_registry
        if plugin_registry is None:
            from atnos.plugins.registry import build_default_plugin_registry

            plugin_registry = build_default_plugin_registry()
        self.plugin_registry = plugin_registry
        if summary_writeback_factory is None:
            from atnos.services.summary_writeback_service import SummaryWritebackService

            summary_writeback_factory = SummaryWritebackService
        self.summary_writeback_factory = summary_writeback_factory
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._muted = False

    def readiness(self) -> AmbientListenerReadiness:
        from atnos.services.runtime_settings_service import RuntimeSettingsService

        with self.database.session_scope() as session:
            effective = RuntimeSettingsService(session, self.settings).build_effective_settings()
            controls = RuntimeSettingsService(session, self.settings).snapshot()
        return self.readiness_for(controls=controls, effective_settings=effective)

    @classmethod
    def readiness_for(
        cls,
        *,
        controls,
        effective_settings: Settings,
    ) -> AmbientListenerReadiness:
        if effective_settings.voice_stt_engine not in {"whisper-local", "faster-whisper-local"} or not (
            effective_settings.voice_stt_model_path or ""
        ).strip():
            return AmbientListenerReadiness(
                available=False,
                enabled=controls.ambient_listening_enabled,
                reason="Ambient listening wymaga lokalnego STT i poprawnej sciezki modelu.",
            )
        dependency_state = cls._inspect_dependencies()
        if dependency_state["reason"] is not None:
            return AmbientListenerReadiness(
                available=False,
                enabled=controls.ambient_listening_enabled,
                reason=str(dependency_state["reason"]),
            )
        if bool(dependency_state["wake_detector_available"]):
            reason = "Ambient listening gotowy. Wake-word bedzie wykrywany lokalnie."
        else:
            reason = (
                "Ambient listening gotowy w trybie fallback. "
                "Atnos nasluchuje mikrofonu i sprawdza lokalnie frazy typu 'Atnos' oraz 'Atnosie'."
            )
        return AmbientListenerReadiness(
            available=True,
            enabled=controls.ambient_listening_enabled,
            reason=reason,
        )

    def start_if_enabled(self) -> AmbientListenerReadiness:
        readiness = self.readiness()
        if not readiness.available or not readiness.enabled:
            return readiness
        if self._thread is not None and self._thread.is_alive():
            return readiness
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="atnos-ambient-listener", daemon=True)
        self._thread.start()
        return readiness

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def simulate_transcript(self, transcript: str) -> dict[str, object]:
        cleaned = " ".join((transcript or "").strip().split())
        if not cleaned:
            return self._dispatch_briefing(trigger="wake_word")
        chat_service_cls, turn_request_cls = _load_chat_turn_dependencies()
        from atnos.services.runtime_settings_service import RuntimeSettingsService

        with self.database.session_scope() as session:
            RuntimeSettingsService(session, self.settings).mark_user_interaction()
            result = chat_service_cls(
                session,
                settings=self.settings,
                provider_registry=self.provider_registry,
                plugin_registry=self.plugin_registry,
                summary_writeback_factory=self.summary_writeback_factory,
            ).handle_turn(
                turn_request_cls(
                    content=cleaned,
                    conversation_id=None,
                    message_metadata_json={"source": "voice_ambient"},
                    assistant_metadata_json={"source": "voice_ambient"},
                )
            )
        return {
            "kind": "chat_turn",
            "conversation_id": result.conversation.id,
            "assistant_message_id": result.assistant_message.id,
            "assistant_message": result.assistant_message.content,
        }

    def simulate_wake_word_only(self) -> dict[str, object]:
        return self._dispatch_briefing(trigger="wake_word")

    def _dispatch_briefing(self, *, trigger: str) -> dict[str, object]:
        briefing_service_cls = _load_briefing_dependency()
        with self.database.session_scope() as session:
            result = briefing_service_cls(
                session,
                settings=self.settings,
                provider_registry=self.provider_registry,
            ).create_briefing_message(trigger=trigger)
        return {
            "kind": "briefing",
            "conversation_id": result.conversation_id,
            "assistant_message_id": result.assistant_message_id,
            "metadata_json": result.metadata_json,
        }

    def _run_loop(self) -> None:
        try:
            import numpy as np
            import pyaudio
        except Exception:
            return
        from atnos.services.runtime_settings_service import RuntimeSettingsService

        with self.database.session_scope() as session:
            controls = RuntimeSettingsService(session, self.settings).snapshot()
            wake_aliases = self._resolve_wake_aliases(controls.ambient_wake_word or "Atnos")
            effective_settings = RuntimeSettingsService(session, self.settings).build_effective_settings()

        detector = None
        detector_supports_alias = False
        detector_model_names_known = False
        try:
            from openwakeword.model import Model

            detector = Model()
        except Exception:
            detector = None

        recorder = pyaudio.PyAudio()
        buffer_frames = deque(maxlen=int((self.SAMPLE_RATE * self.PRE_WAKE_SECONDS) / self.CHUNK_SIZE))
        input_device_index = self._resolve_input_device_index(recorder, controls.ambient_mic_device)
        stream = self._open_input_stream(
            recorder,
            pyaudio=pyaudio,
            input_device_index=input_device_index,
        )
        cooldown_until = 0.0
        try:
            while not self._stop_event.is_set():
                if self._muted:
                    self._stop_event.wait(0.25)
                    continue
                frame = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                buffer_frames.append(frame)
                audio = np.frombuffer(frame, dtype=np.int16)
                rms = self._frame_rms(audio)
                detector_triggered = False
                if detector is not None:
                    scores = detector.predict(audio)
                    if not detector_model_names_known:
                        detector_supports_alias = self._wake_detector_supports_alias(
                            scores.keys(),
                            wake_aliases,
                        )
                        detector_model_names_known = True
                    if detector_supports_alias:
                        detector_triggered = (
                            self._wake_detector_score(scores, wake_aliases)
                            >= self.WAKE_MODEL_SCORE_THRESHOLD
                        )

                now = time.monotonic()
                if detector_triggered:
                    capture = self._capture_command_frames(
                        stream,
                        buffer_frames=list(buffer_frames),
                    )
                    command = self._transcribe_and_extract_command(
                        capture,
                        effective_settings=effective_settings,
                        wake_aliases=wake_aliases,
                        detector_triggered=True,
                    )
                    if command is None:
                        continue
                    if command == "":
                        self.simulate_wake_word_only()
                    else:
                        self.simulate_transcript(command)
                    cooldown_until = now + self.FALLBACK_TRANSCRIPTION_COOLDOWN_SECONDS
                    continue

                if detector_supports_alias or now < cooldown_until:
                    continue
                if rms < self.FALLBACK_TRIGGER_RMS_THRESHOLD:
                    continue
                capture = self._capture_command_frames(
                    stream,
                    buffer_frames=list(buffer_frames),
                )
                command = self._transcribe_and_extract_command(
                    capture,
                    effective_settings=effective_settings,
                    wake_aliases=wake_aliases,
                    detector_triggered=False,
                )
                cooldown_until = time.monotonic() + self.FALLBACK_TRANSCRIPTION_COOLDOWN_SECONDS
                if command is None:
                    continue
                if command == "":
                    self.simulate_wake_word_only()
                else:
                    self.simulate_transcript(command)
        finally:
            stream.stop_stream()
            stream.close()
            recorder.terminate()

    @classmethod
    def _inspect_dependencies(cls) -> dict[str, object]:
        try:
            import numpy  # noqa: F401
            import pyaudio  # noqa: F401
        except Exception as exc:
            return {
                "ready": False,
                "wake_detector_available": False,
                "reason": f"Ambient listening dependencies are unavailable: {exc}",
            }
        try:
            import openwakeword  # noqa: F401
        except Exception:
            return {
                "ready": True,
                "wake_detector_available": False,
                "reason": None,
            }
        return {
            "ready": True,
            "wake_detector_available": True,
            "reason": None,
        }

    @classmethod
    def _resolve_wake_aliases(cls, wake_word: str) -> tuple[str, ...]:
        base = cls._normalize_phrase(wake_word) or "atnos"
        aliases = {
            base,
            f"hej {base}",
        }
        if base == "atnos":
            aliases.update({"atnosie", "hej atnosie"})
        if base == "okropek":
            aliases.update({"okropku", "hej okropku"})
        return tuple(sorted(aliases, key=len, reverse=True))

    @classmethod
    def _normalize_phrase(cls, value: str) -> str:
        cleaned = re.sub(r"[^\w\s]", " ", (value or "").strip().lower())
        return " ".join(cleaned.split())

    @classmethod
    def _wake_detector_supports_alias(
        cls,
        model_names,
        wake_aliases: tuple[str, ...],
    ) -> bool:
        simplified_aliases = {alias.replace(" ", "") for alias in wake_aliases}
        for model_name in model_names:
            normalized_model = cls._normalize_phrase(str(model_name)).replace(" ", "")
            if any(alias in normalized_model for alias in simplified_aliases):
                return True
        return False

    @classmethod
    def _wake_detector_score(
        cls,
        scores: dict[object, object],
        wake_aliases: tuple[str, ...],
    ) -> float:
        simplified_aliases = {alias.replace(" ", "") for alias in wake_aliases}
        best_score = 0.0
        for model_name, model_score in scores.items():
            normalized_model = cls._normalize_phrase(str(model_name)).replace(" ", "")
            if any(alias in normalized_model for alias in simplified_aliases):
                best_score = max(best_score, float(model_score))
        return best_score

    @classmethod
    def _extract_command_from_transcript(
        cls,
        transcript: str,
        *,
        wake_aliases: tuple[str, ...],
        detector_triggered: bool,
    ) -> str | None:
        cleaned = cls._normalize_phrase(transcript)
        if not cleaned:
            return None
        for alias in wake_aliases:
            if cleaned == alias:
                return ""
            if cleaned.startswith(f"{alias} "):
                return cleaned[len(alias) :].strip()
        if detector_triggered:
            return cleaned
        return None

    @classmethod
    def _resolve_input_device_index(cls, recorder, configured_device: str | None) -> int | None:
        value = (configured_device or "").strip()
        if not value:
            return None
        normalized = value
        if normalized.startswith("index:"):
            normalized = normalized.split(":", 1)[1].strip()
        if normalized.isdigit():
            return int(normalized)
        if normalized.startswith("label:"):
            normalized = normalized.split(":", 1)[1].strip()
        needle = cls._normalize_phrase(normalized)
        if not needle:
            return None
        for device_index in range(int(recorder.get_device_count())):
            try:
                info = recorder.get_device_info_by_index(device_index)
            except Exception:
                continue
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                continue
            name = cls._normalize_phrase(str(info.get("name") or ""))
            if needle and needle in name:
                return device_index
        return None

    def _open_input_stream(self, recorder, *, pyaudio, input_device_index: int | None):
        open_kwargs = {
            "format": pyaudio.paInt16,
            "channels": self.CHANNELS,
            "rate": self.SAMPLE_RATE,
            "input": True,
            "frames_per_buffer": self.CHUNK_SIZE,
        }
        if input_device_index is not None:
            try:
                return recorder.open(**open_kwargs, input_device_index=input_device_index)
            except Exception:
                pass
        return recorder.open(**open_kwargs)

    @classmethod
    def _frame_rms(cls, audio) -> float:
        if getattr(audio, "size", 0) == 0:
            return 0.0
        squares = audio.astype("float32") ** 2
        return float(squares.mean() ** 0.5)

    def _capture_command_frames(self, stream, *, buffer_frames: list[bytes]) -> list[bytes]:
        capture = list(buffer_frames)
        max_chunks = int((self.SAMPLE_RATE * self.MAX_COMMAND_SECONDS) / self.CHUNK_SIZE)
        min_chunks = max(1, int((self.SAMPLE_RATE * self.MIN_COMMAND_SECONDS) / self.CHUNK_SIZE))
        silence_limit = max(
            1,
            int((self.SAMPLE_RATE * self.TRAILING_SILENCE_SECONDS) / self.CHUNK_SIZE),
        )
        silent_chunks = 0
        import numpy as np

        for chunk_index in range(max_chunks):
            if self._stop_event.is_set():
                break
            frame = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
            capture.append(frame)
            level = self._frame_rms(np.frombuffer(frame, dtype=np.int16))
            if level <= self.SILENCE_RMS_THRESHOLD:
                silent_chunks += 1
            else:
                silent_chunks = 0
            if chunk_index >= min_chunks and silent_chunks >= silence_limit:
                break
        return capture

    def _transcribe_and_extract_command(
        self,
        frames: list[bytes],
        *,
        effective_settings: Settings,
        wake_aliases: tuple[str, ...],
        detector_triggered: bool,
    ) -> str | None:
        wav_bytes = self._pcm_frames_to_wav_bytes(frames)
        from atnos.services.voice_transcription_service import VoiceTranscriptionUnavailableError

        try:
            transcript = self._transcribe_bytes(wav_bytes, effective_settings=effective_settings)
        except VoiceTranscriptionUnavailableError:
            return None
        return self._extract_command_from_transcript(
            transcript,
            wake_aliases=wake_aliases,
            detector_triggered=detector_triggered,
        )

    def _transcribe_bytes(self, wav_bytes: bytes, *, effective_settings: Settings) -> str:
        from atnos.services.voice_transcription_service import VoiceTranscriptionService

        service = VoiceTranscriptionService(settings=effective_settings)
        import asyncio

        result = asyncio.run(
            service.transcribe(
                filename="ambient.wav",
                content_type="audio/wav",
                audio_bytes=wav_bytes,
                language="pl",
            )
        )
        return result.transcript

    def _pcm_frames_to_wav_bytes(self, frames: list[bytes]) -> bytes:
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(self.CHANNELS)
            wav_file.setsampwidth(self.SAMPLE_WIDTH)
            wav_file.setframerate(self.SAMPLE_RATE)
            wav_file.writeframes(b"".join(frames))
        return buffer.getvalue()
