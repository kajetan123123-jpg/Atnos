from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

router = APIRouter(tags=["voice"])


def _load_voice_transcription_dependencies():
    from atnos.services.runtime_settings_service import RuntimeSettingsService
    from atnos.services.voice_transcription_service import (
        VoiceTranscriptionService,
        VoiceTranscriptionTooLargeError,
        VoiceTranscriptionUnavailableError,
        VoiceTranscriptionValidationError,
    )

    return (
        RuntimeSettingsService,
        VoiceTranscriptionService,
        VoiceTranscriptionTooLargeError,
        VoiceTranscriptionUnavailableError,
        VoiceTranscriptionValidationError,
    )


class VoiceTranscriptionResponse(BaseModel):
    transcript: str
    language: str
    engine: str
    duration_ms: int = Field(ge=0)


@router.post("/voice/transcriptions", response_model=VoiceTranscriptionResponse)
async def transcribe_voice(
    request: Request,
    audio: UploadFile = File(...),
    language: str | None = Form(default=None),
    conversation_id: int | None = Form(default=None, gt=0),
    transcript_hint: str | None = Form(default=None),
) -> VoiceTranscriptionResponse:
    (
        RuntimeSettingsService,
        VoiceTranscriptionService,
        VoiceTranscriptionTooLargeError,
        VoiceTranscriptionUnavailableError,
        VoiceTranscriptionValidationError,
    ) = _load_voice_transcription_dependencies()
    # conversation_id is accepted as a future-proof hook for per-thread voice flows.
    # Voice v1 does not read or mutate conversation state through this endpoint yet.
    del conversation_id

    with request.app.state.database.session_scope() as session:
        effective_settings = RuntimeSettingsService(
            session,
            request.app.state.settings,
        ).build_effective_settings()

    service = VoiceTranscriptionService(settings=effective_settings)

    try:
        payload = await audio.read()
        result = await service.transcribe(
            filename=audio.filename,
            content_type=audio.content_type,
            audio_bytes=payload,
            language=language,
            transcript_hint=transcript_hint,
        )
    except VoiceTranscriptionTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except VoiceTranscriptionValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except VoiceTranscriptionUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    finally:
        await audio.close()

    return VoiceTranscriptionResponse(
        transcript=result.transcript,
        language=result.language,
        engine=result.engine,
        duration_ms=result.duration_ms,
    )
