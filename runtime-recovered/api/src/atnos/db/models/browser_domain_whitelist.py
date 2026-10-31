from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class BrowserDomainWhitelist(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "browser_domain_whitelist"
    __table_args__ = (
        UniqueConstraint("user_id", "domain", name="uq_browser_domain_whitelist_user_domain"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
