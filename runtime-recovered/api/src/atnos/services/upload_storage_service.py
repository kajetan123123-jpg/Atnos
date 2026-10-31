from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import UploadFile

from atnos.core.config import Settings
from atnos.db.models.enums import AttachmentKind
from atnos.services.attachment_understanding_service import AttachmentUnderstandingService

if TYPE_CHECKING:
    from atnos.repositories.attachment_repository import AttachmentRepository


@dataclass(slots=True, frozen=True)
class StoredUpload:
    filename: str
    storage_path: str
    kind: AttachmentKind
    media_type: str | None
    file_extension: str | None
    size_bytes: int
    extracted_text: str | None = None
    detected_kind: str | None = None
    understanding_status: str | None = None
    understanding_reason: str | None = None
    preview_text: str | None = None
    truncated: bool = False


class UploadCleanupError(Exception):
    pass


class UploadPathError(UploadCleanupError):
    pass


class UploadNotFoundError(UploadCleanupError):
    pass


class UploadAttachedError(UploadCleanupError):
    pass


class UploadStorageService:
    MAX_RAW_READ_BYTES = 64 * 1024
    MAX_PREVIEW_TEXT_CHARS = 8_000

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.attachment_understanding = AttachmentUnderstandingService(
            max_raw_read_bytes=self.MAX_RAW_READ_BYTES,
            max_preview_text_chars=self.MAX_PREVIEW_TEXT_CHARS,
        )

    async def store(self, upload: UploadFile) -> StoredUpload:
        original_filename = Path(upload.filename or "upload.bin").name or "upload.bin"
        file_extension = Path(original_filename).suffix.lower() or None
        media_type = upload.content_type or mimetypes.guess_type(original_filename)[0]

        content = await upload.read()
        if not content:
            raise ValueError("Uploaded file is empty.")
        if len(content) > self.settings.max_upload_size_bytes:
            raise ValueError(
                f"Uploaded file exceeds the {self.settings.max_upload_size_bytes} byte limit."
            )

        understanding = self.attachment_understanding.understand(
            filename=original_filename,
            content=content,
            content_type=media_type,
        )

        stored_name = f"{uuid4().hex}{file_extension or ''}"
        absolute_path = self.settings.uploads_dir / stored_name
        absolute_path.write_bytes(content)

        try:
            storage_path = absolute_path.relative_to(self.settings.app_root).as_posix()
        except ValueError:
            storage_path = absolute_path.as_posix()

        return StoredUpload(
            filename=original_filename,
            storage_path=storage_path,
            kind=understanding.attachment_kind,
            media_type=media_type,
            file_extension=file_extension,
            size_bytes=len(content),
            extracted_text=understanding.extracted_text,
            detected_kind=understanding.detected_kind,
            understanding_status=understanding.status,
            understanding_reason=understanding.status_reason,
            preview_text=understanding.preview_text,
            truncated=understanding.truncated,
        )

    def delete_orphan(self, storage_path: str, attachments: "AttachmentRepository") -> None:
        managed_path, known_storage_paths = self._resolve_managed_upload_path(storage_path)

        if attachments.exists_for_storage_paths(known_storage_paths):
            raise UploadAttachedError("Upload is already attached to a conversation and cannot be deleted.")

        if not managed_path.exists():
            raise UploadNotFoundError("Upload file was not found.")
        if not managed_path.is_file():
            raise UploadPathError("Only uploaded files can be deleted.")

        managed_path.unlink()

    def _resolve_managed_upload_path(self, storage_path: str) -> tuple[Path, tuple[str, ...]]:
        normalized_storage_path = storage_path.strip()
        if not normalized_storage_path:
            raise UploadPathError("storage_path must not be empty.")

        requested_path = Path(normalized_storage_path)
        candidate_path = (
            requested_path.resolve()
            if requested_path.is_absolute()
            else (self.settings.app_root / requested_path).resolve()
        )
        uploads_root = self.settings.uploads_dir.resolve()

        try:
            candidate_path.relative_to(uploads_root)
        except ValueError as exc:
            raise UploadPathError("storage_path must point to a file inside the managed uploads directory.") from exc

        known_storage_paths = {candidate_path.as_posix(), normalized_storage_path}
        try:
            known_storage_paths.add(candidate_path.relative_to(self.settings.app_root.resolve()).as_posix())
        except ValueError:
            pass

        return candidate_path, tuple(sorted(known_storage_paths))
