from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class IntegrationConfig(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "integration_configs"

    integration_key: Mapped[str] = mapped_column(String(length=80), unique=True, nullable=False)
    local_path: Mapped[str | None] = mapped_column(String(length=1024), nullable=True)
    export_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
