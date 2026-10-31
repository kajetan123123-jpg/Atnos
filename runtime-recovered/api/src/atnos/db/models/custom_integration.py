from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class CustomIntegration(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "custom_integrations"

    key: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    permission_level: Mapped[str] = mapped_column(String(32), nullable=False, default="not_connected")
    notes: Mapped[str] = mapped_column(Text(), nullable=False)
    setup_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    setup_requirements_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    missing_requirements_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    next_action_label: Mapped[str] = mapped_column(String(120), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="user_defined")
    has_adapter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
