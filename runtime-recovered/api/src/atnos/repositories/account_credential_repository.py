from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atnos.db.models.account_credential import AccountCredential


class AccountCredentialRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[AccountCredential]:
        statement = select(AccountCredential).order_by(
            AccountCredential.created_at.desc(),
            AccountCredential.id.desc(),
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, account_id: int) -> AccountCredential | None:
        statement = select(AccountCredential).where(AccountCredential.id == account_id)
        return self.session.scalar(statement)

    def find_by_provider_and_identifier(
        self,
        *,
        provider_key: str,
        account_identifier: str,
    ) -> AccountCredential | None:
        statement = select(AccountCredential).where(
            AccountCredential.provider_key == provider_key,
            AccountCredential.account_identifier == account_identifier,
        )
        return self.session.scalar(statement)

    def create(
        self,
        *,
        label: str,
        provider_key: str,
        account_identifier: str,
        secret_type: str,
        storage_backend: str,
        secret_ref: str | None,
        status: str,
        risk_level: str,
        allowed_capabilities_json: list[str],
        linked_integration_key: str | None,
        linked_project_id: int | None,
        notes: str | None,
        last_rotated_at,
        last_used_at,
    ) -> AccountCredential:
        account = AccountCredential(
            label=label,
            provider_key=provider_key,
            account_identifier=account_identifier,
            secret_type=secret_type,
            storage_backend=storage_backend,
            secret_ref=secret_ref,
            status=status,
            risk_level=risk_level,
            allowed_capabilities_json=allowed_capabilities_json,
            linked_integration_key=linked_integration_key,
            linked_project_id=linked_project_id,
            notes=notes,
            last_rotated_at=last_rotated_at,
            last_used_at=last_used_at,
        )
        self.session.add(account)
        self.session.flush()
        return account

    def save(self, account: AccountCredential) -> AccountCredential:
        self.session.add(account)
        self.session.flush()
        return account

    def configured_counts_by_integration_key(self) -> dict[str, int]:
        statement = (
            select(
                AccountCredential.linked_integration_key,
                func.count(AccountCredential.id),
            )
            .where(
                AccountCredential.linked_integration_key.is_not(None),
                AccountCredential.status == "configured",
            )
            .group_by(AccountCredential.linked_integration_key)
        )
        rows = self.session.execute(statement).all()
        return {
            str(linked_integration_key): int(count)
            for linked_integration_key, count in rows
            if linked_integration_key is not None
        }
