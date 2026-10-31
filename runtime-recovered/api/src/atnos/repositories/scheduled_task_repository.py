from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.scheduled_task import ScheduledTask


class ScheduledTaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[ScheduledTask]:
        statement = select(ScheduledTask).order_by(ScheduledTask.updated_at.desc(), ScheduledTask.id.desc())
        return list(self.session.scalars(statement))

    def list_for_project(self, project_id: int) -> list[ScheduledTask]:
        statement = (
            select(ScheduledTask)
            .where(ScheduledTask.project_id == project_id)
            .order_by(ScheduledTask.updated_at.desc(), ScheduledTask.id.desc())
        )
        return list(self.session.scalars(statement))

    def list_active_for_user(self, user_id: int) -> list[ScheduledTask]:
        statement = (
            select(ScheduledTask)
            .where(ScheduledTask.user_id == user_id, ScheduledTask.is_active.is_(True))
            .order_by(ScheduledTask.updated_at.desc(), ScheduledTask.id.desc())
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, task_id: int) -> ScheduledTask | None:
        statement = select(ScheduledTask).where(ScheduledTask.id == task_id)
        return self.session.scalar(statement)

    def create(self, **kwargs) -> ScheduledTask:
        item = ScheduledTask(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: ScheduledTask) -> ScheduledTask:
        self.session.add(item)
        self.session.flush()
        return item

    def delete(self, item: ScheduledTask) -> None:
        self.session.delete(item)
        self.session.flush()
