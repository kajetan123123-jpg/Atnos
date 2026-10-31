from __future__ import annotations

from pathlib import Path

from atnos.core.config import Settings


YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)


class YouTubeAuthError(RuntimeError):
    pass


def get_youtube_credentials(settings: Settings):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except Exception as exc:  # pragma: no cover - depends on optional local package
        raise YouTubeAuthError(
            "Google OAuth dependencies are missing. Install google-auth-oauthlib and google-api-python-client."
        ) from exc

    client_secret = Path(settings.youtube_client_secret_file)
    if not client_secret.exists():
        raise YouTubeAuthError(f"YouTube client secret file is missing: {client_secret}")

    token_path = _token_path(settings)
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes=list(YOUTUBE_SCOPES))
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes=list(YOUTUBE_SCOPES))
        creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def build_youtube_service(settings: Settings):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=get_youtube_credentials(settings), cache_discovery=False)


def build_youtube_analytics_service(settings: Settings):
    from googleapiclient.discovery import build

    return build("youtubeAnalytics", "v2", credentials=get_youtube_credentials(settings), cache_discovery=False)


def youtube_token_path(settings: Settings) -> Path:
    return (settings.data_dir / "youtube" / "token.json").resolve()


def _token_path(settings: Settings) -> Path:
    return youtube_token_path(settings)
