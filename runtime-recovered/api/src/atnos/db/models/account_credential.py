from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class AccountCredential(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "account_credentials"

    label: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    account_identifier: Mapped[str] = mapped_column(String(240), nullable=False)
    secret_type: Mapped[str] = mapped_column(String(40), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(40), nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configured")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    allowed_capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    linked_integration_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    linked_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
