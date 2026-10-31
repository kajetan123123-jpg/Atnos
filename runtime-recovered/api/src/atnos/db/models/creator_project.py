from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class CreatorProject(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "creator_projects"

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    project_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    persona_type: Mapped[str] = mapped_column(String(40), nullable=False, default="none")
    disclosure_policy: Mapped[str] = mapped_column(String(40), nullable=False, default="not_required")
    niche: Mapped[str | None] = mapped_column(String(240), nullable=True)
    target_audience: Mapped[str | None] = mapped_column(String(240), nullable=True)
    monetization_goal: Mapped[str | None] = mapped_column(String(240), nullable=True)
    content_boundaries: Mapped[str | None] = mapped_column(Text(), nullable=True)
    safety_boundaries: Mapped[str | None] = mapped_column(Text(), nullable=True)
    platforms_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    linked_accounts_json: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    linked_integrations_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    operating_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="planning_only")
    approval_policy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="always_require_approval",
    )
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    strategy_profile_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    work_items: Mapped[list["CreatorWorkItem"]] = relationship(
        back_populates="creator_project",
        cascade="all, delete-orphan",
    )
    content_calendar_items: Mapped[list["ContentCalendarItem"]] = relationship(
        back_populates="creator_project",
        cascade="all, delete-orphan",
    )
    project: Mapped["Project | None"] = relationship()


class CreatorWorkItem(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "creator_work_items"

    creator_project_id: Mapped[int] = mapped_column(
        ForeignKey("creator_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    work_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="backlog")
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    platform: Mapped[str | None] = mapped_column(String(80), nullable=True)
    draft_payload_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(240), nullable=True)
    safety_flags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    creator_project: Mapped[CreatorProject] = relationship(back_populates="work_items")
    content_calendar_items: Mapped[list["ContentCalendarItem"]] = relationship(
        back_populates="work_item",
    )


class ContentCalendarItem(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "content_calendar_items"

    creator_project_id: Mapped[int] = mapped_column(
        ForeignKey("creator_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    work_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("creator_work_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(80), nullable=True)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    planned_date: Mapped[date] = mapped_column(Date(), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned")
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    linked_app_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_app_links.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    creator_project: Mapped[CreatorProject] = relationship(back_populates="content_calendar_items")
    work_item: Mapped["CreatorWorkItem | None"] = relationship(back_populates="content_calendar_items")
