from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from atnos.core.config import Settings


DEFAULT_PROVIDER_CARD_IDENTIFIER = "default"
METADATA_ONLY_STORAGE_BACKEND = "metadata_only"


@dataclass(frozen=True, slots=True)
class ProviderRuntimeSecretSyncResult:
    provider_key: str
    env_var: str
    status: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_key": self.provider_key,
            "env_var": self.env_var,
            "status": self.status,
            "reason": self.reason,
        }


class ProviderRuntimeSecretSyncService:
    PROVIDER_ENV_MAP = {
        "anthropic-cloud": "ANTHROPIC_API_KEY",
        "openai-cloud": "OPENAI_API_KEY",
        "gemini-cloud": "GEMINI_API_KEY",
    }

    def __init__(self, session: "Session", settings: "Settings") -> None:
        self.session = session
        self.settings = settings

    def sync_known_cloud_providers(
        self,
        *,
        prefer_vault: bool = False,
    ) -> tuple[ProviderRuntimeSecretSyncResult, ...]:
        self.session.flush()
        provider_enabled = self._provider_enabled_statuses()
        results = [
            self._sync_provider(
                provider_key,
                env_var,
                provider_enabled.get(provider_key),
                prefer_vault=prefer_vault,
            )
            for provider_key, env_var in self.PROVIDER_ENV_MAP.items()
        ]
        return tuple(results)

    def _provider_enabled_statuses(self) -> dict[str, bool]:
        rows = self.session.execute(
            text(
                """
                SELECT key, is_enabled
                FROM providers
                WHERE key IN ('anthropic-cloud', 'openai-cloud', 'gemini-cloud')
                """
            )
        )
        return {str(key): bool(is_enabled) for key, is_enabled in rows}

    def _sync_provider(
        self,
        provider_key: str,
        env_var: str,
        provider_enabled: bool | None,
        prefer_vault: bool,
    ) -> ProviderRuntimeSecretSyncResult:
        if provider_enabled is False:
            os.environ.pop(env_var, None)
            return ProviderRuntimeSecretSyncResult(
                provider_key=provider_key,
                env_var=env_var,
                status="cleared",
                reason="Provider row is disabled in runtime configuration.",
            )

        existing_secret = os.getenv(env_var)
        if existing_secret and not prefer_vault:
            if not self._secret_matches_provider(provider_key, existing_secret):
                os.environ.pop(env_var, None)
                return ProviderRuntimeSecretSyncResult(
                    provider_key=provider_key,
                    env_var=env_var,
                    status="cleared",
                    reason="Existing runtime environment API key does not match this provider family.",
                )
            return ProviderRuntimeSecretSyncResult(
                provider_key=provider_key,
                env_var=env_var,
                status="kept",
                reason="Existing runtime environment API key is configured.",
            )

        account_secret = self._load_account_secret_candidate(provider_key)
        if account_secret is None:
            if os.getenv(env_var):
                return ProviderRuntimeSecretSyncResult(
                    provider_key=provider_key,
                    env_var=env_var,
                    status="kept",
                    reason="Existing runtime environment API key is configured.",
                )
            return ProviderRuntimeSecretSyncResult(
                provider_key=provider_key,
                env_var=env_var,
                status="missing",
                reason="No configured vault secret is available for this provider.",
            )

        secret_value = self._resolve_secret(account_secret)
        if not secret_value:
            return ProviderRuntimeSecretSyncResult(
                provider_key=provider_key,
                env_var=env_var,
                status="missing",
                reason="Configured account exists, but the vault secret could not be resolved.",
            )
        if not self._secret_matches_provider(provider_key, secret_value):
            os.environ.pop(env_var, None)
            return ProviderRuntimeSecretSyncResult(
                provider_key=provider_key,
                env_var=env_var,
                status="cleared",
                reason="Configured vault secret does not match this provider family.",
            )

        os.environ[env_var] = secret_value
        return ProviderRuntimeSecretSyncResult(
            provider_key=provider_key,
            env_var=env_var,
            status="synced",
            reason="Configured vault secret was mirrored into runtime environment.",
        )

    def _load_account_secret_candidate(self, provider_key: str) -> str | None:
        row = self.session.execute(
            text(
                """
                SELECT secret_ref, storage_backend, status
                FROM account_credentials
                WHERE provider_key = :provider_key
                  AND account_identifier = :account_identifier
                LIMIT 1
                """
            ),
            {
                "provider_key": provider_key,
                "account_identifier": DEFAULT_PROVIDER_CARD_IDENTIFIER,
            },
        ).one_or_none()
        if row is None:
            return None

        secret_ref, storage_backend, status = row
        if status != "configured" or not secret_ref or storage_backend == METADATA_ONLY_STORAGE_BACKEND:
            return None
        return str(secret_ref)

    def _resolve_secret(self, secret_ref: str) -> str | None:
        from atnos.services.secret_vault_service import SecretVaultService

        try:
            return SecretVaultService(self.settings).resolve_secret(secret_ref)
        except Exception:
            return None

    @staticmethod
    def _secret_matches_provider(provider_key: str, secret_value: str) -> bool:
        normalized = secret_value.strip()
        if provider_key == "openai-cloud":
            return not normalized.startswith("sk-ant-")
        if provider_key == "anthropic-cloud":
            return not (normalized.startswith("sk-") and not normalized.startswith("sk-ant-"))
        return True
