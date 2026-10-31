from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.attachment import Attachment
from atnos.db.models.enums import AttachmentKind, AttachmentSource, AttachmentStatus


class AttachmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        filename: str,
        storage_path: str,
        kind: AttachmentKind,
        project_id: int | None = None,
        conversation_id: int | None = None,
        message_id: int | None = None,
        source: AttachmentSource = AttachmentSource.UPLOAD,
        status: AttachmentStatus = AttachmentStatus.AVAILABLE,
        media_type: str | None = None,
        file_extension: str | None = None,
        size_bytes: int | None = None,
        extracted_text: str | None = None,
        metadata_json: dict | None = None,
    ) -> Attachment:
        attachment = Attachment(
            filename=filename,
            storage_path=storage_path,
            kind=kind,
            project_id=project_id,
            conversation_id=conversation_id,
            message_id=message_id,
            source=source,
            status=status,
            media_type=media_type,
            file_extension=file_extension,
            size_bytes=size_bytes,
            extracted_text=extracted_text,
            metadata_json=metadata_json,
        )
        self.session.add(attachment)
        self.session.flush()
        return attachment

    def list_for_message(self, message_id: int) -> list[Attachment]:
        statement = select(Attachment).where(Attachment.message_id == message_id).order_by(Attachment.created_at.asc())
        return list(self.session.scalars(statement))

    def exists_for_storage_paths(self, storage_paths: tuple[str, ...]) -> bool:
        if not storage_paths:
            return False

        statement = select(Attachment.id).where(Attachment.storage_path.in_(storage_paths)).limit(1)
        return self.session.execute(statement).scalar_one_or_none() is not None
