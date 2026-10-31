from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.oauth_connection_runtime_service import OAuthConnectionRuntimeService
from atnos.services.oauth_provider_registry import OAuthProviderDescriptor, OAuthProviderRegistry
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore


@dataclass(frozen=True, slots=True)
class GitHubAccountMetadataView:
    login: str | None
    name: str | None
    profile_url: str | None


@dataclass(frozen=True, slots=True)
class GitHubConnectorStatusView:
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    connection_id: int | None
    account_id: int | None
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    warnings: tuple[str, ...]
    connected_at: datetime | None
    disconnected_at: datetime | None
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool


@dataclass(frozen=True, slots=True)
class GitHubConnectorProbeView:
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    probe_status: str
    connection_id: int | None
    account_id: int | None
    provider_reachable: bool
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    warnings: tuple[str, ...]
    account_metadata: GitHubAccountMetadataView | None
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    message: str


class GitHubConnectorProbeService:
    PRIMARY_CONNECTOR_KEY = "github"
    FALLBACK_CONNECTOR_KEY = "github-readonly"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.registry = OAuthProviderRegistry(settings)
        self.connections = OAuthConnectionRepository(session)
        self.token_store = OAuthTokenStore(settings)
        self.runtime = OAuthConnectionRuntimeService(session, settings)

    def get_status(self) -> GitHubConnectorStatusView:
        descriptor, connection = self._resolve_descriptor_and_connection()
        warnings = list(descriptor.setup_warnings)
        token_bundle = self._load_token_bundle_safe(connection, warnings)
        granted_scopes = self._resolve_granted_scopes(connection, descriptor, token_bundle)
        live_write_enabled = "repo" in granted_scopes
        return GitHubConnectorStatusView(
            connector_key=descriptor.connector_key,
            display_name=descriptor.display_name,
            integration_key=descriptor.integration_key,
            oauth_status=self._resolve_oauth_status(connection, descriptor),
            connection_id=connection.id if connection is not None else None,
            account_id=connection.account_id if connection is not None else None,
            provider_enabled=descriptor.enabled,
            provider_configured=self._is_effectively_configured(descriptor),
            token_present=bool(token_bundle and token_bundle.access_token),
            required_scopes=descriptor.scopes,
            granted_scopes=granted_scopes,
            warnings=tuple(dict.fromkeys(warnings)),
            connected_at=connection.connected_at if connection is not None else None,
            disconnected_at=connection.disconnected_at if connection is not None else None,
            checked_at=self._utcnow(),
            live_read_enabled=bool(token_bundle and token_bundle.access_token),
            live_write_enabled=bool(token_bundle and token_bundle.access_token and live_write_enabled),
        )

    def probe(self) -> GitHubConnectorProbeView:
        descriptor, connection = self._resolve_descriptor_and_connection()
        status_view = self.get_status()
        warnings = list(status_view.warnings)

        if not descriptor.enabled or not self._is_effectively_configured(descriptor):
            return GitHubConnectorProbeView(
                connector_key=status_view.connector_key,
                display_name=status_view.display_name,
                integration_key=status_view.integration_key,
                oauth_status=status_view.oauth_status,
                probe_status="not_configured",
                connection_id=status_view.connection_id,
                account_id=status_view.account_id,
                provider_reachable=False,
                provider_enabled=status_view.provider_enabled,
                provider_configured=status_view.provider_configured,
                token_present=status_view.token_present,
                required_scopes=status_view.required_scopes,
                granted_scopes=status_view.granted_scopes,
                warnings=tuple(dict.fromkeys(warnings)),
                account_metadata=None,
                checked_at=self._utcnow(),
                live_read_enabled=status_view.live_read_enabled,
                live_write_enabled=status_view.live_write_enabled,
                message="GitHub OAuth is not configured in this runtime.",
            )

        if connection is None or connection.status != "connected":
            return GitHubConnectorProbeView(
                connector_key=status_view.connector_key,
                display_name=status_view.display_name,
                integration_key=status_view.integration_key,
                oauth_status=status_view.oauth_status,
                probe_status="not_connected",
                connection_id=status_view.connection_id,
                account_id=status_view.account_id,
                provider_reachable=False,
                provider_enabled=status_view.provider_enabled,
                provider_configured=status_view.provider_configured,
                token_present=status_view.token_present,
                required_scopes=status_view.required_scopes,
                granted_scopes=status_view.granted_scopes,
                warnings=tuple(dict.fromkeys(warnings)),
                account_metadata=None,
                checked_at=self._utcnow(),
                live_read_enabled=status_view.live_read_enabled,
                live_write_enabled=status_view.live_write_enabled,
                message="Connect GitHub before running a read-only probe.",
            )

        token_bundle = self._load_token_bundle_safe(connection, warnings)
        if token_bundle is None or not token_bundle.access_token:
            self._append_audit_event(connection, "github_probe_failed", "GitHub read-only probe failed.")
            self.connections.save(connection)
            return GitHubConnectorProbeView(
                connector_key=status_view.connector_key,
                display_name=status_view.display_name,
                integration_key=status_view.integration_key,
                oauth_status=status_view.oauth_status,
                probe_status="error",
                connection_id=status_view.connection_id,
                account_id=status_view.account_id,
                provider_reachable=False,
                provider_enabled=status_view.provider_enabled,
                provider_configured=status_view.provider_configured,
                token_present=False,
                required_scopes=status_view.required_scopes,
                granted_scopes=status_view.granted_scopes,
                warnings=tuple(dict.fromkeys(warnings)),
                account_metadata=None,
                checked_at=self._utcnow(),
                live_read_enabled=status_view.live_read_enabled,
                live_write_enabled=status_view.live_write_enabled,
                message="Stored GitHub token reference is unavailable.",
            )

        probe_granted_scopes = status_view.granted_scopes
        try:
            metadata, granted_scopes = self._probe_provider(descriptor, token_bundle)
            probe_granted_scopes = granted_scopes or status_view.granted_scopes
        except ValueError as exc:
            warnings.append(str(exc))
            self._append_audit_event(connection, "github_probe_failed", "GitHub read-only probe failed.")
            self.connections.save(connection)
            return GitHubConnectorProbeView(
                connector_key=status_view.connector_key,
                display_name=status_view.display_name,
                integration_key=status_view.integration_key,
                oauth_status=status_view.oauth_status,
                probe_status="error",
                connection_id=status_view.connection_id,
                account_id=status_view.account_id,
                provider_reachable=False,
                provider_enabled=status_view.provider_enabled,
                provider_configured=status_view.provider_configured,
                token_present=True,
                required_scopes=status_view.required_scopes,
                granted_scopes=probe_granted_scopes,
                warnings=tuple(dict.fromkeys(warnings)),
                account_metadata=None,
                checked_at=self._utcnow(),
                live_read_enabled=status_view.live_read_enabled,
                live_write_enabled=status_view.live_write_enabled,
                message="GitHub provider probe failed.",
            )

        self._append_audit_event(connection, "github_probe_checked", "GitHub read-only probe completed.")
        self.connections.save(connection)
        return GitHubConnectorProbeView(
            connector_key=status_view.connector_key,
            display_name=status_view.display_name,
            integration_key=status_view.integration_key,
            oauth_status=status_view.oauth_status,
            probe_status="ok",
            connection_id=status_view.connection_id,
            account_id=status_view.account_id,
            provider_reachable=True,
            provider_enabled=status_view.provider_enabled,
            provider_configured=status_view.provider_configured,
            token_present=True,
            required_scopes=status_view.required_scopes,
            granted_scopes=probe_granted_scopes,
            warnings=tuple(dict.fromkeys(warnings)),
            account_metadata=metadata,
            checked_at=self._utcnow(),
            live_read_enabled=status_view.live_read_enabled,
            live_write_enabled=status_view.live_write_enabled,
            message="GitHub probe completed.",
        )

    def _probe_provider(
        self,
        descriptor: OAuthProviderDescriptor,
        token_bundle: OAuthTokenBundle,
    ) -> tuple[GitHubAccountMetadataView, tuple[str, ...]]:
        if descriptor.mode == "fake_provider":
            granted_scopes = tuple(scope for scope in (token_bundle.scope or "").split() if scope)
            if not granted_scopes:
                granted_scopes = descriptor.scopes
            return (
                GitHubAccountMetadataView(
                    login="github-user-later",
                    name="GitHub User Later",
                    profile_url="https://github.com/github-user-later",
                ),
                granted_scopes,
            )

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token_bundle.access_token}",
            "User-Agent": f"Atnos/{self.settings.app_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = httpx.get("https://api.github.com/user", headers=headers, timeout=10)
        except httpx.HTTPError as exc:
            raise ValueError("GitHub API was unreachable for the probe.") from exc
        if response.status_code >= 400:
            raise ValueError(f"GitHub API rejected the read-only probe ({response.status_code}).")
        payload = response.json()
        granted_scopes = tuple(
            scope.strip()
            for scope in response.headers.get("X-OAuth-Scopes", "").split(",")
            if scope.strip()
        )
        return (
            GitHubAccountMetadataView(
                login=_safe_optional_text(payload.get("login")),
                name=_safe_optional_text(payload.get("name")),
                profile_url=_safe_optional_text(payload.get("html_url")),
            ),
            granted_scopes,
        )

    def _resolve_granted_scopes(
        self,
        connection: OAuthConnection | None,
        descriptor: OAuthProviderDescriptor,
        token_bundle: OAuthTokenBundle | None,
    ) -> tuple[str, ...]:
        if token_bundle and token_bundle.scope:
            scopes = tuple(scope for scope in token_bundle.scope.split() if scope)
            if scopes:
                return scopes
        if connection is not None and connection.scopes_json:
            return tuple(str(scope) for scope in connection.scopes_json if isinstance(scope, str))
        return descriptor.scopes

    def _resolve_oauth_status(
        self,
        connection: OAuthConnection | None,
        descriptor: OAuthProviderDescriptor,
    ) -> str:
        if connection is None:
            return "available" if self._is_effectively_configured(descriptor) else "not_configured"
        return connection.status if self._is_effectively_configured(descriptor) else "not_configured"

    def _is_effectively_configured(self, descriptor: OAuthProviderDescriptor) -> bool:
        return descriptor.is_configured or (descriptor.mode == "fake_provider" and descriptor.enabled)

    def _load_token_bundle_safe(
        self,
        connection: OAuthConnection | None,
        warnings: list[str],
    ) -> OAuthTokenBundle | None:
        if connection is None or connection.status != "connected":
            return None
        try:
            descriptor = self._resolve_descriptor_and_connection()[0]
            return self.runtime.load_token_bundle(connection, descriptor=descriptor, refresh_if_needed=True)
        except Exception:
            warnings.append("Stored OAuth token bundle is unavailable for probing.")
            return None

    def _resolve_descriptor_and_connection(
        self,
    ) -> tuple[OAuthProviderDescriptor, OAuthConnection | None]:
        primary = self.registry.get_connector(self.PRIMARY_CONNECTOR_KEY)
        primary_connection = self.connections.get_latest_for_connector(self.PRIMARY_CONNECTOR_KEY)
        if primary_connection is not None or primary.is_configured:
            return primary, primary_connection
        fallback = self.registry.get_connector(self.FALLBACK_CONNECTOR_KEY)
        fallback_connection = self.connections.get_latest_for_connector(self.FALLBACK_CONNECTOR_KEY)
        return fallback, fallback_connection

    def _append_audit_event(self, connection: OAuthConnection, event: str, message: str) -> None:
        audit_log = list(connection.audit_log_json or [])
        audit_log.append(
            {
                "event": event,
                "message": message,
                "status": connection.status,
                "scopes": list(connection.scopes_json or []),
                "at": self._utcnow().isoformat(),
            }
        )
        connection.audit_log_json = audit_log[-20:]

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)


def _safe_optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None
