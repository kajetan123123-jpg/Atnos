from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.hardware_snapshot import HardwareSnapshot


class HardwareSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(self) -> HardwareSnapshot | None:
        statement = (
            select(HardwareSnapshot)
            .order_by(HardwareSnapshot.created_at.desc(), HardwareSnapshot.id.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def create(self, **kwargs) -> HardwareSnapshot:
        item = HardwareSnapshot(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item
