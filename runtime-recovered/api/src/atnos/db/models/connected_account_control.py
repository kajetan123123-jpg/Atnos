from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class ConnectedAccountControl(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "connected_account_controls"

    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_credentials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    integration_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    connector_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="metadata_only")
    permission_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="metadata_only")
    automation_level: Mapped[str] = mapped_column(String(24), nullable=False, default="off")
    allowed_capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    automation_policy_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warnings_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    audit_log_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
