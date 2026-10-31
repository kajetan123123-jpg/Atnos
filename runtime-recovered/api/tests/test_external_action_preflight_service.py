from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atnos.services.trusted_auto_service import TrustedAutoService
from atnos.services.external_action_preflight_service import ExternalActionPreflightService
from atnos.services.oauth_service import OAuthService

from .test_support import bootstrap_test_app, create_test_app


class ExternalActionPreflightServiceTest(unittest.TestCase):
    def test_blocks_api_actions_without_oauth_but_detects_browser_account(self) -> None:
        with TemporaryDirectory(prefix="atnos-preflight-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = root / "missing-client-secret.json"
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    trusted = TrustedAutoService(session, app.state.settings)
                    trusted.upsert_account(
                        account_id=None,
                        label="YouTube Studio test",
                        provider_key="youtube-studio",
                        account_identifier="owner@example.com",
                        secret_type="password",
                        secret_value="stored-account-secret",
                        linked_project_id=None,
                        linked_integration_key="youtube-studio",
                        notes=None,
                        allowed_capabilities=["trusted_login", "browser_workflow"],
                        login_identifier="owner@example.com",
                        password_value=None,
                        totp_value=None,
                        backup_codes_value=None,
                        recovery_email=None,
                        recovery_email_secret_value=None,
                        connector_mapping_json={
                            "login_url": "https://studio.youtube.com/",
                            "validation_url": "https://studio.youtube.com/",
                            "browser_session_service": "youtube-studio",
                        },
                    )

                    result = ExternalActionPreflightService(
                        session,
                        app.state.settings,
                    ).check_shorts_real_actions()

                items = {item.action: item for item in result.items}
                self.assertFalse(result.ready_for_requested_real_test)
                self.assertFalse(items["youtube_private_upload"].ready)
                self.assertIn("OAuth token", " ".join(items["youtube_private_upload"].blockers))
                self.assertIn("oauth_start_endpoint", items["youtube_private_upload"].evidence)
                diagnostics = items["youtube_private_upload"].evidence["client_secret_diagnostics"]
                self.assertFalse(diagnostics["exists"])
                self.assertFalse(diagnostics["import_ready"])
                self.assertIn("client_secret.json file is missing", " ".join(diagnostics["errors"]))
                self.assertFalse(items["gmail_send"].ready)
                self.assertIn("OAuth", " ".join(items["gmail_send"].blockers))
                self.assertTrue(items["youtube_studio_browser_session"].ready)
            finally:
                app.state.database.dispose()

    def test_youtube_private_upload_preflight_surfaces_valid_client_secret_file(self) -> None:
        with TemporaryDirectory(prefix="atnos-preflight-client-secret-") as tmpdir:
            root = Path(tmpdir)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-preflight.apps.googleusercontent.com",
                            "client_secret": "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23456789",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    result = ExternalActionPreflightService(
                        session,
                        app.state.settings,
                    ).check_shorts_real_actions()

                items = {item.action: item for item in result.items}
                diagnostics = items["youtube_private_upload"].evidence["client_secret_diagnostics"]
                self.assertTrue(diagnostics["exists"])
                self.assertTrue(diagnostics["valid_json"])
                self.assertTrue(diagnostics["has_client_id"])
                self.assertTrue(diagnostics["has_client_secret"])
                self.assertTrue(diagnostics["import_ready"])
                self.assertEqual(diagnostics["client_type"], "installed")
                self.assertTrue(diagnostics["redirect_uri_registered"])
                self.assertNotIn("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23456789", str(diagnostics))
            finally:
                app.state.database.dispose()

    def test_youtube_private_upload_preflight_uses_connected_oauth_token_store(self) -> None:
        with TemporaryDirectory(prefix="atnos-preflight-youtube-oauth-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                app.state.settings.oauth_enabled = True
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    oauth = OAuthService(session, app.state.settings)
                    start = oauth.start_connection("youtube-studio")
                    from urllib.parse import parse_qs, urlparse

                    query = parse_qs(urlparse(start.authorization_url).query)
                    oauth.complete_callback(
                        "youtube-studio",
                        code=query["code"][0],
                        state=query["state"][0],
                        error=None,
                    )
                    result = ExternalActionPreflightService(
                        session,
                        app.state.settings,
                    ).check_shorts_real_actions()

                items = {item.action: item for item in result.items}
                self.assertTrue(items["youtube_private_upload"].ready)
                self.assertEqual(items["youtube_private_upload"].evidence["oauth_status"], "connected")
                self.assertEqual(items["youtube_private_upload"].evidence["missing_scopes"], [])
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
