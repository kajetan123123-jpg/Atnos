from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from atnos.db.models.decision_log import DecisionLogEntry
from atnos.db.models.enums import DecisionLogStatus


class DecisionLogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **kwargs) -> DecisionLogEntry:
        now = datetime.now(UTC)
        kwargs.setdefault("created_at", now)
        kwargs.setdefault("updated_at", now)
        item = DecisionLogEntry(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: DecisionLogEntry) -> DecisionLogEntry:
        item.updated_at = datetime.now(UTC)
        self.session.add(item)
        self.session.flush()
        return item

    def get(self, entry_id: int) -> DecisionLogEntry | None:
        return self.session.get(DecisionLogEntry, entry_id)

    def list_for_conversation(self, conversation_id: int, *, limit: int | None = None) -> list[DecisionLogEntry]:
        statement = (
            select(DecisionLogEntry)
            .where(DecisionLogEntry.conversation_id == conversation_id)
            .order_by(DecisionLogEntry.made_at.desc(), DecisionLogEntry.id.desc())
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return list(self.session.scalars(statement))

    def list_for_project(self, project_id: int, *, limit: int | None = None) -> list[DecisionLogEntry]:
        statement = (
            select(DecisionLogEntry)
            .where(DecisionLogEntry.project_id == project_id)
            .order_by(DecisionLogEntry.made_at.desc(), DecisionLogEntry.id.desc())
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return list(self.session.scalars(statement))

    def list_recent(
        self,
        *,
        status: DecisionLogStatus | None = None,
        limit: int | None = None,
    ) -> list[DecisionLogEntry]:
        statement = select(DecisionLogEntry)
        if status is not None:
            statement = statement.where(DecisionLogEntry.status == status)
        statement = statement.order_by(
            DecisionLogEntry.made_at.desc(),
            DecisionLogEntry.id.desc(),
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return list(self.session.scalars(statement))

    def list_active(
        self,
        *,
        project_id: int | None = None,
        due_before_or_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[DecisionLogEntry]:
        clauses = [DecisionLogEntry.status == DecisionLogStatus.ACTIVE]
        if project_id is not None:
            clauses.append(DecisionLogEntry.project_id == project_id)
        if due_before_or_at is not None:
            clauses.append(
                or_(
                    DecisionLogEntry.due_at.is_(None),
                    DecisionLogEntry.due_at <= due_before_or_at,
                )
            )
        statement = (
            select(DecisionLogEntry)
            .where(and_(*clauses))
            .order_by(
                DecisionLogEntry.due_at.asc().nullslast(),
                DecisionLogEntry.made_at.desc(),
                DecisionLogEntry.id.desc(),
            )
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return list(self.session.scalars(statement))
