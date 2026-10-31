from __future__ import annotations

import json
from dataclasses import dataclass

from atnos.core.config import Settings
from atnos.services.secret_vault_service import SecretVaultService


@dataclass(frozen=True, slots=True)
class OAuthTokenBundle:
    access_token: str
    refresh_token: str | None
    token_type: str | None
    expires_in: int | None
    scope: str | None
    id_token: str | None = None


class OAuthTokenStore:
    def __init__(self, settings: Settings) -> None:
        self.vault = SecretVaultService(settings)

    def store_token_bundle(self, bundle: OAuthTokenBundle) -> str | None:
        serialized = _serialize_token_bundle(bundle)
        _backend, secret_ref = self.vault.store_secret(serialized)
        return secret_ref

    def rotate_token_bundle(
        self,
        *,
        bundle: OAuthTokenBundle,
        existing_token_ref: str | None,
    ) -> str | None:
        serialized = _serialize_token_bundle(bundle)
        _backend, secret_ref = self.vault.rotate_secret(
            secret_value=serialized,
            existing_secret_ref=existing_token_ref,
        )
        if existing_token_ref and secret_ref != existing_token_ref:
            self.vault.delete_secret(existing_token_ref)
        return secret_ref

    def load_token_bundle(self, token_ref: str | None) -> OAuthTokenBundle | None:
        if not token_ref:
            return None
        payload = json.loads(self.vault.resolve_secret(token_ref))
        return OAuthTokenBundle(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=_optional_text(payload.get("refresh_token")),
            token_type=_optional_text(payload.get("token_type")),
            expires_in=_optional_int(payload.get("expires_in")),
            scope=_optional_text(payload.get("scope")),
            id_token=_optional_text(payload.get("id_token")),
        )

    def delete_token_bundle(self, token_ref: str | None) -> None:
        self.vault.delete_secret(token_ref)

    def store_code_verifier(self, code_verifier: str) -> str | None:
        _backend, secret_ref = self.vault.store_secret(code_verifier)
        return secret_ref

    def load_code_verifier(self, code_verifier_ref: str | None) -> str | None:
        if not code_verifier_ref:
            return None
        return self.vault.resolve_secret(code_verifier_ref)

    def delete_code_verifier(self, code_verifier_ref: str | None) -> None:
        self.vault.delete_secret(code_verifier_ref)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _serialize_token_bundle(bundle: OAuthTokenBundle) -> str:
    return json.dumps(
        {
            "access_token": bundle.access_token,
            "refresh_token": bundle.refresh_token,
            "token_type": bundle.token_type,
            "expires_in": bundle.expires_in,
            "scope": bundle.scope,
            "id_token": bundle.id_token,
        },
        separators=(",", ":"),
    )
