from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.connected_account_control import ConnectedAccountControl


class AutomationControlRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[ConnectedAccountControl]:
        statement = select(ConnectedAccountControl).order_by(
            ConnectedAccountControl.updated_at.desc(),
            ConnectedAccountControl.id.desc(),
        )
        return list(self.session.scalars(statement))

    def list_for_accounts(self, account_ids: list[int]) -> list[ConnectedAccountControl]:
        if not account_ids:
            return []
        statement = (
            select(ConnectedAccountControl)
            .where(ConnectedAccountControl.account_id.in_(account_ids))
            .order_by(ConnectedAccountControl.updated_at.desc(), ConnectedAccountControl.id.desc())
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, control_id: int) -> ConnectedAccountControl | None:
        statement = select(ConnectedAccountControl).where(ConnectedAccountControl.id == control_id)
        return self.session.scalar(statement)

    def get_by_account_id(self, account_id: int) -> ConnectedAccountControl | None:
        statement = select(ConnectedAccountControl).where(
            ConnectedAccountControl.account_id == account_id,
            ConnectedAccountControl.project_id.is_(None),
        )
        return self.session.scalar(statement)

    def create(self, **kwargs) -> ConnectedAccountControl:
        item = ConnectedAccountControl(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: ConnectedAccountControl) -> ConnectedAccountControl:
        self.session.add(item)
        self.session.flush()
        return item
