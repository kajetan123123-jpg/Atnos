from __future__ import annotations

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import (
    SelfImprovementDecision,
    SelfImprovementSeverity,
    SelfImprovementStatus,
)


class SelfImprovementNote(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "self_improvement_notes"

    source: Mapped[str] = mapped_column(String(80), nullable=False)
    view: Mapped[str] = mapped_column(String(80), nullable=False)
    friction: Mapped[str] = mapped_column(Text(), nullable=False)
    severity: Mapped[SelfImprovementSeverity] = mapped_column(
        Enum(SelfImprovementSeverity, native_enum=False, length=8),
        nullable=False,
    )
    proposed_fix: Mapped[str | None] = mapped_column(Text(), nullable=True)
    chosen_next_patch: Mapped[str | None] = mapped_column(Text(), nullable=True)
    decision: Mapped[SelfImprovementDecision] = mapped_column(
        Enum(SelfImprovementDecision, native_enum=False, length=24),
        default=SelfImprovementDecision.UNDECIDED,
        nullable=False,
    )
    status: Mapped[SelfImprovementStatus] = mapped_column(
        Enum(SelfImprovementStatus, native_enum=False, length=16),
        default=SelfImprovementStatus.OPEN,
        nullable=False,
    )
