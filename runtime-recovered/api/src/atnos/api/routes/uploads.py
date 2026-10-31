from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal

router = APIRouter(tags=["uploads"])

AttachmentKindValue = Literal["document", "image", "audio", "video", "text", "other"]


def _load_upload_storage_dependencies():
    from atnos.services.upload_storage_service import (
        UploadAttachedError,
        UploadNotFoundError,
        UploadPathError,
        UploadStorageService,
    )

    return UploadAttachedError, UploadNotFoundError, UploadPathError, UploadStorageService


def _load_attachment_repository():
    from atnos.repositories.attachment_repository import AttachmentRepository

    return AttachmentRepository


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else str(value)


class UploadResponse(BaseModel):
    filename: str
    storage_path: str
    kind: AttachmentKindValue
    detected_kind: str | None = None
    understanding_status: str | None = None
    understanding_reason: str | None = None
    preview_text: str | None = None
    truncated: bool = False
    media_type: str | None = None
    file_extension: str | None = None
    size_bytes: int = Field(ge=0)
    extracted_text: str | None = None


class UploadsRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadDeleteRequest(UploadsRequestModel):
    storage_path: str = Field(min_length=1)


class UploadDeleteResponse(BaseModel):
    deleted: bool
    storage_path: str


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(request: Request, file: UploadFile = File(...)) -> UploadResponse:
    _, _, _, UploadStorageService = _load_upload_storage_dependencies()
    settings = request.app.state.settings
    service = UploadStorageService(settings)

    try:
        stored_upload = await service.store(file)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await file.close()

    return UploadResponse(
        filename=stored_upload.filename,
        storage_path=stored_upload.storage_path,
        kind=_enum_value(stored_upload.kind),
        detected_kind=stored_upload.detected_kind,
        understanding_status=stored_upload.understanding_status,
        understanding_reason=stored_upload.understanding_reason,
        preview_text=stored_upload.preview_text,
        truncated=stored_upload.truncated,
        media_type=stored_upload.media_type,
        file_extension=stored_upload.file_extension,
        size_bytes=stored_upload.size_bytes,
        extracted_text=stored_upload.extracted_text,
    )


@router.delete("/uploads", response_model=UploadDeleteResponse)
async def delete_upload(request: Request, payload: UploadDeleteRequest) -> UploadDeleteResponse:
    UploadAttachedError, UploadNotFoundError, UploadPathError, UploadStorageService = (
        _load_upload_storage_dependencies()
    )
    AttachmentRepository = _load_attachment_repository()
    settings = request.app.state.settings
    database = request.app.state.database
    service = UploadStorageService(settings)

    with database.session_scope() as session:
        attachments = AttachmentRepository(session)
        try:
            service.delete_orphan(payload.storage_path, attachments)
        except UploadPathError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except UploadNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except UploadAttachedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return UploadDeleteResponse(deleted=True, storage_path=payload.storage_path)
