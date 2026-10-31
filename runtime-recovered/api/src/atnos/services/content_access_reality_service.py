from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Final

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import read_intelligence_account_configs
from atnos.repositories.trust_grant_repository import TrustGrantRepository
from atnos.services.account_credentials_service import AccountCredentialView, AccountCredentialsService
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.creator_ops_service import CreatorOpsService
from atnos.services.github_connector_probe_service import GitHubConnectorProbeService
from atnos.services.google_calendar_connector_service import GoogleCalendarConnectorService
from atnos.services.oauth_service import OAuthConnectionView, OAuthConnectorView, OAuthService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.youtube_connector_service import YouTubeConnectorService


CONTENT_READINESS_STATUSES: Final[set[str]] = {
    "blocked",
    "metadata_only",
    "read_ready",
    "draft_ready",
    "supervised_ready",
}
BOOTSTRAP_MAILBOX_PROVIDER_KEYS: Final[set[str]] = {
    "bootstrap-mailbox",
    "bootstrap_mailbox",
    "recovery-mailbox",
    "recovery_mailbox",
    "int_mail",
    "int-mail",
}
YOUTUBE_CONNECTOR_KEYS: Final[set[str]] = {"youtube", "youtube-studio"}
CALENDAR_CONNECTOR_KEYS: Final[set[str]] = {"google-calendar", "google-calendar-readonly"}
GITHUB_CONNECTOR_KEYS: Final[set[str]] = {"github", "github-readonly"}


@dataclass(frozen=True, slots=True)
class ContentReadinessItem:
    platform_key: str
    display_name: str
    status: str
    available_actions: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    account_label: str | None
    connector_key: str | None
    requires_approval: bool
    last_checked_at: datetime
    connected: bool
    needs_auth: bool
    missing_grant: bool
    connection_id: int | None = None
    account_id: int | None = None
    content_account_id: str | None = None
    granted_scopes: tuple[str, ...] = ()
    linked_projects: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_key": self.platform_key,
            "display_name": self.display_name,
            "status": self.status,
            "available_actions": list(self.available_actions),
            "blocking_reasons": list(self.blocking_reasons),
            "account_label": self.account_label,
            "connector_key": self.connector_key,
            "requires_approval": self.requires_approval,
            "last_checked_at": self.last_checked_at.isoformat(),
            "connected": self.connected,
            "needs_auth": self.needs_auth,
            "missing_grant": self.missing_grant,
            "connection_id": self.connection_id,
            "account_id": self.account_id,
            "content_account_id": self.content_account_id,
            "granted_scopes": list(self.granted_scopes),
            "linked_projects": list(self.linked_projects),
        }


@dataclass(frozen=True, slots=True)
class ContentReadinessSummary:
    total_items: int
    blocked: int
    metadata_only: int
    read_ready: int
    draft_ready: int
    supervised_ready: int
    publish_mode: str
    primary_youtube_status: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ContentReadinessSnapshot:
    snapshot_key: str
    checked_at: datetime
    summary: ContentReadinessSummary
    items: tuple[ContentReadinessItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_key": self.snapshot_key,
            "checked_at": self.checked_at.isoformat(),
            "summary": self.summary.to_dict(),
            "items": [item.to_dict() for item in self.items],
        }


class ContentAccessRealityService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.runtime_settings = RuntimeSettingsService(session, settings)
        self.oauth = OAuthService(session, settings)
        self.accounts = AccountCredentialsService(session, settings)
        self.trust_grants = TrustGrantRepository(session)
        self.creator_ops = CreatorOpsService(session, settings)
        self.browser_runtime = BrowserRuntimeService(settings)

    def build_snapshot(self) -> ContentReadinessSnapshot:
        checked_at = self._utcnow()
        runtime_controls = self.runtime_settings.snapshot()
        oauth_connectors = {item.connector_key: item for item in self.oauth.list_connectors()}
        oauth_connections = self.oauth.list_connections()
        accounts = self.accounts.list_accounts()
        account_by_id = {item.id: item for item in accounts}
        grants = list(self.trust_grants.list_all())
        linked_projects_by_account = self._linked_projects_by_account()
        browser_runtime = self.browser_runtime.describe()

        youtube_items = self._build_youtube_items(
            checked_at=checked_at,
            runtime_controls=runtime_controls,
            oauth_connectors=oauth_connectors,
            oauth_connections=oauth_connections,
            account_by_id=account_by_id,
            grants=grants,
            linked_projects_by_account=linked_projects_by_account,
        )
        items = (
            *youtube_items,
            self._build_calendar_item(
                checked_at=checked_at,
                runtime_controls=runtime_controls,
                account_by_id=account_by_id,
                linked_projects_by_account=linked_projects_by_account,
            ),
            self._build_github_item(
                checked_at=checked_at,
                runtime_controls=runtime_controls,
                account_by_id=account_by_id,
                linked_projects_by_account=linked_projects_by_account,
            ),
            self._build_bootstrap_mailbox_item(
                checked_at=checked_at,
                runtime_controls=runtime_controls,
                browser_runtime_ready=browser_runtime.ready,
                accounts=accounts,
                oauth_connectors=oauth_connectors,
                oauth_connections=oauth_connections,
                linked_projects_by_account=linked_projects_by_account,
            ),
        )
        summary = ContentReadinessSummary(
            total_items=len(items),
            blocked=sum(1 for item in items if item.status == "blocked"),
            metadata_only=sum(1 for item in items if item.status == "metadata_only"),
            read_ready=sum(1 for item in items if item.status == "read_ready"),
            draft_ready=sum(1 for item in items if item.status == "draft_ready"),
            supervised_ready=sum(1 for item in items if item.status == "supervised_ready"),
            publish_mode=(
                "supervised_publish"
                if runtime_controls.publish_automation_enabled
                else "draft_only"
            ),
            primary_youtube_status=next(
                (item.status for item in items if item.platform_key == "youtube" or item.platform_key.startswith("youtube:")),
                None,
            ),
        )
        snapshot_key = self._build_snapshot_key(summary=summary, items=items)
        return ContentReadinessSnapshot(
            snapshot_key=snapshot_key,
            checked_at=checked_at,
            summary=summary,
            items=items,
        )

    def build_chat_summary(self) -> str:
        snapshot = self.build_snapshot()
        item_lines: list[str] = []
        for item in snapshot.items:
            actions = ", ".join(item.available_actions) if item.available_actions else "none"
            blockers = "; ".join(item.blocking_reasons) if item.blocking_reasons else "none"
            linked_projects = ", ".join(item.linked_projects) if item.linked_projects else "none"
            item_lines.append(
                f"- {item.platform_key}: status={item.status}; actions={actions}; "
                f"needs_auth={item.needs_auth}; missing_grant={item.missing_grant}; "
                f"account={item.account_label or 'none'}; linked_projects={linked_projects}; blockers={blockers}"
            )
        return (
            "Content operations reality:\n"
            f"- publish_mode: {snapshot.summary.publish_mode}\n"
            f"- snapshot_key: {snapshot.snapshot_key}\n"
            + "\n".join(item_lines)
        )

    def _build_youtube_items(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
        oauth_connectors: dict[str, OAuthConnectorView],
        oauth_connections: list[OAuthConnectionView],
        account_by_id: dict[int, AccountCredentialView],
        grants: list[object],
        linked_projects_by_account: dict[int, tuple[str, ...]],
    ) -> tuple[ContentReadinessItem, ...]:
        content_accounts = self._active_youtube_content_accounts()
        if not content_accounts:
            return (
                self._build_youtube_item(
                    checked_at=checked_at,
                    runtime_controls=runtime_controls,
                    oauth_connectors=oauth_connectors,
                    account_by_id=account_by_id,
                    grants=grants,
                    linked_projects_by_account=linked_projects_by_account,
                ),
            )
        return tuple(
            self._build_youtube_item_for_content_account(
                checked_at=checked_at,
                runtime_controls=runtime_controls,
                config=config,
                oauth_connections=oauth_connections,
                account_by_id=account_by_id,
                grants=grants,
                linked_projects_by_account=linked_projects_by_account,
            )
            for config in content_accounts
        )

    def _build_youtube_item_for_content_account(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
        config: dict[str, Any],
        oauth_connections: list[OAuthConnectionView],
        account_by_id: dict[int, AccountCredentialView],
        grants: list[object],
        linked_projects_by_account: dict[int, tuple[str, ...]],
    ) -> ContentReadinessItem:
        content_account_id = str(config.get("account_id") or "").strip()
        channel_title = str(config.get("channel_title") or content_account_id or "YouTube channel").strip()
        oauth_account_id = _optional_account_id(config.get("youtube_oauth_account_id"))
        if oauth_account_id is None:
            return ContentReadinessItem(
                platform_key=f"youtube:{content_account_id or 'unmapped'}",
                display_name=channel_title,
                status="metadata_only",
                available_actions=("configure_youtube_oauth_mapping",),
                blocking_reasons=("This channel has no mapped YouTube OAuth account.",),
                account_label=None,
                connector_key="youtube-studio",
                requires_approval=True,
                last_checked_at=checked_at,
                connected=False,
                needs_auth=True,
                missing_grant=True,
                content_account_id=content_account_id or None,
            )

        status_view = YouTubeConnectorService(
            self.session,
            self.settings,
            account_id=oauth_account_id,
        ).get_status()
        connection = self._youtube_connection_for_account(oauth_connections, oauth_account_id)
        account = account_by_id.get(oauth_account_id)
        publish_grant = self._has_enabled_publish_grant(
            account_id=oauth_account_id,
            connector_keys=YOUTUBE_CONNECTOR_KEYS,
            grants=grants,
        )
        blocking_reasons = list(status_view.warnings)
        if connection is not None and connection.status != "connected" and connection.last_error:
            public_error = self._public_youtube_reconnect_reason(connection.last_error)
            if public_error:
                blocking_reasons.append(f"YouTube OAuth wymaga ponownego połączenia: {public_error}")
        available_actions = ["fetch_video_status", "fetch_analytics"]
        if status_view.connected:
            available_actions.append("upload_preview")
        if status_view.connected and publish_grant and runtime_controls.publish_automation_enabled:
            available_actions.append("publish_supervised")

        if not runtime_controls.oauth_enabled:
            blocking_reasons.append("OAuth is disabled in runtime.")
            status = "blocked"
        elif not status_view.configured:
            blocking_reasons.append("YouTube OAuth provider is not configured.")
            status = "blocked"
        elif not status_view.connected:
            blocking_reasons.append("Reconnect this channel's YouTube Studio OAuth before using publishing workflows.")
            status = "metadata_only"
        elif publish_grant and runtime_controls.publish_automation_enabled:
            status = "supervised_ready"
        else:
            if not publish_grant:
                blocking_reasons.append("No enabled trust grant allows supervised YouTube publish for this channel.")
            if not runtime_controls.publish_automation_enabled:
                blocking_reasons.append("Publish automation is disabled in runtime.")
            status = "draft_ready"

        if status_view.connected:
            blocking_reasons.append(
                "Verify Google API project compliance if uploads remain private-only for new API projects."
            )
        return ContentReadinessItem(
            platform_key=f"youtube:{content_account_id or oauth_account_id}",
            display_name=channel_title,
            status=status,
            available_actions=tuple(dict.fromkeys(available_actions)),
            blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
            account_label=account.label if account is not None else status_view.channel_title,
            connector_key=status_view.connector_key,
            requires_approval=True,
            last_checked_at=checked_at,
            connected=status_view.connected,
            needs_auth=not status_view.connected,
            missing_grant=not publish_grant,
            connection_id=connection.id if connection is not None else None,
            account_id=oauth_account_id,
            content_account_id=content_account_id or None,
            granted_scopes=status_view.granted_scopes,
            linked_projects=linked_projects_by_account.get(oauth_account_id, ()),
        )

    def _build_youtube_item(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
        oauth_connectors: dict[str, OAuthConnectorView],
        account_by_id: dict[int, AccountCredentialView],
        grants: list[object],
        linked_projects_by_account: dict[int, tuple[str, ...]],
    ) -> ContentReadinessItem:
        status_view = YouTubeConnectorService(self.session, self.settings).get_status()
        account = account_by_id.get(status_view.account_id) if status_view.account_id is not None else None
        publish_grant = self._has_enabled_publish_grant(
            account_id=status_view.account_id,
            connector_keys=YOUTUBE_CONNECTOR_KEYS,
            grants=grants,
        )
        blocking_reasons: list[str] = list(status_view.warnings)
        available_actions = ["fetch_video_status", "fetch_analytics"]
        if status_view.connected:
            available_actions.append("upload_preview")
        if status_view.connected and publish_grant and runtime_controls.publish_automation_enabled:
            available_actions.append("publish_supervised")

        if not runtime_controls.oauth_enabled:
            blocking_reasons.append("OAuth is disabled in runtime.")
            status = "blocked"
        elif not status_view.configured:
            blocking_reasons.append("YouTube OAuth provider is not configured.")
            status = "blocked"
        elif not status_view.connected:
            blocking_reasons.append("Connect YouTube Studio before using publishing workflows.")
            status = "metadata_only"
        elif publish_grant and runtime_controls.publish_automation_enabled:
            status = "supervised_ready"
        else:
            if not publish_grant:
                blocking_reasons.append("No enabled trust grant allows supervised YouTube publish.")
            if not runtime_controls.publish_automation_enabled:
                blocking_reasons.append("Publish automation is disabled in runtime.")
            status = "draft_ready"

        if status_view.connected:
            blocking_reasons.append(
                "Verify Google API project compliance if uploads remain private-only for new API projects."
            )
        return ContentReadinessItem(
            platform_key="youtube",
            display_name="YouTube",
            status=status,
            available_actions=tuple(dict.fromkeys(available_actions)),
            blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
            account_label=account.label if account is not None else status_view.channel_title,
            connector_key=status_view.connector_key,
            requires_approval=True,
            last_checked_at=checked_at,
            connected=status_view.connected,
            needs_auth=not status_view.connected,
            missing_grant=not publish_grant,
            connection_id=(
                oauth_connectors.get(status_view.connector_key).connection_id
                if oauth_connectors.get(status_view.connector_key) is not None
                else None
            ),
            account_id=status_view.account_id,
            granted_scopes=status_view.granted_scopes,
            linked_projects=linked_projects_by_account.get(status_view.account_id or -1, ()),
        )

    def _build_calendar_item(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
        account_by_id: dict[int, AccountCredentialView],
        linked_projects_by_account: dict[int, tuple[str, ...]],
    ) -> ContentReadinessItem:
        status_view = GoogleCalendarConnectorService(self.session, self.settings).get_status()
        account = account_by_id.get(status_view.account_id) if status_view.account_id is not None else None
        blocking_reasons: list[str] = list(status_view.warnings)
        available_actions = ["list_events", "find_free_slots"]
        if status_view.live_write_enabled:
            available_actions.extend(["create_event_preview", "delete_event_preview"])

        if not runtime_controls.oauth_enabled:
            blocking_reasons.append("OAuth is disabled in runtime.")
            status = "blocked"
        elif not status_view.provider_configured:
            blocking_reasons.append("Google Calendar OAuth provider is not configured.")
            status = "blocked"
        elif not status_view.connected:
            blocking_reasons.append("Connect Google Calendar before using planning workflows.")
            status = "metadata_only"
        elif status_view.live_write_enabled:
            status = "supervised_ready"
        else:
            blocking_reasons.append("Current Google Calendar connector is read-only.")
            status = "read_ready"

        return ContentReadinessItem(
            platform_key="google-calendar",
            display_name="Google Calendar",
            status=status,
            available_actions=tuple(dict.fromkeys(available_actions)),
            blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
            account_label=account.label if account is not None else None,
            connector_key=status_view.connector_key,
            requires_approval=status_view.live_write_enabled,
            last_checked_at=checked_at,
            connected=status_view.connected,
            needs_auth=not status_view.connected,
            missing_grant=False,
            connection_id=status_view.connection_id,
            account_id=status_view.account_id,
            granted_scopes=status_view.granted_scopes,
            linked_projects=linked_projects_by_account.get(status_view.account_id or -1, ()),
        )

    def _build_github_item(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
        account_by_id: dict[int, AccountCredentialView],
        linked_projects_by_account: dict[int, tuple[str, ...]],
    ) -> ContentReadinessItem:
        status_view = GitHubConnectorProbeService(self.session, self.settings).get_status()
        account = account_by_id.get(status_view.account_id) if status_view.account_id is not None else None
        blocking_reasons: list[str] = list(status_view.warnings)
        available_actions = ["list_user_repos", "list_repo_issues", "read_file_content"]
        if status_view.live_write_enabled:
            available_actions.extend(["create_issue_preview", "create_pull_request_preview"])

        if not runtime_controls.oauth_enabled:
            blocking_reasons.append("OAuth is disabled in runtime.")
            status = "blocked"
        elif not status_view.provider_configured:
            blocking_reasons.append("GitHub OAuth provider is not configured.")
            status = "blocked"
        elif status_view.oauth_status != "connected":
            blocking_reasons.append("Connect GitHub before using repo ops workflows.")
            status = "metadata_only"
        elif status_view.live_write_enabled:
            status = "supervised_ready"
        else:
            blocking_reasons.append("Current GitHub connector is read-only.")
            status = "read_ready"

        return ContentReadinessItem(
            platform_key="github",
            display_name="GitHub",
            status=status,
            available_actions=tuple(dict.fromkeys(available_actions)),
            blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
            account_label=account.label if account is not None else None,
            connector_key=status_view.connector_key,
            requires_approval=status_view.live_write_enabled,
            last_checked_at=checked_at,
            connected=status_view.oauth_status == "connected",
            needs_auth=status_view.oauth_status != "connected",
            missing_grant=False,
            connection_id=status_view.connection_id,
            account_id=status_view.account_id,
            granted_scopes=status_view.granted_scopes,
            linked_projects=linked_projects_by_account.get(status_view.account_id or -1, ()),
        )

    def _build_bootstrap_mailbox_item(
        self,
        *,
        checked_at: datetime,
        runtime_controls,
        browser_runtime_ready: bool,
        accounts: list[AccountCredentialView],
        oauth_connectors: dict[str, OAuthConnectorView],
        oauth_connections: list[OAuthConnectionView],
        linked_projects_by_account: dict[int, tuple[str, ...]],
    ) -> ContentReadinessItem:
        account = next(
            (
                item
                for item in accounts
                if item.provider_key in BOOTSTRAP_MAILBOX_PROVIDER_KEYS
                or (item.linked_integration_key or "") in BOOTSTRAP_MAILBOX_PROVIDER_KEYS
            ),
            None,
        )
        gmail_connector = oauth_connectors.get("gmail") or oauth_connectors.get("gmail-readonly")
        blocking_reasons: list[str] = []
        available_actions = ["signup_recovery_anchor", "verification_email_review"]
        connected = bool(account is not None and account.has_secret)
        if account is None:
            blocking_reasons.append("Bootstrap mailbox is not configured in vault-backed trusted accounts.")
            status = "blocked"
        elif not account.has_secret:
            blocking_reasons.append("Bootstrap mailbox exists, but no secret is stored.")
            status = "metadata_only"
        elif runtime_controls.browser_control_enabled and runtime_controls.login_automation_enabled and browser_runtime_ready:
            status = "supervised_ready"
            available_actions.append("browser_supervised_onboarding")
        else:
            if not runtime_controls.browser_control_enabled:
                blocking_reasons.append("Browser control is disabled in runtime.")
            if not runtime_controls.login_automation_enabled:
                blocking_reasons.append("Login automation is disabled in runtime.")
            if not browser_runtime_ready:
                blocking_reasons.append("Dedicated browser runtime is not ready.")
            status = "draft_ready"
        if gmail_connector is not None and gmail_connector.status == "connected":
            available_actions.append("gmail_connector_read")

        return ContentReadinessItem(
            platform_key="bootstrap-mailbox",
            display_name="Bootstrap Mailbox",
            status=status,
            available_actions=tuple(dict.fromkeys(available_actions)),
            blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
            account_label=account.label if account is not None else None,
            connector_key="bootstrap-mailbox",
            requires_approval=True,
            last_checked_at=checked_at,
            connected=connected,
            needs_auth=False,
            missing_grant=False,
            connection_id=self._gmail_connection_id(oauth_connections),
            account_id=account.id if account is not None else None,
            granted_scopes=tuple(gmail_connector.scopes) if gmail_connector is not None and gmail_connector.status == "connected" else (),
            linked_projects=linked_projects_by_account.get(account.id if account is not None else -1, ()),
        )

    def _build_snapshot_key(
        self,
        *,
        summary: ContentReadinessSummary,
        items: tuple[ContentReadinessItem, ...],
    ) -> str:
        payload = {
            "summary": summary.to_dict(),
            "items": [
                {
                    "platform_key": item.platform_key,
                    "status": item.status,
                    "available_actions": list(item.available_actions),
                    "blocking_reasons": list(item.blocking_reasons),
                    "account_label": item.account_label,
                    "connector_key": item.connector_key,
                    "requires_approval": item.requires_approval,
                    "connected": item.connected,
                    "needs_auth": item.needs_auth,
                    "missing_grant": item.missing_grant,
                    "connection_id": item.connection_id,
                    "account_id": item.account_id,
                    "granted_scopes": list(item.granted_scopes),
                    "linked_projects": list(item.linked_projects),
                }
                for item in items
            ],
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _gmail_connection_id(connections: list[OAuthConnectionView]) -> int | None:
        for item in connections:
            if item.connector_key in {"gmail", "gmail-readonly"} and item.status == "connected":
                return item.id
        return None

    @staticmethod
    def _has_enabled_publish_grant(
        *,
        account_id: int | None,
        connector_keys: set[str],
        grants: list[object],
    ) -> bool:
        if account_id is None:
            return False
        for grant in grants:
            if getattr(grant, "account_id", None) != account_id:
                continue
            if getattr(grant, "connector_key", None) not in connector_keys:
                continue
            if not bool(getattr(grant, "is_enabled", False)):
                continue
            if bool(getattr(grant, "publish_allowed", False)) or bool(getattr(grant, "connector_write_allowed", False)):
                return True
        return False

    def _linked_projects_by_account(self) -> dict[int, tuple[str, ...]]:
        mapping: dict[int, list[str]] = {}
        try:
            projects = self.creator_ops.list_projects()
        except Exception:
            return {}
        for project in projects:
            for account_id in project.linked_account_ids:
                mapping.setdefault(account_id, [])
                if project.name not in mapping[account_id]:
                    mapping[account_id].append(project.name)
        return {key: tuple(value) for key, value in mapping.items()}

    def _active_youtube_content_accounts(self) -> list[dict[str, Any]]:
        """Zwraca wlaczone kanaly z Intelligence, zamiast zgadywac po ostatnim OAuth."""
        try:
            configs = read_intelligence_account_configs(self.settings)
        except Exception:
            return []
        active = [
            dict(config)
            for config in configs
            if bool(config.get("enabled"))
            and str(config.get("account_id") or "").strip().casefold().startswith("youtube:")
        ]
        return sorted(active, key=lambda item: str(item.get("channel_title") or item.get("account_id") or "").casefold())

    @staticmethod
    def _public_youtube_reconnect_reason(value: object) -> str | None:
        message = str(value or "").strip()
        if not message:
            return None
        if "redacted" in message.casefold():
            return "The previous OAuth session cannot be reused."
        return message

    @staticmethod
    def _youtube_connection_for_account(
        connections: list[OAuthConnectionView],
        account_id: int,
    ) -> OAuthConnectionView | None:
        candidates = [
            item
            for item in connections
            if item.connector_key in YOUTUBE_CONNECTOR_KEYS and item.account_id == account_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.updated_at, item.id))

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)


def _optional_account_id(value: object) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None
