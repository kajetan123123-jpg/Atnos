from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class HardwareSnapshot(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "hardware_snapshots"

    snapshot_type: Mapped[str] = mapped_column(String(64), nullable=False, default="runtime")
    free_vram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_vram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_vram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
