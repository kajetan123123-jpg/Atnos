from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class ProjectAppLink(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "project_app_links"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    integration_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    custom_integration_id: Mapped[int | None] = mapped_column(
        ForeignKey("custom_integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_credentials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, default="account")
    resource_external_id: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    resource_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    binding_role: Mapped[str] = mapped_column(String(64), nullable=False, default="primary")
    environment: Mapped[str] = mapped_column(String(24), nullable=False, default="production")
    confirmation_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="supervised")
    delegation_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="project_only")
    allow_shared_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permission_scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unverified", index=True)
    connection_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="configured")
    permission_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    safety_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    is_default_for_project: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default_for_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default_for_analytics: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default_for_storage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default_for_publish_preview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="app_links")
