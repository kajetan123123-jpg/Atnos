from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.browser_domain_whitelist import BrowserDomainWhitelist


class BrowserDomainWhitelistRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_user(self, user_id: int) -> list[BrowserDomainWhitelist]:
        statement = (
            select(BrowserDomainWhitelist)
            .where(BrowserDomainWhitelist.user_id == user_id)
            .order_by(BrowserDomainWhitelist.domain.asc(), BrowserDomainWhitelist.id.asc())
        )
        return list(self.session.scalars(statement))

    def get(self, whitelist_id: int) -> BrowserDomainWhitelist | None:
        statement = select(BrowserDomainWhitelist).where(BrowserDomainWhitelist.id == whitelist_id)
        return self.session.scalar(statement)

    def find_by_domain(self, *, user_id: int, domain: str) -> BrowserDomainWhitelist | None:
        statement = select(BrowserDomainWhitelist).where(
            BrowserDomainWhitelist.user_id == user_id,
            BrowserDomainWhitelist.domain == domain,
        )
        return self.session.scalar(statement)

    def create(self, *, user_id: int, domain: str) -> BrowserDomainWhitelist:
        item = BrowserDomainWhitelist(user_id=user_id, domain=domain)
        self.session.add(item)
        self.session.flush()
        return item

    def delete(self, item: BrowserDomainWhitelist) -> None:
        self.session.delete(item)
        self.session.flush()
