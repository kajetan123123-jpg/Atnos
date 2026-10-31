from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

from atnos.core.config import Settings


class VoiceTranscriptionError(RuntimeError):
    pass


class VoiceTranscriptionValidationError(VoiceTranscriptionError):
    pass


class VoiceTranscriptionTooLargeError(VoiceTranscriptionValidationError):
    pass


class VoiceTranscriptionUnavailableError(VoiceTranscriptionError):
    pass


@dataclass(slots=True, frozen=True)
class VoiceTranscriptionResult:
    transcript: str
    language: str
    engine: str
    duration_ms: int


class VoiceTranscriptionService:
    MAX_AUDIO_BYTES = 25 * 1024 * 1024

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    async def transcribe(
        self,
        *,
        filename: str | None,
        content_type: str | None,
        audio_bytes: bytes,
        language: str | None = None,
        transcript_hint: str | None = None,
    ) -> VoiceTranscriptionResult:
        started_at = perf_counter()
        normalized_language = self._normalize_language(language)

        if not audio_bytes:
            raise VoiceTranscriptionValidationError("Audio file is empty.")

        if len(audio_bytes) > self.MAX_AUDIO_BYTES:
            raise VoiceTranscriptionTooLargeError(
                "Audio file exceeds the 25 MB limit."
            )

        if not self._is_supported_content_type(content_type):
            raise VoiceTranscriptionValidationError("Unsupported audio content type.")

        if self._is_local_engine_configured():
            return self._transcribe_with_local_engine(
                filename=filename,
                audio_bytes=audio_bytes,
                language=language,
                started_at=started_at,
            )

        normalized_hint = (transcript_hint or "").strip()
        if normalized_hint:
            return VoiceTranscriptionResult(
                transcript=normalized_hint,
                language=normalized_language,
                engine="browser-speech",
                duration_ms=max(1, int((perf_counter() - started_at) * 1000)),
            )

        raise VoiceTranscriptionUnavailableError(
            "Voice transcription is unavailable in this environment."
        )

    def _is_local_engine_configured(self) -> bool:
        return self._settings.voice_stt_engine in {
            "whisper-local",
            "faster-whisper-local",
        } and bool((self._settings.voice_stt_model_path or "").strip())

    def _transcribe_with_local_engine(
        self,
        *,
        filename: str | None,
        audio_bytes: bytes,
        language: str | None,
        started_at: float,
    ) -> VoiceTranscriptionResult:
        engine = self._settings.voice_stt_engine

        if engine == "faster-whisper-local":
            transcript, detected_language = self._transcribe_with_faster_whisper(
                filename=filename,
                audio_bytes=audio_bytes,
                language=language,
            )
        elif engine == "whisper-local":
            transcript, detected_language = self._transcribe_with_whisper(
                filename=filename,
                audio_bytes=audio_bytes,
                language=language,
            )
        else:
            raise VoiceTranscriptionUnavailableError(
                "Voice transcription is unavailable in this environment."
            )

        normalized_transcript = transcript.strip()
        if not normalized_transcript:
            raise VoiceTranscriptionUnavailableError(
                "Local STT produced an empty transcript."
            )

        return VoiceTranscriptionResult(
            transcript=normalized_transcript,
            language=self._normalize_language(detected_language or language),
            engine=engine,
            duration_ms=max(1, int((perf_counter() - started_at) * 1000)),
        )

    def _transcribe_with_faster_whisper(
        self,
        *,
        filename: str | None,
        audio_bytes: bytes,
        language: str | None,
    ) -> tuple[str, str | None]:
        try:
            model = self._load_faster_whisper_model(
                self._resolved_model_path,
                self._settings.voice_stt_device,
            )
        except ImportError as exc:
            raise VoiceTranscriptionUnavailableError(
                "Configured local STT engine 'faster-whisper-local' is unavailable because faster-whisper is not installed."
            ) from exc
        except Exception as exc:
            self._raise_local_engine_runtime_error(
                engine="faster-whisper-local",
                phase="model loading",
                exc=exc,
            )

        temp_path = self._write_temp_audio_file(filename, audio_bytes)
        try:
            try:
                segments, info = model.transcribe(
                    temp_path.as_posix(),
                    language=self._transcription_language_or_none(language),
                )
            except Exception as exc:
                self._raise_local_engine_runtime_error(
                    engine="faster-whisper-local",
                    phase="transcription",
                    exc=exc,
                )
            transcript = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            )
            detected_language = getattr(info, "language", None)
            return transcript, detected_language
        finally:
            temp_path.unlink(missing_ok=True)

    def _transcribe_with_whisper(
        self,
        *,
        filename: str | None,
        audio_bytes: bytes,
        language: str | None,
    ) -> tuple[str, str | None]:
        try:
            model = self._load_whisper_model(
                self._resolved_model_path,
                self._settings.voice_stt_device,
            )
        except ImportError as exc:
            raise VoiceTranscriptionUnavailableError(
                "Configured local STT engine 'whisper-local' is unavailable because whisper is not installed."
            ) from exc
        except Exception as exc:
            self._raise_local_engine_runtime_error(
                engine="whisper-local",
                phase="model loading",
                exc=exc,
            )

        temp_path = self._write_temp_audio_file(filename, audio_bytes)
        try:
            try:
                result = model.transcribe(
                    temp_path.as_posix(),
                    language=self._transcription_language_or_none(language),
                )
            except Exception as exc:
                self._raise_local_engine_runtime_error(
                    engine="whisper-local",
                    phase="transcription",
                    exc=exc,
                )
            transcript = str(result.get("text", ""))
            detected_language = result.get("language")
            return transcript, detected_language if isinstance(detected_language, str) else None
        finally:
            temp_path.unlink(missing_ok=True)

    @property
    def _resolved_model_path(self) -> str:
        normalized = (self._settings.voice_stt_model_path or "").strip()
        if normalized:
            return normalized
        raise VoiceTranscriptionUnavailableError(
            "Voice transcription is unavailable in this environment."
        )

    @staticmethod
    def _transcription_language_or_none(language: str | None) -> str | None:
        normalized = (language or "").strip().lower()
        return normalized[:16] or None

    @staticmethod
    def _write_temp_audio_file(filename: str | None, audio_bytes: bytes) -> Path:
        suffix = Path(filename or "dictation.webm").suffix or ".webm"
        with NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(audio_bytes)
            return Path(handle.name)

    @staticmethod
    @lru_cache(maxsize=4)
    def _load_faster_whisper_model(model_path: str, device: str):
        from faster_whisper import WhisperModel

        return WhisperModel(model_path, device=device)

    @staticmethod
    @lru_cache(maxsize=4)
    def _load_whisper_model(model_path: str, device: str):
        import whisper

        return whisper.load_model(model_path, device=device)

    @staticmethod
    def _raise_local_engine_runtime_error(
        *,
        engine: str,
        phase: str,
        exc: Exception,
    ) -> None:
        detail = str(exc).strip() or exc.__class__.__name__
        raise VoiceTranscriptionUnavailableError(
            f"Configured local STT engine '{engine}' failed during {phase}: {detail}"
        ) from exc

    @staticmethod
    def _normalize_language(language: str | None) -> str:
        normalized = (language or "").strip().lower()
        if not normalized:
            return "unknown"
        return normalized[:16]

    @staticmethod
    def _is_supported_content_type(content_type: str | None) -> bool:
        normalized = (content_type or "").strip().lower()
        if not normalized:
            return False
        if normalized.startswith("audio/"):
            return True
        return normalized == "application/octet-stream"
