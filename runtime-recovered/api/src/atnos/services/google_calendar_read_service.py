from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.oauth_token_store import OAuthTokenStore


@dataclass(frozen=True, slots=True)
class CalendarEventView:
    title: str
    starts_at: str | None
    ends_at: str | None
    location: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "location": self.location,
        }


class GoogleCalendarReadService:
    CONNECTOR_KEY = "google-calendar-readonly"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.connections = OAuthConnectionRepository(session)
        self.tokens = OAuthTokenStore(settings)

    def is_connected(self) -> bool:
        connection = self.connections.get_latest_for_connector(self.CONNECTOR_KEY)
        return bool(connection is not None and connection.status == "connected" and connection.token_ref)

    def list_today_events(self, *, limit: int = 3, target_date: date | None = None) -> list[CalendarEventView]:
        connection = self.connections.get_latest_for_connector(self.CONNECTOR_KEY)
        if connection is None or connection.status != "connected":
            return []
        token_bundle = self.tokens.load_token_bundle(connection.token_ref)
        if token_bundle is None or not token_bundle.access_token:
            return []

        resolved_date = target_date or datetime.now(UTC).date()
        start_of_day = datetime.combine(resolved_date, time.min, tzinfo=UTC)
        end_of_day = start_of_day + timedelta(days=1)
        response = httpx.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token_bundle.access_token}"},
            params={
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": max(1, limit),
                "timeMin": start_of_day.isoformat().replace("+00:00", "Z"),
                "timeMax": end_of_day.isoformat().replace("+00:00", "Z"),
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        events: list[CalendarEventView] = []
        for item in items[: max(1, limit)]:
            if not isinstance(item, dict):
                continue
            events.append(
                CalendarEventView(
                    title=str(item.get("summary") or "Bez tytulu"),
                    starts_at=self._read_event_time(item.get("start")),
                    ends_at=self._read_event_time(item.get("end")),
                    location=self._optional_text(item.get("location")),
                )
            )
        return events

    @staticmethod
    def _read_event_time(raw_value: object) -> str | None:
        if not isinstance(raw_value, dict):
            return None
        date_time = raw_value.get("dateTime")
        if isinstance(date_time, str) and date_time.strip():
            return date_time.strip()
        all_day = raw_value.get("date")
        if isinstance(all_day, str) and all_day.strip():
            return all_day.strip()
        return None

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return None
