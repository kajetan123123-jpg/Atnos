from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class TrustGrant(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "trust_grants"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "account_id",
            "connector_key",
            name="uq_trust_grants_project_account_connector",
        ),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account_credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(24), nullable=False, default="blocked")
    domain_allowlist_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    action_families_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    file_write_scope_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    login_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    two_factor_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    publish_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    browser_automation_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connector_write_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    local_render_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
