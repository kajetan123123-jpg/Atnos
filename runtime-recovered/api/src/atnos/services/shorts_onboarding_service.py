from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect
from atnos.production.readiness import ShortProductionReadinessService
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.gmail_connector_service import GmailConnectorService
from atnos.services.smtp_mail_connector_service import SmtpMailConnectorService
from atnos.services.youtube_connector_service import YouTubeConnectorService


OnboardingStatus = Literal["ready", "missing", "needs_action", "optional"]
OnboardingCategory = Literal["research", "upload", "notification", "strategy", "monetization", "runtime"]


@dataclass(frozen=True, slots=True)
class ShortsOnboardingItem:
    key: str
    label: str
    category: OnboardingCategory
    status: OnboardingStatus
    required: bool
    user_data_needed: tuple[str, ...]
    unlocks: tuple[str, ...]
    evidence: dict[str, Any]
    next_steps: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status in {"ready", "optional"} or not self.required

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ready"] = self.ready
        return payload


@dataclass(frozen=True, slots=True)
class ShortsOnboardingChecklist:
    checked_at: datetime
    ready_for_local_production: bool
    ready_for_private_upload: bool
    ready_for_revenue_ops: bool
    blocking_keys: tuple[str, ...]
    items: tuple[ShortsOnboardingItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at.isoformat(),
            "ready_for_local_production": self.ready_for_local_production,
            "ready_for_private_upload": self.ready_for_private_upload,
            "ready_for_revenue_ops": self.ready_for_revenue_ops,
            "blocking_keys": list(self.blocking_keys),
            "items": [item.to_dict() for item in self.items],
            "minimal_user_inputs": [
                step
                for item in self.items
                if item.required and item.status != "ready"
                for step in item.user_data_needed
            ],
        }


class ShortsOnboardingService:
    """Maps real runtime readiness to the exact data the operator still needs to provide."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def build_checklist(self) -> ShortsOnboardingChecklist:
        readiness = await ShortProductionReadinessService(
            self.settings,
            session=self.session,
        ).check(include_live_checks=True)
        readiness_items = {str(item["key"]): item for item in readiness.get("items", [])}
        counts = self._intelligence_counts()
        accounts = AccountCredentialsService(self.session, self.settings).list_accounts()
        account_counts = _count_accounts_by_provider(accounts)
        youtube_status = YouTubeConnectorService(self.session, self.settings).get_status()
        gmail_status = GmailConnectorService(self.session, self.settings).get_status()
        smtp_status = SmtpMailConnectorService(self.session, self.settings).get_status()

        items = (
            self._google_oauth_item(readiness_items.get("youtube_oauth", {}), youtube_status),
            self._youtube_data_api_item(counts, youtube_status),
            self._channel_strategy_item(readiness_items.get("obsidian_memory", {}), account_counts),
            self._reference_sources_item(counts),
            self._notification_item(gmail_status, smtp_status),
            self._runtime_item(readiness_items),
            self._private_upload_item(youtube_status),
            self._publication_policy_item(),
            self._monetization_item(account_counts),
        )
        blocking = tuple(item.key for item in items if item.required and item.status != "ready")
        local_blocking = {
            "youtube_data_api_key",
            "channel_strategy",
            "runtime_stack",
            "reference_sources",
        }
        upload_blocking = {"google_oauth", "youtube_private_upload"}
        revenue_blocking = {"monetization_plan"}
        return ShortsOnboardingChecklist(
            checked_at=datetime.now(timezone.utc),
            ready_for_local_production=not any(key in blocking for key in local_blocking),
            ready_for_private_upload=not any(key in blocking for key in upload_blocking),
            ready_for_revenue_ops=not any(key in blocking for key in (*local_blocking, *upload_blocking, *revenue_blocking)),
            blocking_keys=blocking,
            items=items,
        )

    def _google_oauth_item(self, youtube_readiness: dict[str, Any], youtube_status: Any) -> ShortsOnboardingItem:
        evidence = dict(youtube_readiness.get("evidence") or {})
        diagnostics = evidence.get("client_secret_diagnostics") if isinstance(evidence.get("client_secret_diagnostics"), dict) else {}
        configured = bool(youtube_status.configured)
        import_ready = bool(diagnostics.get("import_ready"))
        likely_placeholder = bool(diagnostics.get("likely_placeholder"))
        ready = bool(youtube_status.connected)
        return ShortsOnboardingItem(
            key="google_oauth",
            label="Google OAuth dla YouTube/Gmail",
            category="upload",
            status="ready" if ready else "missing",
            required=True,
            user_data_needed=(
                "Google OAuth client_secret.json z Google Cloud Console.",
                "Client ID/secret musi obslugiwac callback Atnosa i scope YouTube/Gmail.",
                "Login i haslo do konta Google/YouTube nie wystarcza do YouTube Data API; wymagany jest OAuth consent.",
            ),
            unlocks=("YouTube private upload", "YouTube Analytics", "Gmail API send"),
            evidence={
                "configured": configured,
                "client_secret_file": evidence.get("client_secret_file") or str(self.settings.youtube_client_secret_file),
                "client_secret_import_ready": import_ready,
                "client_secret_likely_placeholder": likely_placeholder,
                "client_secret_status_endpoint": evidence.get("client_secret_status_endpoint") or "GET /oauth/youtube-studio/client-secret-status",
                "connect_endpoint": evidence.get("connect_endpoint") or "POST /oauth/youtube-studio/connect",
                "oauth_start_endpoint": evidence.get("oauth_start_endpoint") or "POST /oauth/youtube-studio/start",
                "import_endpoint": evidence.get("import_client_secret_endpoint") or "POST /oauth/youtube-studio/import-client-secret",
                "password_login_supported": False,
                "required_authorization_method": "Google OAuth consent with YouTube scopes",
                "required_google_credential_type": "OAuth 2.0 Client ID: Desktop app / installed application JSON",
                "google_cloud_credentials_url": "https://console.cloud.google.com/apis/credentials",
                "youtube_data_api_url": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
                "youtube_analytics_api_url": "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
            },
            next_steps=(
                f"Zapisz client_secret.json w {self.settings.youtube_client_secret_file}.",
                "W Google Cloud utworz OAuth 2.0 Client ID typu Desktop app, nie API key i nie web-only sample z dokumentacji.",
                "Uruchom POST /oauth/youtube-studio/connect i otworz zwrocony authorization_url.",
                "Nie wpisuj hasla Google/YouTube do Atnosa; po zgodzie OAuth token zostanie zapisany w szyfrowanym vault.",
            ) if not ready else (),
        )

    def _youtube_data_api_item(self, counts: dict[str, int], youtube_status: Any) -> ShortsOnboardingItem:
        has_key = bool(self.settings.youtube_data_api_key)
        has_oauth = bool(youtube_status.ready)
        has_signals = counts.get("yt_signals", 0) > 0 and counts.get("gtrends_signals", 0) > 0
        ready = has_signals or has_key or has_oauth
        return ShortsOnboardingItem(
            key="youtube_data_api_key",
            label="Research: YouTube Data API + Google Trends",
            category="research",
            status="ready" if ready else "missing",
            required=True,
            user_data_needed=(
                "YOUTUBE_DATA_API_KEY albo aktywny YouTube OAuth.",
                "Lista nisz/keywordow dla kanalu, np. finanse osobiste, budzet domowy, inwestowanie edukacyjne.",
            ),
            unlocks=("yt_signals", "topic scoring without guessing", "topic generation"),
            evidence={
                "youtube_data_api_key_configured": has_key,
                "youtube_oauth_ready": has_oauth,
                "yt_signals": counts.get("yt_signals", 0),
                "gtrends_signals": counts.get("gtrends_signals", 0),
                "account_seed_endpoint": "POST /accounts",
                "account_seed_fields": [
                    "intelligence_niches",
                    "intelligence_region",
                    "intelligence_timeframe",
                    "intelligence_refresh_now",
                ],
                "autonomous_config_endpoint": "POST /api/intelligence/accounts",
                "manual_collection_endpoint": "POST /api/intelligence/run",
            },
            next_steps=(
                "Dodaj YOUTUBE_DATA_API_KEY lub polacz YouTube OAuth.",
                "Zapisz nisze przez /accounts pole intelligence_niches albo POST /api/intelligence/accounts.",
                "Poczekaj na scheduler albo wymus POST /api/intelligence/run dla nisz kanalu.",
            ) if not ready else (),
        )

    def _channel_strategy_item(self, obsidian_item: dict[str, Any], account_counts: dict[str, int]) -> ShortsOnboardingItem:
        evidence = dict(obsidian_item.get("evidence") or {})
        style_ready = bool(evidence.get("style_guide_count", 0) > 0 or evidence.get("learning_note_count", 0) > 0)
        youtube_account_exists = account_counts.get("youtube-studio", 0) + account_counts.get("youtube", 0) > 0
        ready = style_ready and youtube_account_exists
        return ShortsOnboardingItem(
            key="channel_strategy",
            label="Strategia kanalu i style guide",
            category="strategy",
            status="ready" if ready else "missing",
            required=True,
            user_data_needed=(
                "Nazwa kanalu i glowna nisza.",
                "Styl: edukacyjny, premium finance, viral, storytelling.",
                "Style guide w Obsidian albo zgoda na wygenerowanie domyslnego style guide.",
            ),
            unlocks=("script style", "visual style", "safer financial claims"),
            evidence={
                "style_guide_count": evidence.get("style_guide_count", 0),
                "style_guides": evidence.get("style_guides", []),
                "learning_note_count": evidence.get("learning_note_count", 0),
                "learning_notes": evidence.get("learning_notes", []),
                "youtube_account_exists": youtube_account_exists,
            },
            next_steps=(
                "Utworz style_guide.md dla kanalu w Obsidian albo uruchom production analytics follow-up, zeby zapisac learning note.",
                "Dodaj konto YouTube Studio w Settings -> Konta, jezeli go nie ma.",
            ) if not ready else (),
        )

    def _reference_sources_item(self, counts: dict[str, int]) -> ShortsOnboardingItem:
        ready = counts.get("reference_channels", 0) > 0 or counts.get("competitor_channels", 0) > 0
        return ShortsOnboardingItem(
            key="reference_sources",
            label="Kanaly referencyjne i konkurencja",
            category="research",
            status="ready" if ready else "missing",
            required=True,
            user_data_needed=(
                "3-5 URL-i kanalow referencyjnych.",
                "3-10 URL-i konkurencji w tej samej niszy.",
            ),
            unlocks=("competitor analysis", "hook pattern benchmarking", "thumbnail/style benchmarks"),
            evidence={
                "reference_channels": counts.get("reference_channels", 0),
                "competitor_channels": counts.get("competitor_channels", 0),
                "competitor_videos": counts.get("competitor_videos", 0),
            },
            next_steps=(
                "Dodaj kanaly referencyjne, np. Alux i podobne formaty.",
                "Uruchom /api/intelligence/reference oraz /api/intelligence/competitors.",
            ) if not ready else (),
        )

    def _notification_item(self, gmail_status: Any, smtp_status: Any) -> ShortsOnboardingItem:
        gmail_ready = bool(gmail_status.live_write_enabled)
        smtp_configured = bool(smtp_status.configured and smtp_status.has_secret)
        ready = gmail_ready or smtp_configured
        status: OnboardingStatus = "ready" if ready else "needs_action"
        return ShortsOnboardingItem(
            key="notification_channel",
            label="Powiadomienia email",
            category="notification",
            status=status,
            required=False,
            user_data_needed=(
                "Gmail OAuth z gmail.send albo poprawne haslo/app-password SMTP dla INT.",
            ),
            unlocks=("QA reports by email", "upload notifications", "operator alerts"),
            evidence={
                "gmail_live_write_enabled": gmail_ready,
                "gmail_provider_configured": gmail_status.provider_configured,
                "int_smtp_configured": smtp_status.configured,
                "int_smtp_has_secret": smtp_status.has_secret,
                "int_smtp_probe_status": smtp_status.probe_status,
            },
            next_steps=(
                "Dla Gmail: skonfiguruj Google OAuth i scope gmail.send.",
                "Dla INT: ustaw poprawne haslo SMTP albo haslo aplikacji i uruchom probe.",
            ) if not ready else (),
        )

    def _runtime_item(self, readiness_items: dict[str, dict[str, Any]]) -> ShortsOnboardingItem:
        required = (
            "production_modules",
            "script_generation_contract",
            "asset_planner_contract",
            "qa_contract",
            "ollama",
            "audio_stack",
            "ffmpeg",
            "whisper_srt",
            "comfyui",
            "models",
            "workflow_templates",
            "analytics_score_loop",
        )
        statuses = {key: readiness_items.get(key, {}).get("status") for key in required}
        ready = all(status == "ready" for status in statuses.values())
        return ShortsOnboardingItem(
            key="runtime_stack",
            label="Lokalny runtime shortow",
            category="runtime",
            status="ready" if ready else "needs_action",
            required=True,
            user_data_needed=(
                "Uruchom Ollama/ComfyUI i pobierz brakujace modele, jesli readiness pokazuje braki.",
            ),
            unlocks=("script", "voice", "SRT", "video", "thumbnail", "analytics loop"),
            evidence=statuses,
            next_steps=("Uruchom /api/production/readiness?live=true i usun wskazane blokery runtime.",) if not ready else (),
        )

    def _private_upload_item(self, youtube_status: Any) -> ShortsOnboardingItem:
        ready = bool(youtube_status.ready)
        return ShortsOnboardingItem(
            key="youtube_private_upload",
            label="Prywatny upload YouTube",
            category="upload",
            status="ready" if ready else "missing",
            required=True,
            user_data_needed=(
                "Zgoda OAuth dla YouTube Studio z youtube.upload, youtube.readonly i yt-analytics.readonly.",
            ),
            unlocks=("private upload", "processing status", "analytics follow-ups"),
            evidence={
                "configured": youtube_status.configured,
                "connected": youtube_status.connected,
                "ready": youtube_status.ready,
                "granted_scopes": list(youtube_status.granted_scopes),
                "channel_title": youtube_status.channel_title,
            },
            next_steps=("Uruchom POST /oauth/youtube-studio/start i dokoncz zgode Google.",) if not ready else (),
        )

    def _publication_policy_item(self) -> ShortsOnboardingItem:
        private_first = self.settings.youtube_upload_privacy == "private"
        public_blocked = not self.settings.allow_public_upload
        return ShortsOnboardingItem(
            key="publication_policy",
            label="Polityka publikacji",
            category="upload",
            status="ready" if private_first and public_blocked else "needs_action",
            required=True,
            user_data_needed=(
                "Decyzja: tylko private po QA, czy harmonogram public po recenzji.",
            ),
            unlocks=("safe upload flow", "no accidental public publishing"),
            evidence={
                "youtube_upload_privacy": self.settings.youtube_upload_privacy,
                "allow_public_upload": self.settings.allow_public_upload,
                "require_upload_approval": self.settings.require_upload_approval,
            },
            next_steps=("Ustaw private-first i approval przed publicznym uploadem.",) if not (private_first and public_blocked) else (),
        )

    def _monetization_item(self, account_counts: dict[str, int]) -> ShortsOnboardingItem:
        has_payment_or_crm = any(account_counts.get(key, 0) > 0 for key in ("stripe", "hubspot"))
        return ShortsOnboardingItem(
            key="monetization_plan",
            label="Plan zarabiania",
            category="monetization",
            status="ready" if has_payment_or_crm else "missing",
            required=True,
            user_data_needed=(
                "Model zarabiania: AdSense, afiliacje, leady, produkt cyfrowy albo newsletter.",
                "Linki afiliacyjne/CTA i lista tematow zakazanych.",
            ),
            unlocks=("descriptions with CTA", "revenue tracking", "campaign optimization"),
            evidence={
                "stripe_account_exists": account_counts.get("stripe", 0) > 0,
                "hubspot_account_exists": account_counts.get("hubspot", 0) > 0,
                "note": "Konto narzedziowe nie zastepuje decyzji o modelu zarabiania i CTA.",
            },
            next_steps=(
                "Podaj docelowy model zarabiania oraz linki/CTA, ktore Atnos ma wpisywac w opisy.",
            ) if not has_payment_or_crm else (
                "Dopisz konkretne CTA i linki afiliacyjne, jezeli maja wejsc do opisow.",
            ),
        )

    def _intelligence_counts(self) -> dict[str, int]:
        tables = ("yt_signals", "gtrends_signals", "competitor_channels", "competitor_videos", "reference_channels")
        counts: dict[str, int] = {}
        try:
            with connect(self.settings) as db:
                existing = {
                    str(row["name"])
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                for table in tables:
                    counts[table] = (
                        int(db.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
                        if table in existing
                        else 0
                    )
        except Exception:
            return {table: 0 for table in tables}
        return counts


def _count_accounts_by_provider(accounts: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for account in accounts:
        key = str(getattr(account, "provider_key", "") or "").strip().lower()
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts
