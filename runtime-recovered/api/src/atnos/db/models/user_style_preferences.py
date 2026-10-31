from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import ResponseLengthPreference, ResponseStructure, ResponseTone


class UserStylePreferences(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "user_style_preferences"
    __table_args__ = (
        CheckConstraint("criticality_level BETWEEN 1 AND 5", name="ck_user_style_preferences_criticality_range"),
        CheckConstraint("supportiveness_level BETWEEN 1 AND 5", name="ck_user_style_preferences_supportiveness_range"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    response_length: Mapped[ResponseLengthPreference] = mapped_column(
        Enum(ResponseLengthPreference, native_enum=False, length=16),
        default=ResponseLengthPreference.MEDIUM,
        nullable=False,
    )
    tone: Mapped[ResponseTone] = mapped_column(
        Enum(ResponseTone, native_enum=False, length=24),
        default=ResponseTone.NATURAL,
        nullable=False,
    )
    structure: Mapped[ResponseStructure] = mapped_column(
        Enum(ResponseStructure, native_enum=False, length=24),
        default=ResponseStructure.MIXED,
        nullable=False,
    )
    criticality_level: Mapped[int] = mapped_column(default=3, nullable=False)
    supportiveness_level: Mapped[int] = mapped_column(default=3, nullable=False)
    professional_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship(back_populates="style_preferences")
