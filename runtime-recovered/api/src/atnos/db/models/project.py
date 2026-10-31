from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Enum, Float, ForeignKey, ForeignKeyConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import (
    PersonaProfile,
    ProjectMemoryKind,
    ProjectMemoryPriority,
    ProjectStatus,
    ProviderPosture,
)


class Project(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, native_enum=False, length=32),
        default=ProjectStatus.ACTIVE,
        nullable=False,
    )
    provider_posture: Mapped[ProviderPosture | None] = mapped_column(
        Enum(ProviderPosture, native_enum=False, length=40),
        nullable=True,
    )
    persona_profile: Mapped[PersonaProfile | None] = mapped_column(
        Enum(PersonaProfile, native_enum=False, length=24),
        nullable=True,
    )
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="project")
    memories: Mapped[list["ProjectMemory"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    app_links: Mapped[list["ProjectAppLink"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="project")
    execution_traces: Mapped[list["ExecutionTrace"]] = relationship(back_populates="project")
    goals: Mapped[list["ProjectGoal"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    brain_state: Mapped["ProjectState | None"] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        uselist=False,
    )
    brain_blockers: Mapped[list["ProjectBlocker"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    brain_facts: Mapped[list["ProjectFact"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    brain_evidence: Mapped[list["ProjectEvidence"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    rollback_points: Mapped[list["RollbackPoint"]] = relationship(back_populates="project")


class ProjectMemory(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "project_memories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_message_id", "source_conversation_id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_project_memories_message_conversation",
        ),
        CheckConstraint(
            "source_message_id IS NULL OR source_conversation_id IS NOT NULL",
            name="ck_project_memories_message_requires_conversation",
        ),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    source_conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_message_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )
    memory_kind: Mapped[ProjectMemoryKind] = mapped_column(
        Enum(ProjectMemoryKind, native_enum=False, length=32),
        nullable=False,
    )
    priority: Mapped[ProjectMemoryPriority] = mapped_column(
        Enum(ProjectMemoryPriority, native_enum=False, length=32),
        default=ProjectMemoryPriority.MEDIUM,
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="memories")
    source_conversation: Mapped["Conversation | None"] = relationship(
        back_populates="memory_entries",
        overlaps="memory_entries,source_message",
    )
    source_message: Mapped["Message | None"] = relationship(
        back_populates="memory_entries",
        overlaps="memory_entries,source_conversation",
    )
