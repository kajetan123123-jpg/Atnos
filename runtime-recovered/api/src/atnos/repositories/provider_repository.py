from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.provider import Provider


class ProviderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, provider_id: int) -> Provider | None:
        return self.session.get(Provider, provider_id)

    def get_by_key(self, key: str) -> Provider | None:
        statement = select(Provider).where(Provider.key == key)
        return self.session.scalar(statement)

    def list_enabled(self) -> list[Provider]:
        statement = select(Provider).where(Provider.is_enabled.is_(True)).order_by(Provider.key.asc())
        return list(self.session.scalars(statement))

    def list_all(self) -> list[Provider]:
        statement = select(Provider).order_by(Provider.key.asc())
        return list(self.session.scalars(statement))

    def create(self, **kwargs) -> Provider:
        provider = Provider(**kwargs)
        self.session.add(provider)
        self.session.flush()
        return provider

    def update_endpoint_url(
        self,
        provider: Provider,
        endpoint_url: str | None,
    ) -> Provider:
        provider.endpoint_url = endpoint_url
        self.session.add(provider)
        self.session.flush()
        return provider
