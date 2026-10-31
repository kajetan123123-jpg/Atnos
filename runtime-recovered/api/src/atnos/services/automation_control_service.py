from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Final

from sqlalchemy.orm import Session

from atnos.db.models.account_credential import AccountCredential
from atnos.db.models.connected_account_control import ConnectedAccountControl
from atnos.db.models.project import Project
from atnos.db.models.project_app_link import ProjectAppLink
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.automation_control_repository import AutomationControlRepository
from atnos.repositories.creator_project_repository import CreatorProjectRepository
from atnos.repositories.project_app_link_repository import ProjectAppLinkRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS

POLICY_ALLOWED_KEYS: Final = {
    "requires_user_approval",
    "dry_run_required",
    "audit_required",
    "external_write_allowed",
    "publish_allowed",
    "upload_allowed",
    "trading_allowed",
    "credential_entry_allowed",
    "blocked_action_types",
}
PERMISSION_MODES: Final = {
    "metadata_only",
    "read_ready",
    "draft_ready",
    "supervised_ready",
    "blocked",
}
AUTOMATION_LEVELS: Final = {"off", "suggest", "draft", "supervised"}
RISK_LEVELS: Final = {"low", "medium", "high", "critical", "blocked"}
ALLOWED_AUTOMATION_CAPABILITIES: Final = {
    "read_metadata",
    "read_content",
    "draft_write",
    "create_draft",
    "update_draft",
    "confirmed_write",
    "publish_preview",
    "publish_confirmed_later",
    "message_preview",
    "message_confirmed_later",
    "upload_preview",
    "upload_confirmed_later",
    "calendar_read",
    "calendar_draft_event",
    "calendar_confirmed_event_later",
    "analytics_read",
    "filesystem_read_later",
    "browser_use_later",
    "pc_operator_later",
    "financial_read_later",
    "trading_blocked",
}
READ_CAPABILITIES: Final = {
    "read_metadata",
    "read_content",
    "analytics_read",
    "filesystem_read_later",
    "financial_read_later",
    "calendar_read",
}
DRAFT_CAPABILITIES: Final = {
    "draft_write",
    "create_draft",
    "update_draft",
    "publish_preview",
    "message_preview",
    "upload_preview",
    "calendar_draft_event",
}
SUPERVISED_CAPABILITIES: Final = {
    "confirmed_write",
    "publish_confirmed_later",
    "message_confirmed_later",
    "upload_confirmed_later",
    "calendar_confirmed_event_later",
}
LOCAL_INTEGRATION_KEYS: Final = {
    "obsidian",
    "local-files",
    "ollama-local",
    "llamacpp-local",
    "whisper-local",
    "fred",
    "yahoo-finance",
    "investing.com",
}
OAUTH_CONNECTED_CAPABILITIES: Final[dict[str, set[str]]] = {
    "github": {"read_metadata", "read_content", "draft_write", "confirmed_write"},
    "github-readonly": {"read_metadata"},
    "google-drive-readonly": {"read_metadata"},
    "gmail": {"read_metadata", "read_content", "draft_write", "confirmed_write"},
    "gmail-readonly": {"read_metadata"},
    "google-calendar": {
        "read_metadata",
        "read_content",
        "draft_write",
        "confirmed_write",
    },
    "google-calendar-readonly": {"read_metadata"},
    "youtube-studio": {
        "read_metadata",
        "read_content",
        "draft_write",
        "confirmed_write",
    },
}
SECRET_LIKE_PATTERN: Final = re.compile(
    r"(?i)(bearer\s+[a-z0-9_\-\.]+|sk-[a-z0-9_\-]+|api[_-]?key|token|password|client_secret|secret_ref)"
)
LEGACY_ACCOUNT_TO_AUTOMATION_CAPABILITIES: Final[dict[str, tuple[str, ...]]] = {
    "read_metadata": ("read_metadata",),
    "read_content": ("read_content",),
    "draft_write": ("draft_write", "create_draft", "update_draft"),
    "confirmed_write_later": ("confirmed_write",),
    "publish_later": ("publish_preview", "publish_confirmed_later"),
    "browser_login_later": ("browser_use_later",),
    "financial_read_later": ("financial_read_later", "analytics_read"),
    "trading_later": ("trading_blocked",),
}
LEGACY_APP_LINK_TO_AUTOMATION_CAPABILITIES: Final[dict[str, tuple[str, ...]]] = {
    "read_metadata": ("read_metadata",),
    "read_content": ("read_content",),
    "draft_write": ("draft_write", "create_draft", "update_draft"),
    "confirmed_write_later": ("confirmed_write",),
    "publish_later": ("publish_preview", "publish_confirmed_later"),
    "browser_login_later": ("browser_use_later",),
    "financial_read_later": ("financial_read_later", "analytics_read"),
    "trading_later": ("trading_blocked",),
}


@dataclass(frozen=True, slots=True)
class AutomationPolicyView:
    id: int
    account_id: int | None
    project_id: int | None
    integration_key: str
    connector_key: str | None
    connection_status: str
    permission_mode: str
    automation_level: str
    allowed_capabilities: tuple[str, ...]
    automation_policy: dict[str, object]
    risk_level: str
    last_checked_at: datetime | None
    warnings: tuple[str, ...]
    audit_log: tuple[dict[str, object], ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GlobalAutomationReadinessView:
    account_counts_by_permission_mode: dict[str, int]
    account_counts_by_automation_level: dict[str, int]
    blocked_count: int
    supervised_ready_count: int
    warnings: tuple[str, ...]
    account_controls: tuple[AutomationPolicyView, ...]


@dataclass(frozen=True, slots=True)
class EffectiveAppLinkPolicyView:
    app_link_id: int
    account_id: int | None
    integration_key: str
    account_permission_mode: str | None
    app_link_status: str
    effective_permission_mode: str
    effective_capabilities: tuple[str, ...]
    risk_level: str
    recipe_usable: bool
    missing_requirements: tuple[str, ...]
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectAutomationReadinessView:
    project_id: int
    account_controls: tuple[AutomationPolicyView, ...]
    app_links_effective_policy: tuple[EffectiveAppLinkPolicyView, ...]
    missing_requirements: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    readiness_status: str


class AutomationControlService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.accounts = AccountCredentialRepository(session)
        self.controls = AutomationControlRepository(session)
        self.projects = ProjectRepository(session)
        self.project_links = ProjectAppLinkRepository(session)
        self.creator_projects = CreatorProjectRepository(session)

    def get_global_readiness(self) -> GlobalAutomationReadinessView:
        controls = [self._ensure_account_control(account) for account in self.accounts.list_all()]
        for control in controls:
            self._append_audit_event(control, "readiness_checked", "Global automation readiness reviewed.")

        permission_counts = {mode: 0 for mode in sorted(PERMISSION_MODES)}
        automation_counts = {level: 0 for level in sorted(AUTOMATION_LEVELS)}
        warnings: list[str] = []
        for control in controls:
            permission_counts[control.permission_mode] = permission_counts.get(control.permission_mode, 0) + 1
            automation_counts[control.automation_level] = automation_counts.get(control.automation_level, 0) + 1
            warnings.extend(item for item in control.warnings_json if item not in warnings)

        views = tuple(self._build_policy_view(control) for control in controls)
        return GlobalAutomationReadinessView(
            account_counts_by_permission_mode=permission_counts,
            account_counts_by_automation_level=automation_counts,
            blocked_count=permission_counts.get("blocked", 0),
            supervised_ready_count=permission_counts.get("supervised_ready", 0),
            warnings=tuple(warnings),
            account_controls=views,
        )

    def get_project_readiness(self, project_id: int) -> ProjectAutomationReadinessView | None:
        project = self.projects.get(project_id)
        if project is None:
            return None

        creator_project = self.creator_projects.get_project_by_canonical_project_id(project_id)
        project_type = creator_project.project_type if creator_project is not None else None
        app_links = self.project_links.list_for_project(project_id)
        relevant_account_ids = {
            link.account_id
            for link in app_links
            if link.account_id is not None
        }
        for account in self.accounts.list_all():
            if account.linked_project_id == project_id:
                relevant_account_ids.add(account.id)

        control_map = {
            control.account_id: control
            for control in self.controls.list_for_accounts(
                sorted(account_id for account_id in relevant_account_ids if account_id is not None)
            )
        }
        for account_id in sorted(account_id for account_id in relevant_account_ids if account_id is not None):
            account = self.accounts.get_by_id(account_id)
            if account is not None:
                control_map[account_id] = self._ensure_account_control(account)

        account_views = tuple(
            self._build_policy_view(control_map[account_id])
            for account_id in sorted(control_map)
        )
        effective_policies: list[EffectiveAppLinkPolicyView] = []
        missing_requirements: list[str] = []
        blocked_reasons: list[str] = []

        for link in app_links:
            control = control_map.get(link.account_id) if link.account_id is not None else None
            view = self._build_effective_app_link_policy(
                project=project,
                project_type=project_type,
                link=link,
                control=control,
            )
            effective_policies.append(view)
            for item in view.missing_requirements:
                if item not in missing_requirements:
                    missing_requirements.append(item)
            for item in view.blocked_reasons:
                if item not in blocked_reasons:
                    blocked_reasons.append(item)

        for control in control_map.values():
            self._append_audit_event(
                control,
                "readiness_checked",
                f"Project automation readiness reviewed for project {project_id}.",
            )

        readiness_status = self._resolve_project_readiness_status(
            account_controls=list(control_map.values()),
            app_link_policies=effective_policies,
            blocked_reasons=blocked_reasons,
        )

        return ProjectAutomationReadinessView(
            project_id=project_id,
            account_controls=account_views,
            app_links_effective_policy=tuple(effective_policies),
            missing_requirements=tuple(missing_requirements),
            blocked_reasons=tuple(blocked_reasons),
            readiness_status=readiness_status,
        )

    def get_account_policy(self, account_id: int) -> AutomationPolicyView | None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            return None
        control = self._ensure_account_control(account)
        self._append_audit_event(control, "policy_viewed", "Account automation policy viewed.")
        return self._build_policy_view(control)

    def update_account_policy(
        self,
        account_id: int,
        *,
        permission_mode: str | None,
        automation_level: str | None,
        requires_user_approval: bool | None,
        dry_run_required: bool | None,
        audit_required: bool | None,
        external_write_allowed: bool | None,
        publish_allowed: bool | None,
        upload_allowed: bool | None,
        trading_allowed: bool | None,
        credential_entry_allowed: bool | None,
    ) -> AutomationPolicyView | None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            return None
        control = self._ensure_account_control(account)
        has_active_oauth_connection = bool(
            control.connector_key and control.connection_status == "connected"
        )

        updated_policy = dict(control.automation_policy_json or {})
        if requires_user_approval is not None:
            updated_policy["requires_user_approval"] = requires_user_approval
        if dry_run_required is not None:
            updated_policy["dry_run_required"] = dry_run_required
        if audit_required is not None:
            updated_policy["audit_required"] = audit_required
        if external_write_allowed is not None:
            updated_policy["external_write_allowed"] = external_write_allowed
        if publish_allowed is not None:
            updated_policy["publish_allowed"] = publish_allowed
        if upload_allowed is not None:
            updated_policy["upload_allowed"] = upload_allowed
        if trading_allowed is not None:
            updated_policy["trading_allowed"] = trading_allowed
        if credential_entry_allowed is not None:
            updated_policy["credential_entry_allowed"] = credential_entry_allowed

        requested_permission_mode = control.permission_mode if permission_mode is None else self._normalize_permission_mode(permission_mode)
        requested_automation_level = control.automation_level if automation_level is None else self._normalize_automation_level(automation_level)

        self._reject_unsafe_policy_fields(updated_policy)
        self._validate_requested_policy(
            account=account,
            requested_permission_mode=requested_permission_mode,
            requested_automation_level=requested_automation_level,
            policy=updated_policy,
            capabilities=set(control.allowed_capabilities_json or []),
            has_active_oauth_connection=has_active_oauth_connection,
        )

        control.permission_mode = requested_permission_mode
        control.automation_level = requested_automation_level
        control.automation_policy_json = updated_policy
        control.connection_status = account.status
        control.risk_level = self._derive_control_risk_level(
            automation_policy=updated_policy,
            capabilities=set(control.allowed_capabilities_json or []),
            account=account,
        )
        control.last_checked_at = self._utcnow()
        control.warnings_json = self._build_warnings(
            account=account,
            permission_mode=control.permission_mode,
            capabilities=set(control.allowed_capabilities_json or []),
            automation_policy=control.automation_policy_json,
            connection_status=control.connection_status,
            connector_key=control.connector_key,
            has_active_oauth_connection=has_active_oauth_connection,
        )
        self._append_audit_event(control, "policy_updated", "Account automation policy updated.")
        if control.permission_mode == "blocked":
            self._append_audit_event(control, "policy_blocked", "Account automation policy is blocked.")
        for warning in control.warnings_json:
            self._append_audit_event(control, "policy_warning_added", warning)
        self.controls.save(control)
        return self._build_policy_view(control)

    def upsert_oauth_connected_control(
        self,
        account: AccountCredential,
        *,
        connector_key: str,
        integration_key: str,
    ) -> AutomationPolicyView:
        control = self.controls.get_by_account_id(account.id)
        default_policy = self._default_automation_policy()
        oauth_capabilities = self._oauth_capabilities_for_connector(connector_key)
        combined_capabilities = self._map_legacy_account_capabilities(account.allowed_capabilities_json or [])
        combined_capabilities.update(oauth_capabilities)

        if control is None:
            control = self.controls.create(
                account_id=account.id,
                project_id=None,
                integration_key=integration_key.lower(),
                connector_key=connector_key,
                connection_status="connected",
                permission_mode="read_ready",
                automation_level="off",
                allowed_capabilities_json=sorted(combined_capabilities),
                automation_policy_json=default_policy,
                risk_level=self._derive_control_risk_level(
                    automation_policy=default_policy,
                    capabilities=combined_capabilities,
                    account=account,
                ),
                last_checked_at=self._utcnow(),
                warnings_json=[],
                audit_log_json=[],
            )
            self._append_audit_event(control, "control_created", "Connected account control created.")
        else:
            control.integration_key = integration_key.lower()
            control.connector_key = connector_key
            control.connection_status = "connected"
            control.permission_mode = "read_ready"
            control.automation_level = "off"
            control.allowed_capabilities_json = sorted(combined_capabilities)
            control.automation_policy_json = default_policy
            control.last_checked_at = self._utcnow()
            control.risk_level = self._derive_control_risk_level(
                automation_policy=default_policy,
                capabilities=combined_capabilities,
                account=account,
            )

        control.warnings_json = self._build_warnings(
            account=account,
            permission_mode=control.permission_mode,
            capabilities=combined_capabilities,
            automation_policy=control.automation_policy_json,
            connection_status="connected",
            connector_key=connector_key,
            has_active_oauth_connection=True,
        )
        self.controls.save(control)
        return self._build_policy_view(control)

    def mark_oauth_disconnected(
        self,
        *,
        account_id: int,
        connector_key: str,
    ) -> None:
        account = self.accounts.get_by_id(account_id)
        control = self.controls.get_by_account_id(account_id)
        if account is None or control is None:
            return
        combined_capabilities = set(control.allowed_capabilities_json or [])
        combined_capabilities.update(self._oauth_capabilities_for_connector(connector_key))
        control.connector_key = connector_key
        control.connection_status = "disconnected"
        control.permission_mode = "metadata_only"
        control.automation_level = "off"
        control.last_checked_at = self._utcnow()
        control.warnings_json = self._build_warnings(
            account=account,
            permission_mode=control.permission_mode,
            capabilities=combined_capabilities,
            automation_policy=control.automation_policy_json or self._default_automation_policy(),
            connection_status="disconnected",
            connector_key=connector_key,
            has_active_oauth_connection=False,
        )
        self._append_audit_event(control, "policy_warning_added", "Active OAuth connection is missing.")
        self.controls.save(control)

    def _ensure_account_control(self, account: AccountCredential) -> ConnectedAccountControl:
        existing = self.controls.get_by_account_id(account.id)
        if existing is None:
            capabilities = self._map_legacy_account_capabilities(account.allowed_capabilities_json or [])
            default_policy = self._default_automation_policy()
            has_active_oauth_connection = False
            permission_mode = self._derive_permission_mode(
                account=account,
                capabilities=capabilities,
                requested_permission_mode=None,
                automation_policy=default_policy,
                has_active_oauth_connection=has_active_oauth_connection,
            )
            item = self.controls.create(
                account_id=account.id,
                project_id=None,
                integration_key=(account.linked_integration_key or account.provider_key).lower(),
                connector_key=None,
                connection_status=account.status,
                permission_mode=permission_mode,
                automation_level="off",
                allowed_capabilities_json=sorted(capabilities),
                automation_policy_json=default_policy,
                risk_level=self._derive_control_risk_level(
                    automation_policy=default_policy,
                    capabilities=capabilities,
                    account=account,
                ),
                last_checked_at=self._utcnow(),
                warnings_json=self._build_warnings(
                    account=account,
                    permission_mode=permission_mode,
                    capabilities=capabilities,
                    automation_policy=default_policy,
                    connection_status=account.status,
                    connector_key=None,
                    has_active_oauth_connection=has_active_oauth_connection,
                ),
                audit_log_json=[],
            )
            self._append_audit_event(item, "control_created", "Connected account control created.")
            if item.permission_mode == "blocked":
                self._append_audit_event(item, "policy_blocked", "Connected account control is blocked.")
            for warning in item.warnings_json:
                self._append_audit_event(item, "policy_warning_added", warning)
            self.controls.save(item)
            return item

        has_active_oauth_connection = bool(
            existing.connector_key and existing.connection_status == "connected"
        )
        capabilities = self._map_legacy_account_capabilities(account.allowed_capabilities_json or [])
        capabilities.update(self._oauth_capabilities_for_connector(existing.connector_key))
        existing.integration_key = (account.linked_integration_key or account.provider_key).lower()
        existing.connection_status = self._derive_connection_status(
            account=account,
            connector_key=existing.connector_key,
            existing_status=existing.connection_status,
        )
        existing.allowed_capabilities_json = sorted(capabilities)
        existing.last_checked_at = self._utcnow()
        existing.permission_mode = self._derive_permission_mode(
            account=account,
            capabilities=capabilities,
            requested_permission_mode=existing.permission_mode,
            automation_policy=existing.automation_policy_json or self._default_automation_policy(),
            has_active_oauth_connection=has_active_oauth_connection,
        )
        existing.risk_level = self._derive_control_risk_level(
            automation_policy=existing.automation_policy_json or self._default_automation_policy(),
            capabilities=capabilities,
            account=account,
        )
        existing.warnings_json = self._build_warnings(
            account=account,
            permission_mode=existing.permission_mode,
            capabilities=capabilities,
            automation_policy=existing.automation_policy_json or self._default_automation_policy(),
            connection_status=existing.connection_status,
            connector_key=existing.connector_key,
            has_active_oauth_connection=has_active_oauth_connection,
        )
        self.controls.save(existing)
        return existing

    def _build_effective_app_link_policy(
        self,
        *,
        project: Project,
        project_type: str | None,
        link: ProjectAppLink,
        control: ConnectedAccountControl | None,
    ) -> EffectiveAppLinkPolicyView:
        account_permission_mode = control.permission_mode if control is not None else None
        link_capabilities = self._map_legacy_app_link_capabilities(link.allowed_capabilities_json or [])
        blocked_reasons: list[str] = []
        missing_requirements: list[str] = []
        account_capabilities = set(control.allowed_capabilities_json or []) if control is not None else set()

        if link.status == "disabled":
            blocked_reasons.append("App link is disabled.")
        elif link.status == "needs_review":
            missing_requirements.append("App link needs review.")
        elif link.status == "planned":
            missing_requirements.append("App link is still planned.")

        has_local_mode = link.integration_key in LOCAL_INTEGRATION_KEYS
        if link.account_id is None and not has_local_mode:
            missing_requirements.append("Linked account metadata is missing.")
        if control is None and link.account_id is not None:
            missing_requirements.append("Account automation control is missing.")
        if control is not None and control.connector_key and control.connection_status != "connected":
            missing_requirements.append("Active OAuth connection is missing.")

        if control is not None and control.connection_status == "disabled":
            blocked_reasons.append("Linked account is disabled.")
        if control is not None and control.permission_mode == "metadata_only" and link_capabilities:
            missing_requirements.append("Linked account is metadata-only.")

        effective_capabilities = set(link_capabilities)
        if control is not None:
            effective_capabilities = {cap for cap in effective_capabilities if cap in account_capabilities}
            effective_capabilities = self._constrain_capabilities_by_permission_mode(
                permission_mode=control.permission_mode,
                capabilities=effective_capabilities,
            )
            missing_account_caps = sorted(cap for cap in link_capabilities if cap not in account_capabilities)
            if missing_account_caps:
                missing_requirements.append(
                    f"Linked account is missing capabilities: {', '.join(missing_account_caps)}."
                )
            constrained_link_caps = sorted(cap for cap in link_capabilities if cap not in effective_capabilities)
            if constrained_link_caps and control.permission_mode in {"metadata_only", "read_ready", "draft_ready"}:
                missing_requirements.append(
                    f"Account automation policy limits capabilities beyond {control.permission_mode}: {', '.join(constrained_link_caps)}."
                )

        if "trading_blocked" in effective_capabilities or "trading_blocked" in link_capabilities:
            blocked_reasons.append("Trading execution is always blocked.")
        if project_type == "market_lab" and any(
            capability in effective_capabilities
            for capability in {"publish_preview", "publish_confirmed_later", "upload_preview", "upload_confirmed_later"}
        ):
            blocked_reasons.append("Market Lab projects cannot expose publish or upload automation.")

        effective_permission_mode = self._derive_effective_permission_mode(
            account_permission_mode=account_permission_mode,
            control=control,
            effective_capabilities=effective_capabilities,
            blocked_reasons=blocked_reasons,
        )
        recipe_usable = (
            effective_permission_mode in {"read_ready", "draft_ready", "supervised_ready"}
            and not blocked_reasons
            and not missing_requirements
        )
        risk_level = self._derive_effective_risk_level(
            effective_permission_mode=effective_permission_mode,
            capabilities=effective_capabilities,
            blocked_reasons=blocked_reasons,
        )

        return EffectiveAppLinkPolicyView(
            app_link_id=link.id,
            account_id=link.account_id,
            integration_key=link.integration_key,
            account_permission_mode=account_permission_mode,
            app_link_status=link.status,
            effective_permission_mode=effective_permission_mode,
            effective_capabilities=tuple(sorted(effective_capabilities)),
            risk_level=risk_level,
            recipe_usable=recipe_usable,
            missing_requirements=tuple(dict.fromkeys(missing_requirements)),
            blocked_reasons=tuple(dict.fromkeys(blocked_reasons)),
        )

    def _derive_effective_permission_mode(
        self,
        *,
        account_permission_mode: str | None,
        control: ConnectedAccountControl | None,
        effective_capabilities: set[str],
        blocked_reasons: list[str],
    ) -> str:
        if blocked_reasons:
            return "blocked"
        if control is not None and control.permission_mode == "blocked":
            return "blocked"
        if control is not None and control.permission_mode == "metadata_only" and effective_capabilities:
            return "metadata_only"
        if effective_capabilities & SUPERVISED_CAPABILITIES:
            return "supervised_ready"
        if effective_capabilities & DRAFT_CAPABILITIES:
            return "draft_ready"
        if effective_capabilities & READ_CAPABILITIES:
            return "read_ready"
        if account_permission_mode is not None:
            return account_permission_mode
        return "metadata_only"

    def _derive_effective_risk_level(
        self,
        *,
        effective_permission_mode: str,
        capabilities: set[str],
        blocked_reasons: list[str],
    ) -> str:
        if blocked_reasons or effective_permission_mode == "blocked":
            return "blocked"
        if capabilities & {
            "publish_confirmed_later",
            "message_confirmed_later",
            "upload_confirmed_later",
            "calendar_confirmed_event_later",
            "confirmed_write",
        }:
            return "high"
        if capabilities & {
            "draft_write",
            "create_draft",
            "update_draft",
            "publish_preview",
            "message_preview",
            "upload_preview",
            "calendar_draft_event",
            "browser_use_later",
            "pc_operator_later",
        }:
            return "medium"
        return "low"

    def _resolve_project_readiness_status(
        self,
        *,
        account_controls: list[ConnectedAccountControl],
        app_link_policies: list[EffectiveAppLinkPolicyView],
        blocked_reasons: list[str],
    ) -> str:
        if blocked_reasons:
            return "blocked"
        if not account_controls and not app_link_policies:
            return "not_ready"
        if any(item.effective_permission_mode == "supervised_ready" and item.recipe_usable for item in app_link_policies):
            return "supervised_ready"
        if any(item.effective_permission_mode == "draft_ready" and item.recipe_usable for item in app_link_policies):
            return "draft_ready"
        if any(item.effective_permission_mode == "read_ready" and item.recipe_usable for item in app_link_policies):
            return "read_ready"
        if any(item.permission_mode == "metadata_only" for item in account_controls):
            return "metadata_only"
        return "not_ready"

    def _build_policy_view(self, control: ConnectedAccountControl) -> AutomationPolicyView:
        return AutomationPolicyView(
            id=control.id,
            account_id=control.account_id,
            project_id=control.project_id,
            integration_key=control.integration_key,
            connector_key=control.connector_key,
            connection_status=control.connection_status,
            permission_mode=control.permission_mode,
            automation_level=control.automation_level,
            allowed_capabilities=tuple(control.allowed_capabilities_json or []),
            automation_policy=dict(control.automation_policy_json or {}),
            risk_level=control.risk_level,
            last_checked_at=control.last_checked_at,
            warnings=tuple(control.warnings_json or []),
            audit_log=tuple(dict(item) for item in (control.audit_log_json or [])),
            created_at=control.created_at,
            updated_at=control.updated_at,
        )

    def _validate_requested_policy(
        self,
        *,
        account: AccountCredential,
        requested_permission_mode: str,
        requested_automation_level: str,
        policy: dict[str, object],
        capabilities: set[str],
        has_active_oauth_connection: bool,
    ) -> None:
        if account.status == "disabled":
            if requested_permission_mode != "blocked":
                raise ValueError("Disabled account cannot become supervised-ready or usable.")
        if (
            account.status == "metadata_only"
            and not has_active_oauth_connection
            and requested_permission_mode in {"read_ready", "draft_ready", "supervised_ready"}
        ):
            raise ValueError("Metadata-only account cannot be promoted beyond metadata_only.")
        if requested_permission_mode == "supervised_ready" and not (capabilities & SUPERVISED_CAPABILITIES):
            raise ValueError("Account cannot become supervised_ready without supervised capabilities.")
        if requested_permission_mode == "draft_ready" and not (capabilities & (DRAFT_CAPABILITIES | SUPERVISED_CAPABILITIES)):
            raise ValueError("Account cannot become draft_ready without draft or supervised capabilities.")
        if requested_permission_mode == "read_ready" and not (capabilities & READ_CAPABILITIES):
            raise ValueError("Account cannot become read_ready without readable capabilities.")
        if requested_automation_level == "supervised" and requested_permission_mode not in {"draft_ready", "supervised_ready"}:
            raise ValueError("supervised automation_level requires draft_ready or supervised_ready permission mode.")
        if requested_automation_level == "draft" and requested_permission_mode == "metadata_only":
            raise ValueError("draft automation_level requires read_ready, draft_ready, or supervised_ready permission mode.")
        if requested_automation_level in {"draft", "supervised"} and not bool(policy.get("requires_user_approval", True)):
            raise ValueError("Medium and high risk automation requires supervised approval.")

        if bool(policy.get("trading_allowed")):
            raise ValueError("Trading execution is always blocked.")
        if bool(policy.get("credential_entry_allowed")):
            raise ValueError("Credential entry is always blocked.")
        if bool(policy.get("publish_allowed")) and requested_permission_mode not in {"supervised_ready"}:
            raise ValueError("Publish confirmation can only exist on supervised-ready accounts.")
        if bool(policy.get("upload_allowed")) and requested_permission_mode not in {"draft_ready", "supervised_ready"}:
            raise ValueError("Upload confirmation requires draft or supervised readiness.")
        if bool(policy.get("external_write_allowed")) and not policy.get("requires_user_approval", True):
            raise ValueError("External write requires explicit user approval.")
        if not bool(policy.get("dry_run_required", True)):
            raise ValueError("dry_run_required cannot be disabled in this build.")
        if not bool(policy.get("audit_required", True)):
            raise ValueError("audit_required cannot be disabled in this build.")

    def _derive_permission_mode(
        self,
        *,
        account: AccountCredential,
        capabilities: set[str],
        requested_permission_mode: str | None,
        automation_policy: dict[str, object],
        has_active_oauth_connection: bool,
    ) -> str:
        if account.status == "disabled":
            return "blocked"
        if account.status == "metadata_only" and not has_active_oauth_connection:
            return "metadata_only"
        if "trading_blocked" in capabilities:
            return "blocked"

        inferred = "metadata_only"
        if capabilities & SUPERVISED_CAPABILITIES:
            inferred = "supervised_ready"
        elif capabilities & DRAFT_CAPABILITIES:
            inferred = "draft_ready"
        elif capabilities & READ_CAPABILITIES:
            inferred = "read_ready"

        if requested_permission_mode is None:
            return inferred
        if requested_permission_mode == "blocked":
            return "blocked"

        normalized_requested = self._normalize_permission_mode(requested_permission_mode)
        ordered_modes = ["metadata_only", "read_ready", "draft_ready", "supervised_ready"]
        return ordered_modes[min(ordered_modes.index(inferred), ordered_modes.index(normalized_requested))]

    def _derive_control_risk_level(
        self,
        *,
        automation_policy: dict[str, object],
        capabilities: set[str],
        account: AccountCredential,
    ) -> str:
        if account.status == "disabled":
            return "blocked"
        if capabilities & {"trading_blocked"}:
            return "blocked"
        if account.risk_level == "critical":
            return "critical"
        if capabilities & SUPERVISED_CAPABILITIES:
            return "high"
        if capabilities & DRAFT_CAPABILITIES:
            return "medium"
        if capabilities & READ_CAPABILITIES:
            return "low"
        return "medium" if bool(automation_policy.get("external_write_allowed")) else "low"

    def _build_warnings(
        self,
        *,
        account: AccountCredential,
        permission_mode: str,
        capabilities: set[str],
        automation_policy: dict[str, object],
        connection_status: str,
        connector_key: str | None,
        has_active_oauth_connection: bool,
    ) -> list[str]:
        warnings: list[str] = []
        if account.status == "metadata_only" and not has_active_oauth_connection:
            warnings.append("Account is metadata-only and cannot be used for supervised automation.")
        if permission_mode == "blocked":
            warnings.append("Account is blocked for automation in this build.")
        if "trading_blocked" in capabilities:
            warnings.append("Trading execution is always blocked.")
        if connector_key and connection_status != "connected":
            warnings.append("Active OAuth connection is missing.")
        if not bool(automation_policy.get("requires_user_approval", True)):
            warnings.append("User approval cannot be disabled in this build.")
        if account.status == "invalid":
            warnings.append("Account is marked invalid and needs review.")
        return list(dict.fromkeys(warnings))

    def _derive_connection_status(
        self,
        *,
        account: AccountCredential,
        connector_key: str | None,
        existing_status: str,
    ) -> str:
        if account.status == "disabled":
            return "disabled"
        if connector_key:
            return existing_status
        return account.status

    @staticmethod
    def _oauth_capabilities_for_connector(connector_key: str | None) -> set[str]:
        if connector_key is None:
            return set()
        return set(OAUTH_CONNECTED_CAPABILITIES.get(connector_key, {"read_metadata"}))

    def _default_automation_policy(self) -> dict[str, object]:
        return {
            "requires_user_approval": True,
            "dry_run_required": True,
            "audit_required": True,
            "external_write_allowed": False,
            "publish_allowed": False,
            "upload_allowed": False,
            "trading_allowed": False,
            "credential_entry_allowed": False,
            "blocked_action_types": [
                "payment",
                "banking",
                "credential_entry",
                "trading_execution",
                "delete_external_data",
            ],
        }

    def _map_legacy_account_capabilities(self, capabilities: list[str]) -> set[str]:
        mapped: set[str] = set()
        for capability in capabilities:
            mapped.update(LEGACY_ACCOUNT_TO_AUTOMATION_CAPABILITIES.get(capability, ()))
        return mapped & ALLOWED_AUTOMATION_CAPABILITIES

    def _map_legacy_app_link_capabilities(self, capabilities: list[str]) -> set[str]:
        mapped: set[str] = set()
        for capability in capabilities:
            mapped.update(LEGACY_APP_LINK_TO_AUTOMATION_CAPABILITIES.get(capability, ()))
        return mapped & ALLOWED_AUTOMATION_CAPABILITIES

    def _constrain_capabilities_by_permission_mode(
        self,
        *,
        permission_mode: str,
        capabilities: set[str],
    ) -> set[str]:
        if permission_mode == "blocked":
            return set()
        if permission_mode == "metadata_only":
            return set()
        if permission_mode == "read_ready":
            return capabilities & READ_CAPABILITIES
        if permission_mode == "draft_ready":
            return capabilities & (READ_CAPABILITIES | DRAFT_CAPABILITIES)
        return capabilities

    def _reject_unsafe_policy_fields(self, policy: dict[str, object]) -> None:
        for key, value in policy.items():
            lowered_key = key.lower()
            if lowered_key not in POLICY_ALLOWED_KEYS:
                raise ValueError("Automation policy contains unknown or unsafe fields.")
            if isinstance(value, str) and SECRET_LIKE_PATTERN.search(value):
                raise ValueError("Automation policy cannot contain secret-like values.")
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and SECRET_LIKE_PATTERN.search(item):
                        raise ValueError("Automation policy cannot contain secret-like values.")

    def _append_audit_event(self, control: ConnectedAccountControl, event: str, message: str) -> None:
        safe_message = self._sanitize_audit_text(message, limit=240)
        if not safe_message:
            return
        audit_log = list(control.audit_log_json or [])
        audit_log.append(
            {
                "event": event,
                "message": safe_message,
                "at": self._utcnow().isoformat(),
            }
        )
        control.audit_log_json = audit_log[-20:]

    @staticmethod
    def _sanitize_audit_text(value: str, *, limit: int) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            return ""
        if any(token in normalized.lower() for token in FORBIDDEN_KEY_TOKENS):
            return ""
        if SECRET_LIKE_PATTERN.search(normalized):
            return ""
        if len(normalized) > limit:
            return normalized[: limit - 3].rstrip() + "..."
        return normalized

    @staticmethod
    def _normalize_permission_mode(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in PERMISSION_MODES:
            allowed = ", ".join(sorted(PERMISSION_MODES))
            raise ValueError(f"permission_mode must be one of: {allowed}.")
        return normalized

    @staticmethod
    def _normalize_automation_level(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in AUTOMATION_LEVELS:
            allowed = ", ".join(sorted(AUTOMATION_LEVELS))
            raise ValueError(f"automation_level must be one of: {allowed}.")
        return normalized

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
