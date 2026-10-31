from __future__ import annotations

"""Trwała tożsamość kanału YouTube i zatwierdzone zasady publikacji.

Ten model jest celowo oddzielony od jednorazowych tokenów OAuth. Token potwierdza
dostęp techniczny, a profil poniżej określa *na który kanał* Atnos może działać
i na jakich zasadach. Dzięki temu nazwa wyświetlana kanału nigdy nie jest jedynym
kluczem routingu uploadu.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class YouTubeChannelIdentity(IdentityMixin, TimestampMixin, Base):
    """Kanał przypisany do projektu przez stabilny identyfikator i binding konta."""

    __tablename__ = "youtube_channel_identities"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_youtube_channel_identities_project_id"),
        UniqueConstraint("account_binding_key", name="uq_youtube_channel_identities_account_binding_key"),
        UniqueConstraint("youtube_channel_id", name="uq_youtube_channel_identities_youtube_channel_id"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Canonical application-side identifier, for example youtube:owner@example.com.
    account_binding_key: Mapped[str] = mapped_column(String(320), nullable=False)
    # Stable UC... identifier. Nullable only while the channel is still being inventoried.
    youtube_channel_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_handle: Mapped[str | None] = mapped_column(String(160), nullable=True)
    channel_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    owner_label: Mapped[str | None] = mapped_column(String(240), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(24), nullable=True)
    target_market: Mapped[str | None] = mapped_column(String(80), nullable=True)
    niche: Mapped[str | None] = mapped_column(Text(), nullable=True)
    oauth_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("oauth_connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    identity_status: Mapped[str] = mapped_column(String(40), nullable=False, default="insufficient_data", index=True)
    owner_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    manifest_status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    manifest_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publishing_policy_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    publishing_policy_status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    publishing_policy_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
