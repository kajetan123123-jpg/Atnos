from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.oauth_service import OAuthService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.secret_vault_service import SecretVaultResolutionError, SecretVaultService


@dataclass(frozen=True, slots=True)
class GoogleOAuthImportResult:
    imported: bool
    client_id: str | None
    client_secret_ref: str | None
    client_secret_path: Path
    redirect_uri: str
    env_path: Path
    warnings: tuple[str, ...]
    connector: object | None


@dataclass(frozen=True, slots=True)
class GoogleOAuthClientSecretDiagnostics:
    path: Path
    exists: bool
    valid_json: bool
    client_type: str | None
    has_client_id: bool
    has_client_secret: bool
    redirect_uri: str
    redirect_uri_registered: bool | None
    likely_placeholder: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def import_ready(self) -> bool:
        return self.exists and self.valid_json and self.has_client_id and self.has_client_secret and not self.likely_placeholder

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "valid_json": self.valid_json,
            "client_type": self.client_type,
            "has_client_id": self.has_client_id,
            "has_client_secret": self.has_client_secret,
            "redirect_uri": self.redirect_uri,
            "redirect_uri_registered": self.redirect_uri_registered,
            "likely_placeholder": self.likely_placeholder,
            "import_ready": self.import_ready,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


class GoogleOAuthClientSecretImporter:
    """Imports a Google OAuth client_secret.json into Atnos runtime config and vault."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def import_file(
        self,
        *,
        client_secret_path: Path | None = None,
        redirect_base_url: str = "http://127.0.0.1:8000",
        enable_oauth: bool = True,
    ) -> GoogleOAuthImportResult:
        path = (client_secret_path or self.settings.youtube_client_secret_file).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Google OAuth client secret file was not found: {path}")

        client_payload = json.loads(path.read_text(encoding="utf-8"))
        client = extract_google_oauth_client(client_payload)
        redirect_base_url = redirect_base_url.strip().rstrip("/") or "http://127.0.0.1:8000"
        redirect_uri = f"{redirect_base_url}/oauth/youtube-studio/callback"
        warnings = tuple(google_client_secret_warnings(client, redirect_uri))
        if looks_like_google_oauth_placeholder(client):
            raise ValueError(
                "Google OAuth client_secret.json looks like documentation/sample data. "
                "Download a real OAuth client JSON from Google Cloud Console."
            )

        _backend, secret_ref = SecretVaultService(self.settings).rotate_secret(
            secret_value=client["client_secret"],
            existing_secret_ref=self.settings.oauth_google_client_secret_ref,
        )

        self.settings.oauth_enabled = enable_oauth
        self.settings.oauth_redirect_base_url = redirect_base_url
        self.settings.oauth_google_client_id = client["client_id"]
        self.settings.oauth_google_client_secret_ref = secret_ref
        RuntimeSettingsService(self.session, self.settings).update_owner_controls(oauth_enabled=enable_oauth)

        env_path = persist_oauth_runtime_env(
            self.settings.runtime_env_file,
            {
                "OAUTH_ENABLED": "true" if enable_oauth else "false",
                "OAUTH_REDIRECT_BASE_URL": redirect_base_url,
                "OAUTH_GOOGLE_CLIENT_ID": client["client_id"],
                "OAUTH_GOOGLE_CLIENT_SECRET_REF": secret_ref,
                "YOUTUBE_CLIENT_SECRET_FILE": str(path),
            },
        )
        connector = next(
            item
            for item in OAuthService(self.session, self.settings).list_connectors()
            if item.connector_key == "youtube-studio"
        )
        return GoogleOAuthImportResult(
            imported=True,
            client_id=client["client_id"],
            client_secret_ref=secret_ref,
            client_secret_path=path,
            redirect_uri=redirect_uri,
            env_path=env_path,
            warnings=warnings,
            connector=connector,
        )

    def auto_import_if_available(self) -> GoogleOAuthImportResult | None:
        """Import at startup when Google OAuth is missing or the stored secret is stale."""
        path = self.settings.youtube_client_secret_file
        if not path.exists():
            return None
        if self.settings.oauth_google_client_id and self.settings.oauth_google_client_secret_ref:
            try:
                client_payload = json.loads(path.read_text(encoding="utf-8"))
                client = extract_google_oauth_client(client_payload)
            except (OSError, ValueError, json.JSONDecodeError):
                return None
            if str(client.get("client_id") or "").strip() == self.settings.oauth_google_client_id:
                try:
                    stored_secret = SecretVaultService(self.settings).resolve_secret(
                        self.settings.oauth_google_client_secret_ref
                    )
                except SecretVaultResolutionError:
                    stored_secret = ""
                file_secret = str(client.get("client_secret") or "")
                if _sha256(stored_secret) == _sha256(file_secret):
                    return None
        return self.import_file(client_secret_path=path, redirect_base_url=self.settings.oauth_redirect_base_url)


def extract_google_oauth_client(payload: dict[str, object]) -> dict[str, object]:
    client = payload.get("installed") or payload.get("web")
    if not isinstance(client, dict):
        raise ValueError("Google OAuth client secret must contain an 'installed' or 'web' client block.")
    client_id = str(client.get("client_id") or "").strip()
    client_secret = str(client.get("client_secret") or "").strip()
    if not client_id:
        raise ValueError("Google OAuth client secret file is missing client_id.")
    if not client_secret:
        raise ValueError("Google OAuth client secret file is missing client_secret.")
    redirect_uris = client.get("redirect_uris")
    if not isinstance(redirect_uris, list):
        redirect_uris = []
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": [str(item).strip() for item in redirect_uris if str(item).strip()],
        "client_type": "installed" if "installed" in payload else "web",
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def google_client_secret_warnings(client: dict[str, object], redirect_uri: str) -> list[str]:
    warnings: list[str] = []
    redirect_uris = client.get("redirect_uris")
    if (
        isinstance(redirect_uris, list)
        and redirect_uris
        and not google_redirect_uri_matches(client, redirect_uri)
    ):
        warnings.append(
            f"Google OAuth client redirect_uris does not list {redirect_uri}. Add it in Google Cloud if callback fails."
        )
    if client.get("client_type") == "web":
        warnings.append("Google OAuth web client imported. Installed/Desktop client is usually simpler for local Atnos.")
    return warnings


def diagnose_google_oauth_client_secret_file(
    path: Path,
    *,
    redirect_base_url: str = "http://127.0.0.1:8000",
) -> GoogleOAuthClientSecretDiagnostics:
    """Return safe client_secret.json diagnostics without exposing the secret value."""
    resolved = path.expanduser().resolve()
    redirect_base_url = redirect_base_url.strip().rstrip("/") or "http://127.0.0.1:8000"
    redirect_uri = f"{redirect_base_url}/oauth/youtube-studio/callback"
    if not resolved.exists():
        return GoogleOAuthClientSecretDiagnostics(
            path=resolved,
            exists=False,
            valid_json=False,
            client_type=None,
            has_client_id=False,
            has_client_secret=False,
            redirect_uri=redirect_uri,
            redirect_uri_registered=None,
            likely_placeholder=False,
            warnings=(),
            errors=("client_secret.json file is missing.",),
        )

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return GoogleOAuthClientSecretDiagnostics(
            path=resolved,
            exists=True,
            valid_json=False,
            client_type=None,
            has_client_id=False,
            has_client_secret=False,
            redirect_uri=redirect_uri,
            redirect_uri_registered=None,
            likely_placeholder=False,
            warnings=(),
            errors=("client_secret.json is not valid JSON.",),
        )

    try:
        client = extract_google_oauth_client(payload)
    except ValueError as exc:
        raw_client = payload.get("installed") or payload.get("web") if isinstance(payload, dict) else None
        has_client_id = bool(isinstance(raw_client, dict) and str(raw_client.get("client_id") or "").strip())
        has_client_secret = bool(isinstance(raw_client, dict) and str(raw_client.get("client_secret") or "").strip())
        client_type = "installed" if isinstance(payload, dict) and "installed" in payload else ("web" if isinstance(payload, dict) and "web" in payload else None)
        return GoogleOAuthClientSecretDiagnostics(
            path=resolved,
            exists=True,
            valid_json=True,
            client_type=client_type,
            has_client_id=has_client_id,
            has_client_secret=has_client_secret,
            redirect_uri=redirect_uri,
            redirect_uri_registered=None,
            likely_placeholder=False,
            warnings=(),
            errors=(str(exc),),
        )

    redirect_uris = client.get("redirect_uris")
    redirect_uri_registered = (
        google_redirect_uri_matches(client, redirect_uri)
        if isinstance(redirect_uris, list) and redirect_uris
        else None
    )
    likely_placeholder = looks_like_google_oauth_placeholder(client)
    warnings = google_client_secret_warnings(client, redirect_uri)
    if likely_placeholder:
        warnings.append(
            "client_secret.json looks like documentation/sample data, not a real Google Cloud OAuth client."
        )
    return GoogleOAuthClientSecretDiagnostics(
        path=resolved,
        exists=True,
        valid_json=True,
        client_type=str(client.get("client_type") or "") or None,
        has_client_id=bool(client.get("client_id")),
        has_client_secret=bool(client.get("client_secret")),
        redirect_uri=redirect_uri,
        redirect_uri_registered=redirect_uri_registered,
        likely_placeholder=likely_placeholder,
        warnings=tuple(warnings),
        errors=(),
    )


def looks_like_google_oauth_placeholder(client: dict[str, object]) -> bool:
    """Detect obvious Google documentation examples without exposing real secret values."""
    client_id = str(client.get("client_id") or "").strip().lower()
    client_secret = str(client.get("client_secret") or "").strip().lower()
    placeholder_fragments = (
        "asdf",
        "asdlk",
        "example",
        "paradygmat",
        "password",
        "haslo",
        "hasło",
        "google-client-secret-test",
        "loopback-google-client-secret",
        "fake-client-secret",
    )
    if any(fragment in client_id or fragment in client_secret for fragment in placeholder_fragments):
        return True
    if client_secret in {"1912308409123890", "asdlkfjaskd"}:
        return True
    if "apps.googleusercontent.com" not in client_id:
        return True
    if len(client_secret) < 16:
        return True
    return False


def google_redirect_uri_matches(client: dict[str, object], redirect_uri: str) -> bool:
    """Google Desktop OAuth loopback clients often list only http://localhost."""
    redirect_uris = client.get("redirect_uris")
    if not isinstance(redirect_uris, list):
        return False
    normalized_redirect_uri = redirect_uri.strip().rstrip("/")
    normalized_registered = {str(item).strip().rstrip("/") for item in redirect_uris if str(item).strip()}
    if normalized_redirect_uri in normalized_registered:
        return True
    if client.get("client_type") != "installed":
        return False
    loopback_prefixes = (
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
    )
    if not normalized_redirect_uri.startswith(loopback_prefixes):
        return False
    return any(item in loopback_prefixes for item in normalized_registered)


def persist_oauth_runtime_env(env_path: Path, updates: dict[str, str]) -> Path:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = set(updates)
    output: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={quote_env_value(updates[key])}")
            remaining.discard(key)
        else:
            output.append(line)
    if remaining and output and output[-1].strip():
        output.append("")
    for key in sorted(remaining):
        output.append(f"{key}={quote_env_value(updates[key])}")
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return env_path


def quote_env_value(value: str) -> str:
    if not value or any(char.isspace() for char in value) or any(char in value for char in ['"', "'", "#"]):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
