from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.trust_grant import TrustGrant


class TrustGrantRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[TrustGrant]:
        statement = select(TrustGrant).order_by(TrustGrant.updated_at.desc(), TrustGrant.id.desc())
        return list(self.session.scalars(statement))

    def list_for_project(self, project_id: int) -> list[TrustGrant]:
        statement = (
            select(TrustGrant)
            .where(TrustGrant.project_id == project_id)
            .order_by(TrustGrant.updated_at.desc(), TrustGrant.id.desc())
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, trust_grant_id: int) -> TrustGrant | None:
        statement = select(TrustGrant).where(TrustGrant.id == trust_grant_id)
        return self.session.scalar(statement)

    def get_by_scope(self, *, project_id: int, account_id: int, connector_key: str) -> TrustGrant | None:
        statement = select(TrustGrant).where(
            TrustGrant.project_id == project_id,
            TrustGrant.account_id == account_id,
            TrustGrant.connector_key == connector_key,
        )
        return self.session.scalar(statement)

    def create(self, **kwargs) -> TrustGrant:
        item = TrustGrant(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: TrustGrant) -> TrustGrant:
        self.session.add(item)
        self.session.flush()
        return item
