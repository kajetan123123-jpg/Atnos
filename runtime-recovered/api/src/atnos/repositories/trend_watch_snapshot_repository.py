from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.trend_watch_snapshot import TrendWatchSnapshot


class TrendWatchSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **kwargs) -> TrendWatchSnapshot:
        item = TrendWatchSnapshot(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: TrendWatchSnapshot) -> TrendWatchSnapshot:
        self.session.add(item)
        self.session.flush()
        return item

    def latest_for_project(self, project_id: int | None) -> TrendWatchSnapshot | None:
        statement = (
            select(TrendWatchSnapshot)
            .where(TrendWatchSnapshot.project_id.is_(None) if project_id is None else TrendWatchSnapshot.project_id == project_id)
            .order_by(TrendWatchSnapshot.collected_at.desc(), TrendWatchSnapshot.id.desc())
        )
        return self.session.scalar(statement)

    def latest_project_or_global(self, project_id: int | None) -> TrendWatchSnapshot | None:
        if project_id is not None:
            scoped = self.latest_for_project(project_id)
            if scoped is not None:
                return scoped
        return self.latest_for_project(None)
