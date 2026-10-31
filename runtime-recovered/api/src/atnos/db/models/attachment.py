from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, ForeignKeyConstraint, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import AttachmentKind, AttachmentSource, AttachmentStatus


class Attachment(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_attachments_message_conversation",
        ),
        CheckConstraint(
            "project_id IS NOT NULL OR conversation_id IS NOT NULL OR message_id IS NOT NULL",
            name="ck_attachments_has_owner",
        ),
        CheckConstraint(
            "("
            "project_id IS NOT NULL AND conversation_id IS NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NOT NULL"
            ")",
            name="ck_attachments_owner_pattern",
        ),
    )

    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        index=True,
        nullable=True,
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    kind: Mapped[AttachmentKind] = mapped_column(
        Enum(AttachmentKind, native_enum=False, length=32),
        nullable=False,
    )
    source: Mapped[AttachmentSource] = mapped_column(
        Enum(AttachmentSource, native_enum=False, length=32),
        default=AttachmentSource.UPLOAD,
        nullable=False,
    )
    status: Mapped[AttachmentStatus] = mapped_column(
        Enum(AttachmentStatus, native_enum=False, length=32),
        default=AttachmentStatus.AVAILABLE,
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_extension: Mapped[str | None] = mapped_column(String(24), nullable=True)
    storage_path: Mapped[str] = mapped_column(Text(), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped["Conversation | None"] = relationship(
        back_populates="attachments",
        overlaps="attachments,message",
    )
    message: Mapped["Message | None"] = relationship(
        back_populates="attachments",
        overlaps="attachments,conversation",
    )
    project: Mapped["Project | None"] = relationship(back_populates="attachments")
