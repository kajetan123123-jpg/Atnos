from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.google_calendar_connector_service import GoogleCalendarConnectorService

from .test_support import bootstrap_test_app, create_test_app


class GoogleCalendarConnectorServiceTest(unittest.TestCase):
    def test_list_events_and_create_delete_previews_work(self) -> None:
        with TemporaryDirectory(prefix="atnos-calendar-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                database = app.state.database

                with database.session_scope() as session:
                    account = AccountCredentialsService(session, settings).create_account(
                        label="Calendar account",
                        provider_key="google-calendar",
                        account_identifier="calendar:owner",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=["read_metadata", "connector_write", "confirmed_write_later"],
                        linked_integration_key="google-calendar",
                        linked_project_id=None,
                        notes=None,
                    )
                    service = GoogleCalendarConnectorService(session, settings)

                    with patch.object(
                        service,
                        "_google_get",
                        return_value={
                            "items": [
                                {
                                    "id": "evt-1",
                                    "summary": "Sprint review",
                                    "status": "confirmed",
                                    "htmlLink": "https://calendar.google.com/event?evt-1",
                                    "start": {"dateTime": "2026-05-13T10:00:00Z"},
                                    "end": {"dateTime": "2026-05-13T11:00:00Z"},
                                }
                            ]
                        },
                    ):
                        result = service.list_events(
                            time_min="2026-05-13T00:00:00Z",
                            time_max="2026-05-14T00:00:00Z",
                        )
                    self.assertEqual(result["status"], "ready")
                    self.assertEqual(result["items"][0]["event_id"], "evt-1")

                    create_preview = service.create_event_preview(
                        account_id=account.id,
                        project_id=None,
                        payload={"summary": "Nowe spotkanie"},
                    )
                    delete_preview = service.delete_event_preview(
                        account_id=account.id,
                        project_id=None,
                        event_id="evt-1",
                    )
                    self.assertEqual(create_preview.action_type, "calendar_write_preview")
                    self.assertEqual(create_preview.preview_payload["action"], "create_event")
                    self.assertEqual(delete_preview.preview_payload["action"], "delete_event")
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
