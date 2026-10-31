from __future__ import annotations

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import VoiceProviderMode


class VoiceProfile(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (
        Index(
            "uq_voice_profiles_user_default_true",
            "user_id",
            unique=True,
            sqlite_where=text("is_default = 1"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    voice_key: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_mode: Mapped[VoiceProviderMode] = mapped_column(
        Enum(VoiceProviderMode, native_enum=False, length=24),
        default=VoiceProviderMode.REMOTE,
        nullable=False,
    )
    style: Mapped[str | None] = mapped_column(String(80), nullable=True)
    language: Mapped[str] = mapped_column(String(24), default="pl-PL", nullable=False)
    speaking_rate: Mapped[str | None] = mapped_column(String(24), nullable=True)
    supports_realtime: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_read_responses: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    user: Mapped["User"] = relationship(back_populates="voice_profiles")
    provider: Mapped["Provider | None"] = relationship(back_populates="voice_profiles")
