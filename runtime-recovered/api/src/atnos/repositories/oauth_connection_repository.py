from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, desc, select
from sqlalchemy.orm import Session

from atnos.db.models.oauth_connection import OAuthConnection


class OAuthConnectionRepository:
    PENDING_FLOW_TTL = timedelta(minutes=15)

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[OAuthConnection]:
        statement = (
            select(OAuthConnection)
            .order_by(
                case(
                    (OAuthConnection.status == "connected", 0),
                    (OAuthConnection.status == "pending", 1),
                    else_=2,
                ),
                desc(OAuthConnection.updated_at),
                desc(OAuthConnection.id),
            )
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, connection_id: int) -> OAuthConnection | None:
        statement = select(OAuthConnection).where(OAuthConnection.id == connection_id)
        return self.session.scalar(statement)

    def get_latest_for_connector(self, connector_key: str) -> OAuthConnection | None:
        pending_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - self.PENDING_FLOW_TTL
        statement = (
            select(OAuthConnection)
            .where(OAuthConnection.connector_key == connector_key)
            .order_by(
                case(
                    (OAuthConnection.status == "connected", 0),
                    (
                        (OAuthConnection.status == "pending")
                        & (OAuthConnection.updated_at >= pending_cutoff),
                        1,
                    ),
                    (OAuthConnection.status == "error", 2),
                    else_=3,
                ),
                desc(OAuthConnection.updated_at),
                desc(OAuthConnection.id),
            )
            .limit(1)
        )
        return self.session.scalar(statement)

    def get_latest_for_connector_account(self, connector_key: str, account_id: int) -> OAuthConnection | None:
        pending_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - self.PENDING_FLOW_TTL
        statement = (
            select(OAuthConnection)
            .where(
                OAuthConnection.connector_key == connector_key,
                OAuthConnection.account_id == account_id,
            )
            .order_by(
                case(
                    (OAuthConnection.status == "connected", 0),
                    (
                        (OAuthConnection.status == "pending")
                        & (OAuthConnection.updated_at >= pending_cutoff),
                        1,
                    ),
                    (OAuthConnection.status == "error", 2),
                    else_=3,
                ),
                desc(OAuthConnection.updated_at),
                desc(OAuthConnection.id),
            )
            .limit(1)
        )
        return self.session.scalar(statement)

    def get_pending_by_state_hash(
        self,
        *,
        connector_key: str,
        state_hash: str,
    ) -> OAuthConnection | None:
        statement = select(OAuthConnection).where(
            OAuthConnection.connector_key == connector_key,
            OAuthConnection.state_hash == state_hash,
            OAuthConnection.status == "pending",
        )
        return self.session.scalar(statement)

    def list_pending_for_connector(self, connector_key: str) -> list[OAuthConnection]:
        statement = (
            select(OAuthConnection)
            .where(
                OAuthConnection.connector_key == connector_key,
                OAuthConnection.status == "pending",
            )
            .order_by(desc(OAuthConnection.updated_at), desc(OAuthConnection.id))
        )
        return list(self.session.scalars(statement))

    def create(self, **kwargs) -> OAuthConnection:
        item = OAuthConnection(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: OAuthConnection) -> OAuthConnection:
        self.session.add(item)
        self.session.flush()
        return item
