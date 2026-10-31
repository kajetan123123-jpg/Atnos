from __future__ import annotations

from dataclasses import dataclass
import hashlib

from atnos.core.config import Settings


YOUTUBE_OPTIONAL_CAPTION_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


def oauth_client_fingerprint(client_id: str | None) -> str | None:
    """Return a non-secret identifier used to detect OAuth client replacement."""
    normalized = (client_id or "").strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class OAuthProviderDescriptor:
    connector_key: str
    display_name: str
    authorization_url: str
    token_url: str
    redirect_uri: str
    scopes: tuple[str, ...]
    supports_pkce: bool
    enabled: bool
    setup_warnings: tuple[str, ...]
    mode: str
    client_id: str | None
    client_secret_ref: str | None
    integration_key: str

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.client_id) and bool(self.client_secret_ref)


class OAuthProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_connectors(self) -> list[OAuthProviderDescriptor]:
        return [
            self.get_connector("github"),
            self.get_connector("google-drive-readonly"),
            self.get_connector("gmail"),
            self.get_connector("google-calendar"),
            self.get_connector("youtube-studio"),
        ]

    def get_connector(self, connector_key: str) -> OAuthProviderDescriptor:
        normalized = connector_key.strip().lower()
        if normalized == "github":
            return self._build_github_provider(
                connector_key="github",
                display_name="GitHub",
                scopes=("read:user", "repo"),
            )
        if normalized == "github-readonly":
            return self._build_github_provider(
                connector_key="github-readonly",
                display_name="GitHub (read-only)",
                scopes=("read:user", "repo:status"),
            )
        if normalized == "google-drive-readonly":
            return self._build_google_provider(
                connector_key="google-drive-readonly",
                display_name="Google Drive (read-only)",
                scopes=("openid", "email", "profile", "https://www.googleapis.com/auth/drive.metadata.readonly"),
                integration_key="google-drive",
            )
        if normalized == "gmail":
            return self._build_google_provider(
                connector_key="gmail",
                display_name="Gmail",
                scopes=(
                    "openid",
                    "email",
                    "profile",
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.compose",
                    "https://www.googleapis.com/auth/gmail.send",
                ),
                integration_key="gmail",
            )
        if normalized == "gmail-readonly":
            return self._build_google_provider(
                connector_key="gmail-readonly",
                display_name="Gmail (read-only)",
                scopes=("openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly"),
                integration_key="gmail",
            )
        if normalized == "google-calendar":
            return self._build_google_provider(
                connector_key="google-calendar",
                display_name="Google Calendar",
                scopes=("openid", "email", "profile", "https://www.googleapis.com/auth/calendar"),
                integration_key="google-calendar",
            )
        if normalized == "google-calendar-readonly":
            return self._build_google_provider(
                connector_key="google-calendar-readonly",
                display_name="Google Calendar (read-only)",
                scopes=("openid", "email", "profile", "https://www.googleapis.com/auth/calendar.readonly"),
                integration_key="google-calendar",
            )
        if normalized == "youtube-studio":
            return self._build_google_provider(
                connector_key="youtube-studio",
                display_name="YouTube Studio",
                scopes=(
                    "openid",
                    "email",
                    "profile",
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/yt-analytics.readonly",
                    YOUTUBE_OPTIONAL_CAPTION_SCOPE,
                ),
                integration_key="youtube",
            )
        raise KeyError(connector_key)

    def _build_github_provider(
        self,
        *,
        connector_key: str,
        display_name: str,
        scopes: tuple[str, ...],
    ) -> OAuthProviderDescriptor:
        warnings: list[str] = []
        enabled = bool(self.settings.oauth_enabled)
        if not enabled:
            warnings.append("OAuth is disabled in this runtime.")
        if not self.settings.oauth_github_client_id:
            warnings.append("GitHub OAuth client ID is missing.")
        if not self.settings.oauth_github_client_secret_ref:
            warnings.append("GitHub OAuth client secret ref is missing.")
        return OAuthProviderDescriptor(
            connector_key=connector_key,
            display_name=display_name,
            authorization_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            redirect_uri=f"{self.settings.oauth_redirect_base_url.rstrip('/')}/oauth/{connector_key}/callback",
            scopes=scopes,
            supports_pkce=True,
            enabled=enabled,
            setup_warnings=tuple(warnings),
            mode="fake_provider" if self.settings.app_env == "test" else "real_provider",
            client_id=self.settings.oauth_github_client_id,
            client_secret_ref=self.settings.oauth_github_client_secret_ref,
            integration_key="github",
        )

    def _build_google_provider(
        self,
        *,
        connector_key: str,
        display_name: str,
        scopes: tuple[str, ...],
        integration_key: str,
    ) -> OAuthProviderDescriptor:
        warnings: list[str] = []
        enabled = bool(self.settings.oauth_enabled)
        if not enabled:
            warnings.append("OAuth is disabled in this runtime.")
        if not self.settings.oauth_google_client_id:
            warnings.append("Google OAuth client ID is missing.")
        if not self.settings.oauth_google_client_secret_ref:
            warnings.append("Google OAuth client secret ref is missing.")
        return OAuthProviderDescriptor(
            connector_key=connector_key,
            display_name=display_name,
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri=f"{self.settings.oauth_redirect_base_url.rstrip('/')}/oauth/{connector_key}/callback",
            scopes=scopes,
            supports_pkce=True,
            enabled=enabled,
            setup_warnings=tuple(warnings),
            mode="fake_provider" if self.settings.app_env == "test" else "real_provider",
            client_id=self.settings.oauth_google_client_id,
            client_secret_ref=self.settings.oauth_google_client_secret_ref,
            integration_key=integration_key,
        )
