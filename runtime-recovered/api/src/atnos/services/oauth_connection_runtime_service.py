from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.oauth_provider_registry import (
    OAuthProviderDescriptor,
    OAuthProviderRegistry,
    oauth_client_fingerprint,
)
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore
from atnos.services.secret_vault_service import SecretVaultService

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REFRESH_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class OAuthConnectionHealth:
    ok: bool
    status: str
    message: str


class OAuthConnectionRuntimeService:
    """Shared runtime helper for OAuth-backed connectors.

    This keeps token loading and refresh policy in one place instead of
    duplicating it inside each connector service.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.registry = OAuthProviderRegistry(settings)
        self.connections = OAuthConnectionRepository(session)
        self.token_store = OAuthTokenStore(settings)
        self.secret_vault = SecretVaultService(settings)

    def load_token_bundle(
        self,
        connection: OAuthConnection,
        *,
        descriptor: OAuthProviderDescriptor | None = None,
        refresh_if_needed: bool = True,
        force_refresh: bool = False,
    ) -> OAuthTokenBundle | None:
        bundle = self.token_store.load_token_bundle(connection.token_ref)
        if bundle is None:
            return None
        descriptor = descriptor or self.registry.get_connector(connection.connector_key)
        if refresh_if_needed:
            return self._refresh_if_needed(
                connection=connection,
                descriptor=descriptor,
                bundle=bundle,
                force_refresh=force_refresh,
            )
        return bundle

    def refresh_if_needed(self, connection_id: int, *, force_refresh: bool = False) -> OAuthTokenBundle | None:
        connection = self.connections.get_by_id(connection_id)
        if connection is None or connection.status != "connected":
            return None
        descriptor = self.registry.get_connector(connection.connector_key)
        return self.load_token_bundle(
            connection,
            descriptor=descriptor,
            refresh_if_needed=True,
            force_refresh=force_refresh,
        )

    def health_check(self, connection_id: int) -> OAuthConnectionHealth:
        connection = self.connections.get_by_id(connection_id)
        if connection is None:
            return OAuthConnectionHealth(ok=False, status="missing", message="OAuth connection was not found.")
        if connection.status != "connected":
            metadata = connection.metadata_json or {}
            if connection.status == "error":
                reconnect_required = bool(metadata.get("oauth_reconnect_required"))
                return OAuthConnectionHealth(
                    ok=False,
                    status="reconnect_required" if reconnect_required else "error",
                    message=connection.last_error or "OAuth connection failed and must be reviewed.",
                )
            if connection.status == "pending":
                return OAuthConnectionHealth(
                    ok=False,
                    status="pending",
                    message="OAuth authorization is pending user completion.",
                )
            if connection.status == "disconnected":
                return OAuthConnectionHealth(
                    ok=False,
                    status="disconnected",
                    message="OAuth connection was disconnected.",
                )
            return OAuthConnectionHealth(
                ok=False,
                status="not_connected",
                message="OAuth connection is not currently connected.",
            )
        try:
            bundle = self.load_token_bundle(connection, refresh_if_needed=True)
        except Exception as exc:
            return OAuthConnectionHealth(ok=False, status="error", message=str(exc))
        if bundle is None or not bundle.access_token:
            return OAuthConnectionHealth(ok=False, status="missing_token", message="Stored OAuth token is unavailable.")
        return OAuthConnectionHealth(ok=True, status="ok", message="OAuth connection is ready.")

    def _refresh_if_needed(
        self,
        *,
        connection: OAuthConnection,
        descriptor: OAuthProviderDescriptor,
        bundle: OAuthTokenBundle,
        force_refresh: bool,
    ) -> OAuthTokenBundle:
        if descriptor.mode == "fake_provider":
            return bundle
        stored_fingerprint = _optional_text((connection.metadata_json or {}).get("oauth_client_fingerprint"))
        current_fingerprint = oauth_client_fingerprint(descriptor.client_id)
        if stored_fingerprint and current_fingerprint and stored_fingerprint != current_fingerprint:
            error = RuntimeError("oauth_client_changed")
            self._mark_reconnect_required(connection, error=error)
            raise error
        if descriptor.token_url != _GOOGLE_TOKEN_URL:
            return bundle
        if not bundle.refresh_token:
            return bundle
        expires_at = self._parse_expires_at(connection.metadata_json or {})
        should_refresh = force_refresh
        if not should_refresh and expires_at is not None:
            should_refresh = expires_at <= datetime.now(timezone.utc) + timedelta(seconds=_REFRESH_SKEW_SECONDS)
        if not should_refresh:
            return bundle
        try:
            refreshed = self._refresh_google_bundle(descriptor=descriptor, bundle=bundle)
        except Exception as exc:
            if _requires_oauth_reconnect(exc):
                self._mark_reconnect_required(connection, error=exc)
            raise
        connection.token_ref = self.token_store.rotate_token_bundle(
            bundle=refreshed,
            existing_token_ref=connection.token_ref,
        )
        metadata = dict(connection.metadata_json or {})
        if refreshed.expires_in is not None:
            metadata["token_expires_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=max(refreshed.expires_in - _REFRESH_SKEW_SECONDS, 0))
            ).isoformat()
        metadata["oauth_last_refreshed_at"] = datetime.now(timezone.utc).isoformat()
        metadata["oauth_client_fingerprint"] = current_fingerprint
        connection.metadata_json = metadata
        connection.last_error = None
        self.connections.save(connection)
        self.session.flush()
        return refreshed

    def _mark_reconnect_required(self, connection: OAuthConnection, *, error: Exception) -> None:
        """Zatrzymaj petle odswiezania niewaznego tokenu i pokaz konkretny krok w UI."""
        failure_code = _oauth_failure_code(error)
        metadata = dict(connection.metadata_json or {})
        metadata.update(
            {
                "oauth_reconnect_required": True,
                "oauth_refresh_failure_code": failure_code,
                "oauth_refresh_failed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        connection.metadata_json = metadata
        connection.status = "error"
        connection.last_error = _public_reconnect_message(failure_code)
        audit_log = list(connection.audit_log_json or [])
        audit_log.append(
            {
                "event": "oauth_refresh_reconnect_required",
                "at": datetime.now(timezone.utc).isoformat(),
                "message": "Stored OAuth authorization can no longer be refreshed. Reconnect is required.",
            }
        )
        connection.audit_log_json = audit_log[-20:]
        self.connections.save(connection)
        self.session.flush()

    def _refresh_google_bundle(
        self,
        *,
        descriptor: OAuthProviderDescriptor,
        bundle: OAuthTokenBundle,
    ) -> OAuthTokenBundle:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": bundle.refresh_token,
            "client_id": descriptor.client_id or "",
        }
        client_secret = self._resolve_client_secret(descriptor)
        if client_secret:
            payload["client_secret"] = client_secret
        response = httpx.post(descriptor.token_url, data=payload, headers={"Accept": "application/json"}, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(_safe_google_oauth_error(response))
        response.raise_for_status()
        data = response.json()
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("OAuth refresh returned no access token.")
        return OAuthTokenBundle(
            access_token=access_token,
            refresh_token=str(data.get("refresh_token") or bundle.refresh_token or "").strip() or bundle.refresh_token,
            token_type=_optional_text(data.get("token_type")) or bundle.token_type,
            expires_in=_optional_int(data.get("expires_in")),
            scope=_optional_text(data.get("scope")) or bundle.scope,
            id_token=_optional_text(data.get("id_token")) or bundle.id_token,
        )

    def _resolve_client_secret(self, descriptor: OAuthProviderDescriptor) -> str | None:
        if not descriptor.client_secret_ref:
            return None
        return self.secret_vault.resolve_secret(descriptor.client_secret_ref)

    @staticmethod
    def _parse_expires_at(metadata: dict[str, object]) -> datetime | None:
        raw = metadata.get("token_expires_at")
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _safe_google_oauth_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = str(payload.get("error") or "").strip()
    description = str(payload.get("error_description") or "").strip()
    if error:
        return f"{error}: {description}" if description else error
    return f"Google OAuth token refresh failed with HTTP {response.status_code}."


def _oauth_failure_code(error: Exception) -> str:
    """Znormalizuj tylko bezpieczne kody bledow zwracane przez Google OAuth."""
    text = " ".join(str(error).split()).casefold()
    if "invalid_grant" in text:
        return "invalid_grant"
    if "invalid_client" in text:
        return "invalid_client"
    if "oauth_client_changed" in text:
        return "oauth_client_changed"
    return "unknown"


def _requires_oauth_reconnect(error: Exception) -> bool:
    return _oauth_failure_code(error) in {"invalid_grant", "invalid_client", "oauth_client_changed"}


def _public_reconnect_message(failure_code: str) -> str:
    if failure_code == "oauth_client_changed":
        return "Klient OAuth zostal zmieniony. Polacz konto ponownie, aby zapisac trwala autoryzacje."
    if failure_code == "invalid_grant":
        return "Autoryzacja Google wygasła albo została unieważniona. Połącz konto ponownie."
    if failure_code == "invalid_client":
        return "Konfiguracja klienta Google OAuth została zmieniona. Sprawdź klienta OAuth i połącz konto ponownie."
    return "Autoryzacja Google OAuth wymaga ponownego połączenia."
