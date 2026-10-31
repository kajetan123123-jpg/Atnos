from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atnos.core.config import Settings
from youtube.youtube_auth import YOUTUBE_SCOPES, youtube_token_path
from youtube.youtube_readiness import YouTubeReadinessService, check_youtube_readiness


def make_settings(root: Path) -> Settings:
    return Settings(
        app_env="test",
        app_root=root,
        data_dir=root / "data",
        youtube_client_secret_file=root / "client_secret.json",
    )


def write_client_secret(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "fake-client-id.apps.googleusercontent.com",
                    "client_secret": "fake-client-secret",
                    "redirect_uris": ["http://localhost"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )


def write_token(settings: Settings, scopes: list[str] | None = None) -> Path:
    token_path = youtube_token_path(settings)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "fake-client-id.apps.googleusercontent.com",
                "client_secret": "fake-client-secret",
                "scopes": scopes if scopes is not None else list(YOUTUBE_SCOPES),
            }
        ),
        encoding="utf-8",
    )
    return token_path


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeChannels:
    def list(self, **kwargs):
        self.kwargs = kwargs
        return _FakeExecute({"items": [{"id": "UC123", "snippet": {"title": "Atnos Test Channel"}}]})


class _FakeYouTube:
    def __init__(self):
        self._channels = _FakeChannels()

    def channels(self):
        return self._channels


class YouTubeReadinessTest(unittest.TestCase):
    def test_missing_client_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            result = YouTubeReadinessService(settings).check()

            self.assertEqual(result.status, "missing_client_secret")
            self.assertIn("client_secret.json", result.message)

    def test_missing_token_after_client_secret_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            write_client_secret(root / "client_secret.json")

            result = YouTubeReadinessService(settings).check()

            self.assertEqual(result.status, "missing_token")
            self.assertEqual(result.token_path, youtube_token_path(settings))

    def test_invalid_token_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            write_client_secret(root / "client_secret.json")
            token_path = youtube_token_path(settings)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text("{bad-json", encoding="utf-8")

            result = YouTubeReadinessService(settings).check()

            self.assertEqual(result.status, "invalid_token")
            self.assertIn("Invalid OAuth token JSON", result.message)

    def test_invalid_token_missing_required_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            write_client_secret(root / "client_secret.json")
            write_token(settings, scopes=["https://www.googleapis.com/auth/youtube.readonly"])

            result = YouTubeReadinessService(settings).check()

            self.assertEqual(result.status, "invalid_token")
            self.assertIn("https://www.googleapis.com/auth/youtube.upload", result.missing_scopes)
            self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", result.missing_scopes)

    def test_configured_without_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            write_client_secret(root / "client_secret.json")
            write_token(settings)

            result = check_youtube_readiness(settings, run_probe=False)

            self.assertEqual(result.status, "configured")
            self.assertEqual(set(result.granted_scopes), set(YOUTUBE_SCOPES))

    def test_ready_with_readonly_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            write_client_secret(root / "client_secret.json")
            write_token(settings)

            result = YouTubeReadinessService(settings).check(youtube_service=_FakeYouTube())

            self.assertEqual(result.status, "ready")
            self.assertEqual(result.channel_id, "UC123")
            self.assertEqual(result.channel_title, "Atnos Test Channel")
            self.assertTrue(any("private uploads" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
