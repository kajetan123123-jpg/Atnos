from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.local_file_grant import LocalFileGrant


class LocalFileGrantRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_user(self, user_id: int) -> list[LocalFileGrant]:
        statement = (
            select(LocalFileGrant)
            .where(LocalFileGrant.user_id == user_id)
            .order_by(LocalFileGrant.created_at.desc(), LocalFileGrant.id.desc())
        )
        return list(self.session.scalars(statement))

    def get(self, grant_id: int) -> LocalFileGrant | None:
        statement = select(LocalFileGrant).where(LocalFileGrant.id == grant_id)
        return self.session.scalar(statement)

    def create(
        self,
        *,
        user_id: int,
        grant_root: str,
        mode: str,
        status: str,
        max_file_size_mb: int,
    ) -> LocalFileGrant:
        item = LocalFileGrant(
            user_id=user_id,
            grant_root=grant_root,
            mode=mode,
            status=status,
            max_file_size_mb=max_file_size_mb,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: LocalFileGrant) -> LocalFileGrant:
        self.session.add(item)
        self.session.flush()
        return item
