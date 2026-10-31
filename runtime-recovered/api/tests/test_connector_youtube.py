from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from googleapiclient.errors import HttpError

from atnos.db.models.oauth_connection import OAuthConnection
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore
from atnos.services.youtube_connector_service import YouTubeConnectorService, YouTubeQuotaExceededError

from .test_support import bootstrap_test_app, create_test_app


class YouTubeConnectorServiceTest(unittest.TestCase):
    def test_comment_preview_requires_approval_and_status_surfaces_quota_warning(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                database = app.state.database

                with database.session_scope() as session:
                    account = AccountCredentialsService(session, settings).create_account(
                        label="YouTube account",
                        provider_key="youtube",
                        account_identifier="youtube:owner",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=["read_metadata", "read_content", "confirmed_write_later"],
                        linked_integration_key="youtube",
                        linked_project_id=None,
                        notes=None,
                    )
                    service = YouTubeConnectorService(session, settings)
                    preview = service.create_comment_preview(
                        account_id=account.id,
                        project_id=None,
                        video_id="abc123",
                        text="Dobra robota.",
                    )
                    self.assertEqual(preview.action_type, "message_preview")
                    self.assertEqual(preview.preview_payload["approval_level"], "single")

                    status = service.get_status()
                    self.assertTrue(
                        any(
                            "kontroli zgodności YouTube API" in warning
                            for warning in status.warnings
                        )
                    )
            finally:
                app.state.database.dispose()

    def test_status_requires_upload_readonly_and_analytics_scopes(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-scopes-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.oauth_enabled = True
                settings.oauth_google_client_id = "google-client-id"
                secret_ref = OAuthTokenStore(settings).vault.store_secret("google-client-secret")[1]
                settings.oauth_google_client_secret_ref = secret_ref
                token_ref = OAuthTokenStore(settings).store_token_bundle(
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

                    status = YouTubeConnectorService(session, settings).get_status()

                self.assertTrue(status.connected)
                self.assertFalse(status.ready)
                warning_text = " ".join(status.warnings)
                self.assertIn("youtube.upload", warning_text)
                self.assertIn("yt-analytics.readonly", warning_text)
            finally:
                app.state.database.dispose()

    def test_status_requires_refresh_token_for_real_youtube_autonomy(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-refresh-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.app_env = "production"
                settings.oauth_enabled = True
                settings.oauth_google_client_id = "google-client-id"
                secret_ref = OAuthTokenStore(settings).vault.store_secret("google-client-secret")[1]
                settings.oauth_google_client_secret_ref = secret_ref
                required_scopes = [
                    "openid",
                    "email",
                    "profile",
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/yt-analytics.readonly",
                ]
                token_ref = OAuthTokenStore(settings).store_token_bundle(
                    OAuthTokenBundle(
                        access_token="access-token",
                        refresh_token=None,
                        token_type="Bearer",
                        expires_in=3600,
                        scope=" ".join(required_scopes),
                    )
                )

                with app.state.database.session_scope() as session:
                    session.add(
                        OAuthConnection(
                            connector_key="youtube-studio",
                            display_name="YouTube Studio",
                            status="connected",
                            scopes_json=required_scopes,
                            account_id=None,
                            token_ref=token_ref,
                            code_verifier_ref=None,
                            state_hash=None,
                            metadata_json={},
                            audit_log_json=[],
                        )
                    )
                    session.flush()

                    status = YouTubeConnectorService(session, settings).get_status()

                self.assertTrue(status.connected)
                self.assertFalse(status.ready)
                self.assertIn("refresh_token", " ".join(status.warnings))
            finally:
                app.state.database.dispose()

    def test_status_surfaces_revoked_youtube_refresh_token_without_leaking_secret(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-invalid-grant-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
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
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/yt-analytics.readonly",
                ]
                token_ref = OAuthTokenStore(settings).store_token_bundle(
                    OAuthTokenBundle(
                        access_token="access-token",
                        refresh_token="sensitive-refresh-token",
                        token_type="Bearer",
                        expires_in=3600,
                        scope=" ".join(required_scopes),
                    )
                )

                with app.state.database.session_scope() as session:
                    session.add(
                        OAuthConnection(
                            connector_key="youtube-studio",
                            display_name="YouTube Studio",
                            status="connected",
                            scopes_json=required_scopes,
                            account_id=None,
                            token_ref=token_ref,
                            code_verifier_ref=None,
                            state_hash=None,
                            metadata_json={},
                            audit_log_json=[],
                        )
                    )
                    session.flush()

                    with patch(
                        "atnos.services.oauth_connection_runtime_service.OAuthConnectionRuntimeService.load_token_bundle",
                        side_effect=RuntimeError("invalid_grant: Token has been expired or revoked."),
                    ):
                        status = YouTubeConnectorService(session, settings).get_status()

                warning_text = " ".join(status.warnings)
                self.assertFalse(status.connected)
                self.assertFalse(status.ready)
                self.assertIn("invalid_grant", warning_text)
                self.assertIn("wygasła albo została unieważniona", warning_text)
                self.assertNotIn("sensitive-refresh-token", warning_text)
                self.assertNotIn("access-token", warning_text)
            finally:
                app.state.database.dispose()

    def test_status_can_target_specific_oauth_account_instead_of_latest_connection(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-multi-account-") as tmpdir:
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
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/yt-analytics.readonly",
                ]
                token_store = OAuthTokenStore(settings)

                with app.state.database.session_scope() as session:
                    accounts = AccountCredentialsService(session, settings)
                    first = accounts.create_account(
                        label="YouTube first",
                        provider_key="youtube",
                        account_identifier="first@example.com",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=["confirmed_write_later"],
                        linked_integration_key="youtube",
                        linked_project_id=None,
                        notes=None,
                    )
                    latest = accounts.create_account(
                        label="YouTube latest",
                        provider_key="youtube",
                        account_identifier="latest@example.com",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=["confirmed_write_later"],
                        linked_integration_key="youtube",
                        linked_project_id=None,
                        notes=None,
                    )
                    for account, access_token in ((first, "first-token"), (latest, "latest-token")):
                        session.add(
                            OAuthConnection(
                                connector_key="youtube-studio",
                                display_name="YouTube Studio",
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

                    default_status = YouTubeConnectorService(session, settings).get_status()
                    targeted_status = YouTubeConnectorService(session, settings, account_id=first.id).get_status()

                self.assertEqual(default_status.account_id, latest.id)
                self.assertEqual(targeted_status.account_id, first.id)
                self.assertTrue(targeted_status.ready)
            finally:
                app.state.database.dispose()

    def test_upload_short_uses_resumable_chunks_and_configured_youtube_metadata(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-upload-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                settings.youtube_category_id = "28"
                settings.youtube_default_language = "en"
                settings.youtube_default_audio_language = "en"
                video_path = root / "short.mp4"
                video_path.write_bytes(b"fake mp4")
                youtube = _FakeYouTube([_FakeUploadRequest([(_UploadProgress(4), None), (None, {"id": "yt-1"})])])

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service.fetch_video_status = lambda video_id: {"video_id": video_id, "found": True}
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}

                result = service.upload_short(
                    video_path=video_path,
                    title="Tytuł testowy",
                    description="Opis",
                    tags=["shorts"],
                    thumbnail_path=None,
                    captions_path=None,
                    privacy_status="private",
                )

                self.assertEqual(result["video_id"], "yt-1")
                self.assertEqual(result["upload_progress"]["percent"], 100.0)
                self.assertEqual(result["upload_progress"]["total_bytes"], video_path.stat().st_size)
                media_body = youtube.videos_resource.last_insert_media_body
                self.assertEqual(media_body._mimetype, "video/mp4")
                self.assertEqual(media_body._chunksize, 1 * 1024 * 1024)
                self.assertTrue(media_body._resumable)
                body = youtube.videos_resource.last_insert_body
                self.assertEqual(body["snippet"]["categoryId"], "28")
                self.assertEqual(body["snippet"]["defaultLanguage"], "en")
                self.assertEqual(body["snippet"]["defaultAudioLanguage"], "en")
                self.assertEqual(youtube.all_upload_requests[0].next_chunk_calls, 2)

            finally:
                app.state.database.dispose()

    def test_upload_short_can_override_youtube_language_per_video(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-upload-language-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                settings.youtube_default_language = "en"
                settings.youtube_default_audio_language = "en"
                video_path = root / "short.mp4"
                captions_path = root / "captions.srt"
                video_path.write_bytes(b"fake mp4")
                captions_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nSave this\n", encoding="utf-8")
                youtube = _FakeYouTube([_FakeUploadRequest([(None, {"id": "yt-en"})])])

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service.fetch_video_status = lambda video_id: {"video_id": video_id, "found": True}
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}
                service._has_granted_scope = lambda scope: True

                service.upload_short(
                    video_path=video_path,
                    title="English short",
                    description="English description",
                    tags=["shorts"],
                    thumbnail_path=None,
                    captions_path=captions_path,
                    privacy_status="private",
                    default_language="en",
                    default_audio_language="en",
                )

                body = youtube.videos_resource.last_insert_body
                self.assertEqual(body["snippet"]["defaultLanguage"], "en")
                self.assertEqual(body["snippet"]["defaultAudioLanguage"], "en")
                self.assertEqual(youtube.captions_resource.last_body["snippet"]["language"], "en")
            finally:
                app.state.database.dispose()

    def test_upload_short_uploads_thumbnail_and_srt_captions(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-assets-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                settings.youtube_default_language = "en"
                video_path = root / "short.mp4"
                thumbnail_path = root / "thumb.png"
                captions_path = root / "captions.srt"
                video_path.write_bytes(b"fake mp4")
                thumbnail_path.write_bytes(b"fake png")
                captions_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
                youtube = _FakeYouTube([_FakeUploadRequest([(None, {"id": "yt-assets"})])])

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service.fetch_video_status = lambda video_id: {"video_id": video_id, "found": True}
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}

                result = service.upload_short(
                    video_path=video_path,
                    title="Assets",
                    description="Description",
                    tags=["shorts"],
                    thumbnail_path=thumbnail_path,
                    captions_path=captions_path,
                    privacy_status="private",
                )

                self.assertTrue(result["thumbnail_uploaded"])
                self.assertTrue(result["captions_uploaded"])
                self.assertEqual(youtube.thumbnails_resource.last_video_id, "yt-assets")
                self.assertEqual(youtube.captions_resource.last_body["snippet"]["videoId"], "yt-assets")
                self.assertEqual(youtube.captions_resource.last_body["snippet"]["language"], "en")
                self.assertEqual(youtube.captions_resource.last_media_body._mimetype, "application/x-subrip")
            finally:
                app.state.database.dispose()

    def test_upload_short_skips_captions_without_force_ssl_scope(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-captions-scope-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                video_path = root / "short.mp4"
                captions_path = root / "captions.srt"
                video_path.write_bytes(b"fake mp4")
                captions_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nSiema\n", encoding="utf-8")
                youtube = _FakeYouTube([_FakeUploadRequest([(None, {"id": "yt-no-captions"})])])

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service.fetch_video_status = lambda video_id: {"video_id": video_id, "found": True}
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}
                service._has_granted_scope = lambda scope: False

                result = service.upload_short(
                    video_path=video_path,
                    title="Captions scope",
                    description="Opis",
                    tags=["shorts"],
                    thumbnail_path=None,
                    captions_path=captions_path,
                    privacy_status="private",
                )

                self.assertFalse(result["captions_uploaded"])
                self.assertIsNone(youtube.captions_resource.last_body)
                self.assertTrue(any("Captions upload skipped" in warning for warning in result["warnings"]))
            finally:
                app.state.database.dispose()

    def test_upload_short_retries_transient_resumable_upload_error(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-retry-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                video_path = root / "short.mp4"
                video_path.write_bytes(b"fake mp4")
                youtube = _FakeYouTube(
                    [
                        _FakeUploadRequest([_http_error(503)]),
                        _FakeUploadRequest([(None, {"id": "yt-retry"})]),
                    ]
                )

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service.fetch_video_status = lambda video_id: {"video_id": video_id, "found": True}
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}

                from atnos.services import youtube_connector_service as module

                original_sleep = module.time.sleep
                module.time.sleep = lambda _seconds: None
                try:
                    result = service.upload_short(
                        video_path=video_path,
                        title="Retry",
                        description="Opis",
                        tags=[],
                        thumbnail_path=None,
                        captions_path=None,
                        privacy_status="private",
                    )
                finally:
                    module.time.sleep = original_sleep

                self.assertEqual(result["video_id"], "yt-retry")
                self.assertEqual(result["upload_progress"]["attempts"], 2)
                self.assertEqual(len(youtube.all_upload_requests), 2)
            finally:
                app.state.database.dispose()

    def test_upload_short_raises_quota_exceeded_without_retrying_until_tomorrow(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-quota-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                video_path = root / "short.mp4"
                video_path.write_bytes(b"fake mp4")
                upload_request = _FakeUploadRequest([_http_error(403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}')])
                youtube = _FakeYouTube([upload_request])

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service.fetch_video_status = lambda video_id: {"video_id": video_id, "found": True}
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}

                with self.assertRaises(YouTubeQuotaExceededError):
                    service.upload_short(
                        video_path=video_path,
                        title="Quota",
                        description="Opis",
                        tags=[],
                        thumbnail_path=None,
                        captions_path=None,
                        privacy_status="private",
                    )

                self.assertEqual(len(youtube.all_upload_requests), 1)
                self.assertEqual(upload_request.next_chunk_calls, 1)
            finally:
                app.state.database.dispose()

    def test_fetch_analytics_requests_full_feedback_metrics_and_maps_row(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-analytics-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service._require_connection = lambda: object()
                service._descriptor = lambda: object()
                service._credentials_for_connection = lambda connection, descriptor: object()
                fake_analytics = _FakeAnalytics()

                with patch("atnos.services.youtube_connector_service.build", return_value=fake_analytics):
                    result = service.fetch_analytics("yt-analytics-1")

                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["views"], 1500)
                self.assertEqual(result["estimated_minutes_watched"], 420.5)
                self.assertEqual(result["average_view_duration"], 38.0)
                self.assertEqual(result["average_view_percentage"], 81.2)
                self.assertEqual(result["subscribers_gained"], 12)
                self.assertEqual(result["likes"], 88)
                self.assertEqual(result["comments"], 9)
                self.assertEqual(result["shares"], 7)
                self.assertEqual(result["annotation_click_through_rate"], 1.2)
                self.assertEqual(result["card_click_rate"], 2.4)
                metrics = fake_analytics.reports_resource.last_query["metrics"]
                self.assertIn("estimatedMinutesWatched", metrics)
                self.assertIn("subscribersGained", metrics)
                self.assertIn("cardClickRate", metrics)
            finally:
                app.state.database.dispose()

    def test_fetch_analytics_falls_back_to_data_api_when_analytics_api_disabled(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-analytics-fallback-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service._require_connection = lambda: object()
                service._descriptor = lambda: object()
                service._credentials_for_connection = lambda connection, descriptor: object()
                current_video = {
                    "id": "yt-fallback-1",
                    "snippet": {"title": "Fallback Short"},
                    "statistics": {"viewCount": "2400", "likeCount": "180", "commentCount": "12"},
                }
                youtube = _FakeYouTube([], current_video=current_video)
                service._youtube_client = lambda: youtube
                fake_analytics = _FakeAnalytics(error=_http_error(
                    403,
                    b'{"error":{"errors":[{"reason":"accessNotConfigured"}],"message":"YouTube Analytics API has not been used in project 265469749111 before or it is disabled. Enable it."}}',
                ))

                with patch("atnos.services.youtube_connector_service.build", return_value=fake_analytics):
                    result = service.fetch_analytics("yt-fallback-1")

                self.assertEqual(result["status"], "ready_degraded")
                self.assertEqual(result["fallback_source"], "youtube_data_api_v3")
                self.assertEqual(result["views"], 2400)
                self.assertEqual(result["likes"], 180)
                self.assertEqual(result["comments"], 12)
                self.assertIsNone(result["average_view_percentage"])
                self.assertIn("average_view_percentage", result["metrics_missing"])
                self.assertEqual(youtube.videos_resource.last_list_kwargs["part"], "statistics,snippet")
                self.assertEqual(youtube.videos_resource.last_list_kwargs["id"], "yt-fallback-1")
            finally:
                app.state.database.dispose()

    def test_promote_private_video_publishes_public_only_with_approval_and_flag(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-promote-public-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                settings.allow_public_upload = True
                youtube = _FakeYouTube([], current_video=_private_video_item("yt-public"))

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}

                result = service.promote_private_video(video_id="yt-public", approved=True)

                self.assertEqual(result["status"], "published_public")
                self.assertEqual(result["privacy_status"], "public")
                self.assertEqual(result["youtube_url"], "https://www.youtube.com/watch?v=yt-public")
                body = youtube.videos_resource.last_update_body
                self.assertEqual(body["id"], "yt-public")
                self.assertEqual(body["status"]["privacyStatus"], "public")
                self.assertNotIn("publishAt", body["status"])
            finally:
                app.state.database.dispose()

    def test_promote_private_video_schedules_future_publish_at(self) -> None:
        with TemporaryDirectory(prefix="atnos-youtube-promote-scheduled-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                settings = app.state.settings
                settings.allow_public_upload = True
                youtube = _FakeYouTube([], current_video=_private_video_item("yt-schedule"))
                future = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat()

                service = YouTubeConnectorService.__new__(YouTubeConnectorService)
                service.settings = settings
                service._youtube_client = lambda: youtube
                service._quota_diagnostics = lambda: {"quota_surface": "youtube_data_api_v3"}

                result = service.promote_private_video(video_id="yt-schedule", approved=True, publish_at=future)

                self.assertEqual(result["status"], "scheduled_public")
                self.assertEqual(result["privacy_status"], "private")
                self.assertTrue(result["publish_at"].endswith("Z"))
                body = youtube.videos_resource.last_update_body
                self.assertEqual(body["status"]["privacyStatus"], "private")
                self.assertEqual(body["status"]["publishAt"], result["publish_at"])
            finally:
                app.state.database.dispose()


class _UploadProgress:
    def __init__(self, resumable_progress: int) -> None:
        self.resumable_progress = resumable_progress


class _FakeUploadRequest:
    def __init__(self, chunks: list[tuple[object | None, dict | None] | BaseException]) -> None:
        self.chunks = list(chunks)
        self.next_chunk_calls = 0

    def next_chunk(self, num_retries: int = 0):
        self.next_chunk_calls += 1
        item = self.chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def execute(self):
        raise AssertionError("upload_short must use next_chunk() for resumable uploads")


class _FakeVideosResource:
    def __init__(self, upload_requests: list[_FakeUploadRequest], current_video: dict | None = None) -> None:
        self.upload_requests = upload_requests
        self.last_insert_body = None
        self.last_insert_media_body = None
        self.current_video = current_video
        self.last_list_kwargs = None
        self.last_update_body = None

    def insert(self, **kwargs):
        self.last_insert_body = kwargs["body"]
        self.last_insert_media_body = kwargs["media_body"]
        return self.upload_requests.pop(0)

    def list(self, **kwargs):
        self.last_list_kwargs = kwargs
        return _ExecutePayload({"items": [self.current_video] if self.current_video else []})

    def update(self, **kwargs):
        self.last_update_body = kwargs["body"]
        status = dict(self.last_update_body.get("status") or {})
        return _ExecutePayload({"id": self.last_update_body.get("id"), "status": status})


class _FakeYouTube:
    def __init__(self, upload_requests: list[_FakeUploadRequest], current_video: dict | None = None) -> None:
        self.all_upload_requests = list(upload_requests)
        self.videos_resource = _FakeVideosResource(upload_requests, current_video=current_video)
        self.thumbnails_resource = _FakeThumbnailsResource()
        self.captions_resource = _FakeCaptionsResource()

    def videos(self):
        return self.videos_resource

    def thumbnails(self):
        return self.thumbnails_resource

    def captions(self):
        return self.captions_resource


class _ExecuteOk:
    def execute(self):
        return {"status": "ok"}


class _ExecutePayload:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self):
        return self.payload


def _private_video_item(video_id: str) -> dict:
    return {
        "id": video_id,
        "snippet": {
            "title": "Private Short",
            "description": "Description",
            "tags": ["shorts"],
            "categoryId": "28",
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }


class _FakeThumbnailsResource:
    def __init__(self) -> None:
        self.last_video_id = None
        self.last_media_body = None

    def set(self, **kwargs):
        self.last_video_id = kwargs["videoId"]
        self.last_media_body = kwargs["media_body"]
        return _ExecuteOk()


class _FakeCaptionsResource:
    def __init__(self) -> None:
        self.last_body = None
        self.last_media_body = None

    def insert(self, **kwargs):
        self.last_body = kwargs["body"]
        self.last_media_body = kwargs["media_body"]
        return _ExecuteOk()


class _FakeAnalyticsReports:
    def __init__(self, error: BaseException | None = None) -> None:
        self.last_query = None
        self.error = error

    def query(self, **kwargs):
        self.last_query = kwargs
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        return {"rows": [[1500, 420.5, 38.0, 81.2, 12, 88, 9, 7, 1.2, 2.4]]}


class _FakeAnalytics:
    def __init__(self, error: BaseException | None = None) -> None:
        self.reports_resource = _FakeAnalyticsReports(error=error)

    def reports(self):
        return self.reports_resource


class _HttpResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "test"

    def getheaders(self):
        return {}


def _http_error(status: int, content: bytes = b"transient") -> HttpError:
    return HttpError(_HttpResponse(status), content)


if __name__ == "__main__":
    unittest.main()
