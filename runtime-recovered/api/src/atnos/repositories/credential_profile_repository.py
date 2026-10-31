from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.credential_profile import CredentialProfile


class CredentialProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[CredentialProfile]:
        statement = select(CredentialProfile).order_by(
            CredentialProfile.updated_at.desc(),
            CredentialProfile.id.desc(),
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, credential_profile_id: int) -> CredentialProfile | None:
        statement = select(CredentialProfile).where(CredentialProfile.id == credential_profile_id)
        return self.session.scalar(statement)

    def get_by_account_id(self, account_id: int) -> CredentialProfile | None:
        statement = select(CredentialProfile).where(CredentialProfile.account_id == account_id)
        return self.session.scalar(statement)

    def create(self, **kwargs) -> CredentialProfile:
        item = CredentialProfile(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: CredentialProfile) -> CredentialProfile:
        self.session.add(item)
        self.session.flush()
        return item
