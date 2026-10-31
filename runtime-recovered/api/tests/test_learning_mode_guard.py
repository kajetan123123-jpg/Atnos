from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from atnos.services.learning_mode_guard import (
    LEARNING_MODE_BLOCK_REASON,
    classify_blocked_http_action,
    youtube_write_block_response,
)
from atnos.services.youtube_connector_service import YouTubeConnectorService

from .test_support import create_test_app


def _enable_learning_mode(settings) -> None:
    settings.learning_mode = True
    settings.youtube_write_block = True
    settings.youtube_upload_enabled = False
    settings.youtube_edit_enabled = False
    settings.youtube_delete_enabled = False
    settings.youtube_comment_enabled = False
    settings.youtube_playlist_write_enabled = False
    settings.youtube_scope_escalation_allowed = False
    settings.external_ai_transfer_allowed = False
    settings.cloud_llm_allowed = False
    settings.local_processing_only = True


def test_guard_returns_required_dry_run_contract() -> None:
    with TemporaryDirectory(prefix="atnos-learning-guard-") as tmpdir:
        app = create_test_app(Path(tmpdir))
        _enable_learning_mode(app.state.settings)
        payload = youtube_write_block_response(app.state.settings)

        assert payload is not None
        assert payload["mode"] == "learning"
        assert payload["allowed"] is False
        assert payload["action"] == "youtube_write_operation"
        assert payload["reason"] == LEARNING_MODE_BLOCK_REASON
        assert "+" in payload["timestamp"]


def test_guard_classifies_every_required_write_surface() -> None:
    with TemporaryDirectory(prefix="atnos-learning-actions-") as tmpdir:
        app = create_test_app(Path(tmpdir))
        settings = app.state.settings
        _enable_learning_mode(settings)
        paths = (
            ("POST", "/api/youtube/upload"),
            ("PATCH", "/api/youtube/videos/abc/metadata"),
            ("DELETE", "/api/youtube/videos/abc/delete"),
            ("POST", "/api/youtube/videos/abc/thumbnail"),
            ("POST", "/api/youtube/playlists"),
            ("POST", "/api/youtube/comments"),
            ("POST", "/api/youtube/subscriptions"),
            ("POST", "/api/youtube/rating"),
            ("POST", "/oauth/youtube-studio/start"),
            ("POST", "/oauth/youtube-studio/runtime-config"),
            ("GET", "/oauth/youtube-studio/callback"),
            ("POST", "/api/production/jobs/job-1/upload-private"),
            ("POST", "/api/production/jobs/job-1/schedule-public"),
            ("POST", "/runs/run-1/approve-publish"),
            ("POST", "/api/creator-cockpit/external-publish"),
            ("POST", "/api/production/money-machine/run-publication"),
        )
        assert all(classify_blocked_http_action(method, path, settings) for method, path in paths)
        assert classify_blocked_http_action("GET", "/api/intelligence/youtube-signals", settings) is None
        assert classify_blocked_http_action("POST", "/oauth/gmail/start", settings) is None
        assert classify_blocked_http_action("GET", "/oauth/gmail/callback", settings) is None


def test_http_middleware_blocks_oauth_scope_escalation_with_exact_payload() -> None:
    with TemporaryDirectory(prefix="atnos-learning-http-") as tmpdir:
        app = create_test_app(Path(tmpdir))
        _enable_learning_mode(app.state.settings)
        with TestClient(app) as client:
            response = client.post("/oauth/youtube-studio/start", json={})

        assert response.status_code == 423
        assert response.json()["reason"] == LEARNING_MODE_BLOCK_REASON
        assert response.json()["allowed"] is False


def test_direct_connector_calls_cannot_bypass_learning_mode() -> None:
    with TemporaryDirectory(prefix="atnos-learning-direct-") as tmpdir:
        app = create_test_app(Path(tmpdir))
        _enable_learning_mode(app.state.settings)
        service = YouTubeConnectorService.__new__(YouTubeConnectorService)
        service.settings = app.state.settings
        service._youtube_client = lambda: (_ for _ in ()).throw(AssertionError("network client must not be built"))

        video = Path(tmpdir) / "blocked.mp4"
        result = service.upload_short(
            video_path=video,
            title="blocked",
            description="blocked",
            tags=[],
            thumbnail_path=None,
            captions_path=None,
        )
        comment = service.post_comment(video_id="abc", text="blocked")
        promote = service.promote_private_video(video_id="abc", approved=True)

        assert result["allowed"] is False
        assert comment["allowed"] is False
        assert promote["allowed"] is False
