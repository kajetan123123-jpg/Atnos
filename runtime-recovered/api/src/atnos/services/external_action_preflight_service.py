from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.account_credential import AccountCredential
from atnos.db.models.credential_profile import CredentialProfile
from atnos.intelligence.storage import read_intelligence_account_config
from atnos.services.gmail_connector_service import GmailConnectorService
from atnos.services.google_oauth_client_secret_importer import diagnose_google_oauth_client_secret_file
from atnos.services.oauth_provider_registry import OAuthProviderRegistry
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.smtp_mail_connector_service import SmtpMailConnectorService
from atnos.services.youtube_connector_service import YouTubeConnectorService
from youtube.youtube_readiness import YouTubeReadinessService


@dataclass(frozen=True, slots=True)
class ActionPreflightItem:
    action: str
    ready: bool
    mode: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: dict[str, Any]
    next_steps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ShortsRealActionsPreflight:
    checked_at: datetime
    ready_for_requested_real_test: bool
    items: tuple[ActionPreflightItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at.isoformat(),
            "ready_for_requested_real_test": self.ready_for_requested_real_test,
            "items": [item.to_dict() for item in self.items],
        }


class ExternalActionPreflightService:
    """Preflight for real external actions before Atnos attempts uploads or sends messages."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def check_shorts_real_actions(self, *, account_id: str | None = None) -> ShortsRealActionsPreflight:
        items = (
            self._youtube_private_upload_modern(account_id=account_id),
            self._gmail_send(),
            self._int_mail_smtp_send(),
            self._browser_account(
                action="youtube_studio_browser_session",
                provider_keys=("youtube-studio", "youtube"),
                mode="browser_session",
                required_capability="browser_workflow",
                next_step_label="Zaloguj YouTube Studio w Settings -> Konta i potwierdź zapis sesji browserowej.",
            ),
            self._browser_account(
                action="int_mail_browser_session",
                provider_keys=("int-mail", "int_mail", "bootstrap-mailbox"),
                mode="browser_session",
                required_capability="browser_workflow",
                next_step_label="Zaloguj INT mail w Settings -> Konta i potwierdź zapis sesji browserowej.",
            ),
            self._browser_account(
                action="gmail_browser_session",
                provider_keys=("gmail",),
                mode="browser_session",
                required_capability="browser_workflow",
                next_step_label="Zaloguj Gmail w Settings -> Konta i potwierdź zapis sesji browserowej.",
            ),
        )
        required_api_actions = {"youtube_private_upload", "gmail_send", "int_mail_smtp_send"}
        ready_for_real_api_test = all(
            item.ready for item in items if item.action in required_api_actions
        )
        return ShortsRealActionsPreflight(
            checked_at=self._utcnow(),
            ready_for_requested_real_test=ready_for_real_api_test,
            items=items,
        )

    def _youtube_private_upload(self) -> ActionPreflightItem:
        readiness = YouTubeReadinessService(self.settings).check(run_probe=False)
        ready = readiness.status in {"configured", "ready"}
        blockers: list[str] = []
        next_steps: list[str] = []
        if readiness.status == "missing_client_secret":
            blockers.append(readiness.message)
            next_steps.extend(
                (
                    f"Umieść Google OAuth client_secret.json w {readiness.client_secret_path}.",
                    "Uruchom OAuth dla YouTube Studio i nadaj scope youtube.upload, youtube.readonly oraz yt-analytics.readonly.",
                )
            )
        elif readiness.status == "missing_token":
            blockers.append(readiness.message)
            next_steps.append("Uruchom OAuth flow dla YouTube Studio, żeby utworzyć token upload/analytics.")
        elif readiness.status == "invalid_token":
            blockers.append(readiness.message)
            next_steps.append("Odśwież lub usuń błędny token i przejdź OAuth flow ponownie.")
        if not status.ready:
            next_steps.append("Recommended: run POST /oauth/youtube-studio/connect after placing a real client_secret.json.")

        return ActionPreflightItem(
            action="youtube_private_upload",
            ready=ready,
            mode="youtube_data_api",
            blockers=tuple(blockers),
            warnings=tuple(readiness.warnings),
            evidence={
                "status": readiness.status,
                "client_secret_path": str(readiness.client_secret_path),
                "token_path": str(readiness.token_path),
                "required_scopes": list(readiness.required_scopes),
                "granted_scopes": list(readiness.granted_scopes),
                "missing_scopes": list(readiness.missing_scopes),
                "channel_id": readiness.channel_id,
                "channel_title": readiness.channel_title,
            },
            next_steps=tuple(dict.fromkeys(next_steps)),
        )

    def _gmail_send(self) -> ActionPreflightItem:
        status = GmailConnectorService(self.session, self.settings).get_status()
        blockers: list[str] = []
        next_steps: list[str] = []
        if not status.provider_enabled:
            blockers.append("Gmail OAuth provider is disabled.")
            next_steps.append("Włącz OAuth runtime w Settings/API.")
        if not status.provider_configured:
            blockers.append("Gmail OAuth provider is not configured.")
            next_steps.append("Dodaj Google OAuth client ID i client secret ref.")
        if not status.connected or not status.token_present:
            blockers.append("No connected Gmail OAuth token is available.")
            next_steps.append("Uruchom OAuth flow dla Gmail ze scope gmail.send.")
        if status.connected and not status.live_write_enabled:
            blockers.append("Connected Gmail token does not grant send/compose scope.")
            next_steps.append("Połącz Gmail ponownie ze scope gmail.compose oraz gmail.send.")
        return ActionPreflightItem(
            action="gmail_send",
            ready=bool(status.live_write_enabled),
            mode="gmail_api",
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(status.warnings),
            evidence={
                "connector_key": status.connector_key,
                "oauth_status": status.oauth_status,
                "connection_id": status.connection_id,
                "account_id": status.account_id,
                "provider_enabled": status.provider_enabled,
                "provider_configured": status.provider_configured,
                "token_present": status.token_present,
                "live_read_enabled": status.live_read_enabled,
                "live_write_enabled": status.live_write_enabled,
                "required_scopes": list(status.required_scopes),
                "granted_scopes": list(status.granted_scopes),
            },
            next_steps=tuple(dict.fromkeys(next_steps)),
        )

    def _int_mail_smtp_send(self) -> ActionPreflightItem:
        status = SmtpMailConnectorService(self.session, self.settings).probe()
        blockers: list[str] = []
        next_steps: list[str] = []
        if not status.configured:
            blockers.append("INT mail SMTP account is not configured.")
            next_steps.append("Dodaj konto INT mail z hasłem lub hasłem aplikacji w Settings -> Konta.")
        if status.probe_status == "auth_failed":
            blockers.append("INT mail SMTP authentication failed.")
            next_steps.extend(
                (
                    "Sprawdź hasło zapisane dla konta INT mail w Settings -> Konta.",
                    "Jeśli INT wymaga osobnego hasła aplikacji lub włączenia SMTP/IMAP, ustaw je i obróć sekret w Atnosie.",
                )
            )
        elif status.probe_status in {"connection_failed", "failed"}:
            blockers.append("INT mail SMTP provider could not be reached.")
            next_steps.append("Sprawdź połączenie sieciowe i dostęp do poczta.int.pl na portach 465/587.")
        elif not status.live_send_enabled:
            blockers.append("INT mail SMTP send is not verified.")
            next_steps.append("Uruchom /connectors/smtp-mail/probe po poprawieniu konfiguracji konta.")
        return ActionPreflightItem(
            action="int_mail_smtp_send",
            ready=bool(status.live_send_enabled),
            mode="smtp",
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(status.warnings),
            evidence={
                "connector_key": status.connector_key,
                "provider_key": status.provider_key,
                "account_id": status.account_id,
                "account_identifier": status.account_identifier,
                "configured": status.configured,
                "has_secret": status.has_secret,
                "probe_status": status.probe_status,
                "live_login_enabled": status.live_login_enabled,
                "live_send_enabled": status.live_send_enabled,
                "message": status.message,
            },
            next_steps=tuple(dict.fromkeys(next_steps)),
        )

    def _youtube_private_upload_modern(self, *, account_id: str | None = None) -> ActionPreflightItem:
        effective_settings = RuntimeSettingsService(self.session, self.settings).build_effective_settings()
        descriptor = OAuthProviderRegistry(effective_settings).get_connector("youtube-studio")
        oauth_account_id = _youtube_oauth_account_id(self.settings, account_id)
        status = YouTubeConnectorService(self.session, self.settings, account_id=oauth_account_id).get_status()
        client_secret_diagnostics = diagnose_google_oauth_client_secret_file(
            effective_settings.youtube_client_secret_file,
            redirect_base_url=effective_settings.oauth_redirect_base_url,
        )
        required_scopes = tuple(descriptor.scopes)
        granted_scopes = tuple(status.granted_scopes)
        missing_scopes = _missing_oauth_scopes(required_scopes, granted_scopes) if status.connected else sorted(required_scopes)

        blockers: list[str] = []
        next_steps: list[str] = []
        if not descriptor.enabled:
            blockers.append("YouTube OAuth provider is disabled.")
            next_steps.append("Włącz OAuth runtime albo zaimportuj client_secret.json przez /oauth/youtube-studio/import-client-secret.")
        if not (descriptor.is_configured or descriptor.mode == "fake_provider"):
            blockers.append("YouTube OAuth provider is not configured.")
            next_steps.extend(
                (
                    f"Połóż Google client_secret.json w {self.settings.youtube_client_secret_file} albo uruchom POST /oauth/youtube-studio/import-client-secret.",
                    "Uruchom POST /oauth/youtube-studio/start i zaakceptuj scope youtube.upload, youtube.readonly oraz yt-analytics.readonly.",
                )
            )
            if not client_secret_diagnostics.exists:
                next_steps.append(f"Pobierz OAuth Client ID typu Desktop/Web z Google Cloud i zapisz jako {client_secret_diagnostics.path}.")
            elif not client_secret_diagnostics.import_ready:
                next_steps.append("Popraw client_secret.json: musi byc poprawnym JSON z client_id i client_secret.")
        if not status.connected:
            blockers.append("No connected YouTube Studio OAuth token is available.")
            next_steps.append("Uruchom POST /oauth/youtube-studio/start i dokończ callback Google.")
        if status.connected and missing_scopes:
            blockers.append("Connected YouTube token is missing required upload/analytics scopes.")
            next_steps.append("Połącz YouTube Studio ponownie z pełnym zakresem scope.")

        return ActionPreflightItem(
            action="youtube_private_upload",
            ready=bool(status.ready),
            mode="youtube_data_api",
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(status.warnings),
            evidence={
                "connector_key": status.connector_key,
                "oauth_status": "connected" if status.connected else ("configured" if descriptor.is_configured else "not_configured"),
                "provider_enabled": descriptor.enabled,
                "provider_configured": descriptor.is_configured or descriptor.mode == "fake_provider",
                "connected": status.connected,
                "ready": status.ready,
                "content_account_id": account_id,
                "account_id": status.account_id,
                "channel_title": status.channel_title,
                "client_secret_file": str(self.settings.youtube_client_secret_file),
                "client_secret_diagnostics": client_secret_diagnostics.to_dict(),
                "client_secret_status_endpoint": "GET /oauth/youtube-studio/client-secret-status",
                "oauth_readiness_endpoint": "GET /oauth/youtube-studio/status",
                "connect_endpoint": "POST /oauth/youtube-studio/connect",
                "import_client_secret_endpoint": "POST /oauth/youtube-studio/import-client-secret",
                "oauth_start_endpoint": "POST /oauth/youtube-studio/start",
                "required_scopes": list(required_scopes),
                "granted_scopes": list(granted_scopes),
                "missing_scopes": missing_scopes,
            },
            next_steps=tuple(dict.fromkeys(next_steps)),
        )

    def _browser_account(
        self,
        *,
        action: str,
        provider_keys: tuple[str, ...],
        mode: str,
        required_capability: str,
        next_step_label: str,
    ) -> ActionPreflightItem:
        rows = (
            self.session.query(AccountCredential, CredentialProfile)
            .outerjoin(CredentialProfile, CredentialProfile.account_id == AccountCredential.id)
            .filter(AccountCredential.provider_key.in_(provider_keys))
            .order_by(AccountCredential.id)
            .all()
        )
        candidates: list[dict[str, Any]] = []
        for account, profile in rows:
            mapping = dict(profile.connector_mapping_json or {}) if profile is not None else {}
            has_profile_secret = bool(profile and profile.password_secret_ref)
            has_account_secret = bool(account.secret_ref and account.storage_backend != "metadata_only")
            capabilities = tuple(account.allowed_capabilities_json or ())
            candidates.append(
                {
                    "account_id": account.id,
                    "label": account.label,
                    "provider_key": account.provider_key,
                    "status": account.status,
                    "storage_backend": account.storage_backend,
                    "has_secret": has_account_secret or has_profile_secret,
                    "has_login_url": bool(str(mapping.get("login_url") or mapping.get("primary_url") or "").strip()),
                    "has_required_capability": required_capability in capabilities,
                    "login_url": mapping.get("login_url") or mapping.get("primary_url"),
                }
            )
        usable = [
            item
            for item in candidates
            if item["status"] == "configured"
            and item["has_secret"]
            and item["has_login_url"]
            and item["has_required_capability"]
        ]
        blockers: list[str] = []
        if not candidates:
            blockers.append(f"No trusted account exists for providers: {', '.join(provider_keys)}.")
        elif not usable:
            blockers.append("Trusted account exists, but it is missing a secret, login URL, configured status, or browser capability.")
        return ActionPreflightItem(
            action=action,
            ready=bool(usable),
            mode=mode,
            blockers=tuple(blockers),
            warnings=(),
            evidence={
                "candidate_count": len(candidates),
                "usable_count": len(usable),
                "candidates": candidates,
            },
            next_steps=() if usable else (next_step_label,),
        )

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)


def _youtube_oauth_account_id(settings: Settings, content_account_id: str | None) -> int | None:
    if not content_account_id:
        return None
    config = read_intelligence_account_config(settings, account_id=content_account_id) or {}
    raw = config.get("youtube_oauth_account_id")
    try:
        return int(raw) if raw is not None and str(raw).strip() else None
    except (TypeError, ValueError):
        return None


def _missing_oauth_scopes(required_scopes: tuple[str, ...], granted_scopes: tuple[str, ...]) -> list[str]:
    """Compare requested OAuth scopes while accepting Google's canonical OIDC aliases."""
    from atnos.services.oauth_provider_registry import YOUTUBE_OPTIONAL_CAPTION_SCOPE

    granted = {_normalize_oauth_scope(scope) for scope in granted_scopes}
    optional = {_normalize_oauth_scope(YOUTUBE_OPTIONAL_CAPTION_SCOPE)}
    return sorted(scope for scope in required_scopes if _normalize_oauth_scope(scope) not in granted and _normalize_oauth_scope(scope) not in optional)


def _normalize_oauth_scope(scope: str) -> str:
    normalized = str(scope or "").strip()
    aliases = {
        "https://www.googleapis.com/auth/userinfo.email": "email",
        "https://www.googleapis.com/auth/userinfo.profile": "profile",
    }
    return aliases.get(normalized, normalized)
