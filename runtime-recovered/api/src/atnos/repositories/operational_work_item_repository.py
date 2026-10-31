from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from atnos.db.models.enums import OperationalWorkItemPriority, OperationalWorkItemStatus
from atnos.db.models.operational_work_item import OperationalWorkItem


class OperationalWorkItemRepository:
    OPEN_STATUSES = (
        OperationalWorkItemStatus.ACTIVE,
        OperationalWorkItemStatus.BLOCKED,
        OperationalWorkItemStatus.WAITING,
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **kwargs) -> OperationalWorkItem:
        item = OperationalWorkItem(**kwargs)
        self.session.add(item)
        self.session.flush()
        self.session.refresh(item)
        return item

    def save(self, item: OperationalWorkItem) -> OperationalWorkItem:
        self.session.add(item)
        self.session.flush()
        self.session.refresh(item)
        return item

    def get(self, work_item_id: int) -> OperationalWorkItem | None:
        return self.session.get(OperationalWorkItem, work_item_id)

    def list(
        self,
        *,
        project_id: int | None = None,
        source_note_id: int | None = None,
        status: OperationalWorkItemStatus | None = None,
        limit: int | None = None,
    ) -> list[OperationalWorkItem]:
        statement = select(OperationalWorkItem)
        if project_id is not None:
            statement = statement.where(OperationalWorkItem.project_id == project_id)
        if source_note_id is not None:
            statement = statement.where(OperationalWorkItem.source_note_id == source_note_id)
        if status is not None:
            statement = statement.where(OperationalWorkItem.status == status)
        statement = statement.order_by(
            case(
                (OperationalWorkItem.status == OperationalWorkItemStatus.ACTIVE, 0),
                (OperationalWorkItem.status == OperationalWorkItemStatus.BLOCKED, 1),
                (OperationalWorkItem.status == OperationalWorkItemStatus.WAITING, 2),
                else_=3,
            ),
            case(
                (OperationalWorkItem.priority == OperationalWorkItemPriority.P0, 0),
                (OperationalWorkItem.priority == OperationalWorkItemPriority.P1, 1),
                else_=2,
            ),
            OperationalWorkItem.updated_at.desc(),
            OperationalWorkItem.id.desc(),
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return list(self.session.scalars(statement))

    def get_open_for_source_note(self, source_note_id: int, *, excluding_id: int | None = None) -> OperationalWorkItem | None:
        statement = select(OperationalWorkItem).where(
            OperationalWorkItem.source_note_id == source_note_id,
            OperationalWorkItem.status.in_(self.OPEN_STATUSES),
        )
        if excluding_id is not None:
            statement = statement.where(OperationalWorkItem.id != excluding_id)
        statement = statement.order_by(OperationalWorkItem.updated_at.desc(), OperationalWorkItem.id.desc()).limit(1)
        return self.session.scalar(statement)
