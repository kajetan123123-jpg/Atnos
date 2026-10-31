from __future__ import annotations

from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class CredentialProfile(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "credential_profiles"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_credential_profiles_account"),
    )

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account_credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    login_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backup_codes_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recovery_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recovery_email_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connector_mapping_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configured")
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
