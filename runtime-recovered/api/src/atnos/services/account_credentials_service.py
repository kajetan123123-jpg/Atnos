from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.account_credential import AccountCredential
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.services.secret_vault_service import (
    METADATA_ONLY_SECRET_REF,
    METADATA_ONLY_STORAGE_BACKEND,
    SecretStorageStatus,
    SecretVaultService,
)


ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}
ALLOWED_SECRET_TYPES = {
    "password",
    "api_key",
    "access_token",
    "refresh_token",
    "app_password",
    "oauth_token_later",
    "session_cookie_later",
}
SECRET_TYPE_ALIASES = {
    "oauth_token": "oauth_token_later",
    "session_cookie": "session_cookie_later",
    "password_bundle": "password",
}
ALLOWED_ACCOUNT_CAPABILITIES = {
    "read_metadata",
    "read_content",
    "draft_write",
    "confirmed_write_later",
    "browser_login_later",
    "publish_later",
    "financial_read_later",
    "trading_later",
    "trusted_login",
    "two_factor",
    "browser_workflow",
    "connector_write",
    "local_render",
    "oauth_session",
}
ALLOWED_OS_KEYRING_STATUSES = {"configured", "disabled", "needs_rotation", "invalid"}
ALLOWED_METADATA_ONLY_STATUSES = {"metadata_only", "disabled", "needs_rotation", "invalid"}
UNSET: Final = object()


@dataclass(frozen=True, slots=True)
class AccountCredentialView:
    id: int
    label: str
    provider_key: str
    account_identifier: str
    secret_type: str
    storage_backend: str
    status: str
    has_secret: bool
    risk_level: str
    allowed_capabilities: tuple[str, ...]
    linked_integration_key: str | None
    linked_project_id: int | None
    notes: str | None
    masked_secret_state: str
    created_at: datetime
    updated_at: datetime
    last_rotated_at: datetime | None
    last_used_at: datetime | None


class AccountCredentialsService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = AccountCredentialRepository(session)
        self.secret_vault = SecretVaultService(settings)

    def get_storage_status(self) -> SecretStorageStatus:
        return self.secret_vault.get_storage_status()

    def list_accounts(self) -> list[AccountCredentialView]:
        return [self._build_view(item) for item in self.repository.list_all()]

    def get_account(self, account_id: int) -> AccountCredentialView | None:
        account = self.repository.get_by_id(account_id)
        if account is None:
            return None
        return self._build_view(account)

    def create_account(
        self,
        *,
        label: str,
        provider_key: str,
        account_identifier: str,
        secret_type: str,
        secret_value: str | None,
        risk_level: str,
        allowed_capabilities: list[str],
        linked_integration_key: str | None,
        linked_project_id: int | None,
        notes: str | None,
    ) -> AccountCredentialView:
        normalized_risk_level = self._normalize_risk_level(risk_level)
        normalized_secret_value = self._normalize_optional_secret(secret_value)
        if normalized_secret_value is None:
            storage_backend = METADATA_ONLY_STORAGE_BACKEND
            secret_ref = None
            status = "metadata_only"
            last_rotated_at = None
        else:
            storage_backend, secret_ref = self.secret_vault.store_secret(normalized_secret_value)
            status = "configured" if storage_backend != METADATA_ONLY_STORAGE_BACKEND else "metadata_only"
            last_rotated_at = self._utcnow()
        account = self.repository.create(
            label=self._normalize_required_text(label, "label"),
            provider_key=self._normalize_required_text(provider_key, "provider_key"),
            account_identifier=self._normalize_required_text(account_identifier, "account_identifier"),
            secret_type=self._normalize_secret_type(secret_type),
            storage_backend=storage_backend,
            secret_ref=secret_ref,
            status=status,
            risk_level=normalized_risk_level,
            allowed_capabilities_json=self._normalize_capabilities(allowed_capabilities),
            linked_integration_key=self._normalize_optional_text(linked_integration_key),
            linked_project_id=linked_project_id,
            notes=self._normalize_optional_text(notes),
            last_rotated_at=last_rotated_at,
            last_used_at=None,
        )
        return self._build_view(account)

    def update_account(
        self,
        account_id: int,
        *,
        label: str | None | object = UNSET,
        provider_key: str | None | object = UNSET,
        account_identifier: str | None | object = UNSET,
        secret_type: str | None | object = UNSET,
        risk_level: str | None | object = UNSET,
        allowed_capabilities: list[str] | None | object = UNSET,
        linked_integration_key: str | None | object = UNSET,
        linked_project_id: int | None | object = UNSET,
        notes: str | None | object = UNSET,
        status: str | None | object = UNSET,
    ) -> AccountCredentialView | None:
        account = self.repository.get_by_id(account_id)
        if account is None:
            return None

        if label is not UNSET and label is not None:
            account.label = self._normalize_required_text(label, "label")
        if provider_key is not UNSET and provider_key is not None:
            account.provider_key = self._normalize_required_text(provider_key, "provider_key")
        if account_identifier is not UNSET and account_identifier is not None:
            account.account_identifier = self._normalize_required_text(
                account_identifier,
                "account_identifier",
            )
        if secret_type is not UNSET and secret_type is not None:
            account.secret_type = self._normalize_secret_type(secret_type)
        if risk_level is not UNSET and risk_level is not None:
            account.risk_level = self._normalize_risk_level(risk_level)
        if allowed_capabilities is not UNSET and allowed_capabilities is not None:
            account.allowed_capabilities_json = self._normalize_capabilities(allowed_capabilities)
        if linked_integration_key is not UNSET:
            account.linked_integration_key = self._normalize_optional_text(linked_integration_key)
        if linked_project_id is not UNSET:
            account.linked_project_id = linked_project_id
        if notes is not UNSET:
            account.notes = self._normalize_optional_text(notes)
        if status is not UNSET and status is not None:
            account.status = self._normalize_status_for_storage(
                status,
                storage_backend=account.storage_backend,
            )
        self.repository.save(account)
        return self._build_view(account)

    def rotate_secret(self, account_id: int, *, secret_value: str) -> AccountCredentialView | None:
        account = self.repository.get_by_id(account_id)
        if account is None:
            return None

        storage_backend, secret_ref = self.secret_vault.rotate_secret(
            secret_value=secret_value,
            existing_secret_ref=account.secret_ref,
        )
        account.storage_backend = storage_backend
        account.secret_ref = secret_ref
        account.status = "configured" if storage_backend != METADATA_ONLY_STORAGE_BACKEND else "metadata_only"
        account.last_rotated_at = self._utcnow()
        self.repository.save(account)
        return self._build_view(account)

    def disable_account(self, account_id: int) -> AccountCredentialView | None:
        account = self.repository.get_by_id(account_id)
        if account is None:
            return None

        account.status = "disabled"
        self.repository.save(account)
        return self._build_view(account)

    def resolve_secret_for_adapter(self, account_id: int) -> str | None:
        account = self.repository.get_by_id(account_id)
        if account is None or not account.secret_ref:
            return None
        return self.secret_vault.resolve_secret(account.secret_ref)

    @staticmethod
    def _build_view(account: AccountCredential) -> AccountCredentialView:
        has_secret = bool(
            account.secret_ref
            and account.storage_backend != METADATA_ONLY_STORAGE_BACKEND
        )
        if account.status == "metadata_only" and not has_secret and account.secret_ref is None:
            masked_secret_state = "Metadata only"
        elif not account.secret_ref:
            masked_secret_state = "Not configured"
        elif account.storage_backend == METADATA_ONLY_STORAGE_BACKEND and account.secret_ref == METADATA_ONLY_SECRET_REF:
            masked_secret_state = "Configured (metadata only)"
        else:
            masked_secret_state = "Configured (masked)"
        return AccountCredentialView(
            id=account.id,
            label=account.label,
            provider_key=account.provider_key,
            account_identifier=account.account_identifier,
            secret_type=account.secret_type,
            storage_backend=account.storage_backend,
            status=account.status,
            has_secret=has_secret,
            risk_level=account.risk_level,
            allowed_capabilities=tuple(account.allowed_capabilities_json or []),
            linked_integration_key=account.linked_integration_key,
            linked_project_id=account.linked_project_id,
            notes=account.notes,
            masked_secret_state=masked_secret_state,
            created_at=account.created_at,
            updated_at=account.updated_at,
            last_rotated_at=account.last_rotated_at,
            last_used_at=account.last_used_at,
        )

    @staticmethod
    def _normalize_required_text(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        normalized = value.strip() if value is not None else ""
        return normalized or None

    @staticmethod
    def _normalize_required_secret(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("secret_value cannot be empty.")
        return normalized

    @classmethod
    def _normalize_optional_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_status(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"configured", "disabled", "needs_rotation", "invalid", "metadata_only"}:
            raise ValueError(
                "status must be one of: configured, disabled, needs_rotation, invalid, metadata_only."
            )
        return normalized

    @classmethod
    def _normalize_status_for_storage(cls, value: str, *, storage_backend: str) -> str:
        normalized = cls._normalize_status(value)
        allowed_statuses = (
            ALLOWED_METADATA_ONLY_STATUSES
            if storage_backend == METADATA_ONLY_STORAGE_BACKEND
            else ALLOWED_OS_KEYRING_STATUSES
        )
        if normalized not in allowed_statuses:
            allowed = ", ".join(sorted(allowed_statuses))
            raise ValueError(
                f"status must be one of: {allowed} for storage backend {storage_backend}."
            )
        return normalized

    @staticmethod
    def _normalize_secret_type(value: str) -> str:
        normalized = value.strip().lower()
        normalized = SECRET_TYPE_ALIASES.get(normalized, normalized)
        if normalized not in ALLOWED_SECRET_TYPES:
            allowed = ", ".join(sorted(ALLOWED_SECRET_TYPES))
            raise ValueError(f"secret_type must be one of: {allowed}.")
        return normalized

    @staticmethod
    def _normalize_capabilities(capabilities: list[str]) -> list[str]:
        normalized = []
        seen: set[str] = set()
        for item in capabilities:
            value = item.strip().lower()
            if not value or value in seen:
                continue
            if value not in ALLOWED_ACCOUNT_CAPABILITIES:
                allowed = ", ".join(sorted(ALLOWED_ACCOUNT_CAPABILITIES))
                raise ValueError(f"allowed_capabilities must use only: {allowed}.")
            normalized.append(value)
            seen.add(value)
        return normalized

    @staticmethod
    def _normalize_risk_level(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_RISK_LEVELS:
            raise ValueError("risk_level must be one of: low, medium, high, critical.")
        return normalized

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
