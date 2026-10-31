from __future__ import annotations

from pathlib import Path
import json
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import httpx

from fastapi.testclient import TestClient

from atnos.db.models.oauth_connection import OAuthConnection
from atnos.intelligence.storage import (
    read_intelligence_account_config,
    upsert_intelligence_account_config,
)
from atnos.services.oauth_connection_runtime_service import OAuthConnectionRuntimeService
from atnos.services.oauth_provider_registry import (
    YOUTUBE_OPTIONAL_CAPTION_SCOPE,
    OAuthProviderDescriptor,
    oauth_client_fingerprint,
)
from atnos.services.oauth_service import OAuthService
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore

from .test_support import bootstrap_test_app, build_test_settings, create_test_app


def test_test_settings_cannot_write_operator_runtime_env(monkeypatch, tmp_path: Path) -> None:
    operator_env = tmp_path / "operator" / ".env"
    monkeypatch.setenv("ATNOS_ENV_FILE", str(operator_env))
    test_root = tmp_path / "isolated-test"

    settings = build_test_settings(test_root)

    assert settings.runtime_env_file == (test_root / "config" / ".env").resolve()
    assert settings.runtime_env_file != operator_env.resolve()


class OAuthFlowPerProviderTest(unittest.TestCase):
    def test_listing_connections_expires_abandoned_pending_session(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-expired-pending-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                app.state.settings.oauth_enabled = True
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    service = OAuthService(session, app.state.settings)
                    started = service.start_connection("gmail")
                    pending = service.connections.get_by_id(started.connection_id)
                    assert pending is not None
                    pending.updated_at = (
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        - service.connections.PENDING_FLOW_TTL
                        - timedelta(seconds=1)
                    )
                    service.connections.save(pending)

                    listed = {item.id: item for item in service.list_connections()}

                    self.assertEqual(listed[started.connection_id].status, "error")
                    self.assertIn("Sesja OAuth wygasla", listed[started.connection_id].last_error or "")
                    self.assertIsNone(pending.state_hash)
                    self.assertIsNone(pending.code_verifier_ref)
                    self.assertTrue(
                        any(
                            entry.get("event") == "oauth_pending_expired"
                            for entry in (pending.audit_log_json or [])
                        )
                    )
            finally:
                app.state.database.dispose()

    def test_generic_redacted_reconnect_error_is_mapped_to_actionable_message(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-public-error-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    service = OAuthService(session, app.state.settings)
                    connection = OAuthConnection(
                        connector_key="youtube-studio",
                        display_name="YouTube Studio",
                        status="error",
                        scopes_json=["openid"],
                        account_id=None,
                        token_ref=None,
                        code_verifier_ref=None,
                        state_hash=None,
                        metadata_json={
                            "oauth_reconnect_required": True,
                            "oauth_refresh_failure_code": "invalid_grant",
                        },
                        audit_log_json=[],
                        last_error="Sensitive OAuth data was redacted.",
                    )

                    self.assertEqual(
                        service._connection_public_error(connection),
                        "Autoryzacja Google wygasla lub zostala uniewazniona. Polacz konto ponownie.",
                    )
            finally:
                app.state.database.dispose()

    def test_new_oauth_flow_supersedes_only_older_pending_flow_for_same_target(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-supersede-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                app.state.settings.oauth_enabled = True
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    service = OAuthService(session, app.state.settings)
                    first = service.start_connection(
                        "youtube-studio",
                        content_account_id="youtube:account-3f2eb91b@example.invalid",
                    )
                    other_target = service.start_connection(
                        "youtube-studio",
                        content_account_id="youtube:account-b0431fe8@example.invalid",
                    )
                    replacement = service.start_connection(
                        "youtube-studio",
                        content_account_id="youtube:account-3f2eb91b@example.invalid",
                    )

                    first_connection = service.connections.get_by_id(first.connection_id)
                    other_connection = service.connections.get_by_id(other_target.connection_id)
                    replacement_connection = service.connections.get_by_id(replacement.connection_id)

                    assert first_connection is not None
                    assert other_connection is not None
                    assert replacement_connection is not None
                    self.assertEqual(first_connection.status, "error")
                    self.assertIsNone(first_connection.state_hash)
                    self.assertIsNone(first_connection.code_verifier_ref)
                    self.assertEqual(other_connection.status, "pending")
                    self.assertEqual(replacement_connection.status, "pending")
                    self.assertEqual(
                        replacement_connection.metadata_json.get("oauth_client_fingerprint"),
                        oauth_client_fingerprint(service.registry.get_connector("youtube-studio").client_id),
                    )
                    self.assertTrue(
                        any(
                            entry.get("event") == "oauth_connection_superseded"
                            for entry in (first_connection.audit_log_json or [])
                        )
                    )
            finally:
                app.state.database.dispose()

    def test_changed_oauth_client_requires_reconnect_before_refresh_request(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-client-changed-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                token_store = OAuthTokenStore(settings)
                token_ref = token_store.store_token_bundle(
                    OAuthTokenBundle(
                        access_token="expired-access-token",
                        refresh_token="refresh-token-for-test",
                        token_type="Bearer",
                        expires_in=3600,
                        scope="https://www.googleapis.com/auth/youtube.readonly",
                    )
                )
                descriptor = OAuthProviderDescriptor(
                    connector_key="youtube-studio",
                    display_name="YouTube Studio",
                    authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
                    token_url="https://oauth2.googleapis.com/token",
                    redirect_uri="http://127.0.0.1:8000/oauth/youtube-studio/callback",
                    scopes=("https://www.googleapis.com/auth/youtube.readonly",),
                    supports_pkce=True,
                    enabled=True,
                    setup_warnings=(),
                    mode="real_provider",
                    client_id="new-client.apps.googleusercontent.com",
                    client_secret_ref=None,
                    integration_key="youtube",
                )
                with app.state.database.session_scope() as session:
                    connection = OAuthConnection(
                        connector_key="youtube-studio",
                        display_name="YouTube Studio",
                        status="connected",
                        scopes_json=list(descriptor.scopes),
                        account_id=None,
                        token_ref=token_ref,
                        code_verifier_ref=None,
                        state_hash=None,
                        metadata_json={
                            "token_expires_at": "2000-01-01T00:00:00+00:00",
                            "oauth_client_fingerprint": oauth_client_fingerprint(
                                "retired-client.apps.googleusercontent.com"
                            ),
                        },
                        audit_log_json=[],
                    )
                    session.add(connection)
                    session.flush()
                    runtime = OAuthConnectionRuntimeService(session, settings)

                    with patch("atnos.services.oauth_connection_runtime_service.httpx.post") as post:
                        with self.assertRaisesRegex(RuntimeError, "oauth_client_changed"):
                            runtime.load_token_bundle(
                                connection,
                                descriptor=descriptor,
                                force_refresh=True,
                            )

                    self.assertEqual(post.call_count, 0)
                    self.assertEqual(connection.status, "error")
                    self.assertTrue(connection.metadata_json["oauth_reconnect_required"])
                    self.assertEqual(
                        connection.metadata_json["oauth_refresh_failure_code"],
                        "oauth_client_changed",
                    )
                    self.assertIn("Klient OAuth zostal zmieniony", connection.last_error or "")
            finally:
                app.state.database.dispose()

    def test_token_exchange_reports_safe_google_error_code_without_response_secrets(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-safe-token-error-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                descriptor = OAuthProviderDescriptor(
                    connector_key="gmail",
                    display_name="Gmail",
                    authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
                    token_url="https://oauth2.googleapis.com/token",
                    redirect_uri="http://127.0.0.1:8000/oauth/gmail/callback",
                    scopes=("https://www.googleapis.com/auth/gmail.send",),
                    supports_pkce=True,
                    enabled=True,
                    setup_warnings=(),
                    mode="real_provider",
                    client_id="safe-client.apps.googleusercontent.com",
                    client_secret_ref=None,
                    integration_key="gmail",
                )
                response = httpx.Response(
                    400,
                    json={
                        "error": "invalid_client",
                        "error_description": "sensitive provider response must not be copied",
                        "access_token": "must-never-leak",
                    },
                    request=httpx.Request("POST", descriptor.token_url),
                )
                with app.state.database.session_scope() as session:
                    service = OAuthService(session, app.state.settings)
                    with patch("atnos.services.oauth_service.httpx.post", return_value=response):
                        with self.assertRaisesRegex(RuntimeError, r"invalid_client, HTTP 400") as error:
                            service._exchange_code_for_token(
                                descriptor=descriptor,
                                code="one-time-value",
                                code_verifier="pkce-value",
                            )
                self.assertNotIn("must-never-leak", str(error.exception))
                self.assertNotIn("sensitive provider response", str(error.exception))
                self.assertEqual(
                    service._sanitize_public_error(str(error.exception)),
                    str(error.exception),
                )
            finally:
                app.state.database.dispose()

    def test_targeted_youtube_oauth_callback_updates_only_requested_channel_mapping(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-targeted-channel-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                app.state.settings.oauth_enabled = True
                bootstrap_test_app(app)
                content_account_id = "youtube:account-a914f7bd@example.invalid"
                upsert_intelligence_account_config(
                    app.state.settings,
                    account_id=content_account_id,
                    niches=["personal finance"],
                    channel_title="Wealth Blueprint",
                )

                with app.state.database.session_scope() as session:
                    service = OAuthService(session, app.state.settings)
                    start = service.start_connection(
                        "youtube-studio",
                        content_account_id=content_account_id,
                    )
                    query = parse_qs(urlparse(start.authorization_url).query)
                    callback = service.complete_callback(
                        "youtube-studio",
                        code=query["code"][0],
                        state=query["state"][0],
                        error=None,
                    )

                stored = read_intelligence_account_config(app.state.settings, account_id=content_account_id)
                self.assertEqual(start.content_account_id, content_account_id)
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored["youtube_oauth_account_id"], callback.account_id)
                self.assertEqual(
                    callback.connection.metadata.get("content_account_id"),
                    content_account_id,
                )
                self.assertTrue(
                    any(entry["event"] == "oauth_content_account_mapped" for entry in callback.connection.audit_log)
                )
            finally:
                app.state.database.dispose()

    def test_invalid_grant_marks_connection_for_reconnect_and_stops_refresh_retries(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-invalid-grant-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.oauth_enabled = True
                settings.oauth_google_client_id = "123456789012-invalid-grant.apps.googleusercontent.com"
                token_store = OAuthTokenStore(settings)
                settings.oauth_google_client_secret_ref = token_store.vault.store_secret(
                    "test-google-client-secret-not-a-real-secret"
                )[1]
                token_ref = token_store.store_token_bundle(
                    OAuthTokenBundle(
                        access_token="expired-access-token",
                        refresh_token="refresh-token-for-test",
                        token_type="Bearer",
                        expires_in=3600,
                        scope="https://www.googleapis.com/auth/youtube.readonly",
                    )
                )
                with app.state.database.session_scope() as session:
                    connection = OAuthConnection(
                        connector_key="youtube-studio",
                        display_name="YouTube Studio",
                        status="connected",
                        scopes_json=["https://www.googleapis.com/auth/youtube.readonly"],
                        account_id=None,
                        token_ref=token_ref,
                        code_verifier_ref=None,
                        state_hash=None,
                        metadata_json={"token_expires_at": "2000-01-01T00:00:00+00:00"},
                        audit_log_json=[],
                    )
                    session.add(connection)
                    session.flush()
                    connection_id = connection.id
                    runtime = OAuthConnectionRuntimeService(session, settings)
                    descriptor = OAuthProviderDescriptor(
                        connector_key="youtube-studio",
                        display_name="YouTube Studio",
                        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
                        token_url="https://oauth2.googleapis.com/token",
                        redirect_uri="http://127.0.0.1:8000/oauth/youtube-studio/callback",
                        scopes=("https://www.googleapis.com/auth/youtube.readonly",),
                        supports_pkce=True,
                        enabled=True,
                        setup_warnings=(),
                        mode="real_provider",
                        client_id=settings.oauth_google_client_id,
                        client_secret_ref=settings.oauth_google_client_secret_ref,
                        integration_key="youtube",
                    )
                    response = httpx.Response(
                        400,
                        json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
                        request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
                    )
                    with patch("atnos.services.oauth_connection_runtime_service.httpx.post", return_value=response) as post:
                        with self.assertRaisesRegex(RuntimeError, "invalid_grant"):
                            runtime.load_token_bundle(
                                connection,
                                descriptor=descriptor,
                                force_refresh=True,
                            )
                        self.assertEqual(connection.status, "error")
                        self.assertEqual(connection.metadata_json["oauth_refresh_failure_code"], "invalid_grant")
                        self.assertTrue(connection.metadata_json["oauth_reconnect_required"])
                        self.assertIn("Połącz konto ponownie", connection.last_error or "")
                        self.assertEqual(post.call_count, 1)

                        health = runtime.health_check(connection_id)
                        self.assertFalse(health.ok)
                        self.assertEqual(health.status, "reconnect_required")
                        self.assertEqual(health.message, connection.last_error)

                        self.assertIsNone(runtime.refresh_if_needed(connection_id, force_refresh=True))
                        self.assertEqual(post.call_count, 1)
            finally:
                app.state.database.dispose()

    def test_fake_oauth_flow_connects_refreshes_and_disconnects_supported_connectors(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                app.state.settings.oauth_enabled = True
                bootstrap_test_app(app)
                database = app.state.database

                with database.session_scope() as session:
                    service = OAuthService(session, app.state.settings)
                    runtime = OAuthConnectionRuntimeService(session, app.state.settings)

                    for connector_key in ("youtube-studio", "google-calendar", "gmail", "github"):
                        start = service.start_connection(connector_key)
                        query = parse_qs(urlparse(start.authorization_url).query)
                        callback = service.complete_callback(
                            connector_key,
                            code=query["code"][0],
                            state=query["state"][0],
                            error=None,
                        )
                        self.assertEqual(callback.status, "connected")
                        self.assertIsNotNone(callback.account_id)

                        bundle = runtime.refresh_if_needed(callback.connection.id, force_refresh=True)
                        self.assertIsNotNone(bundle)
                        self.assertTrue(bundle.access_token.startswith("fake-access-"))

                        disconnected = service.disconnect(callback.connection.id)
                        self.assertIsNotNone(disconnected)
                        assert disconnected is not None
                        self.assertEqual(disconnected.status, "disconnected")
            finally:
                app.state.database.dispose()

    def test_youtube_runtime_config_stores_secret_in_vault_and_persists_env_ref(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-config-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.post(
                        "/oauth/youtube-studio/runtime-config",
                        json={
                            "client_id": "123456789012-test.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["connector"]["configured"])
                self.assertIn(str(payload["client_secret_ref"]).split(":", 1)[0], {"aes_256_gcm", "os_keyring"})
                self.assertTrue(payload["secret_stored"])
                env_text = app.state.settings.runtime_env_file.read_text(encoding="utf-8")
                self.assertIn("OAUTH_ENABLED=true", env_text)
                self.assertIn("OAUTH_GOOGLE_CLIENT_ID=123456789012-test.apps.googleusercontent.com", env_text)
                self.assertIn(f"OAUTH_GOOGLE_CLIENT_SECRET_REF={payload['client_secret_ref']}", env_text)
                self.assertNotIn("test-google-client-secret-not-a-real-secret", env_text)
                vault_bytes = b"".join(path.read_bytes() for path in app.state.settings.account_secret_vault_dir.glob("*.bin"))
                self.assertNotIn(b"test-google-client-secret-not-a-real-secret", vault_bytes)
            finally:
                app.state.database.dispose()

    def test_youtube_runtime_config_rejects_google_password_as_client_secret(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-config-password-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.post(
                        "/oauth/youtube-studio/runtime-config",
                        json={
                            "client_id": "123456789012-test.apps.googleusercontent.com",
                            "client_secret": "ExampleUserPassword-NotAClientSecret!",
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )
                self.assertEqual(response.status_code, 400)
                self.assertIn("Do not enter a Google/YouTube password", response.text)
                self.assertNotIn("ExampleUserPassword-NotAClientSecret", response.text)
            finally:
                app.state.database.dispose()

    def test_youtube_client_secret_import_configures_runtime_without_leaking_secret(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-import-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-import.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.post(
                        "/oauth/youtube-studio/import-client-secret",
                        json={
                            "client_secret_path": str(client_secret_path),
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["connector"]["configured"])
                self.assertEqual(payload["client_id"], "123456789012-import.apps.googleusercontent.com")
                self.assertEqual(payload["authorization_start_endpoint"], "POST /oauth/youtube-studio/start")
                self.assertNotIn("test-google-client-secret-not-a-real-secret", response.text)
                env_text = app.state.settings.runtime_env_file.read_text(encoding="utf-8")
                self.assertIn("OAUTH_ENABLED=true", env_text)
                self.assertIn("OAUTH_GOOGLE_CLIENT_ID=123456789012-import.apps.googleusercontent.com", env_text)
                self.assertIn("OAUTH_GOOGLE_CLIENT_SECRET_REF=", env_text)
                self.assertIn("YOUTUBE_CLIENT_SECRET_FILE=", env_text)
                self.assertNotIn("test-google-client-secret-not-a-real-secret", env_text)
                vault_bytes = b"".join(path.read_bytes() for path in app.state.settings.account_secret_vault_dir.glob("*.bin"))
                self.assertNotIn(b"test-google-client-secret-not-a-real-secret", vault_bytes)
            finally:
                app.state.database.dispose()

    def test_youtube_client_secret_json_import_configures_runtime_without_manual_file_copy(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-json-import-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            app.state.settings.youtube_client_secret_file = client_secret_path
            client_secret_payload = {
                "installed": {
                    "client_id": "123456789012-json.apps.googleusercontent.com",
                    "client_secret": "test-google-client-secret-not-a-real-secret",
                    "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                }
            }
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.post(
                        "/oauth/youtube-studio/import-client-secret-json",
                        json={
                            "client_secret_json": client_secret_payload,
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["connector"]["configured"])
                self.assertEqual(payload["client_id"], "123456789012-json.apps.googleusercontent.com")
                self.assertEqual(payload["client_secret_path"], str(client_secret_path.resolve()))
                self.assertTrue(client_secret_path.exists())
                self.assertNotIn("test-google-client-secret-not-a-real-secret", response.text)
                env_text = app.state.settings.runtime_env_file.read_text(encoding="utf-8")
                self.assertIn("OAUTH_ENABLED=true", env_text)
                self.assertIn("OAUTH_GOOGLE_CLIENT_ID=123456789012-json.apps.googleusercontent.com", env_text)
                self.assertIn("OAUTH_GOOGLE_CLIENT_SECRET_REF=", env_text)
                self.assertNotIn("test-google-client-secret-not-a-real-secret", env_text)
                vault_bytes = b"".join(path.read_bytes() for path in app.state.settings.account_secret_vault_dir.glob("*.bin"))
                self.assertNotIn(b"test-google-client-secret-not-a-real-secret", vault_bytes)
            finally:
                app.state.database.dispose()

    def test_youtube_client_secret_json_import_rejects_documentation_placeholder_without_leaking_secret(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-json-placeholder-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = root / "client_secret.json"
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.post(
                        "/oauth/youtube-studio/import-client-secret-json",
                        json={
                            "client_secret_json": {
                                "web": {
                                    "client_id": "asdfjasdljfasdkjf",
                                    "client_secret": "1912308409123890",
                                    "redirect_uris": ["https://www.example.com/oauth2callback"],
                                }
                            }
                        },
                    )

                self.assertEqual(response.status_code, 400)
                self.assertIn("documentation/sample", response.text)
                self.assertNotIn("1912308409123890", response.text)
                self.assertFalse((root / "client_secret.json").exists())
            finally:
                app.state.database.dispose()

    def test_youtube_client_secret_import_enables_http_oauth_start(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-import-start-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-start.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    import_response = client.post(
                        "/oauth/youtube-studio/import-client-secret",
                        json={
                            "client_secret_path": str(client_secret_path),
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )
                    start_response = client.post("/oauth/youtube-studio/start")

                self.assertEqual(import_response.status_code, 200)
                self.assertEqual(start_response.status_code, 200)
                payload = start_response.json()
                self.assertEqual(payload["connector_key"], "youtube-studio")
                self.assertIn("authorization_url", payload)
                self.assertIn("https://www.googleapis.com/auth/youtube.upload", payload["scopes"])
                self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", payload["scopes"])
                self.assertNotIn("test-google-client-secret-not-a-real-secret", start_response.text)
            finally:
                app.state.database.dispose()

    def test_youtube_oauth_start_requests_offline_refresh_token(self) -> None:
        descriptor = OAuthProviderDescriptor(
            connector_key="youtube-studio",
            display_name="YouTube Studio",
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="http://127.0.0.1:8000/oauth/youtube-studio/callback",
            scopes=(
                "openid",
                "email",
                "profile",
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly",
            ),
            supports_pkce=True,
            enabled=True,
            setup_warnings=(),
            mode="real_provider",
            client_id="123456789012-offline.apps.googleusercontent.com",
            client_secret_ref="aes_256_gcm:test",
            integration_key="youtube",
        )

        service = OAuthService.__new__(OAuthService)
        authorization_url = service._build_authorization_url(
            descriptor=descriptor,
            state="state-token",
            code_challenge="pkce-challenge",
            connection_id=1,
        )
        query = parse_qs(urlparse(authorization_url).query)
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["include_granted_scopes"], ["true"])
        self.assertIn("https://www.googleapis.com/auth/youtube.upload", query["scope"][0])
        self.assertNotIn("client_secret", query)

    def test_youtube_client_secret_status_reports_missing_file_and_next_steps(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-status-missing-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            missing_path = root / "client_secret.json"
            app.state.settings.youtube_client_secret_file = missing_path
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.get("/oauth/youtube-studio/client-secret-status")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertFalse(payload["exists"])
                self.assertFalse(payload["import_ready"])
                self.assertEqual(payload["path"], str(missing_path.resolve()))
                self.assertIn("POST /oauth/youtube-studio/import-client-secret", payload["import_endpoint"])
                self.assertIn("POST /oauth/youtube-studio/start", payload["authorization_start_endpoint"])
                self.assertEqual(payload["connect_endpoint"], "POST /oauth/youtube-studio/connect")
                self.assertEqual(payload["google_cloud_credentials_url"], "https://console.cloud.google.com/apis/credentials")
                self.assertEqual(payload["youtube_data_api_url"], "https://console.cloud.google.com/apis/library/youtube.googleapis.com")
                self.assertEqual(
                    payload["youtube_analytics_api_url"],
                    "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
                )
                self.assertIn("https://www.googleapis.com/auth/youtube.upload", payload["required_scopes"])
                self.assertTrue(any("client_secret" in step for step in payload["next_steps"]))
                self.assertTrue(any(str(missing_path.resolve()) in step for step in payload["next_steps"]))
            finally:
                app.state.database.dispose()

    def test_youtube_client_secret_status_rejects_documentation_placeholder_without_leaking_secret(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-status-placeholder-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "web": {
                            "client_id": "asdfjasdljfasdkjf",
                            "client_secret": "1912308409123890",
                            "redirect_uris": ["https://www.example.com/oauth2callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    status_response = client.get("/oauth/youtube-studio/client-secret-status")
                    import_response = client.post(
                        "/oauth/youtube-studio/import-client-secret",
                        json={
                            "client_secret_path": str(client_secret_path),
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )

                self.assertEqual(status_response.status_code, 200)
                payload = status_response.json()
                self.assertTrue(payload["exists"])
                self.assertTrue(payload["valid_json"])
                self.assertTrue(payload["likely_placeholder"])
                self.assertFalse(payload["import_ready"])
                self.assertNotIn("1912308409123890", status_response.text)
                self.assertEqual(import_response.status_code, 400)
                self.assertIn("documentation/sample", import_response.text)
                self.assertNotIn("1912308409123890", import_response.text)
            finally:
                app.state.database.dispose()

    def test_youtube_client_secret_status_accepts_installed_loopback_redirect(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-status-loopback-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-loopback.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://localhost"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.get("/oauth/youtube-studio/client-secret-status")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["import_ready"])
                self.assertTrue(payload["redirect_uri_registered"])
                self.assertNotIn("Add it in Google Cloud", " ".join(payload["warnings"]))
                self.assertNotIn("test-google-client-secret-not-a-real-secret", response.text)
            finally:
                app.state.database.dispose()

    def test_youtube_connect_returns_diagnostics_when_client_secret_is_missing(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-connect-missing-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = root / "client_secret.json"
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.post(
                        "/oauth/youtube-studio/connect",
                        json={
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "needs_client_secret")
                self.assertFalse(payload["imported"])
                self.assertFalse(payload["oauth_configured"])
                self.assertIsNone(payload["authorization_url"])
                self.assertFalse(payload["client_secret_status"]["exists"])
                self.assertTrue(payload["client_secret_status"]["next_steps"])
            finally:
                app.state.database.dispose()

    def test_youtube_oauth_requires_client_secret_ref_not_only_client_id(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-no-secret-ref-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            app.state.settings.app_env = "production"
            app.state.settings.oauth_enabled = True
            app.state.settings.oauth_google_client_id = "123456789012-no-secret.apps.googleusercontent.com"
            app.state.settings.oauth_google_client_secret_ref = None
            app.state.settings.youtube_client_secret_file = root / "missing-client-secret.json"
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    connectors_response = client.get("/oauth/connectors")
                    start_response = client.post("/oauth/youtube-studio/start")

                self.assertEqual(connectors_response.status_code, 200)
                youtube = next(item for item in connectors_response.json() if item["connector_key"] == "youtube-studio")
                self.assertFalse(youtube["configured"])
                self.assertEqual(youtube["status"], "not_configured")
                self.assertTrue(any("client secret ref" in warning.lower() for warning in youtube["setup_warnings"]))
                self.assertEqual(start_response.status_code, 400)
                self.assertEqual(start_response.json()["detail"], "OAuth provider is not configured.")
            finally:
                app.state.database.dispose()

    def test_youtube_oauth_status_combines_client_secret_token_and_scope_readiness(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-readiness-missing-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = root / "client_secret.json"
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.get("/oauth/youtube-studio/status")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertFalse(payload["ready"])
                self.assertFalse(payload["client_secret_ready"])
                self.assertFalse(payload["token_ready"])
                self.assertFalse(payload["client_secret_status"]["exists"])
                self.assertEqual(payload["token_store"]["service"], "atnos.services.oauth_token_store.OAuthTokenStore")
                self.assertEqual(payload["token_store"]["required_backend"], "aes_256_gcm")
                self.assertTrue(payload["token_store"]["encrypted"])
                self.assertEqual(
                    payload["upload_contract"]["service"],
                    "atnos.services.youtube_connector_service.YouTubeConnectorService.upload_short",
                )
                self.assertTrue(payload["upload_contract"]["resumable"])
                self.assertEqual(payload["upload_contract"]["chunk_size_bytes"], 1024 * 1024)
                self.assertEqual(payload["upload_contract"]["max_attempts"], 3)
                self.assertIn("https://www.googleapis.com/auth/youtube.upload", payload["required_scopes"])
                self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", payload["missing_scopes"])
                self.assertTrue(any("client_secret" in step for step in payload["next_steps"]))
            finally:
                app.state.database.dispose()

    def test_youtube_setup_guide_separates_api_key_from_oauth_requirements(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-setup-guide-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = root / "client_secret.json"
            app.state.settings.youtube_data_api_key = ""
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.get("/oauth/youtube-studio/setup-guide")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "needs_setup")
                self.assertFalse(payload["ready"])
                self.assertFalse(payload["can_start_oauth"])
                self.assertFalse(payload["can_collect_public_trends"])
                self.assertIn("youtube_data_api_key_for_public_trends", payload["missing"])
                self.assertIn("google_oauth_client_secret_json", payload["missing"])
                self.assertIn("youtube_oauth_token", payload["missing"])
                self.assertEqual(payload["endpoints"]["configure_youtube_data_api_key"], "POST /api/intelligence/youtube-data-api-key")
                self.assertEqual(payload["endpoints"]["configure_oauth_client_id_secret"], "POST /oauth/youtube-studio/runtime-config")
                self.assertEqual(payload["endpoints"]["import_client_secret_json"], "POST /oauth/youtube-studio/import-client-secret-json")
                self.assertEqual(payload["endpoints"]["start_oauth"], "POST /oauth/youtube-studio/start")
                self.assertFalse(payload["youtube_data_api_key"]["configured"])
                self.assertIn("private_upload", payload["youtube_data_api_key"]["not_enough_for"])
                self.assertFalse(payload["oauth"]["ready"])
                self.assertIn("private_upload", payload["oauth"]["required_for"])
                self.assertIn("Google Cloud downloaded client_secret.json", payload["oauth"]["accepted_credentials"])
                self.assertIn("Google/YouTube login password", payload["oauth"]["not_accepted_credentials"])
                self.assertIn("documentation sample client_secret.json", payload["oauth"]["not_accepted_credentials"])
                self.assertTrue(any("runtime-config" in step for step in payload["ordered_steps"]))
                self.assertTrue(any("YOUTUBE" not in step or "POST" in step for step in payload["ordered_steps"]))
                self.assertNotIn("client_secret", payload["google_cloud"]["credentials_url"])
            finally:
                app.state.database.dispose()

    def test_youtube_oauth_status_reports_missing_scopes_for_existing_connection(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-readiness-scopes-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-status.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.oauth_enabled = True
                settings.oauth_google_client_id = "123456789012-status.apps.googleusercontent.com"
                token_store = OAuthTokenStore(settings)
                settings.oauth_google_client_secret_ref = token_store.vault.store_secret("test-google-client-secret-not-a-real-secret")[1]
                token_ref = token_store.store_token_bundle(
                    OAuthTokenBundle(
                        access_token="access-token",
                        refresh_token=None,
                        token_type="Bearer",
                        expires_in=3600,
                        scope="https://www.googleapis.com/auth/youtube.readonly",
                    )
                )
                with app.state.database.session_scope() as session:
                    session.add(
                        OAuthConnection(
                            connector_key="youtube-studio",
                            display_name="YouTube Studio",
                            status="connected",
                            scopes_json=["https://www.googleapis.com/auth/youtube.readonly"],
                            account_id=None,
                            token_ref=token_ref,
                            code_verifier_ref=None,
                            state_hash=None,
                            metadata_json={},
                            audit_log_json=[],
                        )
                    )
                    session.flush()

                with TestClient(app) as client:
                    response = client.get("/oauth/youtube-studio/status")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertFalse(payload["ready"])
                self.assertTrue(payload["client_secret_ready"])
                self.assertFalse(payload["token_ready"])
                self.assertIn("https://www.googleapis.com/auth/youtube.upload", payload["missing_scopes"])
                self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", payload["missing_scopes"])
                self.assertTrue(any("scope" in step.lower() for step in payload["next_steps"]))
                self.assertNotIn("test-google-client-secret-not-a-real-secret", response.text)
            finally:
                app.state.database.dispose()

    def test_youtube_oauth_status_accepts_google_userinfo_scope_aliases(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-readiness-oidc-alias-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-alias.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.oauth_enabled = True
                settings.oauth_google_client_id = "123456789012-alias.apps.googleusercontent.com"
                token_store = OAuthTokenStore(settings)
                settings.oauth_google_client_secret_ref = token_store.vault.store_secret("test-google-client-secret-not-a-real-secret")[1]
                token_ref = token_store.store_token_bundle(
                    OAuthTokenBundle(
                        access_token="access-token",
                        refresh_token="refresh-token",
                        token_type="Bearer",
                        expires_in=3600,
                        scope=(
                            "openid "
                            "https://www.googleapis.com/auth/userinfo.email "
                            "https://www.googleapis.com/auth/userinfo.profile "
                            "https://www.googleapis.com/auth/youtube.upload "
                            "https://www.googleapis.com/auth/youtube.readonly "
                            "https://www.googleapis.com/auth/yt-analytics.readonly"
                        ),
                    )
                )
                with app.state.database.session_scope() as session:
                    session.add(
                        OAuthConnection(
                            connector_key="youtube-studio",
                            display_name="YouTube Studio",
                            status="connected",
                            scopes_json=[
                                "openid",
                                "email",
                                "profile",
                                "https://www.googleapis.com/auth/youtube.upload",
                                "https://www.googleapis.com/auth/youtube.readonly",
                                "https://www.googleapis.com/auth/yt-analytics.readonly",
                            ],
                            account_id=None,
                            token_ref=token_ref,
                            code_verifier_ref=None,
                            state_hash=None,
                            metadata_json={},
                            audit_log_json=[],
                        )
                    )
                    session.flush()

                with TestClient(app) as client:
                    response = client.get("/oauth/youtube-studio/status")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ready"])
                self.assertTrue(payload["token_ready"])
                self.assertTrue(payload["private_upload_ready"])
                self.assertFalse(payload["captions_api_ready"])
                self.assertFalse(payload["channel_management_ready"])
                self.assertEqual(payload["missing_scopes"], [])
                self.assertEqual(payload["optional_missing_scopes"], [YOUTUBE_OPTIONAL_CAPTION_SCOPE])
                self.assertTrue(payload["reconnect_recommended"])
                self.assertEqual(payload["reconnect_endpoint"], "POST /oauth/youtube-studio/connect")
                self.assertIn("https://www.googleapis.com/auth/userinfo.email", payload["granted_scopes"])
                self.assertNotIn("test-google-client-secret-not-a-real-secret", response.text)
            finally:
                app.state.database.dispose()

    def test_youtube_oauth_status_reports_full_channel_management_when_optional_caption_scope_is_granted(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-readiness-caption-scope-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-captions.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.oauth_enabled = True
                settings.oauth_google_client_id = "123456789012-captions.apps.googleusercontent.com"
                token_store = OAuthTokenStore(settings)
                settings.oauth_google_client_secret_ref = token_store.vault.store_secret("test-google-client-secret-not-a-real-secret")[1]
                granted = (
                    "openid "
                    "email "
                    "profile "
                    "https://www.googleapis.com/auth/youtube.upload "
                    "https://www.googleapis.com/auth/youtube.readonly "
                    "https://www.googleapis.com/auth/yt-analytics.readonly "
                    f"{YOUTUBE_OPTIONAL_CAPTION_SCOPE}"
                )
                token_ref = token_store.store_token_bundle(
                    OAuthTokenBundle(
                        access_token="access-token",
                        refresh_token="refresh-token",
                        token_type="Bearer",
                        expires_in=3600,
                        scope=granted,
                    )
                )
                with app.state.database.session_scope() as session:
                    session.add(
                        OAuthConnection(
                            connector_key="youtube-studio",
                            display_name="YouTube Studio",
                            status="connected",
                            scopes_json=granted.split(),
                            account_id=None,
                            token_ref=token_ref,
                            code_verifier_ref=None,
                            state_hash=None,
                            metadata_json={},
                            audit_log_json=[],
                        )
                    )
                    session.flush()

                with TestClient(app) as client:
                    response = client.get("/oauth/youtube-studio/status")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ready"])
                self.assertTrue(payload["private_upload_ready"])
                self.assertTrue(payload["captions_api_ready"])
                self.assertTrue(payload["channel_management_ready"])
                self.assertEqual(payload["missing_scopes"], [])
                self.assertEqual(payload["optional_missing_scopes"], [])
                self.assertFalse(payload["reconnect_recommended"])
                self.assertIsNone(payload["reconnect_reason"])
                self.assertIn(YOUTUBE_OPTIONAL_CAPTION_SCOPE, payload["granted_scopes"])
                self.assertNotIn("test-google-client-secret-not-a-real-secret", response.text)
            finally:
                app.state.database.dispose()

    def test_youtube_connect_imports_client_secret_and_starts_authorization_without_leaking_secret(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-connect-start-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-connect.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                bootstrap_test_app(app)
                with TestClient(app) as client:
                    response = client.post(
                        "/oauth/youtube-studio/connect",
                        json={
                            "redirect_base_url": "http://127.0.0.1:8000",
                            "enable_oauth": True,
                        },
                    )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "authorization_started")
                self.assertTrue(payload["imported"])
                self.assertTrue(payload["oauth_configured"])
                self.assertIn("authorization_url", payload)
                self.assertIn("https://www.googleapis.com/auth/youtube.upload", payload["scopes"])
                self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", payload["scopes"])
                self.assertEqual(payload["connector"]["connector_key"], "youtube-studio")
                self.assertNotIn("test-google-client-secret-not-a-real-secret", response.text)
                env_text = app.state.settings.runtime_env_file.read_text(encoding="utf-8")
                self.assertNotIn("test-google-client-secret-not-a-real-secret", env_text)
            finally:
                app.state.database.dispose()

    def test_startup_auto_imports_youtube_client_secret_when_file_exists(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-auto-import-") as tmpdir:
            root = Path(tmpdir)
            client_secret_path = root / "client_secret.json"
            client_secret_path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "123456789012-auto.apps.googleusercontent.com",
                            "client_secret": "test-google-client-secret-not-a-real-secret",
                            "redirect_uris": ["http://127.0.0.1:8000/oauth/youtube-studio/callback"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            app = create_test_app(root)
            app.state.settings.youtube_client_secret_file = client_secret_path
            try:
                with TestClient(app) as client:
                    response = client.get("/oauth/connectors")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                youtube = next(item for item in payload if item["connector_key"] == "youtube-studio")
                self.assertTrue(youtube["configured"])
                self.assertEqual(getattr(app.state, "google_oauth_auto_import")["imported"], True)
                env_text = app.state.settings.runtime_env_file.read_text(encoding="utf-8")
                self.assertIn("OAUTH_ENABLED=true", env_text)
                self.assertIn("OAUTH_GOOGLE_CLIENT_ID=123456789012-auto.apps.googleusercontent.com", env_text)
                self.assertNotIn("test-google-client-secret-not-a-real-secret", env_text)
                vault_bytes = b"".join(path.read_bytes() for path in app.state.settings.account_secret_vault_dir.glob("*.bin"))
                self.assertNotIn(b"test-google-client-secret-not-a-real-secret", vault_bytes)
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
