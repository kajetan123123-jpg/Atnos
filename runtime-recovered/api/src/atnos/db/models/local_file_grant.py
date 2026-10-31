from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class LocalFileGrant(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "local_file_grants"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grant_root: Mapped[str] = mapped_column(String(1024), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False, default="read_only")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    max_file_size_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
