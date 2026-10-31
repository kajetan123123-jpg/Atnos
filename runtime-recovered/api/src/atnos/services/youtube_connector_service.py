from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.learning_mode_guard import youtube_write_block_response
from atnos.services.oauth_connection_runtime_service import OAuthConnectionRuntimeService
from atnos.services.oauth_provider_registry import YOUTUBE_OPTIONAL_CAPTION_SCOPE, OAuthProviderDescriptor, OAuthProviderRegistry
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore
from atnos.services.runtime_settings_service import RuntimeSettingsService
from youtube.upload_guard import assert_upload_video_file


class YouTubeConnectorError(RuntimeError):
    pass


class YouTubeQuotaExceededError(YouTubeConnectorError):
    pass


_UPLOAD_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
_UPLOAD_MAX_ATTEMPTS = 3
_UPLOAD_RETRY_BASE_DELAY_SECONDS = 2.0
_YOUTUBE_CAPTIONS_SCOPE = YOUTUBE_OPTIONAL_CAPTION_SCOPE
_ANALYTICS_METRICS = (
    "views",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "subscribersGained",
    "likes",
    "comments",
    "shares",
    "annotationClickThroughRate",
    "cardClickRate",
)


@dataclass(frozen=True, slots=True)
class YouTubeConnectionStatus:
    connector_key: str
    display_name: str
    configured: bool
    connected: bool
    ready: bool
    warnings: tuple[str, ...]
    account_id: int | None
    channel_title: str | None = None
    granted_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_key": self.connector_key,
            "display_name": self.display_name,
            "configured": self.configured,
            "connected": self.connected,
            "ready": self.ready,
            "warnings": list(self.warnings),
            "account_id": self.account_id,
            "channel_title": self.channel_title,
            "granted_scopes": list(self.granted_scopes),
        }


class YouTubeConnectorService:
    CONNECTOR_KEY = "youtube-studio"
    VIDEO_UPLOAD_CHUNK_SIZE_BYTES = 1 * 1024 * 1024

    def __init__(self, session: Session, settings: Settings, *, account_id: int | None = None) -> None:
        self.session = session
        self.settings = RuntimeSettingsService(session, settings).build_effective_settings()
        self.account_id = account_id
        self.registry = OAuthProviderRegistry(self.settings)
        self.connections = OAuthConnectionRepository(session)
        self.token_store = OAuthTokenStore(self.settings)
        self.runtime = OAuthConnectionRuntimeService(session, self.settings)
        self.action_broker = AccountActionBrokerService(session, self.settings)

    def get_status(self) -> YouTubeConnectionStatus:
        descriptor = self._descriptor()
        connection = self._latest_connection()
        warnings = list(descriptor.setup_warnings)
        warnings.extend(self._quota_diagnostic_warnings())
        if connection is not None and connection.status != "connected" and connection.last_error:
            safe_error = _safe_oauth_runtime_error(RuntimeError(connection.last_error)).rstrip(".")
            warnings.append(f"YouTube OAuth wymaga ponownego połączenia: {safe_error}.")
        token_bundle = self._load_token_bundle_safe(connection, warnings)
        connected = connection is not None and connection.status == "connected" and token_bundle is not None
        granted_scopes = self._granted_scopes(connection, descriptor, token_bundle)
        missing_scopes = self._missing_required_scopes(descriptor, granted_scopes)
        missing_refresh_token = (
            descriptor.connector_key == self.CONNECTOR_KEY
            and descriptor.mode != "fake_provider"
            and connected
            and (token_bundle is None or not token_bundle.refresh_token)
        )
        if missing_scopes:
            warnings.append(f"YouTube OAuth token is missing required scopes: {', '.join(missing_scopes)}.")
        if connected and _YOUTUBE_CAPTIONS_SCOPE not in {_normalize_oauth_scope(scope) for scope in granted_scopes}:
            warnings.append(
                "Optional YouTube captions API scope is missing; Atnos will rely on burned-in subtitles until YouTube OAuth is reconnected with youtube.force-ssl."
            )
        if missing_refresh_token:
            warnings.append(
                "YouTube OAuth token has no refresh_token. Reconnect with offline access so Atnos can upload and sync analytics after the access token expires."
            )
        return YouTubeConnectionStatus(
            connector_key=descriptor.connector_key,
            display_name=descriptor.display_name,
            configured=descriptor.is_configured or descriptor.mode == "fake_provider",
            connected=connected,
            ready=connected and not missing_scopes and not missing_refresh_token,
            warnings=tuple(dict.fromkeys(warnings)),
            account_id=connection.account_id if connection is not None else None,
            granted_scopes=granted_scopes,
        )

    def probe(self) -> YouTubeConnectionStatus:
        status = self.get_status()
        if not status.ready:
            return status
        channel = self.get_owned_channel()
        title = channel.get("title")
        return YouTubeConnectionStatus(
            connector_key=status.connector_key,
            display_name=status.display_name,
            configured=status.configured,
            connected=status.connected,
            ready=True,
            warnings=status.warnings,
            account_id=status.account_id,
            channel_title=title if isinstance(title, str) else None,
            granted_scopes=status.granted_scopes,
        )

    def get_owned_channel(self) -> dict[str, Any]:
        """Read the OAuth-selected channel without changing YouTube state."""
        status = self.get_status()
        if not status.ready:
            detail = "; ".join(status.warnings) or "YouTube OAuth is not ready."
            raise YouTubeConnectorError(detail)

        youtube = self._youtube_client()
        try:
            response = youtube.channels().list(part="id,snippet", mine=True).execute()
        except HttpError as exc:
            raise YouTubeConnectorError(f"YouTube channel identity probe failed: {exc}") from exc

        items = response.get("items") if isinstance(response, dict) else []
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise YouTubeConnectorError("The active OAuth token did not return a YouTube channel.")

        item = items[0]
        channel_id = str(item.get("id") or "").strip()
        if not channel_id:
            raise YouTubeConnectorError("YouTube returned a channel record without a stable channel id.")
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        custom_url = str(snippet.get("customUrl") or "").strip() or None
        return {
            "channel_id": channel_id,
            "title": str(snippet.get("title") or "").strip() or None,
            "custom_url": custom_url,
            "channel_url": f"https://www.youtube.com/channel/{channel_id}",
            "source": "youtube.channels.list(mine=true)",
        }

    def create_comment_preview(
        self,
        *,
        account_id: int,
        project_id: int | None,
        video_id: str,
        text: str,
    ):
        normalized_video_id = video_id.strip()
        normalized_text = text.strip()
        if not normalized_video_id:
            raise YouTubeConnectorError("video_id is required.")
        if not normalized_text:
            raise YouTubeConnectorError("Comment text is required.")
        return self.action_broker.create_action_draft(
            account_id=account_id,
            integration_key="youtube",
            project_id=project_id,
            action_type="message_preview",
            action_title="Post YouTube comment",
            action_summary="Posting a YouTube comment stays supervised and requires explicit approval.",
            preview_payload={
                "platform": "youtube",
                "action": "post_comment",
                "video_id": normalized_video_id,
                "text": normalized_text,
                "approval_level": "single",
            },
            approval_notes="YouTube comment posting never runs silently in v1.",
            created_by="atnos",
        )

    def upload_short(
        self,
        *,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        thumbnail_path: Path | None,
        captions_path: Path | None,
        privacy_status: str = "private",
        made_for_kids: bool = False,
        default_language: str | None = None,
        default_audio_language: str | None = None,
    ) -> dict[str, Any]:
        blocked = youtube_write_block_response(self.settings)
        if blocked is not None:
            return blocked
        try:
            video_path = assert_upload_video_file(video_path, settings=self.settings, require_run_artifacts=False)
        except ValueError as exc:
            raise YouTubeConnectorError(str(exc)) from exc
        youtube = self._youtube_client()
        snippet_language = _language_or_default(default_language, self.settings.youtube_default_language)
        snippet_audio_language = _language_or_default(
            default_audio_language or default_language,
            self.settings.youtube_default_audio_language or snippet_language,
        )
        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:15],
                "categoryId": self.settings.youtube_category_id,
                "defaultLanguage": snippet_language,
                "defaultAudioLanguage": snippet_audio_language,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        media_uploads: list[Any] = []

        def request_factory():
            media_body = MediaFileUpload(
                str(video_path),
                mimetype="video/mp4",
                chunksize=self.VIDEO_UPLOAD_CHUNK_SIZE_BYTES,
                resumable=True,
            )
            media_uploads.append(media_body)
            return youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media_body,
            )

        try:
            response, upload_progress = self._execute_resumable_upload(
                request_factory=request_factory,
                total_bytes=video_path.stat().st_size,
                operation_name="YouTube video upload",
            )
        finally:
            for media_upload in media_uploads:
                _close_media_upload(media_upload)
        video_id = str(response.get("id") or "").strip()
        if not video_id:
            raise YouTubeConnectorError("YouTube upload returned no video id.")

        warnings: list[str] = []
        thumbnail_uploaded = False
        captions_uploaded = False
        if thumbnail_path is not None and thumbnail_path.exists():
            media_body = MediaFileUpload(str(thumbnail_path), mimetype="image/png")
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=media_body,
                ).execute()
                thumbnail_uploaded = True
            except HttpError as exc:
                warnings.append(f"Thumbnail upload failed: {exc}")
            finally:
                _close_media_upload(media_body)
        if captions_path is not None and captions_path.exists():
            if not self._has_granted_scope(_YOUTUBE_CAPTIONS_SCOPE):
                warnings.append(
                    "Captions upload skipped: missing optional YouTube scope "
                    f"{_YOUTUBE_CAPTIONS_SCOPE}. Burned-in subtitles remain in the video."
                )
            else:
                captions_uploaded = self._upload_captions(
                    youtube=youtube,
                    video_id=video_id,
                    captions_path=captions_path,
                    captions_language=snippet_language,
                    warnings=warnings,
                )

        status = self.fetch_video_status(video_id)
        return {
            "video_id": video_id,
            "privacy_status": privacy_status,
            "warnings": warnings,
            "thumbnail_uploaded": thumbnail_uploaded,
            "captions_uploaded": captions_uploaded,
            "upload_progress": upload_progress,
            "quota_diagnostics": self._quota_diagnostics(),
            "status": status,
        }

    def _upload_captions(
        self,
        *,
        youtube,
        video_id: str,
        captions_path: Path,
        captions_language: str,
        warnings: list[str],
    ) -> bool:
        media_body = MediaFileUpload(str(captions_path), mimetype="application/x-subrip")
        try:
            youtube.captions().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "language": captions_language,
                        "name": "ATNOS captions",
                        "isDraft": False,
                    }
                },
                media_body=media_body,
            ).execute()
            return True
        except HttpError as exc:
            warnings.append(f"Captions upload failed: {exc}")
            return False
        finally:
            _close_media_upload(media_body)

    def _has_granted_scope(self, scope: str) -> bool:
        """Return whether the active OAuth token has a scope; legacy test shims default to permissive."""
        if not hasattr(self, "connections") or not hasattr(self, "token_store") or not hasattr(self, "registry"):
            return True
        connection = self._latest_connection()
        descriptor = self._descriptor()
        token_bundle = self._load_token_bundle_safe(connection, [])
        granted = {_normalize_oauth_scope(str(item)) for item in self._granted_scopes(connection, descriptor, token_bundle)}
        return _normalize_oauth_scope(scope) in granted

    def _execute_resumable_upload(
        self,
        *,
        request_factory,
        total_bytes: int,
        operation_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run a Google API resumable upload with explicit retry and progress reporting."""
        safe_total = max(int(total_bytes or 0), 0)
        last_progress = 0
        for attempt in range(1, _UPLOAD_MAX_ATTEMPTS + 1):
            request = request_factory()
            response: dict[str, Any] | None = None
            try:
                while response is None:
                    status, raw_response = request.next_chunk(num_retries=0)
                    if status is not None:
                        last_progress = max(last_progress, _resumable_progress_bytes(status))
                    if raw_response is not None:
                        if not isinstance(raw_response, dict):
                            raise YouTubeConnectorError(f"{operation_name} returned an invalid response.")
                        response = raw_response
                return response, {
                    "bytes_uploaded": safe_total if safe_total else last_progress,
                    "total_bytes": safe_total,
                    "percent": 100.0,
                    "attempts": attempt,
                }
            except HttpError as exc:
                if _is_quota_exceeded_error(exc):
                    raise YouTubeQuotaExceededError(f"{operation_name} quota exceeded: {exc}") from exc
                if not _is_retryable_upload_error(exc) or attempt >= _UPLOAD_MAX_ATTEMPTS:
                    raise YouTubeConnectorError(f"{operation_name} failed: {exc}") from exc
                time.sleep(_UPLOAD_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))

        raise YouTubeConnectorError(f"{operation_name} failed after {_UPLOAD_MAX_ATTEMPTS} attempts.")

    def post_comment(self, *, video_id: str, text: str) -> dict[str, Any]:
        blocked = youtube_write_block_response(self.settings)
        if blocked is not None:
            return blocked
        youtube = self._youtube_client()
        try:
            response = youtube.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id.strip(),
                        "topLevelComment": {
                            "snippet": {
                                "textOriginal": text.strip(),
                            }
                        },
                    }
                },
            ).execute()
        except HttpError as exc:
            raise YouTubeConnectorError(f"YouTube comment insert failed: {exc}") from exc
        snippet = (response.get("snippet") or {}) if isinstance(response, dict) else {}
        return {
            "status": "posted",
            "comment_thread_id": str(response.get("id") or "").strip() or None,
            "video_id": str((snippet.get("videoId") or video_id)).strip(),
            "text": text.strip(),
        }

    def get_subscribers_count(self, channel_id: str | None = None) -> dict[str, Any]:
        youtube = self._youtube_client()
        params: dict[str, Any] = {"part": "statistics,snippet"}
        if channel_id and channel_id.strip().lower() != "mine":
            params["id"] = channel_id.strip()
        else:
            params["mine"] = True
        response = youtube.channels().list(**params).execute()
        items = response.get("items") or []
        if not items:
            return {
                "status": "missing",
                "channel_id": channel_id.strip() if channel_id else "mine",
                "subscribers_count": None,
                "channel_title": None,
            }
        item = items[0]
        stats = item.get("statistics") or {}
        snippet = item.get("snippet") or {}
        count = stats.get("subscriberCount")
        return {
            "status": "ready",
            "channel_id": str(item.get("id") or channel_id or "mine"),
            "subscribers_count": int(count) if isinstance(count, str) and count.isdigit() else (int(count) if isinstance(count, int) else None),
            "channel_title": str(snippet.get("title") or "").strip() or None,
            "quota_diagnostics": self._quota_diagnostics(),
        }

    def fetch_video_status(self, video_id: str) -> dict[str, Any]:
        youtube = self._youtube_client()
        response = youtube.videos().list(
            part="status,processingDetails,snippet",
            id=video_id,
        ).execute()
        items = response.get("items") or []
        if not items:
            return {"video_id": video_id, "found": False}
        item = items[0]
        return {
            "video_id": video_id,
            "found": True,
            "privacy_status": ((item.get("status") or {}).get("privacyStatus")),
            "upload_status": ((item.get("status") or {}).get("uploadStatus")),
            "processing_status": ((item.get("processingDetails") or {}).get("processingStatus")),
            "title": ((item.get("snippet") or {}).get("title")),
            "channel_id": ((item.get("snippet") or {}).get("channelId")),
            "publish_at": ((item.get("status") or {}).get("publishAt")),
            "rejection_reason": ((item.get("status") or {}).get("rejectionReason")),
        }

    def promote_private_video(
        self,
        *,
        video_id: str,
        approved: bool,
        publish_at: str | None = None,
    ) -> dict[str, Any]:
        """Promote an already uploaded private video to public or scheduled-public."""
        blocked = youtube_write_block_response(self.settings)
        if blocked is not None:
            return blocked
        normalized_video_id = str(video_id or "").strip()
        if not normalized_video_id:
            raise YouTubeConnectorError("video_id is required for publication.")
        if not approved:
            raise YouTubeConnectorError("Explicit approval is required before public publication.")
        if not self.settings.allow_public_upload:
            raise YouTubeConnectorError("ALLOW_PUBLIC_UPLOAD is disabled.")

        youtube = self._youtube_client()
        current = youtube.videos().list(part="snippet,status", id=normalized_video_id).execute()
        items = current.get("items") or []
        if not items:
            raise YouTubeConnectorError(f"YouTube video {normalized_video_id} was not found.")
        item = items[0] if isinstance(items[0], dict) else {}
        snippet = dict(item.get("snippet") or {})
        status = dict(item.get("status") or {})
        current_privacy = str(status.get("privacyStatus") or "").strip().lower()
        if current_privacy and current_privacy != "private":
            raise YouTubeConnectorError(
                f"Only private videos can be promoted by this endpoint; current privacy is {current_privacy}."
            )

        publish_at_iso = _normalize_publish_at(publish_at)
        target_status = {
            "privacyStatus": "private" if publish_at_iso else "public",
            "selfDeclaredMadeForKids": bool(status.get("selfDeclaredMadeForKids", False)),
        }
        if publish_at_iso:
            target_status["publishAt"] = publish_at_iso
        update_body = {
            "id": normalized_video_id,
            "snippet": {
                "title": str(snippet.get("title") or "Atnos Short")[:100],
                "description": str(snippet.get("description") or "")[:5000],
                "tags": list(snippet.get("tags") or [])[:15],
                "categoryId": str(snippet.get("categoryId") or self.settings.youtube_category_id),
                "defaultLanguage": str(snippet.get("defaultLanguage") or self.settings.youtube_default_language),
                "defaultAudioLanguage": str(
                    snippet.get("defaultAudioLanguage") or self.settings.youtube_default_audio_language
                ),
            },
            "status": target_status,
        }
        try:
            response = youtube.videos().update(part="snippet,status", body=update_body).execute()
        except HttpError as exc:
            if _is_quota_exceeded_error(exc):
                raise YouTubeQuotaExceededError(f"YouTube publication quota exceeded: {exc}") from exc
            raise YouTubeConnectorError(f"YouTube publication update failed: {exc}") from exc
        result_status = (response.get("status") or {}) if isinstance(response, dict) else {}
        return {
            "status": "scheduled_public" if publish_at_iso else "published_public",
            "video_id": normalized_video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={normalized_video_id}",
            "publish_at": publish_at_iso,
            "privacy_status": result_status.get("privacyStatus") or target_status["privacyStatus"],
            "youtube_response_status": result_status,
            "quota_diagnostics": self._quota_diagnostics(),
        }

    def list_comment_threads(self, video_id: str, *, max_results: int = 100) -> dict[str, Any]:
        """Public comment threads for one video. Uses the existing Data API client."""
        youtube = self._youtube_client()
        try:
            response = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id.strip(),
                maxResults=max(1, min(int(max_results), 100)),
                textFormat="plainText",
                order="time",
            ).execute()
        except HttpError as exc:
            if _is_quota_exceeded_error(exc):
                raise YouTubeQuotaExceededError(f"YouTube comments quota exceeded: {exc}") from exc
            raise YouTubeConnectorError(f"YouTube comments list failed: {exc}") from exc
        rows = []
        for item in response.get("items") or []:
            snippet = (item.get("snippet") or {}) if isinstance(item, dict) else {}
            top = (snippet.get("topLevelComment") or {}).get("snippet") or {}
            rows.append({
                "comment_id": str(item.get("id") or "").strip(),
                "video_id": str(snippet.get("videoId") or video_id).strip(),
                "text": str(top.get("textOriginal") or top.get("textDisplay") or "").strip(),
                "published_at": str(top.get("publishedAt") or ""),
                "like_count": _int_or_none(top.get("likeCount")),
                "reply_count": _int_or_none(snippet.get("totalReplyCount")),
                "author_channel_id": str(((top.get("authorChannelId") or {}) if isinstance(top.get("authorChannelId"), dict) else {}).get("value") or ""),
            })
        return {"status": "ready", "video_id": video_id, "comments": [row for row in rows if row["comment_id"] and row["text"]]}

    def list_public_channel_videos(self, channel_id: str, *, max_results: int = 15) -> dict[str, Any]:
        """Public uploads for a competitor channel. Missing fields stay absent, not zero."""
        youtube = self._youtube_client()
        try:
            response = youtube.search().list(
                part="snippet",
                channelId=channel_id.strip(),
                type="video",
                order="date",
                maxResults=max(1, min(int(max_results), 25)),
            ).execute()
        except HttpError as exc:
            if _is_quota_exceeded_error(exc):
                raise YouTubeQuotaExceededError(f"YouTube search quota exceeded: {exc}") from exc
            raise YouTubeConnectorError(f"YouTube channel search failed: {exc}") from exc
        videos = []
        for item in response.get("items") or []:
            snippet = item.get("snippet") if isinstance(item, dict) else None
            if not isinstance(snippet, dict):
                continue
            video_id = str((item.get("id") or {}).get("videoId") or "").strip()
            if not video_id:
                continue
            videos.append({
                "video_id": video_id,
                "title": str(snippet.get("title") or "").strip(),
                "description": str(snippet.get("description") or "").strip(),
                "published_at": str(snippet.get("publishedAt") or ""),
                "channel_id": channel_id.strip(),
                "views": None,
                "likes": None,
                "comments": None,
                "duration": None,
            })
        return {"status": "ready", "channel_id": channel_id, "videos": videos, "metrics_status": "UNKNOWN"}

    def fetch_analytics(self, video_id: str) -> dict[str, Any]:
        connection = self._require_connection()
        descriptor = self._descriptor()
        creds = self._credentials_for_connection(connection, descriptor)
        analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        end_date = date.today()
        start_date = end_date - timedelta(days=7)
        try:
            response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date.isoformat(),
                endDate=end_date.isoformat(),
                metrics=",".join(_ANALYTICS_METRICS),
                filters=f"video=={video_id}",
            ).execute()
        except HttpError as exc:
            if _is_youtube_analytics_access_not_configured(exc):
                return self._fetch_analytics_data_api_fallback(video_id, analytics_error=str(exc))
            return {
                "video_id": video_id,
                "status": "unavailable",
                "message": str(exc),
            }
        rows = response.get("rows") or []
        if not rows:
            return {
                "video_id": video_id,
                "status": "pending",
                "message": "YouTube Analytics has no data yet.",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "window_days": 7,
            }
        row = rows[0]
        values = _analytics_row_to_dict(row, _ANALYTICS_METRICS)
        return {
            "video_id": video_id,
            "status": "ready",
            "views": _int_or_none(values.get("views")),
            "estimated_minutes_watched": _float_or_none(values.get("estimatedMinutesWatched")),
            "average_view_duration": _float_or_none(values.get("averageViewDuration")),
            "average_view_percentage": _float_or_none(values.get("averageViewPercentage")),
            "subscribers_gained": _int_or_none(values.get("subscribersGained")),
            "likes": _int_or_none(values.get("likes")),
            "comments": _int_or_none(values.get("comments")),
            "shares": _int_or_none(values.get("shares")),
            "annotation_click_through_rate": _float_or_none(values.get("annotationClickThroughRate")),
            "card_click_rate": _float_or_none(values.get("cardClickRate")),
            "metrics": dict(zip(_ANALYTICS_METRICS, row, strict=False)),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "window_days": 7,
        }

    def _fetch_analytics_data_api_fallback(self, video_id: str, *, analytics_error: str) -> dict[str, Any]:
        """Fetch limited video statistics when YouTube Analytics API is disabled."""
        try:
            youtube = self._youtube_client()
            response = youtube.videos().list(part="statistics,snippet", id=video_id).execute()
        except Exception as exc:
            return {
                "video_id": video_id,
                "status": "unavailable",
                "message": analytics_error,
                "fallback_status": "failed",
                "fallback_error": str(exc),
            }
        items = response.get("items") or []
        if not items:
            return {
                "video_id": video_id,
                "status": "pending",
                "message": "YouTube Data API fallback found no video stats yet.",
                "analytics_api_error": analytics_error,
                "fallback_source": "youtube_data_api_v3",
            }
        item = items[0] if isinstance(items[0], dict) else {}
        stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        return {
            "video_id": video_id,
            "status": "ready_degraded",
            "message": "YouTube Analytics API unavailable; using YouTube Data API statistics fallback without retention metrics.",
            "analytics_api_error": analytics_error,
            "fallback_source": "youtube_data_api_v3",
            "title": str(snippet.get("title") or "").strip() or None,
            "views": _int_or_none(stats.get("viewCount")),
            "likes": _int_or_none(stats.get("likeCount")),
            "comments": _int_or_none(stats.get("commentCount")),
            "shares": None,
            "estimated_minutes_watched": None,
            "average_view_duration": None,
            "average_view_percentage": None,
            "subscribers_gained": None,
            "metrics_available": ["views", "likes", "comments"],
            "metrics_missing": [
                "estimated_minutes_watched",
                "average_view_duration",
                "average_view_percentage",
                "subscribers_gained",
                "shares",
            ],
            "lifetime_totals": True,
            "window_days": None,
        }

    def build_data_client(self):
        return self._youtube_client()

    def build_analytics_client(self):
        connection = self._require_connection()
        descriptor = self._descriptor()
        creds = self._credentials_for_connection(connection, descriptor)
        return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)

    def _youtube_client(self):
        connection = self._require_connection()
        descriptor = self._descriptor()
        creds = self._credentials_for_connection(connection, descriptor)
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def _credentials_for_connection(
        self,
        connection: OAuthConnection,
        descriptor: OAuthProviderDescriptor,
    ) -> Credentials:
        token_bundle = self.token_store.load_token_bundle(connection.token_ref)
        if token_bundle is None or not token_bundle.access_token:
            raise YouTubeConnectorError("Stored YouTube OAuth token is unavailable.")
        credentials = Credentials(
            token=token_bundle.access_token,
            refresh_token=token_bundle.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=descriptor.client_id,
            client_secret=self._resolve_client_secret(descriptor),
            scopes=list(self._granted_scopes(connection, descriptor, token_bundle) or descriptor.scopes),
        )
        return credentials

    def _resolve_client_secret(self, descriptor: OAuthProviderDescriptor) -> str | None:
        if not descriptor.client_secret_ref:
            return None
        return self.token_store.vault.resolve_secret(descriptor.client_secret_ref)

    def _require_connection(self) -> OAuthConnection:
        connection = self._latest_connection()
        if connection is None or connection.status != "connected":
            raise YouTubeConnectorError("Connect YouTube Studio before running this action.")
        return connection

    def _latest_connection(self) -> OAuthConnection | None:
        if self.account_id is not None:
            return self.connections.get_latest_for_connector_account(self.CONNECTOR_KEY, self.account_id)
        return self.connections.get_latest_for_connector(self.CONNECTOR_KEY)

    def _descriptor(self) -> OAuthProviderDescriptor:
        return self.registry.get_connector(self.CONNECTOR_KEY)

    def _load_token_bundle_safe(
        self,
        connection: OAuthConnection | None,
        warnings: list[str],
    ) -> OAuthTokenBundle | None:
        if connection is None or connection.status != "connected":
            return None
        try:
            return self.runtime.load_token_bundle(connection, descriptor=self._descriptor(), refresh_if_needed=True)
        except Exception as exc:
            detail = _safe_oauth_runtime_error(exc)
            warnings.append(
                "Stored YouTube OAuth token bundle is unavailable."
                + (f" Reconnect YouTube OAuth: {detail}." if detail else "")
            )
            return None

    def _quota_diagnostic_warnings(self) -> list[str]:
        return [
            "Nowe, niezweryfikowane projekty Google API mogą wymuszać prywatność uploadów do czasu zakończenia kontroli zgodności YouTube API.",
        ]

    def _quota_diagnostics(self) -> dict[str, Any]:
        return {
            "supervised_publish_only": True,
            "project_verification_warning": "Nowe, niezweryfikowane projekty Google API mogą wymuszać prywatność uploadów do czasu zakończenia kontroli zgodności YouTube API.",
            "quota_surface": "youtube_data_api_v3",
        }

    @staticmethod
    def _granted_scopes(
        connection: OAuthConnection | None,
        descriptor: OAuthProviderDescriptor,
        token_bundle: OAuthTokenBundle | None,
    ) -> tuple[str, ...]:
        if token_bundle and token_bundle.scope:
            scopes = tuple(scope for scope in token_bundle.scope.split() if scope)
            if scopes:
                return scopes
        if connection is not None and connection.scopes_json:
            return tuple(str(scope) for scope in connection.scopes_json if isinstance(scope, str))
        return descriptor.scopes

    @staticmethod
    def _missing_required_scopes(
        descriptor: OAuthProviderDescriptor,
        granted_scopes: tuple[str, ...],
    ) -> tuple[str, ...]:
        if descriptor.connector_key != YouTubeConnectorService.CONNECTOR_KEY:
            return ()
        granted = set(granted_scopes)
        optional = {YOUTUBE_OPTIONAL_CAPTION_SCOPE}
        return tuple(
            scope
            for scope in descriptor.scopes
            if scope.startswith("https://www.googleapis.com/auth/") and scope not in granted and scope not in optional
        )


def _resumable_progress_bytes(status: object) -> int:
    progress = getattr(status, "resumable_progress", 0)
    try:
        return max(int(progress or 0), 0)
    except (TypeError, ValueError):
        return 0


def _safe_oauth_runtime_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    if not text:
        return ""
    lowered = text.casefold()
    if "authorization expired" in lowered or "was revoked" in lowered:
        return "Google OAuth invalid_grant: autoryzacja wygasła albo została unieważniona. Połącz konto ponownie"
    if "client configuration changed" in lowered:
        return "Konfiguracja klienta Google OAuth została zmieniona. Sprawdź klienta OAuth i połącz konto ponownie"
    if "invalid_grant" in lowered:
        return "Google OAuth invalid_grant: autoryzacja wygasła albo została unieważniona. Połącz konto ponownie"
    if "invalid_client" in lowered:
        return "Konfiguracja klienta Google OAuth jest nieprawidłowa. Sprawdź klienta OAuth i połącz konto ponownie"
    if "400 bad request" in lowered:
        return "Google token refresh returned 400"
    if "secret" in lowered or "token" in lowered:
        return "OAuth secret/token storage could not be opened"
    return text[:180]


def _is_retryable_upload_error(exc: HttpError) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) in _UPLOAD_RETRYABLE_HTTP_STATUSES
    except (TypeError, ValueError):
        return False


def _is_quota_exceeded_error(exc: HttpError) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        if int(status) != 403:
            return False
    except (TypeError, ValueError):
        return False
    lowered = _http_error_content(exc)
    return any(marker in lowered for marker in ("quotaexceeded", "dailylimitexceeded", "quota exceeded"))


def _is_youtube_analytics_access_not_configured(exc: HttpError) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        if int(status) != 403:
            return False
    except (TypeError, ValueError):
        return False
    lowered = _http_error_content(exc)
    return (
        "youtube analytics api" in lowered
        and any(marker in lowered for marker in ("accessnotconfigured", "has not been used", "disabled", "enable it"))
    )


def _http_error_content(exc: HttpError) -> str:
    try:
        return exc.content.decode("utf-8", errors="ignore").casefold()
    except Exception:
        return str(exc).casefold()


def _close_media_upload(media_upload: Any) -> None:
    """Release MediaFileUpload file handles so Windows can clean temporary job files."""
    for attr_name in ("_fd", "fd"):
        handle = getattr(media_upload, attr_name, None)
        close = getattr(handle, "close", None)
        if callable(close):
            close()
            return
    close = getattr(media_upload, "close", None)
    if callable(close):
        close()


def _analytics_row_to_dict(row: list[Any], metrics: tuple[str, ...]) -> dict[str, Any]:
    return {metric: row[index] if index < len(row) else None for index, metric in enumerate(metrics)}


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_oauth_scope(scope: str) -> str:
    return str(scope or "").strip().casefold()


def _language_or_default(value: str | None, default: str | None) -> str:
    raw = str(value or "").strip().lower()
    fallback = str(default or "en").strip().lower() or "en"
    if not raw:
        raw = fallback
    normalized = raw.replace("_", "-")
    if not normalized or len(normalized) > 12:
        return fallback[:12] or "en"
    return normalized


def _normalize_publish_at(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise YouTubeConnectorError("publish_at must be a valid ISO 8601 datetime.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed_utc = parsed.astimezone(timezone.utc)
    if parsed_utc <= datetime.now(timezone.utc) + timedelta(minutes=5):
        raise YouTubeConnectorError("publish_at must be at least 5 minutes in the future.")
    return parsed_utc.isoformat().replace("+00:00", "Z")
