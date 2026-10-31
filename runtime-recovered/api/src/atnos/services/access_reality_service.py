from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from time import monotonic
from typing import Any, ClassVar, Final

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.content_access_reality_service import ContentAccessRealityService
from atnos.services.gmail_connector_service import GmailConnectorService
from atnos.services.github_connector_probe_service import GitHubConnectorProbeService
from atnos.services.google_calendar_connector_service import GoogleCalendarConnectorService
from atnos.services.local_host_control_service import LocalHostControlService
from atnos.services.oauth_service import OAuthService
from atnos.services.runtime_inventory_service import RuntimeInventoryService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.trusted_auto_service import TrustedAutoService
from atnos.services.youtube_connector_service import YouTubeConnectorService


ACCESS_LEVELS: Final[set[str]] = {
    "none",
    "identity_only",
    "metadata_only",
    "configured",
    "live_read",
    "live_write",
    "blocked",
    "unavailable",
}
ACCESS_LEVEL_RANK: Final[dict[str, int]] = {
    "none": 0,
    "identity_only": 1,
    "metadata_only": 2,
    "configured": 3,
    "live_read": 4,
    "live_write": 5,
    "blocked": 6,
    "unavailable": 7,
}
PROMPT_EXPANSION_TOKENS: Final[tuple[str, ...]] = (
    "dostep",
    "dostęp",
    "uprawn",
    "konto",
    "konta",
    "mail",
    "email",
    "e-mail",
    "gmail",
    "oauth",
    "connector",
    "polacz",
    "połącz",
    "podlacz",
    "podłącz",
    "youtube",
    "github",
    "calendar",
    "kalendar",
    "browser",
    "plik",
    "pliki",
    "lokaln",
    "internet",
    "moz",
    "moż",
    "moge",
    "mogę",
    "potraf",
    "czy masz",
    "czy mozesz",
    "czy możesz",
)
MAIL_SUBJECT_KEYS: Final[set[str]] = {
    "identity.owner_email",
    "connector.gmail",
    "platform.bootstrap-mailbox",
}


@dataclass(frozen=True, slots=True)
class AccessSubjectStatus:
    subject_key: str
    subject_type: str
    display_name: str
    access_level: str
    summary: str
    reasons: tuple[str, ...] = ()
    available_actions: tuple[str, ...] = ()
    account_label: str | None = None
    connector_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    last_checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "subject_type": self.subject_type,
            "display_name": self.display_name,
            "access_level": self.access_level,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "available_actions": list(self.available_actions),
            "account_label": self.account_label,
            "connector_key": self.connector_key,
            "metadata": self.metadata,
            "last_checked_at": self.last_checked_at.isoformat(),
        }

    def material_payload(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "subject_type": self.subject_type,
            "display_name": self.display_name,
            "access_level": self.access_level,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "available_actions": list(self.available_actions),
            "account_label": self.account_label,
            "connector_key": self.connector_key,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class AccessRealitySummary:
    total_subjects: int
    none: int
    identity_only: int
    metadata_only: int
    configured: int
    live_read: int
    live_write: int
    blocked: int
    unavailable: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total_subjects": self.total_subjects,
            "none": self.none,
            "identity_only": self.identity_only,
            "metadata_only": self.metadata_only,
            "configured": self.configured,
            "live_read": self.live_read,
            "live_write": self.live_write,
            "blocked": self.blocked,
            "unavailable": self.unavailable,
        }


@dataclass(frozen=True, slots=True)
class AccessRealitySnapshot:
    snapshot_key: str
    checked_at: datetime
    summary: AccessRealitySummary
    subjects: tuple[AccessSubjectStatus, ...]
    operational_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_key": self.snapshot_key,
            "checked_at": self.checked_at.isoformat(),
            "summary": self.summary.to_dict(),
            "subjects": [item.to_dict() for item in self.subjects],
            "operational_context": self.operational_context,
        }


@dataclass(frozen=True, slots=True)
class AccessChange:
    subject_key: str
    subject_type: str
    display_name: str
    from_access_level: str
    to_access_level: str
    change_kind: str
    title: str
    detail: str
    user_visible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "subject_type": self.subject_type,
            "display_name": self.display_name,
            "from_access_level": self.from_access_level,
            "to_access_level": self.to_access_level,
            "change_kind": self.change_kind,
            "title": self.title,
            "detail": self.detail,
            "user_visible": self.user_visible,
        }


class AccessRealityService:
    _COMPACT_CHAT_BLOCK_CACHE_TTL_SECONDS: ClassVar[float] = 2.0
    _compact_chat_block_cache: ClassVar[dict[tuple[str, bool], tuple[float, str | None]]] = {}

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.users = UserRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.runtime_settings = RuntimeSettingsService(session, settings)
        self.oauth = OAuthService(session, settings)
        self.accounts = AccountCredentialsService(session, settings)
        self.trusted_auto = TrustedAutoService(session, settings)

    @classmethod
    def clear_cache(cls) -> None:
        cls._compact_chat_block_cache.clear()

    def build_snapshot(self, *, include_live_health: bool = True) -> AccessRealitySnapshot:
        checked_at = self._utcnow()
        owner = self.users.get_owner()
        owner_email = owner.email.strip() if owner and owner.email and owner.email.strip() else None
        policy = self.execution_policies.get_for_user(owner.id) if owner is not None else None
        runtime_controls = self.runtime_settings.snapshot()
        runtime_inventory = RuntimeInventoryService(
            self.session,
            provider_registry=self.provider_registry,
            settings=self.settings,
        ).snapshot(include_live_health=include_live_health)
        oauth_connectors = self.oauth.list_connectors()
        content_snapshot = ContentAccessRealityService(self.session, self.settings).build_snapshot()
        accounts = self.trusted_auto.list_accounts()
        local_host = LocalHostControlService(
            self.session,
            settings=self.settings,
        ).get_capability_snapshot()

        subjects = tuple(
            self._sort_subjects(
                [
                    *self._build_identity_subjects(
                        checked_at=checked_at,
                        owner_email=owner_email,
                    ),
                    *self._build_policy_subjects(
                        checked_at=checked_at,
                        policy=policy,
                    ),
                    *self._build_runtime_subjects(
                        checked_at=checked_at,
                        runtime_controls=runtime_controls,
                    ),
                    *self._build_provider_subjects(
                        checked_at=checked_at,
                        runtime_inventory=runtime_inventory,
                    ),
                    *self._build_connector_subjects(
                        checked_at=checked_at,
                        runtime_controls=runtime_controls,
                        oauth_connectors=oauth_connectors,
                    ),
                    *self._build_platform_subjects(
                        checked_at=checked_at,
                        content_snapshot=content_snapshot,
                    ),
                    *self._build_trusted_account_subjects(
                        checked_at=checked_at,
                        accounts=accounts,
                    ),
                    *self._build_local_host_subjects(
                        checked_at=checked_at,
                        local_host=local_host,
                    ),
                ]
            )
        )
        summary = AccessRealitySummary(
            total_subjects=len(subjects),
            none=sum(1 for item in subjects if item.access_level == "none"),
            identity_only=sum(1 for item in subjects if item.access_level == "identity_only"),
            metadata_only=sum(1 for item in subjects if item.access_level == "metadata_only"),
            configured=sum(1 for item in subjects if item.access_level == "configured"),
            live_read=sum(1 for item in subjects if item.access_level == "live_read"),
            live_write=sum(1 for item in subjects if item.access_level == "live_write"),
            blocked=sum(1 for item in subjects if item.access_level == "blocked"),
            unavailable=sum(1 for item in subjects if item.access_level == "unavailable"),
        )
        operational_context = {
            "app_env": self.settings.app_env,
            "local_platform": local_host.platform,
            "browser_recipe_available": local_host.browser_recipe_available,
            "execution_enabled": local_host.execution_enabled,
            "oauth_enabled": runtime_controls.oauth_enabled,
            "scheduler_enabled": runtime_controls.scheduler_enabled,
        }
        snapshot_key = self._build_snapshot_key(summary=summary, subjects=subjects, operational_context=operational_context)
        return AccessRealitySnapshot(
            snapshot_key=snapshot_key,
            checked_at=checked_at,
            summary=summary,
            subjects=subjects,
            operational_context=operational_context,
        )

    def diff(
        self,
        previous: AccessRealitySnapshot | None,
        current: AccessRealitySnapshot,
    ) -> tuple[AccessChange, ...]:
        if previous is None:
            return ()
        previous_map = {item.subject_key: item for item in previous.subjects}
        current_map = {item.subject_key: item for item in current.subjects}
        changes: list[AccessChange] = []
        for subject_key in sorted(set(previous_map) | set(current_map)):
            before = previous_map.get(subject_key)
            after = current_map.get(subject_key)
            if before is not None and after is not None and before.material_payload() == after.material_payload():
                continue
            changes.append(self._build_change(before=before, after=after))
        return tuple(changes)

    def build_chat_block(
        self,
        *,
        user_message: str,
        include_live_health: bool = True,
    ) -> str | None:
        expanded = self.should_expand_for_message(user_message)
        cache_key = self._compact_chat_block_cache_key(include_live_health=include_live_health) if not expanded else None
        if cache_key is not None:
            hit, cached_block = self._read_compact_chat_block_cache(cache_key)
            if hit:
                return cached_block

        snapshot = self.build_snapshot(include_live_health=include_live_health)
        block = self._render_chat_block(snapshot=snapshot, expanded=expanded)
        if cache_key is not None:
            self._compact_chat_block_cache[cache_key] = (monotonic(), block)
        return block

    def _render_chat_block(self, *, snapshot: AccessRealitySnapshot, expanded: bool) -> str | None:
        subject_lines: list[str] = []
        for item in snapshot.subjects:
            if not expanded and not self._include_in_compact_block(item):
                continue
            reasons = "; ".join(item.reasons) if item.reasons else "none"
            actions = ", ".join(item.available_actions) if item.available_actions else "none"
            account = item.account_label or "none"
            connector = item.connector_key or "none"
            subject_lines.append(
                f"- {item.subject_key}: level={item.access_level}; type={item.subject_type}; "
                f"summary={item.summary}; account={account}; connector={connector}; "
                f"actions={actions}; reasons={reasons}"
            )
        if not subject_lines:
            return None

        mode = "expanded" if expanded else "compact"
        return (
            "Access reality snapshot:\n"
            f"- mode: {mode}\n"
            f"- snapshot_key: {snapshot.snapshot_key}\n"
            f"- owner_email_access: {self._mail_access_line(snapshot)}\n"
            + "\n".join(subject_lines)
            + "\n- The owner email from Settings is identity only unless a matching live mail source is explicitly live_read or live_write."
            + "\n- Claim access only when the snapshot shows live_read or live_write for the relevant source."
            + "\n- If a source is metadata_only or configured, say it exists but live access is not active yet."
            + "\n- If a source is blocked, name the blocker plainly: policy, runtime switch, missing secret, missing OAuth session, missing grant, or unavailable runtime."
            + "\n- If only identity.owner_email exists for mail, say exactly: 'Znam Twoj adres z ustawien, ale nie mam dostepu do skrzynki.'"
        )

    def _compact_chat_block_cache_key(self, *, include_live_health: bool) -> tuple[str, bool]:
        try:
            data_dir = str(self.settings.data_dir.resolve())
        except OSError:
            data_dir = str(self.settings.data_dir)
        return (data_dir, bool(include_live_health))

    @classmethod
    def _read_compact_chat_block_cache(cls, cache_key: tuple[str, bool]) -> tuple[bool, str | None]:
        cached = cls._compact_chat_block_cache.get(cache_key)
        if cached is None:
            return False, None
        created_at, block = cached
        if monotonic() - created_at > cls._COMPACT_CHAT_BLOCK_CACHE_TTL_SECONDS:
            cls._compact_chat_block_cache.pop(cache_key, None)
            return False, None
        return True, block

    @staticmethod
    def should_expand_for_message(user_message: str) -> bool:
        normalized = " ".join((user_message or "").lower().split())
        return any(token in normalized for token in PROMPT_EXPANSION_TOKENS)

    def _build_identity_subjects(
        self,
        *,
        checked_at: datetime,
        owner_email: str | None,
    ) -> list[AccessSubjectStatus]:
        if owner_email is None:
            return [
                AccessSubjectStatus(
                    subject_key="identity.owner_email",
                    subject_type="identity",
                    display_name="Owner email",
                    access_level="none",
                    summary="No owner email is configured in Settings.",
                    reasons=("Owner profile email is empty.",),
                    last_checked_at=checked_at,
                )
            ]
        return [
            AccessSubjectStatus(
                subject_key="identity.owner_email",
                subject_type="identity",
                display_name="Owner email",
                access_level="identity_only",
                summary="Atnos knows the owner email as identity only.",
                reasons=("Profile email does not grant inbox access.",),
                metadata={"email_masked": self._mask_email(owner_email)},
                last_checked_at=checked_at,
            )
        ]

    def _build_policy_subjects(
        self,
        *,
        checked_at: datetime,
        policy,
    ) -> list[AccessSubjectStatus]:
        if policy is None:
            reason = "Execution policy is not configured for the owner user."
            return [
                AccessSubjectStatus(
                    subject_key="policy.local_file_read",
                    subject_type="policy",
                    display_name="Local file read policy",
                    access_level="blocked",
                    summary="Local file read is blocked because execution policy is missing.",
                    reasons=(reason,),
                    last_checked_at=checked_at,
                ),
                AccessSubjectStatus(
                    subject_key="policy.local_file_write",
                    subject_type="policy",
                    display_name="Local file write policy",
                    access_level="blocked",
                    summary="Local file write is blocked because execution policy is missing.",
                    reasons=(reason,),
                    last_checked_at=checked_at,
                ),
                AccessSubjectStatus(
                    subject_key="policy.web_read",
                    subject_type="policy",
                    display_name="Web read policy",
                    access_level="blocked",
                    summary="Web read is blocked because execution policy is missing.",
                    reasons=(reason,),
                    last_checked_at=checked_at,
                ),
                AccessSubjectStatus(
                    subject_key="policy.external_fallback",
                    subject_type="policy",
                    display_name="External fallback policy",
                    access_level="blocked",
                    summary="External fallback is blocked because execution policy is missing.",
                    reasons=(reason,),
                    last_checked_at=checked_at,
                ),
            ]
        return [
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="policy.local_file_read",
                subject_type="policy",
                display_name="Local file read policy",
                enabled=bool(policy.allow_local_file_read),
                enabled_level="live_read",
                enabled_summary="Local file reads are allowed by policy.",
                disabled_summary="Local file reads are blocked by policy.",
            ),
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="policy.local_file_write",
                subject_type="policy",
                display_name="Local file write policy",
                enabled=bool(policy.allow_local_file_write),
                enabled_level="live_write",
                enabled_summary="Local file writes are allowed by policy.",
                disabled_summary="Local file writes are blocked by policy.",
            ),
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="policy.web_read",
                subject_type="policy",
                display_name="Web read policy",
                enabled=bool(policy.allow_web_read),
                enabled_level="live_read",
                enabled_summary="Web read is allowed by policy.",
                disabled_summary="Web read is blocked by policy.",
            ),
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="policy.external_fallback",
                subject_type="policy",
                display_name="External fallback policy",
                enabled=bool(policy.allow_external_fallback),
                enabled_level="configured",
                enabled_summary="External fallback is allowed by policy.",
                disabled_summary="External fallback is blocked by policy.",
            ),
        ]

    def _build_runtime_subjects(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
    ) -> list[AccessSubjectStatus]:
        return [
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="runtime.browser_control",
                subject_type="runtime",
                display_name="Browser control",
                enabled=bool(runtime_controls.browser_control_enabled),
                enabled_level="live_write",
                enabled_summary="Browser control is enabled in runtime.",
                disabled_summary="Browser control is disabled in runtime.",
            ),
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="runtime.external_actions",
                subject_type="runtime",
                display_name="External actions",
                enabled=bool(runtime_controls.external_actions_enabled),
                enabled_level="live_write",
                enabled_summary="External actions are enabled in runtime.",
                disabled_summary="External actions are disabled in runtime.",
            ),
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="runtime.oauth",
                subject_type="runtime",
                display_name="OAuth runtime",
                enabled=bool(runtime_controls.oauth_enabled),
                enabled_level="configured",
                enabled_summary="OAuth is enabled in runtime.",
                disabled_summary="OAuth is disabled in runtime.",
            ),
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="runtime.scheduler",
                subject_type="runtime",
                display_name="Scheduler runtime",
                enabled=bool(runtime_controls.scheduler_enabled),
                enabled_level="configured",
                enabled_summary="Scheduler is enabled in runtime.",
                disabled_summary="Scheduler is disabled in runtime.",
            ),
            self._boolean_subject(
                checked_at=checked_at,
                subject_key="runtime.voice_replies",
                subject_type="runtime",
                display_name="Voice replies",
                enabled=bool(runtime_controls.voice_replies_enabled),
                enabled_level="configured",
                enabled_summary="Voice replies are enabled in runtime.",
                disabled_summary="Voice replies are disabled in runtime.",
            ),
        ]

    def _build_provider_subjects(
        self,
        *,
        checked_at: datetime,
        runtime_inventory,
    ) -> list[AccessSubjectStatus]:
        subjects: list[AccessSubjectStatus] = []
        for provider in runtime_inventory.providers:
            access_level = self._provider_access_level(provider.status, provider.health_status)
            reasons = tuple(
                reason
                for reason in (provider.status_reason, provider.health_reason)
                if reason and reason != provider.status_reason
            )
            primary_reason = provider.health_reason or provider.status_reason
            summary = (
                "Provider is reachable and can enter routing."
                if access_level == "live_read"
                else primary_reason or "Provider is not ready."
            )
            subjects.append(
                AccessSubjectStatus(
                    subject_key=f"provider.{provider.provider_key}",
                    subject_type="provider",
                    display_name=provider.label,
                    access_level=access_level,
                    summary=summary,
                    reasons=reasons,
                    metadata={
                        "provider_key": provider.provider_key,
                        "provider_status": provider.status,
                        "health_status": provider.health_status,
                        "installed_models": list(provider.installed_models),
                    },
                    last_checked_at=checked_at,
                )
            )
        return subjects

    def _build_connector_subjects(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
        oauth_connectors,
    ) -> list[AccessSubjectStatus]:
        subjects: list[AccessSubjectStatus] = []
        handled_keys: set[str] = set()

        gmail_status = GmailConnectorService(self.session, self.settings).get_status()
        subjects.append(
            self._connector_subject_from_status_view(
                checked_at=checked_at,
                connector_key=gmail_status.connector_key,
                display_name=gmail_status.display_name,
                runtime_oauth_enabled=bool(runtime_controls.oauth_enabled),
                provider_configured=bool(gmail_status.provider_configured),
                connected=bool(gmail_status.connected),
                live_write_enabled=bool(gmail_status.live_write_enabled),
                granted_scopes=gmail_status.granted_scopes,
                warnings=gmail_status.warnings,
                summary_connected_read="Gmail connector is connected for live read.",
                summary_connected_write="Gmail connector is connected for live write.",
                summary_needs_auth="Gmail connector exists but has no active OAuth session.",
                summary_not_configured="Gmail connector is not configured in runtime.",
            )
        )
        handled_keys.update({"gmail", "gmail-readonly"})

        calendar_status = GoogleCalendarConnectorService(self.session, self.settings).get_status()
        subjects.append(
            self._connector_subject_from_status_view(
                checked_at=checked_at,
                connector_key=calendar_status.connector_key,
                display_name=calendar_status.display_name,
                runtime_oauth_enabled=bool(runtime_controls.oauth_enabled),
                provider_configured=bool(calendar_status.provider_configured),
                connected=bool(calendar_status.connected),
                live_write_enabled=bool(calendar_status.live_write_enabled),
                granted_scopes=calendar_status.granted_scopes,
                warnings=calendar_status.warnings,
                summary_connected_read="Google Calendar connector is connected for live read.",
                summary_connected_write="Google Calendar connector is connected for live write.",
                summary_needs_auth="Google Calendar connector exists but has no active OAuth session.",
                summary_not_configured="Google Calendar connector is not configured in runtime.",
            )
        )
        handled_keys.update({"google-calendar", "google-calendar-readonly"})

        github_status = GitHubConnectorProbeService(self.session, self.settings).get_status()
        subjects.append(
            self._connector_subject_from_status_view(
                checked_at=checked_at,
                connector_key=github_status.connector_key,
                display_name=github_status.display_name,
                runtime_oauth_enabled=bool(runtime_controls.oauth_enabled),
                provider_configured=bool(github_status.provider_configured),
                connected=bool(github_status.live_read_enabled),
                live_write_enabled=bool(github_status.live_write_enabled),
                granted_scopes=github_status.granted_scopes,
                warnings=github_status.warnings,
                summary_connected_read="GitHub connector is connected for live read.",
                summary_connected_write="GitHub connector is connected for live write.",
                summary_needs_auth="GitHub connector exists but has no active OAuth session.",
                summary_not_configured="GitHub connector is not configured in runtime.",
            )
        )
        handled_keys.update({"github", "github-readonly"})

        youtube_status = YouTubeConnectorService(self.session, self.settings).get_status()
        subjects.append(
            self._connector_subject_from_status_view(
                checked_at=checked_at,
                connector_key=youtube_status.connector_key,
                display_name=youtube_status.display_name,
                runtime_oauth_enabled=bool(runtime_controls.oauth_enabled),
                provider_configured=bool(youtube_status.configured),
                connected=bool(youtube_status.connected),
                live_write_enabled=bool(youtube_status.connected),
                granted_scopes=youtube_status.granted_scopes,
                warnings=youtube_status.warnings,
                summary_connected_read="YouTube connector is connected.",
                summary_connected_write="YouTube connector is connected for supervised publish workflows.",
                summary_needs_auth="YouTube connector exists but has no active OAuth session.",
                summary_not_configured="YouTube connector is not configured in runtime.",
            )
        )
        handled_keys.update({"youtube-studio", "youtube"})

        for connector in sorted(oauth_connectors, key=lambda item: item.connector_key):
            if connector.connector_key in handled_keys:
                continue
            subjects.append(
                self._generic_connector_subject(
                    checked_at=checked_at,
                    runtime_oauth_enabled=bool(runtime_controls.oauth_enabled),
                    connector=connector,
                )
            )
        return subjects

    def _build_platform_subjects(
        self,
        *,
        checked_at: datetime,
        content_snapshot,
    ) -> list[AccessSubjectStatus]:
        subjects: list[AccessSubjectStatus] = []
        for item in content_snapshot.items:
            access_level = self._content_status_to_access_level(item.status)
            summary = self._content_status_summary(item.status, item.display_name)
            subjects.append(
                AccessSubjectStatus(
                    subject_key=f"platform.{item.platform_key}",
                    subject_type="platform",
                    display_name=item.display_name,
                    access_level=access_level,
                    summary=summary,
                    reasons=tuple(item.blocking_reasons),
                    available_actions=tuple(item.available_actions),
                    account_label=item.account_label,
                    connector_key=item.connector_key,
                    metadata={
                        "source_status": item.status,
                        "connected": item.connected,
                        "needs_auth": item.needs_auth,
                        "missing_grant": item.missing_grant,
                        "granted_scopes": list(item.granted_scopes),
                        "linked_projects": list(item.linked_projects),
                    },
                    last_checked_at=checked_at,
                )
            )
        return subjects

    def _build_trusted_account_subjects(
        self,
        *,
        checked_at: datetime,
        accounts: list[dict[str, Any]],
    ) -> list[AccessSubjectStatus]:
        subjects: list[AccessSubjectStatus] = []
        for account in sorted(accounts, key=lambda item: (str(item.get("label") or ""), int(item.get("account_id") or 0))):
            status = str(account.get("status") or "unknown")
            has_secret = bool(account.get("has_secret"))
            allowed_capabilities = tuple(
                str(value) for value in (account.get("allowed_capabilities") or []) if str(value).strip()
            )
            trust_grants = list(account.get("trust_grants") or [])
            write_allowed = any(
                bool(grant.get("publish_allowed")) or bool(grant.get("connector_write_allowed"))
                for grant in trust_grants
                if isinstance(grant, dict)
            ) or any(
                capability in {"draft_write", "confirmed_write_later", "connector_write", "publish_later"}
                for capability in allowed_capabilities
            )
            if status == "disabled":
                access_level = "blocked"
                summary = "Trusted account is disabled."
                reasons = ("Trusted account status is disabled.",)
            elif not has_secret:
                access_level = "metadata_only"
                summary = "Trusted account metadata exists, but no secret is stored."
                reasons = ("No secret is stored for this trusted account.",)
            elif write_allowed:
                access_level = "live_write"
                summary = "Trusted account has a secret and write-capable trust coverage."
                reasons = ()
            else:
                access_level = "configured"
                summary = "Trusted account has a secret, but write coverage is not active."
                reasons = ("No enabled trust grant currently expands this account to live write.",)

            subjects.append(
                AccessSubjectStatus(
                    subject_key=f"trusted_account.{int(account.get('account_id') or 0)}",
                    subject_type="trusted_account",
                    display_name=str(account.get("label") or "Trusted account"),
                    access_level=access_level,
                    summary=summary,
                    reasons=reasons,
                    account_label=str(account.get("label") or "Trusted account"),
                    connector_key=(
                        str(account.get("linked_integration_key"))
                        if account.get("linked_integration_key") is not None
                        else None
                    ),
                    metadata={
                        "provider_key": str(account.get("provider_key") or ""),
                        "status": status,
                        "has_secret": has_secret,
                        "grant_count": len(trust_grants),
                        "write_allowed": write_allowed,
                        "allowed_capabilities": list(allowed_capabilities),
                    },
                    last_checked_at=checked_at,
                )
            )
        return subjects

    def _build_local_host_subjects(
        self,
        *,
        checked_at: datetime,
        local_host,
    ) -> list[AccessSubjectStatus]:
        browser_level = (
            "live_write"
            if local_host.browser_recipe_available
            else ("configured" if local_host.playwright_adapter_available else "unavailable")
        )
        browser_summary = (
            "Local browser recipe executor is ready."
            if local_host.browser_recipe_available
            else (
                "Playwright adapter exists, but browser recipes are not active."
                if local_host.playwright_adapter_available
                else "No local Playwright browser adapter is available."
            )
        )
        browser_reasons = ()
        if not local_host.browser_recipe_available:
            browser_reasons = (
                "Browser runtime requires Playwright plus browser control enabled.",
            )

        file_level = (
            "live_write"
            if local_host.execution_enabled and local_host.powershell_available
            else ("configured" if local_host.powershell_available else "unavailable")
        )
        desktop_level = (
            "live_write"
            if local_host.desktop_input_available and local_host.execution_enabled
            else ("configured" if local_host.desktop_input_available else "unavailable")
        )
        screen_capture_available, screen_capture_reason = self._screen_capture_availability()
        screen_level = "live_read" if screen_capture_available else "unavailable"
        visual_qa_dir = getattr(self.settings, "visual_qa_dir", "")
        return [
            AccessSubjectStatus(
                subject_key="local_host.browser_recipe",
                subject_type="local_host",
                display_name="Local browser recipes",
                access_level=browser_level,
                summary=browser_summary,
                reasons=browser_reasons,
                metadata={
                    "playwright_adapter_available": local_host.playwright_adapter_available,
                    "browser_open_available": local_host.browser_open_available,
                    "browser_recipe_available": local_host.browser_recipe_available,
                },
                last_checked_at=checked_at,
            ),
            AccessSubjectStatus(
                subject_key="local_host.filesystem",
                subject_type="local_host",
                display_name="Local filesystem execution",
                access_level=file_level,
                summary=(
                    "Local command execution is available."
                    if file_level == "live_write"
                    else (
                        "PowerShell exists, but local execution is not active."
                        if file_level == "configured"
                        else "Local command execution is unavailable."
                    )
                ),
                reasons=(
                    ()
                    if file_level == "live_write"
                    else ("Execution mode or PowerShell availability is not sufficient.",)
                ),
                metadata={
                    "powershell_available": local_host.powershell_available,
                    "execution_enabled": local_host.execution_enabled,
                    "current_workdir": local_host.current_workdir,
                },
                last_checked_at=checked_at,
            ),
            AccessSubjectStatus(
                subject_key="local_host.screen_capture",
                subject_type="local_host",
                display_name="Desktop screen capture",
                access_level=screen_level,
                summary=(
                    "Desktop screen capture is available."
                    if screen_capture_available
                    else "Desktop screen capture is unavailable."
                ),
                reasons=() if screen_capture_available else (screen_capture_reason,),
                available_actions=("capture_screenshot", "record_screen") if screen_capture_available else (),
                metadata={
                    "visual_qa_dir": str(visual_qa_dir),
                    "screen_capture_available": screen_capture_available,
                },
                last_checked_at=checked_at,
            ),
            AccessSubjectStatus(
                subject_key="local_host.desktop_input",
                subject_type="local_host",
                display_name="Desktop input bridge",
                access_level=desktop_level,
                summary=(
                    "Desktop input bridge is available."
                    if desktop_level == "live_write"
                    else (
                        "Desktop input bridge exists, but execution is not active."
                        if desktop_level == "configured"
                        else "Desktop input bridge is unavailable."
                    )
                ),
                reasons=(
                    ()
                    if desktop_level == "live_write"
                    else ("Desktop automation requires execution mode plus local desktop input support.",)
                ),
                metadata={
                    "desktop_input_available": local_host.desktop_input_available,
                    "execution_enabled": local_host.execution_enabled,
                    "elevated": local_host.elevated,
                },
                last_checked_at=checked_at,
            ),
        ]

    def _screen_capture_availability(self) -> tuple[bool, str]:
        try:
            from PIL import ImageGrab  # noqa: F401
        except Exception as exc:
            return False, f"Pillow ImageGrab is unavailable: {type(exc).__name__}: {exc}"
        try:
            self.settings.visual_qa_dir.mkdir(parents=True, exist_ok=True)
            (self.settings.visual_qa_dir / "captures").mkdir(parents=True, exist_ok=True)
            (self.settings.visual_qa_dir / "recordings").mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return False, f"Screen artifact directories are not writable: {type(exc).__name__}: {exc}"
        return True, "Screen capture dependencies and artifact directories are available."

    @staticmethod
    def _sort_subjects(subjects: list[AccessSubjectStatus]) -> list[AccessSubjectStatus]:
        return sorted(subjects, key=lambda item: (item.subject_type, item.subject_key))

    def _build_snapshot_key(
        self,
        *,
        summary: AccessRealitySummary,
        subjects: tuple[AccessSubjectStatus, ...],
        operational_context: dict[str, Any],
    ) -> str:
        payload = {
            "summary": summary.to_dict(),
            "subjects": [item.material_payload() for item in subjects],
            "operational_context": operational_context,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def _build_change(
        self,
        *,
        before: AccessSubjectStatus | None,
        after: AccessSubjectStatus | None,
    ) -> AccessChange:
        current = after or before
        assert current is not None
        from_level = before.access_level if before is not None else "none"
        to_level = after.access_level if after is not None else "none"
        change_kind = "state"
        if before is None:
            change_kind = "added"
        elif after is None:
            change_kind = "removed"
        elif before.access_level != after.access_level:
            change_kind = "access_level"

        if to_level == "live_write":
            title = f"{current.display_name} ma juz aktywny zapis"
        elif to_level == "live_read":
            title = f"{current.display_name} jest juz dostepne"
        elif to_level == "blocked":
            title = f"{current.display_name} zostalo zablokowane"
        elif to_level == "unavailable":
            title = f"{current.display_name} jest niedostepne"
        elif to_level == "configured":
            title = f"{current.display_name} jest skonfigurowane"
        elif to_level == "metadata_only":
            title = f"{current.display_name} ma tylko metadane"
        elif to_level == "identity_only":
            title = f"{current.display_name} jest tylko tozsamoscia"
        else:
            title = f"Zmienil sie dostep: {current.display_name}"

        detail = after.summary if after is not None else (before.summary if before is not None else "")
        if after is not None and after.reasons:
            detail = f"{detail} Powod: {after.reasons[0]}"
        # Trusted-account records describe stored bootstrap metadata, not the
        # live OAuth connector. Showing their state as a toast next to the
        # connector result produces contradictory messages such as "write is
        # active" and "metadata only" for the same YouTube account.
        user_visible = current.subject_type not in {"identity", "context", "trusted_account"}
        return AccessChange(
            subject_key=current.subject_key,
            subject_type=current.subject_type,
            display_name=current.display_name,
            from_access_level=from_level,
            to_access_level=to_level,
            change_kind=change_kind,
            title=title,
            detail=detail,
            user_visible=user_visible,
        )

    @staticmethod
    def _provider_access_level(provider_status: str, health_status: str) -> str:
        if provider_status == "ready":
            if health_status == "unreachable":
                return "unavailable"
            return "live_read"
        if provider_status == "disabled":
            return "blocked"
        if provider_status == "configuration_missing":
            return "configured"
        if provider_status == "capability_mismatch":
            return "blocked"
        if provider_status == "missing_adapter":
            return "unavailable"
        return "unavailable"

    @staticmethod
    def _content_status_to_access_level(status: str) -> str:
        mapping = {
            "blocked": "blocked",
            "metadata_only": "metadata_only",
            "read_ready": "live_read",
            "draft_ready": "configured",
            "supervised_ready": "live_write",
        }
        return mapping.get(status, "unavailable")

    @staticmethod
    def _content_status_summary(status: str, display_name: str) -> str:
        if status == "read_ready":
            return f"{display_name} is ready for live read workflows."
        if status == "draft_ready":
            return f"{display_name} is ready for draft workflows but not final write."
        if status == "supervised_ready":
            return f"{display_name} is ready for supervised write or publish."
        if status == "metadata_only":
            return f"{display_name} exists, but live access is not active yet."
        return f"{display_name} is currently blocked."

    @staticmethod
    def _boolean_subject(
        *,
        checked_at: datetime,
        subject_key: str,
        subject_type: str,
        display_name: str,
        enabled: bool,
        enabled_level: str,
        enabled_summary: str,
        disabled_summary: str,
    ) -> AccessSubjectStatus:
        return AccessSubjectStatus(
            subject_key=subject_key,
            subject_type=subject_type,
            display_name=display_name,
            access_level=enabled_level if enabled else "blocked",
            summary=enabled_summary if enabled else disabled_summary,
            reasons=() if enabled else (disabled_summary,),
            last_checked_at=checked_at,
        )

    @staticmethod
    def _connector_subject_from_status_view(
        *,
        checked_at: datetime,
        connector_key: str,
        display_name: str,
        runtime_oauth_enabled: bool,
        provider_configured: bool,
        connected: bool,
        live_write_enabled: bool,
        granted_scopes: tuple[str, ...],
        warnings: tuple[str, ...],
        summary_connected_read: str,
        summary_connected_write: str,
        summary_needs_auth: str,
        summary_not_configured: str,
    ) -> AccessSubjectStatus:
        if not runtime_oauth_enabled:
            access_level = "blocked"
            summary = "OAuth runtime is disabled."
            reasons = ("OAuth is disabled in runtime.",)
        elif not provider_configured:
            access_level = "blocked"
            summary = summary_not_configured
            reasons = tuple(warnings) or ("OAuth provider is not configured.",)
        elif not connected:
            access_level = "configured"
            summary = summary_needs_auth
            reasons = tuple(warnings) or ("No active OAuth session is connected.",)
        elif live_write_enabled:
            access_level = "live_write"
            summary = summary_connected_write
            reasons = tuple(warnings)
        else:
            access_level = "live_read"
            summary = summary_connected_read
            reasons = tuple(warnings)
        return AccessSubjectStatus(
            subject_key=f"connector.{connector_key}",
            subject_type="connector",
            display_name=display_name,
            access_level=access_level,
            summary=summary,
            reasons=reasons,
            connector_key=connector_key,
            metadata={"granted_scopes": list(granted_scopes)},
            last_checked_at=checked_at,
        )

    @staticmethod
    def _generic_connector_subject(
        *,
        checked_at: datetime,
        runtime_oauth_enabled: bool,
        connector,
    ) -> AccessSubjectStatus:
        if not runtime_oauth_enabled:
            access_level = "blocked"
            summary = "OAuth runtime is disabled."
            reasons = ("OAuth is disabled in runtime.",)
        elif not connector.configured:
            access_level = "blocked"
            summary = "Connector is not configured in runtime."
            reasons = tuple(connector.setup_warnings) or ("Connector is not configured.",)
        elif connector.status == "connected":
            access_level = "live_read"
            summary = "Connector is connected."
            reasons = tuple(connector.setup_warnings)
        else:
            access_level = "configured"
            summary = "Connector exists, but no active OAuth session is connected."
            reasons = tuple(connector.setup_warnings) or ("No active OAuth session is connected.",)
        return AccessSubjectStatus(
            subject_key=f"connector.{connector.connector_key}",
            subject_type="connector",
            display_name=connector.display_name,
            access_level=access_level,
            summary=summary,
            reasons=reasons,
            connector_key=connector.connector_key,
            metadata={
                "oauth_status": connector.status,
                "connection_id": connector.connection_id,
                "scopes": list(connector.scopes),
            },
            last_checked_at=checked_at,
        )

    @staticmethod
    def _include_in_compact_block(subject: AccessSubjectStatus) -> bool:
        if subject.subject_type in {"identity", "policy", "runtime"}:
            return subject.access_level in {"blocked", "identity_only", "configured", "live_read", "live_write"}
        if subject.subject_type in {"provider", "connector", "platform", "trusted_account", "local_host"}:
            return subject.access_level in {"metadata_only", "configured", "live_read", "live_write", "blocked", "unavailable"}
        return False

    @staticmethod
    def _mail_access_line(snapshot: AccessRealitySnapshot) -> str:
        live_mail = [
            item.subject_key
            for item in snapshot.subjects
            if item.subject_key in MAIL_SUBJECT_KEYS and item.access_level in {"live_read", "live_write"}
        ]
        if live_mail:
            return "mail_live_access_present"
        return "identity_only_or_no_live_mail"

    @staticmethod
    def _mask_email(email: str) -> str:
        local_part, _, domain = email.partition("@")
        if not local_part or not domain:
            return email
        if len(local_part) <= 2:
            return f"{local_part[0]}*@{domain}"
        return f"{local_part[:2]}***@{domain}"

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
