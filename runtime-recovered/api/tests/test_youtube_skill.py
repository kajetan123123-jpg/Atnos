from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest
from unittest.mock import patch

from atnos.core.config import Settings
from atnos.skills.youtube_skill import detects_youtube_private_upload_request
from youtube.youtube_analytics import get_video_analytics
from youtube.youtube_upload import YouTubeUploadError, prepare_upload, upload_private_short

_ENV_KEYS = (
    "SHORTS_OUTPUT_DIR",
    "YOUTUBE_UPLOAD_PRIVACY",
    "ALLOW_PUBLIC_UPLOAD",
    "REQUIRE_UPLOAD_APPROVAL",
    "YOUTUBE_CLIENT_SECRET_FILE",
)


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeVideos:
    def insert(self, **kwargs):
        self.kwargs = kwargs
        return _FakeExecute({"id": "abc123"})


class _FakeThumbnails:
    def set(self, **kwargs):
        return _FakeExecute({"ok": True})


class _FakeYouTube:
    def __init__(self):
        self._videos = _FakeVideos()
        self._thumbnails = _FakeThumbnails()

    def videos(self):
        return self._videos

    def thumbnails(self):
        return self._thumbnails


class _FakeReports:
    def query(self, **kwargs):
        self.kwargs = kwargs
        return _FakeExecute({"rows": [[10, 22, 3.4, 4, 1, 2, 0]]})


class _FakeAnalytics:
    def __init__(self):
        self._reports = _FakeReports()

    def reports(self):
        return self._reports


class YouTubeSkillTest(unittest.TestCase):
    def test_detects_private_upload_request(self) -> None:
        self.assertTrue(detects_youtube_private_upload_request("Wrzuć tego shorta prywatnie na YouTube"))

    def test_prepare_upload_finds_latest_final_mp4_and_defaults_private(self) -> None:
        settings, run_dir = self._settings_with_short()

        preview = prepare_upload(settings=settings)

        self.assertEqual(preview.metadata.privacy, "private")
        self.assertEqual(preview.local_file, (run_dir / "final.mp4").resolve())
        self.assertTrue(preview.requires_approval)

    def test_prepare_upload_uses_youtube_release_package_metadata(self) -> None:
        settings, run_dir = self._settings_with_short()
        (run_dir / "youtube_upload_package.json").write_text(
            json.dumps(
                {
                    "title": "Lokalne AI bez chmury",
                    "description": "Opis z pakietu produkcyjnego.",
                    "tags": ["Atnos", "AI", "Shorts"],
                    "privacy": "private",
                    "requires_upload_approval": True,
                }
            ),
            encoding="utf-8",
        )

        preview = prepare_upload(settings=settings)

        self.assertEqual(preview.metadata.title, "Lokalne AI bez chmury")
        self.assertEqual(preview.metadata.description, "Opis z pakietu produkcyjnego.")
        self.assertEqual(preview.metadata.tags, ("Atnos", "AI", "Shorts"))

    def test_prepare_upload_uses_title_from_production_report(self) -> None:
        settings, run_dir = self._settings_with_short()
        (run_dir / "production_report.md").write_text(
            "# Production Report\n\n- Title: Lokalne AI jako przewaga twórcy\n- Niche: Local AI\n",
            encoding="utf-8",
        )

        preview = prepare_upload(settings=settings)

        self.assertEqual(preview.metadata.title, "Lokalne AI jako przewaga twórcy")

    def test_public_upload_is_blocked_when_disabled(self) -> None:
        settings, _ = self._settings_with_short()

        with self.assertRaises(YouTubeUploadError):
            prepare_upload(settings=settings, privacy="public")

    def test_upload_private_short_logs_result(self) -> None:
        settings, _ = self._settings_with_short()
        preview = prepare_upload(settings=settings)

        result = upload_private_short(settings=settings, preview=preview, approved=True, youtube_service=_FakeYouTube())

        self.assertEqual(result.video_id, "abc123")
        self.assertEqual(result.privacy, "private")
        log_text = (settings.data_dir / "logs" / "youtube_uploads.jsonl").read_text(encoding="utf-8")
        self.assertIn("abc123", log_text)

    def test_analytics_logs_expected_metrics(self) -> None:
        settings, _ = self._settings_with_short()

        result = get_video_analytics(
            "abc123",
            "2026-05-01",
            "2026-05-17",
            settings=settings,
            analytics_service=_FakeAnalytics(),
        )

        self.assertEqual(result.metrics["views"], 10)
        self.assertEqual(result.metrics["subscribersLost"], 0)
        log_text = (settings.data_dir / "logs" / "youtube_analytics.jsonl").read_text(encoding="utf-8")
        self.assertIn("abc123", log_text)

    def _settings_with_short(self) -> tuple[Settings, Path]:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        run_dir = root / "data" / "shorts" / "20260517-test"
        run_dir.mkdir(parents=True)
        (run_dir / "final.mp4").write_bytes(b"fake-video")
        (run_dir / "thumbnail.png").write_bytes(b"fake-png")
        (run_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")
        (run_dir / "render_qa.json").write_text(
            json.dumps({"has_audio": True, "width": 1080, "height": 1920, "duration_sec": 31.5}),
            encoding="utf-8",
        )
        (run_dir / "production_report.md").write_text("- topic: lokalne AI\n\nRaport", encoding="utf-8")
        env_path = root / ".env"
        env_path.write_text(
            "\n".join(
                (
                    f"SHORTS_OUTPUT_DIR={root / 'data' / 'shorts'}",
                    "YOUTUBE_UPLOAD_PRIVACY=private",
                    "ALLOW_PUBLIC_UPLOAD=false",
                    "REQUIRE_UPLOAD_APPROVAL=true",
                    f"YOUTUBE_CLIENT_SECRET_FILE={root / 'client_secret.json'}",
                )
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {key: "" for key in _ENV_KEYS}, clear=False):
            for key in _ENV_KEYS:
                os.environ.pop(key, None)
            settings = Settings(app_env="test", app_root=root, data_dir=root / "data", _env_file=env_path)
        return settings, run_dir


if __name__ == "__main__":
    unittest.main()
