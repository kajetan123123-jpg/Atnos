from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from atnos.db.models.scheduler_job import SchedulerJob


class SchedulerJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[SchedulerJob]:
        statement = select(SchedulerJob).order_by(SchedulerJob.updated_at.desc(), SchedulerJob.id.desc())
        return list(self.session.scalars(statement))

    def list_for_project(self, project_id: int) -> list[SchedulerJob]:
        statement = (
            select(SchedulerJob)
            .where(SchedulerJob.project_id == project_id)
            .order_by(SchedulerJob.updated_at.desc(), SchedulerJob.id.desc())
        )
        return list(self.session.scalars(statement))

    def list_due(self, *, now: datetime) -> list[SchedulerJob]:
        statement = (
            select(SchedulerJob)
            .where(
                SchedulerJob.status.in_(("queued", "retrying")),
                or_(SchedulerJob.scheduled_for.is_(None), SchedulerJob.scheduled_for <= now),
            )
            .order_by(SchedulerJob.created_at.asc(), SchedulerJob.id.asc())
        )
        return list(self.session.scalars(statement))

    def list_running(self) -> list[SchedulerJob]:
        statement = (
            select(SchedulerJob)
            .where(SchedulerJob.status == "running")
            .order_by(SchedulerJob.updated_at.desc(), SchedulerJob.id.desc())
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, job_id: int) -> SchedulerJob | None:
        statement = select(SchedulerJob).where(SchedulerJob.id == job_id)
        return self.session.scalar(statement)

    def create(self, **kwargs) -> SchedulerJob:
        item = SchedulerJob(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: SchedulerJob) -> SchedulerJob:
        self.session.add(item)
        self.session.flush()
        return item
