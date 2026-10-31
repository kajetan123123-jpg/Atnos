from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.oauth_connection_runtime_service import OAuthConnectionRuntimeService
from atnos.services.oauth_provider_registry import OAuthProviderDescriptor, OAuthProviderRegistry
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository


@dataclass(frozen=True, slots=True)
class GoogleCalendarConnectorStatusView:
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
class GoogleCalendarConnectorProbeView:
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


class GoogleCalendarConnectorService:
    PRIMARY_CONNECTOR_KEY = "google-calendar"
    FALLBACK_CONNECTOR_KEY = "google-calendar-readonly"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.registry = OAuthProviderRegistry(settings)
        self.connections = OAuthConnectionRepository(session)
        self.token_store = OAuthTokenStore(settings)
        self.action_broker = AccountActionBrokerService(session, settings)
        self.runtime = OAuthConnectionRuntimeService(session, settings)

    def get_status(self) -> GoogleCalendarConnectorStatusView:
        descriptor, connection = self._resolve_descriptor_and_connection()
        warnings = list(descriptor.setup_warnings)
        token_bundle = self._load_token_bundle_safe(connection, warnings)
        granted_scopes = self._resolve_granted_scopes(connection, descriptor, token_bundle)
        live_write_enabled = any(
            scope == "https://www.googleapis.com/auth/calendar" for scope in granted_scopes
        )
        connected = connection is not None and connection.status == "connected" and token_bundle is not None
        return GoogleCalendarConnectorStatusView(
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

    def probe(self) -> GoogleCalendarConnectorProbeView:
        status_view = self.get_status()
        if not status_view.provider_enabled or not status_view.provider_configured:
            return GoogleCalendarConnectorProbeView(
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
                warnings=status_view.warnings,
                checked_at=self._utcnow(),
                live_read_enabled=status_view.live_read_enabled,
                live_write_enabled=status_view.live_write_enabled,
                message="Google Calendar OAuth is not configured in this runtime.",
            )
        if not status_view.connected:
            return GoogleCalendarConnectorProbeView(
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
                warnings=status_view.warnings,
                checked_at=self._utcnow(),
                live_read_enabled=status_view.live_read_enabled,
                live_write_enabled=status_view.live_write_enabled,
                message="Connect Google Calendar before running a probe.",
            )

        token_bundle = self._load_required_token_bundle(status_view.connection_id)
        try:
            response = httpx.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {token_bundle.access_token}"},
                params={
                    "maxResults": 1,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "timeMin": self._utcnow().isoformat().replace("+00:00", "Z"),
                },
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return GoogleCalendarConnectorProbeView(
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
                token_present=status_view.token_present,
                required_scopes=status_view.required_scopes,
                granted_scopes=status_view.granted_scopes,
                warnings=status_view.warnings,
                checked_at=self._utcnow(),
                live_read_enabled=status_view.live_read_enabled,
                live_write_enabled=status_view.live_write_enabled,
                message="Google Calendar provider probe failed.",
            )

        return GoogleCalendarConnectorProbeView(
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
            token_present=status_view.token_present,
            required_scopes=status_view.required_scopes,
            granted_scopes=status_view.granted_scopes,
            warnings=status_view.warnings,
            checked_at=self._utcnow(),
            live_read_enabled=status_view.live_read_enabled,
            live_write_enabled=status_view.live_write_enabled,
            message="Google Calendar probe completed.",
        )

    def create_event_preview(
        self,
        *,
        account_id: int,
        project_id: int | None,
        payload: dict[str, object],
    ):
        return self.action_broker.create_action_draft(
            account_id=account_id,
            integration_key="google-calendar",
            project_id=project_id,
            action_type="calendar_write_preview",
            action_title="Create Google Calendar event",
            action_summary="Google Calendar event draft requires supervised approval.",
            preview_payload={"platform": "google-calendar", "action": "create_event", **payload},
            approval_notes="Calendar write stays supervised in content ops v1.",
            created_by="atnos",
        )

    def delete_event_preview(
        self,
        *,
        account_id: int,
        project_id: int | None,
        event_id: str,
    ):
        return self.action_broker.create_action_draft(
            account_id=account_id,
            integration_key="google-calendar",
            project_id=project_id,
            action_type="calendar_write_preview",
            action_title="Delete Google Calendar event",
            action_summary=f"Deleting calendar event {event_id} requires supervised approval.",
            preview_payload={
                "platform": "google-calendar",
                "action": "delete_event",
                "event_id": event_id,
            },
            approval_notes="Calendar delete stays supervised in content ops v1.",
            created_by="atnos",
        )

    def list_events(
        self,
        *,
        time_min: str,
        time_max: str,
        max_results: int = 50,
    ) -> dict[str, object]:
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": max(1, min(int(max_results), 50)),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        response = self._google_get("https://www.googleapis.com/calendar/v3/calendars/primary/events", params=params)
        items = []
        for event in response.get("items") or []:
            if not isinstance(event, dict):
                continue
            items.append(
                {
                    "event_id": str(event.get("id") or "").strip() or None,
                    "summary": str(event.get("summary") or "").strip() or None,
                    "status": str(event.get("status") or "").strip() or None,
                    "html_link": str(event.get("htmlLink") or "").strip() or None,
                    "start": dict(event.get("start") or {}),
                    "end": dict(event.get("end") or {}),
                    "attendees": list(event.get("attendees") or []),
                }
            )
        return {"status": "ready", "items": items}

    def create_event(self, payload: dict[str, object]) -> dict[str, object]:
        response = self._google_post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            json=payload,
        )
        return {
            "status": "created",
            "event_id": str(response.get("id") or "").strip() or None,
            "html_link": str(response.get("htmlLink") or "").strip() or None,
            "summary": str(response.get("summary") or "").strip() or None,
        }

    def delete_event(self, event_id: str) -> dict[str, object]:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id is required.")
        self._google_delete(f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{normalized_event_id}")
        return {"status": "deleted", "event_id": normalized_event_id}

    def find_free_slots(
        self,
        *,
        duration_minutes: int,
        candidates: list[dict[str, str]],
    ) -> dict[str, object]:
        if duration_minutes <= 0:
            raise ValueError("duration_minutes must be greater than zero.")
        windows: list[dict[str, str]] = []
        for item in candidates:
            start = str(item.get("start") or "").strip()
            end = str(item.get("end") or "").strip()
            if start and end:
                windows.append({"start": start, "end": end})
        if not windows:
            raise ValueError("At least one candidate window is required.")
        time_min = min(window["start"] for window in windows)
        time_max = max(window["end"] for window in windows)
        busy = self._google_post(
            "https://www.googleapis.com/calendar/v3/freeBusy",
            json={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": "primary"}],
            },
        )
        busy_slots = ((busy.get("calendars") or {}).get("primary") or {}).get("busy") or []
        free_slots: list[dict[str, str]] = []
        for window in windows:
            if self._window_allows_duration(window, busy_slots, duration_minutes=duration_minutes):
                free_slots.append(window)
        return {"status": "ready", "items": free_slots}

    def _resolve_descriptor_and_connection(self) -> tuple[OAuthProviderDescriptor, OAuthConnection | None]:
        primary = self.registry.get_connector(self.PRIMARY_CONNECTOR_KEY)
        primary_connection = self.connections.get_latest_for_connector(self.PRIMARY_CONNECTOR_KEY)
        if primary_connection is not None or primary.is_configured:
            return primary, primary_connection
        fallback = self.registry.get_connector(self.FALLBACK_CONNECTOR_KEY)
        fallback_connection = self.connections.get_latest_for_connector(self.FALLBACK_CONNECTOR_KEY)
        return fallback, fallback_connection

    def _load_required_token_bundle(self, connection_id: int | None) -> OAuthTokenBundle:
        if connection_id is None:
            raise ValueError("Connected Google Calendar token is unavailable.")
        connection = self.connections.get_by_id(connection_id)
        if connection is None:
            raise ValueError("Connected Google Calendar token is unavailable.")
        token_bundle = self.runtime.load_token_bundle(connection, descriptor=self._resolve_descriptor_and_connection()[0], refresh_if_needed=True)
        if token_bundle is None or not token_bundle.access_token:
            raise ValueError("Connected Google Calendar token is unavailable.")
        return token_bundle

    def _load_token_bundle_safe(
        self,
        connection: OAuthConnection | None,
        warnings: list[str],
    ) -> OAuthTokenBundle | None:
        if connection is None or connection.status != "connected":
            return None
        try:
            return self.runtime.load_token_bundle(connection, descriptor=self._resolve_descriptor_and_connection()[0], refresh_if_needed=True)
        except Exception:
            warnings.append("Stored Google Calendar OAuth token bundle is unavailable.")
            return None

    def _authorized_headers(self) -> dict[str, str]:
        descriptor, connection = self._resolve_descriptor_and_connection()
        if connection is None or connection.status != "connected":
            raise ValueError("Connect Google Calendar before using this action.")
        token_bundle = self.runtime.load_token_bundle(connection, descriptor=descriptor, refresh_if_needed=True)
        if token_bundle is None or not token_bundle.access_token:
            raise ValueError("Connected Google Calendar token is unavailable.")
        return {"Authorization": f"Bearer {token_bundle.access_token}"}

    def _google_get(self, url: str, *, params: dict[str, object]) -> dict[str, object]:
        response = httpx.get(url, headers=self._authorized_headers(), params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _google_post(self, url: str, *, json: dict[str, object]) -> dict[str, object]:
        response = httpx.post(url, headers=self._authorized_headers(), json=json, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _google_delete(self, url: str) -> None:
        response = httpx.delete(url, headers=self._authorized_headers(), timeout=20)
        response.raise_for_status()

    @staticmethod
    def _window_allows_duration(
        window: dict[str, str],
        busy_slots: list[dict[str, object]],
        *,
        duration_minutes: int,
    ) -> bool:
        try:
            start = datetime.fromisoformat(window["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(window["end"].replace("Z", "+00:00"))
        except ValueError:
            return False
        if (end - start).total_seconds() < duration_minutes * 60:
            return False
        for slot in busy_slots:
            try:
                busy_start = datetime.fromisoformat(str(slot.get("start") or "").replace("Z", "+00:00"))
                busy_end = datetime.fromisoformat(str(slot.get("end") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if start < busy_end and busy_start < end:
                return False
        return True

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

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
