from __future__ import annotations

from sqlalchemy import Boolean, Enum, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import PluginPermissionPolicy, PluginSource


class Plugin(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "plugins"

    key: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(Text(), nullable=False)
    source: Mapped[PluginSource] = mapped_column(
        Enum(PluginSource, native_enum=False, length=24),
        default=PluginSource.BUILTIN,
        nullable=False,
    )
    permission_policy: Mapped[PluginPermissionPolicy] = mapped_column(
        Enum(PluginPermissionPolicy, native_enum=False, length=24),
        default=PluginPermissionPolicy.ASK,
        nullable=False,
    )
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
