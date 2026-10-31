from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.account_credential import AccountCredential
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.intelligence.storage import update_intelligence_youtube_oauth_account
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.automation_control_service import AutomationControlService
from atnos.services.oauth_provider_registry import (
    OAuthProviderDescriptor,
    OAuthProviderRegistry,
    oauth_client_fingerprint,
)
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore
from atnos.services.secret_vault_service import SecretVaultResolutionError, SecretVaultService
from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS


@dataclass(frozen=True, slots=True)
class OAuthConnectorView:
    connector_key: str
    display_name: str
    status: str
    scopes: tuple[str, ...]
    supports_pkce: bool
    enabled: bool
    configured: bool
    setup_warnings: tuple[str, ...]
    connection_id: int | None
    connected_at: datetime | None
    disconnected_at: datetime | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class OAuthConnectionView:
    id: int
    connector_key: str
    display_name: str
    status: str
    scopes: tuple[str, ...]
    account_id: int | None
    connected_at: datetime | None
    disconnected_at: datetime | None
    last_error: str | None
    metadata: dict[str, object]
    audit_log: tuple[dict[str, object], ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthStartResult:
    authorization_url: str
    connector_key: str
    status: str
    scopes: tuple[str, ...]
    connection_id: int
    content_account_id: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthCallbackResult:
    connection: OAuthConnectionView
    connector_key: str
    status: str
    account_id: int | None


class OAuthService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = RuntimeSettingsService(session, settings).build_effective_settings()
        self.registry = OAuthProviderRegistry(self.settings)
        self.connections = OAuthConnectionRepository(session)
        self.accounts = AccountCredentialRepository(session)
        self.token_store = OAuthTokenStore(settings)
        self.secret_vault = SecretVaultService(settings)
        self.automation = AutomationControlService(session)

    def list_connectors(self) -> list[OAuthConnectorView]:
        self._expire_stale_pending_connections()
        views: list[OAuthConnectorView] = []
        for descriptor in self.registry.list_connectors():
            connection = self.connections.get_latest_for_connector(descriptor.connector_key)
            effectively_configured = descriptor.is_configured or (
                descriptor.mode == "fake_provider" and descriptor.enabled
            )
            if connection is None:
                status = "available" if effectively_configured else "not_configured"
            else:
                status = connection.status if effectively_configured else "not_configured"
            views.append(
                OAuthConnectorView(
                    connector_key=descriptor.connector_key,
                    display_name=descriptor.display_name,
                    status=status,
                    scopes=descriptor.scopes,
                    supports_pkce=descriptor.supports_pkce,
                    enabled=descriptor.enabled,
                    configured=effectively_configured,
                    setup_warnings=descriptor.setup_warnings,
                    connection_id=connection.id if connection is not None else None,
                    connected_at=connection.connected_at if connection is not None else None,
                    disconnected_at=connection.disconnected_at if connection is not None else None,
                    last_error=self._connection_public_error(connection),
                )
            )
        return views

    def list_connections(self) -> list[OAuthConnectionView]:
        self._expire_stale_pending_connections()
        return [self._build_connection_view(item) for item in self.connections.list_all()]

    def _expire_stale_pending_connections(self) -> None:
        cutoff = self._utcnow() - self.connections.PENDING_FLOW_TTL
        for pending in self.connections.list_all():
            if pending.status != "pending":
                continue
            updated_at = pending.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at >= cutoff:
                continue
            self.token_store.delete_code_verifier(pending.code_verifier_ref)
            pending.code_verifier_ref = None
            pending.state_hash = None
            pending.status = "expired"
            pending.last_error = "Sesja OAuth wygasla. Rozpocznij polaczenie ponownie."
            metadata = dict(pending.metadata_json or {})
            metadata["oauth_pending_expired_at"] = self._utcnow().isoformat()
            pending.metadata_json = metadata
            self._append_audit_event(
                pending,
                "oauth_pending_expired",
                "The pending OAuth session expired before user completion.",
            )
            self.connections.save(pending)

    def start_connection(
        self,
        connector_key: str,
        *,
        content_account_id: str | None = None,
    ) -> OAuthStartResult:
        descriptor = self._get_descriptor(connector_key)
        self._ensure_start_allowed(descriptor)
        target_content_account_id = self._normalize_content_account_target(
            descriptor=descriptor,
            content_account_id=content_account_id,
        )
        self._expire_stale_pending_connections()
        existing = self._reusable_pending_connection(
            descriptor=descriptor,
            content_account_id=target_content_account_id,
        )
        if existing is not None:
            metadata = dict(existing.metadata_json or {})
            authorization_url = str(metadata.get("authorization_url") or "")
            return OAuthStartResult(
                authorization_url=authorization_url,
                connector_key=descriptor.connector_key,
                status="pending",
                scopes=descriptor.scopes,
                connection_id=existing.id,
                content_account_id=target_content_account_id,
            )

        raw_state = secrets.token_urlsafe(32)
        state_hash = self._sha256(raw_state)
        code_verifier = self._generate_code_verifier()
        code_challenge = self._pkce_code_challenge(code_verifier)
        code_verifier_ref = self.token_store.store_code_verifier(code_verifier)

        connection = self.connections.create(
            connector_key=descriptor.connector_key,
            display_name=descriptor.display_name,
            status="pending",
            scopes_json=list(descriptor.scopes),
            state_hash=state_hash,
            code_verifier_ref=code_verifier_ref,
            token_ref=None,
            account_id=None,
            connected_at=None,
            disconnected_at=None,
            last_error=None,
            metadata_json={
                "mode": descriptor.mode,
                "integration_key": descriptor.integration_key,
                "supports_pkce": descriptor.supports_pkce,
                "oauth_client_fingerprint": oauth_client_fingerprint(descriptor.client_id),
                "flow_state": "STARTING_AUTH",
                "expires_at": (self._utcnow() + self.connections.PENDING_FLOW_TTL).isoformat(),
                **({"content_account_id": target_content_account_id} if target_content_account_id else {}),
            },
            audit_log_json=[],
        )
        self._append_audit_event(connection, "oauth_connection_started", "OAuth connection flow started.")
        self.connections.save(connection)

        authorization_url = self._build_authorization_url(
            descriptor=descriptor,
            state=raw_state,
            code_challenge=code_challenge,
            connection_id=connection.id,
        )
        metadata = dict(connection.metadata_json or {})
        metadata["authorization_url"] = authorization_url
        metadata["flow_state"] = "AWAITING_USER_AUTHORIZATION"
        connection.metadata_json = metadata
        self.connections.save(connection)
        self._expire_superseded_pending_connections(
            descriptor=descriptor,
            content_account_id=target_content_account_id,
            keep_id=connection.id,
        )
        return OAuthStartResult(
            authorization_url=authorization_url,
            connector_key=descriptor.connector_key,
            status="pending",
            scopes=descriptor.scopes,
            connection_id=connection.id,
            content_account_id=target_content_account_id,
        )

    def _reusable_pending_connection(
        self,
        *,
        descriptor: OAuthProviderDescriptor,
        content_account_id: str | None,
    ) -> OAuthConnection | None:
        cutoff = self._utcnow() - self.connections.PENDING_FLOW_TTL
        for pending in self.connections.list_pending_for_connector(descriptor.connector_key):
            metadata = dict(pending.metadata_json or {})
            if _optional_text(metadata.get("content_account_id")) != content_account_id:
                continue
            updated_at = pending.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at < cutoff:
                continue
            if not str(metadata.get("authorization_url") or "").strip():
                continue
            return pending
        return None

    def _expire_superseded_pending_connections(
        self,
        *,
        descriptor: OAuthProviderDescriptor,
        content_account_id: str | None,
        keep_id: int | None = None,
    ) -> None:
        """Invalidate older consent windows for the same connector target."""
        for pending in self.connections.list_pending_for_connector(descriptor.connector_key):
            metadata = dict(pending.metadata_json or {})
            pending_target = _optional_text(metadata.get("content_account_id"))
            if pending_target != content_account_id or pending.id == keep_id:
                continue
            self.token_store.delete_code_verifier(pending.code_verifier_ref)
            pending.code_verifier_ref = None
            pending.state_hash = None
            pending.status = "error"
            pending.last_error = "Ta sesja OAuth zostala zastapiona przez nowsza probe polaczenia."
            metadata["oauth_superseded_at"] = self._utcnow().isoformat()
            pending.metadata_json = metadata
            self._append_audit_event(
                pending,
                "oauth_connection_superseded",
                "A newer OAuth connection flow replaced this pending session.",
            )
            self.connections.save(pending)

    def complete_callback(
        self,
        connector_key: str,
        *,
        code: str | None,
        state: str | None,
        error: str | None,
    ) -> OAuthCallbackResult:
        descriptor = self._get_descriptor(connector_key)
        if state is None or not state.strip():
            raise ValueError("OAuth callback is missing state.")

        state_hash = self._sha256(state)
        connection = self.connections.get_pending_by_state_hash(
            connector_key=descriptor.connector_key,
            state_hash=state_hash,
        )
        if connection is not None:
            updated_at = connection.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at < self._utcnow() - self.connections.PENDING_FLOW_TTL:
                connection = None
        if connection is None:
            rejected = self.connections.get_latest_for_connector(descriptor.connector_key)
            if rejected is not None:
                self._append_audit_event(rejected, "oauth_state_rejected", "OAuth callback state was rejected.")
                self.connections.save(rejected)
            raise ValueError("OAuth callback state is invalid.")

        if error:
            cancelled = error.strip().lower() in {"access_denied", "cancelled", "canceled"}
            connection.status = "expired" if cancelled else "error"
            connection.last_error = (
                "Autoryzacja zostala anulowana. Polaczenie nie zostalo zmienione."
                if cancelled
                else self._sanitize_public_error(error) or "OAuth provider returned an error."
            )
            connection.state_hash = None
            self.token_store.delete_code_verifier(connection.code_verifier_ref)
            connection.code_verifier_ref = None
            self._append_audit_event(connection, "oauth_connection_failed", "OAuth provider returned an error.")
            self.connections.save(connection)
            return OAuthCallbackResult(
                connection=self._build_connection_view(connection),
                connector_key=descriptor.connector_key,
                status=connection.status,
                account_id=connection.account_id,
            )

        if code is None or not code.strip():
            raise ValueError("OAuth callback is missing code.")

        try:
            code_verifier = self.token_store.load_code_verifier(connection.code_verifier_ref)
        except SecretVaultResolutionError:
            code_verifier = None
        if not code_verifier:
            connection.status = "error"
            connection.last_error = "OAuth code verifier is unavailable or expired. Start OAuth again."
            connection.state_hash = None
            connection.code_verifier_ref = None
            self._append_audit_event(connection, "oauth_connection_failed", "OAuth code verifier was unavailable.")
            self.connections.save(connection)
            return OAuthCallbackResult(
                connection=self._build_connection_view(connection),
                connector_key=descriptor.connector_key,
                status=connection.status,
                account_id=connection.account_id,
            )

        code_verifier_ref = connection.code_verifier_ref
        try:
            token_bundle = self._exchange_code_for_token(
                descriptor=descriptor,
                code=code.strip(),
                code_verifier=code_verifier,
            )
        except Exception as exc:
            safe_error = self._sanitize_public_error(str(exc)) or "OAuth token exchange failed."
            connection.status = "error"
            connection.last_error = safe_error
            connection.state_hash = None
            self.token_store.delete_code_verifier(code_verifier_ref)
            connection.code_verifier_ref = None
            self._append_audit_event(connection, "oauth_connection_failed", safe_error)
            self.connections.save(connection)
            return OAuthCallbackResult(
                connection=self._build_connection_view(connection),
                connector_key=descriptor.connector_key,
                status=connection.status,
                account_id=connection.account_id,
            )
        token_ref = self.token_store.store_token_bundle(token_bundle)
        self._append_audit_event(connection, "oauth_token_stored", "OAuth token bundle stored by reference.")
        token_expires_at = None
        if token_bundle.expires_in is not None:
            token_expires_at = (
                self._utcnow() + timedelta(seconds=max(token_bundle.expires_in - 60, 0))
            ).isoformat()

        account = self._ensure_account_metadata(connection, descriptor)
        connection.token_ref = token_ref
        connection.code_verifier_ref = None
        connection.state_hash = None
        connection.account_id = account.id
        connection.status = "connected"
        connection.connected_at = self._utcnow()
        connection.disconnected_at = None
        connection.last_error = None
        connection.metadata_json = {
            **dict(connection.metadata_json or {}),
            "integration_key": descriptor.integration_key,
            "account_provider_key": account.provider_key,
            "oauth_client_fingerprint": oauth_client_fingerprint(descriptor.client_id),
            "token_expires_at": token_expires_at,
            "oauth_last_refreshed_at": self._utcnow().isoformat(),
        }
        self.token_store.delete_code_verifier(code_verifier_ref)
        self._append_audit_event(connection, "oauth_connection_completed", "OAuth connection completed.")
        self.connections.save(connection)
        self._sync_targeted_youtube_account(connection, descriptor=descriptor, account_id=account.id)
        self.automation.upsert_oauth_connected_control(
            account,
            connector_key=descriptor.connector_key,
            integration_key=descriptor.integration_key,
        )
        return OAuthCallbackResult(
            connection=self._build_connection_view(connection),
            connector_key=descriptor.connector_key,
            status=connection.status,
            account_id=account.id,
        )

    def disconnect(self, connection_id: int) -> OAuthConnectionView | None:
        connection = self.connections.get_by_id(connection_id)
        if connection is None:
            return None

        self.token_store.delete_token_bundle(connection.token_ref)
        if connection.token_ref:
            self._append_audit_event(connection, "oauth_token_deleted", "OAuth token bundle reference removed.")
        connection.token_ref = None
        connection.code_verifier_ref = None
        connection.state_hash = None
        connection.status = "disconnected"
        connection.disconnected_at = self._utcnow()
        connection.last_error = None
        self._append_audit_event(connection, "oauth_connection_disconnected", "OAuth connection disconnected.")
        self.connections.save(connection)

        if connection.account_id is not None:
            self.automation.mark_oauth_disconnected(
                account_id=connection.account_id,
                connector_key=connection.connector_key,
            )

        return self._build_connection_view(connection)

    def _ensure_start_allowed(self, descriptor: OAuthProviderDescriptor) -> None:
        if not descriptor.enabled:
            raise ValueError("OAuth is disabled in this runtime.")
        if not descriptor.is_configured and descriptor.mode != "fake_provider":
            raise ValueError("OAuth provider is not configured.")

    @staticmethod
    def _normalize_content_account_target(
        *,
        descriptor: OAuthProviderDescriptor,
        content_account_id: str | None,
    ) -> str | None:
        normalized = str(content_account_id or "").strip()
        if not normalized:
            return None
        if descriptor.integration_key != "youtube" or not normalized.casefold().startswith("youtube:"):
            raise ValueError("A content account target is supported only for a YouTube OAuth connection.")
        return normalized

    def _sync_targeted_youtube_account(
        self,
        connection: OAuthConnection,
        *,
        descriptor: OAuthProviderDescriptor,
        account_id: int,
    ) -> None:
        """Aktualizuje mapowanie kanału po callbacku bez zgadywania konta Google."""
        if descriptor.integration_key != "youtube":
            return
        target = str((connection.metadata_json or {}).get("content_account_id") or "").strip()
        if not target:
            return
        if update_intelligence_youtube_oauth_account(
            self.settings,
            account_id=target,
            youtube_oauth_account_id=account_id,
        ):
            self._append_audit_event(
                connection,
                "oauth_content_account_mapped",
                "OAuth connection mapped to the requested content account.",
            )
            self.connections.save(connection)

    def _get_descriptor(self, connector_key: str) -> OAuthProviderDescriptor:
        try:
            return self.registry.get_connector(connector_key)
        except KeyError as exc:
            raise ValueError(f"Unknown connector '{connector_key}'.") from exc

    def _build_authorization_url(
        self,
        *,
        descriptor: OAuthProviderDescriptor,
        state: str,
        code_challenge: str,
        connection_id: int,
    ) -> str:
        if descriptor.mode == "fake_provider":
            query = urlencode(
                {
                    "code": f"fake-code-{connection_id}",
                    "state": state,
                }
            )
            return f"{descriptor.redirect_uri}?{query}"

        query = {
            "client_id": descriptor.client_id or "",
            "redirect_uri": descriptor.redirect_uri,
            "response_type": "code",
            "scope": " ".join(descriptor.scopes),
            "state": state,
        }
        if descriptor.token_url == "https://oauth2.googleapis.com/token":
            query["access_type"] = "offline"
            query["include_granted_scopes"] = "true"
            query["prompt"] = "consent"
        if descriptor.supports_pkce:
            query["code_challenge"] = code_challenge
            query["code_challenge_method"] = "S256"
        return f"{descriptor.authorization_url}?{urlencode(query)}"

    def _exchange_code_for_token(
        self,
        *,
        descriptor: OAuthProviderDescriptor,
        code: str,
        code_verifier: str,
    ) -> OAuthTokenBundle:
        if descriptor.mode == "fake_provider":
            return OAuthTokenBundle(
                access_token=f"fake-access-{descriptor.connector_key}",
                refresh_token=f"fake-refresh-{descriptor.connector_key}",
                token_type="Bearer",
                expires_in=3600,
                scope=" ".join(descriptor.scopes),
                id_token=None,
            )

        client_secret = None
        if descriptor.client_secret_ref:
            client_secret = self.secret_vault.resolve_secret(descriptor.client_secret_ref)

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": descriptor.redirect_uri,
            "client_id": descriptor.client_id or "",
            "code_verifier": code_verifier,
        }
        if client_secret:
            payload["client_secret"] = client_secret

        headers = {"Accept": "application/json"}
        response = httpx.post(descriptor.token_url, data=payload, headers=headers, timeout=30)
        if response.is_error:
            raise RuntimeError(self._safe_token_exchange_error(response))
        data = response.json()
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("OAuth token exchange returned no access token.")
        return OAuthTokenBundle(
            access_token=access_token,
            refresh_token=_optional_text(data.get("refresh_token")),
            token_type=_optional_text(data.get("token_type")),
            expires_in=_optional_int(data.get("expires_in")),
            scope=_optional_text(data.get("scope")),
            id_token=_optional_text(data.get("id_token")),
        )

    @staticmethod
    def _safe_token_exchange_error(response: httpx.Response) -> str:
        """Zwraca bezpieczną diagnozę OAuth bez kopiowania odpowiedzi dostawcy."""

        error_code = "unknown_error"
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            candidate = str(payload.get("error") or "").strip().lower()
            if candidate in {
                "access_denied",
                "invalid_client",
                "invalid_grant",
                "invalid_request",
                "redirect_uri_mismatch",
                "temporarily_unavailable",
                "unauthorized_client",
                "unsupported_grant_type",
            }:
                error_code = candidate

        actions = {
            "invalid_client": "Re-import the matching Google OAuth client JSON.",
            "invalid_grant": "Start OAuth again and complete the consent flow once.",
            "redirect_uri_mismatch": "Add the exact Atnos callback URL to the Google OAuth client.",
            "access_denied": "Grant the requested scopes in the Google consent screen.",
            "temporarily_unavailable": "Retry after Google OAuth becomes available.",
        }
        action = actions.get(error_code, "Check the Google OAuth client configuration and retry.")
        # Keep the public diagnostic free of secret-bearing field names.  The
        # generic metadata sanitizer intentionally blocks the word "token",
        # so using it here would hide this already allow-listed error code.
        return f"OAuth exchange failed ({error_code}, HTTP {response.status_code}). {action}"

    def _ensure_account_metadata(
        self,
        connection: OAuthConnection,
        descriptor: OAuthProviderDescriptor,
    ) -> AccountCredential:
        if connection.account_id is not None:
            existing = self.accounts.get_by_id(connection.account_id)
            if existing is not None:
                return existing

        account_identifier = f"oauth:{descriptor.connector_key}:{connection.id}"
        existing = self.accounts.find_by_provider_and_identifier(
            provider_key=descriptor.integration_key,
            account_identifier=account_identifier,
        )
        if existing is not None:
            return existing

        allowed_capabilities = ["read_metadata"]
        status = "metadata_only"
        notes = "OAuth connection reference stored by Atnos."
        if descriptor.integration_key == "youtube":
            allowed_capabilities = [
                "read_metadata",
                "read_content",
                "draft_write",
                "publish_later",
                "confirmed_write_later",
                "connector_write",
                "oauth_session",
            ]
            status = "configured"
            notes = "Live YouTube Studio OAuth connector managed by Atnos."
        elif descriptor.integration_key == "github":
            if "repo" in descriptor.scopes:
                allowed_capabilities = [
                    "read_metadata",
                    "read_content",
                    "draft_write",
                    "confirmed_write_later",
                    "connector_write",
                    "oauth_session",
                ]
            else:
                allowed_capabilities = ["read_metadata", "read_content", "oauth_session"]
            status = "configured"
            notes = "Live GitHub OAuth connector managed by Atnos."
        elif descriptor.integration_key == "google-calendar":
            if any(scope == "https://www.googleapis.com/auth/calendar" for scope in descriptor.scopes):
                allowed_capabilities = [
                    "read_metadata",
                    "read_content",
                    "draft_write",
                    "confirmed_write_later",
                    "connector_write",
                    "oauth_session",
                ]
            else:
                allowed_capabilities = ["read_metadata", "read_content", "oauth_session"]
            status = "configured"
            notes = "Live Google Calendar OAuth connector managed by Atnos."
        elif descriptor.integration_key == "gmail":
            allowed_capabilities = [
                "read_metadata",
                "read_content",
                "draft_write",
                "confirmed_write_later",
                "connector_write",
                "oauth_session",
            ]
            status = "configured"
            notes = "Live Gmail OAuth connector managed by Atnos."
        elif descriptor.integration_key.startswith("google"):
            allowed_capabilities = ["read_metadata", "read_content", "oauth_session"]
            status = "configured"
            notes = "Live Google OAuth connector managed by Atnos."

        return self.accounts.create(
            label=descriptor.display_name,
            provider_key=descriptor.integration_key,
            account_identifier=account_identifier,
            secret_type="oauth_token_later",
            storage_backend="metadata_only",
            secret_ref=None,
            status=status,
            risk_level="medium",
            allowed_capabilities_json=allowed_capabilities,
            linked_integration_key=descriptor.integration_key,
            linked_project_id=None,
            notes=notes,
            last_rotated_at=None,
            last_used_at=None,
        )

    def _build_connection_view(self, connection: OAuthConnection) -> OAuthConnectionView:
        return OAuthConnectionView(
            id=connection.id,
            connector_key=connection.connector_key,
            display_name=connection.display_name,
            status=connection.status,
            scopes=tuple(connection.scopes_json or []),
            account_id=connection.account_id,
            connected_at=connection.connected_at,
            disconnected_at=connection.disconnected_at,
            last_error=self._connection_public_error(connection),
            metadata=self._sanitize_metadata(connection.metadata_json or {}),
            audit_log=tuple(
                entry
                for entry in (
                    self._sanitize_audit_entry(item)
                    for item in (connection.audit_log_json or [])
                )
                if entry is not None
            ),
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )

    def _append_audit_event(self, connection: OAuthConnection, event: str, message: str) -> None:
        safe_message = self._sanitize_public_error(message)
        if not safe_message:
            return
        audit_log = list(connection.audit_log_json or [])
        audit_log.append(
            {
                "event": event,
                "message": safe_message,
                "at": self._utcnow().isoformat(),
            }
        )
        connection.audit_log_json = audit_log[-20:]

    def _sanitize_public_error(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            return None
        lowered = normalized.lower()
        if any(token in lowered for token in FORBIDDEN_KEY_TOKENS):
            return "Sensitive OAuth data was redacted."
        if any(
            token in lowered
            for token in (
                "access_token",
                "refresh_token",
                "id_token",
                "client_secret",
                "code_verifier",
                "code_verifier_ref",
                "token_ref",
                "state_hash",
                "secret_ref",
                "authorization code",
                "auth_code",
                "bearer ",
                "code=",
                "state=",
            )
        ):
            return "Sensitive OAuth data was redacted."
        if len(normalized) > 240:
            return normalized[:237].rstrip() + "..."
        return normalized

    def _connection_public_error(self, connection: OAuthConnection | None) -> str | None:
        if connection is None:
            return None
        sanitized = self._sanitize_public_error(connection.last_error)
        metadata = dict(connection.metadata_json or {})
        if sanitized != "Sensitive OAuth data was redacted.":
            return sanitized
        if not metadata.get("oauth_reconnect_required"):
            return sanitized
        failure_code = _optional_text(metadata.get("oauth_refresh_failure_code"))
        if failure_code == "invalid_grant":
            return "Autoryzacja Google wygasla lub zostala uniewazniona. Polacz konto ponownie."
        if failure_code in {"invalid_client", "oauth_client_changed"}:
            return "Konfiguracja klienta Google OAuth zostala zmieniona. Polacz konto ponownie."
        return "Polaczenie Google OAuth wymaga ponownej autoryzacji."

    def _sanitize_metadata(self, metadata: dict[str, object]) -> dict[str, object]:
        safe: dict[str, object] = {}
        for key, value in metadata.items():
            lowered = key.lower()
            if any(token in lowered for token in ("token", "secret", "code", "ref")):
                continue
            if isinstance(value, str):
                sanitized = self._sanitize_public_error(value)
                if sanitized is not None:
                    safe[key] = sanitized
                continue
            if isinstance(value, (bool, int, float)) or value is None:
                safe[key] = value
                continue
            if isinstance(value, list):
                sanitized_items: list[str | int | float | bool] = []
                for item in value:
                    if isinstance(item, str):
                        sanitized = self._sanitize_public_error(item)
                        if sanitized is not None:
                            sanitized_items.append(sanitized)
                    elif isinstance(item, (int, float, bool)):
                        sanitized_items.append(item)
                safe[key] = sanitized_items
        return safe

    def _sanitize_audit_entry(self, entry: object) -> dict[str, object] | None:
        if not isinstance(entry, dict):
            return None

        event = entry.get("event")
        if not isinstance(event, str) or not event.strip():
            return None

        safe: dict[str, object] = {"event": event.strip()}
        for field in ("at", "message", "status"):
            value = entry.get(field)
            if not isinstance(value, str):
                continue
            sanitized = self._sanitize_public_error(value)
            if sanitized is not None:
                safe[field] = sanitized

        scopes = entry.get("scopes")
        if isinstance(scopes, list):
            safe_scopes: list[str] = []
            for item in scopes:
                if not isinstance(item, str):
                    continue
                sanitized = self._sanitize_public_error(item)
                if sanitized is not None:
                    safe_scopes.append(sanitized)
            if safe_scopes:
                safe["scopes"] = safe_scopes

        try:
            serialized = json.dumps(safe, separators=(",", ":"))
        except TypeError:
            return None
        if self._sanitize_public_error(serialized) == "Sensitive OAuth data was redacted.":
            return None
        return safe

    @staticmethod
    def _generate_code_verifier() -> str:
        return secrets.token_urlsafe(64)

    @staticmethod
    def _pkce_code_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
