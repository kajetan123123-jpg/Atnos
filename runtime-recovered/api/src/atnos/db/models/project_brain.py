from __future__ import annotations

"""Trwałe, oparte na źródłach elementy Project Brain.

Modele nie przechowują odpowiedzi modelu językowego jako prawdy. Każdy wpis
zawiera pochodzenie, czas obserwacji, pewność i dane dowodowe umożliwiające
ponowne sprawdzenie stanu projektu.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class ProjectGoal(IdentityMixin, TimestampMixin, Base):
    """Cel projektu z identyfikowalnym źródłem i stanem wykonania."""

    __tablename__ = "project_goals"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", index=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped["Project"] = relationship(back_populates="goals")


class ProjectState(IdentityMixin, TimestampMixin, Base):
    """Ostatni znormalizowany stan projektu wraz z dowodem źródłowym."""

    __tablename__ = "project_states"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    phase: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    readiness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", index=True)
    summary: Mapped[str] = mapped_column(Text(), nullable=False)
    working_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    failing_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    risks_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    open_questions_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    next_step_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    source_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped["Project"] = relationship(back_populates="brain_state")


class ProjectBlocker(IdentityMixin, TimestampMixin, Base):
    """Bloker pochodzący z określonego źródła, aktualizowany idempotentnie."""

    __tablename__ = "project_blockers"
    __table_args__ = (UniqueConstraint("project_id", "source_key", name="uq_project_blockers_source_key"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    source_key: Mapped[str] = mapped_column(String(240), nullable=False)
    title: Mapped[str] = mapped_column(String(320), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text(), nullable=True)
    severity: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped["Project"] = relationship(back_populates="brain_blockers")


class ProjectFact(IdentityMixin, TimestampMixin, Base):
    """Zweryfikowany lub obserwowany fakt; nie zastępuje on danych źródłowych."""

    __tablename__ = "project_facts"
    __table_args__ = (UniqueConstraint("project_id", "fact_key", name="uq_project_facts_fact_key"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    fact_key: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="observed")
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped["Project"] = relationship(back_populates="brain_facts")


class ProjectEvidence(IdentityMixin, TimestampMixin, Base):
    """Odniesienie do obserwacji, testu, artefaktu lub śladu wykonania."""

    __tablename__ = "project_evidence"
    __table_args__ = (UniqueConstraint("project_id", "evidence_key", name="uq_project_evidence_key"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    execution_trace_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_traces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    evidence_key: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    summary: Mapped[str] = mapped_column(Text(), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped["Project"] = relationship(back_populates="brain_evidence")
