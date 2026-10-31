from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class OAuthConnection(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "oauth_connections"

    connector_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available", index=True)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    code_verifier_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_credentials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    audit_log_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
