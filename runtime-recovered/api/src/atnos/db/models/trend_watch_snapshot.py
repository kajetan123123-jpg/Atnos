from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class TrendWatchSnapshot(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "trend_watch_snapshots"

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    topic_query: Mapped[str] = mapped_column(String(255), nullable=False, default="short video hooks")
    region_code: Mapped[str] = mapped_column(String(16), nullable=False, default="PL")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ready")
    source_mix_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    top_signals_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    summary_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    evaluation_notes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    note_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
