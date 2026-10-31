from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class AccountActionDraft(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "account_action_drafts"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account_credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    integration_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    action_title: Mapped[str] = mapped_column(String(200), nullable=False)
    action_summary: Mapped[str] = mapped_column(Text(), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preview_payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    approval_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    audit_log_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False, default="user")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
