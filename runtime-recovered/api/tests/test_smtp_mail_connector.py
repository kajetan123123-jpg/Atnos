from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import smtplib
import unittest
from unittest.mock import patch

from atnos.services.smtp_mail_connector_service import SmtpMailConnectorService
from atnos.services.trusted_auto_service import TrustedAutoService

from .test_support import bootstrap_test_app, create_test_app


class SmtpMailConnectorServiceTest(unittest.TestCase):
    def test_status_reports_missing_account(self) -> None:
        with TemporaryDirectory(prefix="atnos-smtp-mail-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    status = SmtpMailConnectorService(session, app.state.settings).get_status()

                self.assertFalse(status.configured)
                self.assertFalse(status.live_send_enabled)
                self.assertEqual(status.probe_status, "not_configured")
            finally:
                app.state.database.dispose()

    def test_probe_reports_ready_when_smtp_login_succeeds(self) -> None:
        with TemporaryDirectory(prefix="atnos-smtp-mail-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    self._create_int_mail_account(session, app)
                    with patch.object(SmtpMailConnectorService, "_login", return_value=None):
                        status = SmtpMailConnectorService(session, app.state.settings).probe()

                self.assertTrue(status.configured)
                self.assertTrue(status.live_login_enabled)
                self.assertTrue(status.live_send_enabled)
                self.assertEqual(status.probe_status, "ready")
            finally:
                app.state.database.dispose()

    def test_probe_reports_auth_failed_without_leaking_secret(self) -> None:
        with TemporaryDirectory(prefix="atnos-smtp-mail-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    self._create_int_mail_account(session, app)
                    error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Error: authentication failed")
                    with patch.object(SmtpMailConnectorService, "_login", side_effect=error):
                        status = SmtpMailConnectorService(session, app.state.settings).probe()

                self.assertFalse(status.live_send_enabled)
                self.assertEqual(status.probe_status, "auth_failed")
                self.assertIn("authentication failed", status.message)
                self.assertNotIn("stored-account-secret", status.message)
            finally:
                app.state.database.dispose()

    def test_send_test_message_attempts_starttls_after_ssl_auth_failure(self) -> None:
        with TemporaryDirectory(prefix="atnos-smtp-mail-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    self._create_int_mail_account(session, app)
                    error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Error: authentication failed")
                    with patch.object(SmtpMailConnectorService, "_send", side_effect=[error, None]) as send_mock:
                        result = SmtpMailConnectorService(session, app.state.settings).send_test_message(
                            to="account-22af0cc8@example.invalid",
                            subject="Atnos test",
                            body="siema",
                        )

                self.assertTrue(result["sent"])
                self.assertEqual(result["mode"], "starttls")
                self.assertEqual([call.kwargs["mode"] for call in send_mock.call_args_list], ["ssl", "starttls"])
                self.assertEqual(result["attempts"][0]["status"], "auth_failed")
                self.assertEqual(result["attempts"][1]["status"], "sent")
            finally:
                app.state.database.dispose()

    def test_send_test_message_preserves_polish_subject_and_body(self) -> None:
        with TemporaryDirectory(prefix="atnos-smtp-mail-") as tmpdir:
            app = create_test_app(Path(tmpdir))
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    self._create_int_mail_account(session, app)
                    with patch.object(SmtpMailConnectorService, "_send", return_value=None) as send_mock:
                        result = SmtpMailConnectorService(session, app.state.settings).send_test_message(
                            to="account-22af0cc8@example.invalid",
                            subject="Atnos - załączniki i próbki",
                            body="Cześć operator,\nZałączniki są gotowe. Możesz sprawdzić jakość głosu.",
                        )

                self.assertTrue(result["sent"])
                message = send_mock.call_args.args[2]
                self.assertEqual(message["Subject"], "Atnos - załączniki i próbki")
                self.assertEqual(message.get_content_charset(), "utf-8")
                self.assertEqual(
                    message.get_content(),
                    "Cześć operator,\nZałączniki są gotowe. Możesz sprawdzić jakość głosu.\n",
                )
            finally:
                app.state.database.dispose()

    @staticmethod
    def _create_int_mail_account(session, app) -> None:
        TrustedAutoService(session, app.state.settings).upsert_account(
            account_id=None,
            label="INT mail",
            provider_key="int-mail",
            account_identifier="account-a580edab@example.invalid",
            secret_type="password",
            secret_value="stored-account-secret",
            linked_project_id=None,
            linked_integration_key="int_mail_browser_session",
            notes=None,
            allowed_capabilities=["trusted_login", "browser_workflow", "confirmed_write_later"],
            login_identifier="account-a580edab@example.invalid",
            password_value=None,
            totp_value=None,
            backup_codes_value=None,
            recovery_email=None,
            recovery_email_secret_value=None,
            connector_mapping_json={"login_url": "https://int.pl/"},
        )


if __name__ == "__main__":
    unittest.main()
