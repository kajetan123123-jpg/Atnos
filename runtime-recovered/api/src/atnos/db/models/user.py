from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class User(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_owner_true",
            "is_owner",
            unique=True,
            sqlite_where=text("is_owner = 1"),
        ),
        CheckConstraint("display_name <> ''", name="ck_users_display_name_not_empty"),
    )

    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    handle: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text(), nullable=True)
    locale: Mapped[str] = mapped_column(String(32), default="pl-PL", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Warsaw", nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    execution_policy: Mapped["ExecutionPolicy | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    style_preferences: Mapped["UserStylePreferences | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    runtime_control_settings: Mapped["RuntimeControlSettings | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    voice_profiles: Mapped[list["VoiceProfile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
