from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
import mimetypes
from pathlib import Path
from typing import Sequence

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.oauth_connection_runtime_service import OAuthConnectionRuntimeService
from atnos.services.oauth_provider_registry import OAuthProviderDescriptor, OAuthProviderRegistry
from atnos.services.oauth_token_store import OAuthTokenBundle
from atnos.services.runtime_settings_service import RuntimeSettingsService


@dataclass(frozen=True, slots=True)
class GmailConnectorStatusView:
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
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    connected: bool


@dataclass(frozen=True, slots=True)
class GmailConnectorProbeView:
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
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    message: str


class GmailConnectorService:
    PRIMARY_CONNECTOR_KEY = "gmail"
    FALLBACK_CONNECTOR_KEY = "gmail-readonly"

    def __init__(self, session: Session, settings: Settings, *, account_id: int | None = None) -> None:
        self.session = session
        self.settings = RuntimeSettingsService(session, settings).build_effective_settings()
        self.account_id = account_id
        self.registry = OAuthProviderRegistry(self.settings)
        self.connections = OAuthConnectionRepository(session)
        self.runtime = OAuthConnectionRuntimeService(session, self.settings)
        self.action_broker = AccountActionBrokerService(session, self.settings)

    def get_status(self) -> GmailConnectorStatusView:
        descriptor, connection = self._resolve_descriptor_and_connection()
        warnings = list(descriptor.setup_warnings)
        token_bundle = self._load_token_bundle_safe(connection, descriptor, warnings)
        granted_scopes = self._resolve_granted_scopes(connection, descriptor, token_bundle)
        connected = connection is not None and connection.status == "connected" and token_bundle is not None
        live_write_enabled = any(
            scope in granted_scopes
            for scope in (
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/gmail.send",
            )
        )
        return GmailConnectorStatusView(
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
            checked_at=self._utcnow(),
            live_read_enabled=connected,
            live_write_enabled=connected and live_write_enabled,
            connected=connected,
        )

    def probe(self) -> GmailConnectorProbeView:
        status = self.get_status()
        if not status.provider_enabled or not status.provider_configured:
            return GmailConnectorProbeView(
                connector_key=status.connector_key,
                display_name=status.display_name,
                integration_key=status.integration_key,
                oauth_status=status.oauth_status,
                probe_status="not_configured",
                connection_id=status.connection_id,
                account_id=status.account_id,
                provider_reachable=False,
                provider_enabled=status.provider_enabled,
                provider_configured=status.provider_configured,
                token_present=status.token_present,
                required_scopes=status.required_scopes,
                granted_scopes=status.granted_scopes,
                warnings=status.warnings,
                checked_at=self._utcnow(),
                live_read_enabled=status.live_read_enabled,
                live_write_enabled=status.live_write_enabled,
                message="Gmail OAuth is not configured in this runtime.",
            )
        if not status.connected:
            return GmailConnectorProbeView(
                connector_key=status.connector_key,
                display_name=status.display_name,
                integration_key=status.integration_key,
                oauth_status=status.oauth_status,
                probe_status="not_connected",
                connection_id=status.connection_id,
                account_id=status.account_id,
                provider_reachable=False,
                provider_enabled=status.provider_enabled,
                provider_configured=status.provider_configured,
                token_present=status.token_present,
                required_scopes=status.required_scopes,
                granted_scopes=status.granted_scopes,
                warnings=status.warnings,
                checked_at=self._utcnow(),
                live_read_enabled=status.live_read_enabled,
                live_write_enabled=status.live_write_enabled,
                message="Connect Gmail before running a probe.",
            )
        try:
            self._gmail_get("https://gmail.googleapis.com/gmail/v1/users/me/profile")
        except httpx.HTTPStatusError as exc:
            detail = _compact_http_error_detail(exc)
            return GmailConnectorProbeView(
                connector_key=status.connector_key,
                display_name=status.display_name,
                integration_key=status.integration_key,
                oauth_status=status.oauth_status,
                probe_status="error",
                connection_id=status.connection_id,
                account_id=status.account_id,
                provider_reachable=False,
                provider_enabled=status.provider_enabled,
                provider_configured=status.provider_configured,
                token_present=status.token_present,
                required_scopes=status.required_scopes,
                granted_scopes=status.granted_scopes,
                warnings=status.warnings,
                checked_at=self._utcnow(),
                live_read_enabled=False,
                live_write_enabled=False,
                message=f"Gmail provider probe failed: {detail}",
            )
        except httpx.HTTPError as exc:
            return GmailConnectorProbeView(
                connector_key=status.connector_key,
                display_name=status.display_name,
                integration_key=status.integration_key,
                oauth_status=status.oauth_status,
                probe_status="error",
                connection_id=status.connection_id,
                account_id=status.account_id,
                provider_reachable=False,
                provider_enabled=status.provider_enabled,
                provider_configured=status.provider_configured,
                token_present=status.token_present,
                required_scopes=status.required_scopes,
                granted_scopes=status.granted_scopes,
                warnings=status.warnings,
                checked_at=self._utcnow(),
                live_read_enabled=False,
                live_write_enabled=False,
                message=f"Gmail provider probe failed: {type(exc).__name__}: {exc}",
            )
        return GmailConnectorProbeView(
            connector_key=status.connector_key,
            display_name=status.display_name,
            integration_key=status.integration_key,
            oauth_status=status.oauth_status,
            probe_status="ok",
            connection_id=status.connection_id,
            account_id=status.account_id,
            provider_reachable=True,
            provider_enabled=status.provider_enabled,
            provider_configured=status.provider_configured,
            token_present=status.token_present,
            required_scopes=status.required_scopes,
            granted_scopes=status.granted_scopes,
            warnings=status.warnings,
            checked_at=self._utcnow(),
            live_read_enabled=status.live_read_enabled,
            live_write_enabled=status.live_write_enabled,
            message="Gmail probe completed.",
        )

    def search_messages(self, *, query: str, max_results: int = 20) -> dict[str, object]:
        payload = self._gmail_get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={
                "q": query.strip(),
                "maxResults": max(1, min(int(max_results), 20)),
            },
        )
        items = []
        for item in payload.get("messages") or []:
            if not isinstance(item, dict):
                continue
            items.append({"message_id": str(item.get("id") or "").strip(), "thread_id": str(item.get("threadId") or "").strip()})
        return {"status": "ready", "items": items}

    def read_message(self, message_id: str) -> dict[str, object]:
        payload = self._gmail_get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id.strip()}",
            params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
        )
        headers = {}
        for item in ((payload.get("payload") or {}).get("headers") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name:
                headers[name] = value
        return {
            "status": "ready",
            "message_id": str(payload.get("id") or "").strip() or message_id.strip(),
            "thread_id": str(payload.get("threadId") or "").strip() or None,
            "snippet": str(payload.get("snippet") or "").strip() or None,
            "headers": headers,
            "label_ids": list(payload.get("labelIds") or []),
        }

    def draft_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachments: Sequence[str | Path] | None = None,
    ) -> dict[str, object]:
        mime = self._build_mime(to=to, subject=subject, body=body, attachments=attachments)
        payload = self._gmail_post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            json={"message": {"raw": mime}},
        )
        draft = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        return {
            "status": "drafted",
            "draft_id": str(payload.get("id") or "").strip() or None,
            "message_id": str(draft.get("id") or "").strip() or None,
            "thread_id": str(draft.get("threadId") or "").strip() or None,
            "to": to.strip(),
            "subject": subject.strip(),
        }

    def create_send_preview(
        self,
        *,
        account_id: int,
        project_id: int | None,
        draft_id: str,
        to: str,
        subject: str,
        body: str,
    ):
        return self.action_broker.create_action_draft(
            account_id=account_id,
            integration_key="gmail",
            project_id=project_id,
            action_type="message_preview",
            action_title="Send Gmail message",
            action_summary="Sending mail stays supervised and requires double approval.",
            preview_payload={
                "platform": "gmail",
                "action": "send_message",
                "draft_id": draft_id.strip(),
                "to": to.strip(),
                "subject": subject.strip(),
                "body": body.strip(),
                "approval_level": "double",
            },
            approval_notes="The first approval accepts the preview. The second confirmation executes the send.",
            created_by="atnos",
        )

    def send_message(self, *, draft_id: str) -> dict[str, object]:
        payload = self._gmail_post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send",
            json={"id": draft_id.strip()},
        )
        return {
            "status": "sent",
            "message_id": str(payload.get("id") or "").strip() or None,
            "thread_id": str(payload.get("threadId") or "").strip() or None,
            "label_ids": list(payload.get("labelIds") or []),
        }

    def _resolve_descriptor_and_connection(self) -> tuple[OAuthProviderDescriptor, OAuthConnection | None]:
        primary = self.registry.get_connector(self.PRIMARY_CONNECTOR_KEY)
        primary_connection = self._latest_connection(self.PRIMARY_CONNECTOR_KEY)
        if primary_connection is not None or primary.is_configured:
            return primary, primary_connection
        fallback = self.registry.get_connector(self.FALLBACK_CONNECTOR_KEY)
        fallback_connection = self._latest_connection(self.FALLBACK_CONNECTOR_KEY)
        if fallback_connection is not None:
            return fallback, fallback_connection
        return primary, primary_connection

    def _latest_connection(self, connector_key: str) -> OAuthConnection | None:
        if self.account_id is not None:
            return self.connections.get_latest_for_connector_account(connector_key, self.account_id)
        return self.connections.get_latest_for_connector(connector_key)

    def _load_token_bundle_safe(
        self,
        connection: OAuthConnection | None,
        descriptor: OAuthProviderDescriptor,
        warnings: list[str],
    ) -> OAuthTokenBundle | None:
        if connection is None or connection.status != "connected":
            return None
        try:
            return self.runtime.load_token_bundle(connection, descriptor=descriptor, refresh_if_needed=True)
        except Exception:
            warnings.append("Stored Gmail OAuth token bundle is unavailable.")
            return None

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

    @staticmethod
    def _is_effectively_configured(descriptor: OAuthProviderDescriptor) -> bool:
        return descriptor.is_configured or (descriptor.mode == "fake_provider" and descriptor.enabled)

    def _authorized_headers(self) -> dict[str, str]:
        descriptor, connection = self._resolve_descriptor_and_connection()
        if connection is None or connection.status != "connected":
            raise ValueError("Connect Gmail before using this action.")
        token_bundle = self.runtime.load_token_bundle(connection, descriptor=descriptor, refresh_if_needed=True)
        if token_bundle is None or not token_bundle.access_token:
            raise ValueError("Connected Gmail token is unavailable.")
        return {"Authorization": f"Bearer {token_bundle.access_token}"}

    def _gmail_get(self, url: str, *, params: dict[str, object] | None = None) -> dict[str, object]:
        response = httpx.get(url, headers=self._authorized_headers(), params=params or {}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _gmail_post(self, url: str, *, json: dict[str, object]) -> dict[str, object]:
        response = httpx.post(url, headers=self._authorized_headers(), json=json, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _build_mime(
        *,
        to: str,
        subject: str,
        body: str,
        attachments: Sequence[str | Path] | None = None,
    ) -> str:
        message = EmailMessage()
        message["To"] = to.strip()
        message["Subject"] = subject.strip()
        message.set_content(body.strip(), charset="utf-8")
        for attachment in attachments or ():
            path = Path(attachment)
            data = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            maintype, subtype = content_type.split("/", 1)
            message.add_attachment(
                data,
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return raw.rstrip("=")

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)


def _compact_http_error_detail(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    if response is None:
        return str(exc)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            status = str(error.get("status") or "").strip()
            message = str(error.get("message") or "").strip()
            reason = ""
            errors = error.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason") or "").strip()
            parts = [part for part in (status, reason, message) if part]
            if parts:
                return " / ".join(parts)
    return response.text.strip()[:500] or str(exc)
