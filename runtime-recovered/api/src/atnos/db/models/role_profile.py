from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Enum, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import RoleProfileScope


class RoleProfile(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "role_profiles"
    __table_args__ = (
        CheckConstraint("criticality_level BETWEEN 1 AND 5", name="ck_role_profiles_criticality_range"),
        CheckConstraint("empathy_level BETWEEN 1 AND 5", name="ck_role_profiles_empathy_range"),
        CheckConstraint("firmness_level BETWEEN 1 AND 5", name="ck_role_profiles_firmness_range"),
        Index(
            "uq_role_profiles_default_true",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text(), nullable=True)
    tone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    response_style: Mapped[str | None] = mapped_column(String(60), nullable=True)
    criticality_level: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    empathy_level: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    firmness_level: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    scope: Mapped[RoleProfileScope] = mapped_column(
        Enum(RoleProfileScope, native_enum=False, length=32),
        default=RoleProfileScope.GLOBAL,
        nullable=False,
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="role_profile")
