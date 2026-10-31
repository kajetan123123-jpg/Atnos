from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Enum, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import (
    ExecutionAutomationLevel,
    ExecutionVerificationMode,
    JarvisAddressAs,
    PersonaProfile,
    ProviderPosture,
)


class ExecutionPolicy(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "execution_policies"
    __table_args__ = (
        CheckConstraint("max_daily_cost IS NULL OR max_daily_cost >= 0", name="ck_execution_policies_daily_cost"),
        CheckConstraint("max_monthly_cost IS NULL OR max_monthly_cost >= 0", name="ck_execution_policies_monthly_cost"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    automation_level: Mapped[ExecutionAutomationLevel] = mapped_column(
        Enum(ExecutionAutomationLevel, native_enum=False, length=24),
        default=ExecutionAutomationLevel.ASSISTED,
        nullable=False,
    )
    verification_mode: Mapped[ExecutionVerificationMode] = mapped_column(
        Enum(ExecutionVerificationMode, native_enum=False, length=24),
        default=ExecutionVerificationMode.SMART,
        nullable=False,
    )
    allow_local_file_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_local_file_write: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_web_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_external_fallback: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_external_attachment_metadata: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_external_attachment_content: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prefer_local_llm: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_provider_posture: Mapped[ProviderPosture] = mapped_column(
        Enum(ProviderPosture, native_enum=False, length=40),
        default=ProviderPosture.PREFER_LOCAL,
        nullable=False,
    )
    default_persona_profile: Mapped[PersonaProfile] = mapped_column(
        Enum(PersonaProfile, native_enum=False, length=24),
        default=PersonaProfile.STRATEGIST,
        nullable=False,
    )
    jarvis_address_as: Mapped[JarvisAddressAs] = mapped_column(
        Enum(JarvisAddressAs, native_enum=False, length=24),
        default=JarvisAddressAs.NONE,
        nullable=False,
    )
    max_external_attachment_text_chars: Mapped[int | None] = mapped_column(nullable=True)
    max_daily_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_monthly_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped["User"] = relationship(back_populates="execution_policy")
