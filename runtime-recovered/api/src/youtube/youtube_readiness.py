from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal

from atnos.core.config import Settings
from youtube.youtube_auth import YOUTUBE_SCOPES, youtube_token_path


YouTubeReadinessStatus = Literal[
    "configured",
    "missing_client_secret",
    "missing_token",
    "invalid_token",
    "ready",
]


@dataclass(frozen=True, slots=True)
class YouTubeReadinessResult:
    status: YouTubeReadinessStatus
    client_secret_path: Path
    token_path: Path
    required_scopes: tuple[str, ...] = YOUTUBE_SCOPES
    granted_scopes: tuple[str, ...] = ()
    missing_scopes: tuple[str, ...] = ()
    channel_id: str | None = None
    channel_title: str | None = None
    message: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "client_secret_path": str(self.client_secret_path),
            "token_path": str(self.token_path),
            "required_scopes": list(self.required_scopes),
            "granted_scopes": list(self.granted_scopes),
            "missing_scopes": list(self.missing_scopes),
            "channel_id": self.channel_id,
            "channel_title": self.channel_title,
            "message": self.message,
            "warnings": list(self.warnings),
        }


class YouTubeReadinessService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client_secret_path = Path(settings.youtube_client_secret_file).resolve()
        self.token_path = youtube_token_path(settings)

    def check(self, *, run_probe: bool = True, youtube_service: Any | None = None) -> YouTubeReadinessResult:
        if not self.client_secret_path.exists():
            return self._result(
                "missing_client_secret",
                message=f"Missing client_secret.json: {self.client_secret_path}",
            )

        if not self.token_path.exists():
            return self._result(
                "missing_token",
                message=f"Missing OAuth token: {self.token_path}",
            )

        try:
            token_payload = self._load_token_payload()
        except ValueError as exc:
            return self._result("invalid_token", message=str(exc))
        granted_scopes = self._extract_scopes(token_payload)
        missing_scopes = tuple(scope for scope in YOUTUBE_SCOPES if scope not in granted_scopes)
        if missing_scopes:
            return self._result(
                "invalid_token",
                granted_scopes=granted_scopes,
                missing_scopes=missing_scopes,
                message="OAuth token exists, but required YouTube scopes are missing.",
            )

        if not run_probe:
            return self._result(
                "configured",
                granted_scopes=granted_scopes,
                message="Client secret and OAuth token are present with required scopes. Readonly probe was not run.",
            )

        try:
            service = youtube_service or self._build_readonly_youtube_service()
            response = service.channels().list(part="id,snippet", mine=True).execute()
        except Exception as exc:
            return self._result(
                "invalid_token",
                granted_scopes=granted_scopes,
                message=f"Readonly YouTube probe failed: {type(exc).__name__}: {exc}",
            )

        items = response.get("items") if isinstance(response, dict) else None
        first = items[0] if isinstance(items, list) and items else {}
        snippet = first.get("snippet") if isinstance(first, dict) else {}
        channel_id = str(first.get("id") or "").strip() if isinstance(first, dict) else ""
        channel_title = str(snippet.get("title") or "").strip() if isinstance(snippet, dict) else ""
        return self._result(
            "ready",
            granted_scopes=granted_scopes,
            channel_id=channel_id or None,
            channel_title=channel_title or None,
            message="YouTube OAuth is ready for private upload and analytics probes.",
            warnings=(
                "Unverified Google Cloud projects created after 2020-07-28 may be restricted to private uploads.",
            ),
        )

    def _load_token_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid OAuth token JSON: {self.token_path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid OAuth token shape: {self.token_path}")
        return payload

    def _extract_scopes(self, token_payload: dict[str, Any]) -> tuple[str, ...]:
        raw_scopes = token_payload.get("scopes")
        if isinstance(raw_scopes, list):
            scopes = [str(scope).strip() for scope in raw_scopes]
        elif isinstance(raw_scopes, str):
            scopes = raw_scopes.split()
        else:
            raw_scope = token_payload.get("scope")
            scopes = raw_scope.split() if isinstance(raw_scope, str) else []
        return tuple(scope for scope in scopes if scope)

    def _build_readonly_youtube_service(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except Exception as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError(
                "Google OAuth dependencies are missing. Install google-auth-oauthlib and google-api-python-client."
            ) from exc

        creds = Credentials.from_authorized_user_file(str(self.token_path), scopes=list(YOUTUBE_SCOPES))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
        if not creds.valid:
            raise RuntimeError("OAuth token is not valid and cannot be refreshed.")
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def _result(
        self,
        status: YouTubeReadinessStatus,
        *,
        granted_scopes: tuple[str, ...] = (),
        missing_scopes: tuple[str, ...] = (),
        channel_id: str | None = None,
        channel_title: str | None = None,
        message: str = "",
        warnings: tuple[str, ...] = (),
    ) -> YouTubeReadinessResult:
        return YouTubeReadinessResult(
            status=status,
            client_secret_path=self.client_secret_path,
            token_path=self.token_path,
            granted_scopes=granted_scopes,
            missing_scopes=missing_scopes,
            channel_id=channel_id,
            channel_title=channel_title,
            message=message,
            warnings=warnings,
        )


def check_youtube_readiness(
    settings: Settings | None = None,
    *,
    run_probe: bool = True,
    youtube_service: Any | None = None,
) -> YouTubeReadinessResult:
    return YouTubeReadinessService(settings or Settings()).check(
        run_probe=run_probe,
        youtube_service=youtube_service,
    )
