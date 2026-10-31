from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atnos.core.config import Settings
from atnos.services.slack_webhook_notifier import SlackWebhookNotifier


class SlackWebhookNotifierTest(unittest.TestCase):
    def test_notifier_is_noop_when_webhook_is_not_configured(self) -> None:
        with TemporaryDirectory(prefix="atnos-slack-noop-") as tmpdir:
            settings = _settings(Path(tmpdir), webhook_url=None)
            result = SlackWebhookNotifier(settings).notify_pipeline_event(
                {"step": "done", "status": "qa_passed", "job_id": "prod-1", "message": "ready"}
            )

        self.assertEqual(result["status"], "not_configured")

    def test_notifier_sends_final_pipeline_event(self) -> None:
        with TemporaryDirectory(prefix="atnos-slack-send-") as tmpdir:
            settings = _settings(Path(tmpdir), webhook_url="https://hooks.slack.test/services/secret-token")
            captured = {}

            def fake_post(url, *, json, timeout):
                captured["url"] = url
                captured["json"] = json
                captured["timeout"] = timeout
                return _FakeResponse()

            from atnos.services import slack_webhook_notifier as module

            original_post = module.httpx.post
            module.httpx.post = fake_post
            try:
                result = SlackWebhookNotifier(settings).notify_pipeline_event(
                    {
                        "step": "private_upload",
                        "status": "uploaded_private",
                        "job_id": "prod-2",
                        "message": "https://www.youtube.com/watch?v=abc",
                        "timestamp": "2026-06-01T12:00:00+00:00",
                    }
                )
            finally:
                module.httpx.post = original_post

        self.assertEqual(result["status"], "sent")
        self.assertEqual(captured["url"], "https://hooks.slack.test/services/secret-token")
        self.assertEqual(captured["timeout"], 5.0)
        text = captured["json"]["text"]
        self.assertIn("Atnos Content Machine", text)
        self.assertIn("prod-2", text)
        self.assertIn("uploaded_private", text)
        self.assertIn("Wiadomość:", text)
        self.assertNotIn("secret-token", text)

    def test_notifier_skips_non_critical_step_updates(self) -> None:
        with TemporaryDirectory(prefix="atnos-slack-skip-") as tmpdir:
            settings = _settings(Path(tmpdir), webhook_url="https://hooks.slack.test/services/secret-token")
            result = SlackWebhookNotifier(settings).notify_pipeline_event(
                {"step": "script", "status": "running", "job_id": "prod-3", "message": "attempt 1"}
            )

        self.assertEqual(result["status"], "skipped")

    def test_notifier_redacts_webhook_and_tokens_from_error_result(self) -> None:
        with TemporaryDirectory(prefix="atnos-slack-error-") as tmpdir:
            settings = _settings(Path(tmpdir), webhook_url="https://hooks.slack.test/services/secret-token")

            def fake_post(url, *, json, timeout):
                raise RuntimeError(f"failed POST {url}?token=abc123 password=secret Bearer xyz.123")

            from atnos.services import slack_webhook_notifier as module

            original_post = module.httpx.post
            module.httpx.post = fake_post
            try:
                result = SlackWebhookNotifier(settings).notify_pipeline_event(
                    {
                        "step": "private_upload",
                        "status": "uploaded_private",
                        "job_id": "prod-4",
                        "message": "callback client_secret: should-not-leak api_key=also-secret Bearer abc.def",
                    }
                )
            finally:
                module.httpx.post = original_post

        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret-token", result["message"])
        self.assertNotIn("abc123", result["message"])
        self.assertNotIn("password=secret", result["message"])
        self.assertNotIn("xyz.123", result["message"])
        self.assertIn("[redacted-slack-webhook]", result["message"])

    def test_notifier_redacts_secret_values_from_sent_text(self) -> None:
        with TemporaryDirectory(prefix="atnos-slack-redact-text-") as tmpdir:
            settings = _settings(Path(tmpdir), webhook_url="https://hooks.slack.test/services/secret-token")
            captured = {}

            def fake_post(url, *, json, timeout):
                captured["json"] = json
                return _FakeResponse()

            from atnos.services import slack_webhook_notifier as module

            original_post = module.httpx.post
            module.httpx.post = fake_post
            try:
                result = SlackWebhookNotifier(settings).notify_pipeline_event(
                    {
                        "step": "done",
                        "status": "qa_failed",
                        "job_id": "prod-5",
                        "message": "client_secret: raw-secret api_key=raw-key Bearer raw-token",
                    }
                )
            finally:
                module.httpx.post = original_post

        self.assertEqual(result["status"], "sent")
        text = captured["json"]["text"]
        self.assertIn("client_secret=[redacted]", text)
        self.assertIn("api_key=[redacted]", text)
        self.assertIn("Bearer [redacted]", text)
        self.assertNotIn("raw-secret", text)
        self.assertNotIn("raw-key", text)
        self.assertNotIn("raw-token", text)


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


def _settings(root: Path, webhook_url: str | None) -> Settings:
    env_path = root / ".env"
    lines = []
    if webhook_url is not None:
        lines.append(f"SLACK_WEBHOOK_URL={webhook_url}")
    env_path.write_text("\n".join(lines), encoding="utf-8")
    return Settings(app_root=root, data_dir=root / "data", _env_file=env_path)


if __name__ == "__main__":
    unittest.main()
