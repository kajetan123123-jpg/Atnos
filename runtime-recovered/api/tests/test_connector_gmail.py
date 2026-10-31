from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atnos.db.models.oauth_connection import OAuthConnection
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.gmail_connector_service import GmailConnectorService
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore

from .test_support import bootstrap_test_app, create_test_app


class GmailConnectorServiceTest(unittest.TestCase):
    def test_search_read_draft_and_send_preview_paths(self) -> None:
        with TemporaryDirectory(prefix="atnos-gmail-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                database = app.state.database

                with database.session_scope() as session:
                    account = AccountCredentialsService(session, settings).create_account(
                        label="Gmail account",
                        provider_key="gmail",
                        account_identifier="gmail:owner",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=[
                            "read_metadata",
                            "read_content",
                            "connector_write",
                            "confirmed_write_later",
                        ],
                        linked_integration_key="gmail",
                        linked_project_id=None,
                        notes=None,
                    )
                    service = GmailConnectorService(session, settings)

                    with patch.object(
                        service,
                        "_gmail_get",
                        side_effect=[
                            {
                                "messages": [{"id": "m1", "threadId": "t1"}],
                                "resultSizeEstimate": 1,
                            },
                            {
                                "id": "m1",
                                "threadId": "t1",
                                "labelIds": ["INBOX"],
                                "payload": {
                                    "headers": [
                                        {"name": "Subject", "value": "Hello"},
                                        {"name": "From", "value": "owner@example.com"},
                                    ]
                                },
                                "snippet": "Preview",
                            },
                        ],
                    ), patch.object(
                        service,
                        "_gmail_post",
                        return_value={"id": "draft-1", "message": {"id": "msg-1", "threadId": "t1"}},
                    ):
                        search = service.search_messages(query="from:owner", max_results=10)
                        message = service.read_message(message_id="m1")
                        draft = service.draft_message(
                            to="target@example.com",
                            subject="Hello",
                            body="Body",
                        )

                    preview = service.create_send_preview(
                        account_id=account.id,
                        project_id=None,
                        draft_id="draft-1",
                        to="target@example.com",
                        subject="Hello",
                        body="Body",
                    )
                    self.assertEqual(search["status"], "ready")
                    self.assertEqual(search["items"][0]["message_id"], "m1")
                    self.assertEqual(message["status"], "ready")
                    self.assertEqual(draft["status"], "drafted")
                    self.assertEqual(preview.action_type, "message_preview")
                    self.assertEqual(preview.preview_payload["approval_level"], "double")
            finally:
                app.state.database.dispose()

    def test_status_can_target_specific_oauth_account_instead_of_latest_connection(self) -> None:
        with TemporaryDirectory(prefix="atnos-gmail-multi-account-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = root / "missing-client-secret.json"
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.oauth_enabled = True
                settings.oauth_google_client_id = "google-client-id"
                settings.oauth_google_client_secret_ref = OAuthTokenStore(settings).vault.store_secret("google-client-secret")[1]
                required_scopes = [
                    "openid",
                    "email",
                    "profile",
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.compose",
                    "https://www.googleapis.com/auth/gmail.send",
                ]
                token_store = OAuthTokenStore(settings)

                with app.state.database.session_scope() as session:
                    accounts = AccountCredentialsService(session, settings)
                    first = accounts.create_account(
                        label="Gmail first",
                        provider_key="gmail",
                        account_identifier="first@example.com",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=["confirmed_write_later"],
                        linked_integration_key="gmail",
                        linked_project_id=None,
                        notes=None,
                    )
                    latest = accounts.create_account(
                        label="Gmail latest",
                        provider_key="gmail",
                        account_identifier="latest@example.com",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=["confirmed_write_later"],
                        linked_integration_key="gmail",
                        linked_project_id=None,
                        notes=None,
                    )
                    for account, access_token in ((first, "first-token"), (latest, "latest-token")):
                        session.add(
                            OAuthConnection(
                                connector_key="gmail",
                                display_name="Gmail",
                                status="connected",
                                scopes_json=required_scopes,
                                account_id=account.id,
                                token_ref=token_store.store_token_bundle(
                                    OAuthTokenBundle(
                                        access_token=access_token,
                                        refresh_token="refresh-token",
                                        token_type="Bearer",
                                        expires_in=3600,
                                        scope=" ".join(required_scopes),
                                    )
                                ),
                                code_verifier_ref=None,
                                state_hash=None,
                                metadata_json={},
                                audit_log_json=[],
                            )
                        )
                        session.flush()

                    default_status = GmailConnectorService(session, settings).get_status()
                    targeted_status = GmailConnectorService(session, settings, account_id=first.id).get_status()

                self.assertEqual(default_status.account_id, latest.id)
                self.assertEqual(targeted_status.account_id, first.id)
                self.assertTrue(targeted_status.connected)
                self.assertTrue(targeted_status.live_write_enabled)
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
