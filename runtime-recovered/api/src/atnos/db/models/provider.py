from __future__ import annotations

from sqlalchemy import Boolean, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import ProviderAuthMode, ProviderKind, ProviderTransport


class Provider(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "providers"

    key: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[ProviderKind] = mapped_column(
        Enum(ProviderKind, native_enum=False, length=32),
        nullable=False,
    )
    transport: Mapped[ProviderTransport] = mapped_column(
        Enum(ProviderTransport, native_enum=False, length=32),
        nullable=False,
    )
    auth_mode: Mapped[ProviderAuthMode] = mapped_column(
        Enum(ProviderAuthMode, native_enum=False, length=24),
        default=ProviderAuthMode.NONE,
        nullable=False,
    )
    endpoint_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    local_first: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_chat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_audio_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_audio_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_image_generation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    model_configs: Mapped[list["ModelConfig"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    voice_profiles: Mapped[list["VoiceProfile"]] = relationship(back_populates="provider")
