from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shutil
import socket

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.integration_config_service import OBSIDIAN_VAULT_NAME
from atnos.services.obsidian_read_service import ObsidianReadService
from atnos.services.runtime_context_preferences import RuntimeContextPreferences
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS

ADAPTER_KEYS = {
    "powershell",
    "playwright",
    "power_automate_desktop",
    "n8n_local",
    "autohotkey",
    "filesystem",
    "obsidian",
    "browser_later",
}
ADAPTER_PERMISSION_MODES = {
    "not_installed",
    "detected",
    "read_only",
    "draft_only",
    "supervised_execution",
    "blocked",
}
DRY_RUN_ACTION_TYPES = {
    "filesystem_read_plan",
    "folder_cleanup_plan",
    "powershell_command_draft",
    "browser_research_plan",
    "obsidian_note_context",
    "n8n_workflow_draft",
    "power_automate_flow_draft",
}
DRY_RUN_STATUSES = {
    "draft",
    "dry_run_ready",
    "awaiting_approval",
    "approved",
    "blocked",
    "cancelled",
    "execution_not_enabled",
}
BLOCKED_ACTION_PATTERNS = {
    "delete": "Deleting files or folders is blocked.",
    "remove-item": "Deleting files or folders is blocked.",
    "credential": "Credential entry is blocked.",
    "password": "Credential entry is blocked.",
    "login": "Login automation is blocked.",
    "2fa": "2FA automation is blocked.",
    "otp": "2FA automation is blocked.",
    "payment": "Payments are blocked.",
    "bank": "Banking actions are blocked.",
    "trade": "Trading and buy/sell orders are blocked.",
    "buy": "Trading and buy/sell orders are blocked.",
    "sell": "Trading and buy/sell orders are blocked.",
    "publish": "Publish and upload actions are blocked.",
    "upload": "Publish and upload actions are blocked.",
    "send message": "Sending messages or emails is blocked.",
    "send email": "Sending messages or emails is blocked.",
    "email": "Sending messages or emails is blocked.",
    "captcha": "Bypassing CAPTCHA or paywalls is blocked.",
    "paywall": "Bypassing CAPTCHA or paywalls is blocked.",
    "install": "Software installation requires explicit setup confirmation.",
}
SECRET_LIKE_TOKENS = {
    *FORBIDDEN_KEY_TOKENS,
    "token",
    "password",
    "api_key",
    "secret_ref",
    "client_secret",
}


@dataclass(frozen=True, slots=True)
class PCAutomationCapabilitySnapshotView:
    files_enabled: bool
    active_document_available: bool
    obsidian_vault_name: str
    obsidian_configured: bool
    obsidian_read_available: bool
    project_memory_available: bool
    autonomy_mode: str
    pc_operator_mode: str
    automation_control_available: bool
    action_broker_mode: str
    execution_enabled: bool
    browser_control_enabled: bool
    external_actions_enabled: bool
    scheduler_enabled: bool
    login_automation_enabled: bool
    two_factor_automation_enabled: bool
    publish_automation_enabled: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PCAutomationAdapterView:
    adapter_key: str
    display_name: str
    installed: bool
    available: bool
    version: str | None
    permission_mode: str
    risk_level: str
    allowed_actions: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    setup_status: str
    setup_instructions: tuple[str, ...]
    last_checked_at: str


@dataclass(frozen=True, slots=True)
class PCAutomationDryRunView:
    adapter_key: str
    action_type: str
    project_id: int | None
    goal: str
    dry_run_required: bool
    requires_user_approval: bool
    risk_level: str
    status: str
    planned_steps: tuple[dict[str, object], ...]
    blocked_reasons: tuple[str, ...]
    audit_preview: tuple[dict[str, object], ...]
    execution_enabled: bool


class PCAutomationService:
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.obsidian = ObsidianReadService(session, settings)
        self.browser_runtime = BrowserRuntimeService(settings)
        self.runtime_controls = RuntimeSettingsService(session, settings).snapshot()

    def get_capability_snapshot(
        self,
        *,
        runtime_preferences: RuntimeContextPreferences | None = None,
        active_document_available: bool = False,
    ) -> PCAutomationCapabilitySnapshotView:
        vault_status = self.obsidian.get_vault_status(include_note_count=False)
        files_enabled = runtime_preferences.files_enabled if runtime_preferences is not None else True
        project_memory_available = (
            bool(runtime_preferences and runtime_preferences.memory_enabled and runtime_preferences.project_id)
            if runtime_preferences is not None
            else True
        )

        return PCAutomationCapabilitySnapshotView(
            files_enabled=files_enabled,
            active_document_available=active_document_available,
            obsidian_vault_name=OBSIDIAN_VAULT_NAME,
            obsidian_configured=vault_status.path_configured,
            obsidian_read_available=vault_status.readable,
            project_memory_available=project_memory_available,
            autonomy_mode=self.runtime_controls.autonomy_mode,
            pc_operator_mode=(
                "trusted_auto"
                if self.runtime_controls.autonomy_mode == "trusted_auto"
                and self.runtime_controls.browser_control_enabled
                and self.runtime_controls.external_actions_enabled
                and not self.runtime_controls.emergency_stop_enabled
                else "supervised"
                if self.runtime_controls.browser_control_enabled or self.runtime_controls.external_actions_enabled
                else "planning_only"
            ),
            automation_control_available=True,
            action_broker_mode=(
                "trusted_auto"
                if self.runtime_controls.autonomy_mode == "trusted_auto"
                and not self.runtime_controls.emergency_stop_enabled
                else "supervised"
                if self.runtime_controls.external_actions_enabled
                else "preview_approval_only"
            ),
            execution_enabled=(
                self.runtime_controls.autonomy_mode == "trusted_auto"
                and self.runtime_controls.browser_control_enabled
                and self.runtime_controls.external_actions_enabled
                and not self.runtime_controls.emergency_stop_enabled
                and not self.runtime_controls.pause_all_jobs
            ),
            browser_control_enabled=self.runtime_controls.browser_control_enabled,
            external_actions_enabled=self.runtime_controls.external_actions_enabled,
            scheduler_enabled=self.runtime_controls.scheduler_enabled,
            login_automation_enabled=self.runtime_controls.login_automation_enabled,
            two_factor_automation_enabled=self.runtime_controls.two_factor_automation_enabled,
            publish_automation_enabled=self.runtime_controls.publish_automation_enabled,
        )

    def list_adapters(self) -> tuple[PCAutomationAdapterView, ...]:
        checked_at = self._utcnow().isoformat()
        adapters = (
            self._build_powershell_adapter(checked_at),
            self._build_playwright_adapter(checked_at),
            self._build_power_automate_adapter(checked_at),
            self._build_n8n_adapter(checked_at),
            self._build_autohotkey_adapter(checked_at),
            self._build_filesystem_adapter(checked_at),
            self._build_obsidian_adapter(checked_at),
            self._build_browser_later_adapter(checked_at),
        )
        return adapters

    def is_playwright_adapter_available_fast(self) -> bool:
        """Fast bootstrap check for the browser adapter without probing all PC adapters."""
        if not self.runtime_controls.browser_control_enabled:
            return False
        if not self._playwright_package_installed():
            return False
        return self.browser_runtime.describe().ready

    def prepare_dry_run(
        self,
        *,
        adapter_key: str,
        action_type: str,
        project_id: int | None,
        goal: str,
    ) -> PCAutomationDryRunView:
        normalized_adapter_key = adapter_key.strip().lower()
        normalized_action_type = action_type.strip().lower()
        normalized_goal = self._sanitize_goal(goal)

        if normalized_adapter_key not in ADAPTER_KEYS:
            raise ValueError(f"Unknown adapter_key '{adapter_key}'.")
        if normalized_action_type not in DRY_RUN_ACTION_TYPES:
            raise ValueError(f"Unknown action_type '{action_type}'.")

        adapter = next(
            (item for item in self.list_adapters() if item.adapter_key == normalized_adapter_key),
            None,
        )
        if adapter is None:
            raise ValueError(f"Unknown adapter_key '{adapter_key}'.")

        blocked_reasons = list(self._detect_blocked_goal(normalized_goal))
        if normalized_action_type == "powershell_command_draft" and normalized_adapter_key != "powershell":
            blocked_reasons.append("PowerShell command drafts require the powershell adapter.")
        if normalized_action_type == "browser_research_plan" and normalized_adapter_key not in {"playwright", "browser_later"}:
            blocked_reasons.append("Browser research plans require playwright or browser_later.")
        if normalized_action_type == "obsidian_note_context" and normalized_adapter_key != "obsidian":
            blocked_reasons.append("Obsidian note context requires the obsidian adapter.")
        if normalized_action_type == "n8n_workflow_draft" and normalized_adapter_key != "n8n_local":
            blocked_reasons.append("n8n workflow drafts require the n8n_local adapter.")
        if normalized_action_type == "power_automate_flow_draft" and normalized_adapter_key != "power_automate_desktop":
            blocked_reasons.append("Power Automate flow drafts require the power_automate_desktop adapter.")
        if normalized_action_type in {"filesystem_read_plan", "folder_cleanup_plan"} and normalized_adapter_key not in {"filesystem", "powershell"}:
            blocked_reasons.append("Filesystem planning requires the filesystem or powershell adapter.")
        if not adapter.available:
            blocked_reasons.append(f"Adapter '{adapter.display_name}' is not currently available.")
        if adapter.permission_mode in {"blocked", "not_installed"}:
            blocked_reasons.append(
                f"Adapter '{adapter.display_name}' is not enabled for supervised automation planning."
            )

        if blocked_reasons:
            return PCAutomationDryRunView(
                adapter_key=normalized_adapter_key,
                action_type=normalized_action_type,
                project_id=project_id,
                goal=normalized_goal,
                dry_run_required=True,
                requires_user_approval=True,
                risk_level="blocked",
                status="blocked",
                planned_steps=tuple(),
                blocked_reasons=tuple(dict.fromkeys(blocked_reasons)),
                audit_preview=self._build_audit_preview(
                    adapter_key=normalized_adapter_key,
                    action_type=normalized_action_type,
                    goal=normalized_goal,
                    status="blocked",
                ),
                execution_enabled=False,
            )

        risk_level = self._derive_risk_level(normalized_action_type)
        requires_user_approval = risk_level in {"medium", "high"}
        planned_steps = self._build_planned_steps(
            adapter_key=normalized_adapter_key,
            action_type=normalized_action_type,
            goal=normalized_goal,
        )

        return PCAutomationDryRunView(
            adapter_key=normalized_adapter_key,
            action_type=normalized_action_type,
            project_id=project_id,
            goal=normalized_goal,
            dry_run_required=True,
            requires_user_approval=requires_user_approval,
            risk_level=risk_level,
            status="dry_run_ready",
            planned_steps=planned_steps,
            blocked_reasons=tuple(),
            audit_preview=self._build_audit_preview(
                adapter_key=normalized_adapter_key,
                action_type=normalized_action_type,
                goal=normalized_goal,
                status="dry_run_ready",
            ),
            execution_enabled=False,
        )

    def _build_powershell_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        executable = shutil.which("powershell") or shutil.which("pwsh")
        installed = executable is not None
        available = installed
        return PCAutomationAdapterView(
            adapter_key="powershell",
            display_name="PowerShell",
            installed=installed,
            available=available,
            version=Path(executable).name if executable else None,
            permission_mode="draft_only" if available else "not_installed",
            risk_level="high",
            allowed_actions=("powershell_command_draft", "filesystem_read_plan", "folder_cleanup_plan"),
            blocked_actions=self._default_blocked_actions(),
            setup_status="ready" if available else "not_installed",
            setup_instructions=(
                "PowerShell detection is local-only in this build.",
                "Use scripts/setup-automation-adapters.ps1 for safe checks and setup guidance.",
            ),
            last_checked_at=checked_at,
        )

    def _build_playwright_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        package_installed = self._playwright_package_installed()
        browser_state = self.browser_runtime.describe()
        runtime_enabled = self.runtime_controls.browser_control_enabled
        available = package_installed and browser_state.ready and runtime_enabled
        permission_mode = (
            "supervised_execution"
            if package_installed
            and browser_state.ready
            and runtime_enabled
            and self.runtime_controls.autonomy_mode == "trusted_auto"
            else "draft_only"
            if package_installed and runtime_enabled
            else "blocked"
            if package_installed
            else "not_installed"
        )
        setup_status = (
            "ready"
            if available
            else "manual_setup_required"
            if package_installed and not browser_state.ready
            else "disabled_in_settings"
            if package_installed and browser_state.ready and not runtime_enabled
            else "not_installed"
        )
        return PCAutomationAdapterView(
            adapter_key="playwright",
            display_name="Playwright",
            installed=package_installed,
            available=available,
            version="node package detected" if package_installed else None,
            permission_mode=permission_mode,
            risk_level="medium",
            allowed_actions=("browser_research_plan",),
            blocked_actions=self._default_blocked_actions(),
            setup_status=setup_status,
            setup_instructions=(
                "Browser runtime Atnosa dziala tylko z kontrolowanych sciezek na F:.",
                f"Profil: {browser_state.profile_dir.as_posix()}",
                f"Assets Playwright: {browser_state.browsers_path.as_posix()}",
                browser_state.status_reason,
            ),
            last_checked_at=checked_at,
        )

    def _playwright_package_installed(self) -> bool:
        return (
            (self.settings.app_root / "node_modules" / "playwright").exists()
            or (self.settings.app_root / "runtime-recovered" / "api" / "node_modules" / "playwright").exists()
            or (Path.cwd() / "node_modules" / "playwright").exists()
        )
    def _build_power_automate_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        return PCAutomationAdapterView(
            adapter_key="power_automate_desktop",
            display_name="Power Automate Desktop",
            installed=False,
            available=False,
            version=None,
            permission_mode="blocked",
            risk_level="high",
            allowed_actions=("power_automate_flow_draft",),
            blocked_actions=self._default_blocked_actions(),
            setup_status="unsupported_in_f_only",
            setup_instructions=(
                "Power Automate Desktop nie jest raportowany jako runtime produkcyjny w trybie F-only.",
                "Jesli ma wrocic, musi dostac kontrolowany wariant uruchamiany z F: i osobny adapter.",
            ),
            last_checked_at=checked_at,
        )
        install_paths = (
            Path("C:/Program Files (x86)/Power Automate Desktop"),
            Path("C:/Program Files/Power Automate Desktop"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Power Automate Desktop",
        )
        installed = any(path.exists() for path in install_paths)
        runtime_enabled = self.runtime_controls.external_actions_enabled
        return PCAutomationAdapterView(
            adapter_key="power_automate_desktop",
            display_name="Power Automate Desktop",
            installed=installed,
            available=installed and runtime_enabled,
            version=None,
            permission_mode="draft_only" if installed and runtime_enabled else "blocked" if installed else "not_installed",
            risk_level="high",
            allowed_actions=("power_automate_flow_draft",),
            blocked_actions=self._default_blocked_actions(),
            setup_status="ready" if installed and runtime_enabled else "disabled_in_settings" if installed else "manual_setup_required",
            setup_instructions=(
                "Włącz akcje zewnętrzne w Ustawieniach, aby odblokować drafty flowów.",
                "Power Automate Desktop is checked by local install presence only.",
                "Manual install is required; this build does not create or run flows automatically.",
            ),
            last_checked_at=checked_at,
        )

    def _build_n8n_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        binary_installed = shutil.which("n8n") is not None
        localhost_open = self._can_connect("127.0.0.1", 5678)
        runtime_enabled = self.runtime_controls.external_actions_enabled
        installed = binary_installed or localhost_open
        available = (localhost_open or binary_installed) and runtime_enabled
        setup_status = (
            "ready"
            if localhost_open and runtime_enabled
            else "disabled_in_settings"
            if installed and not runtime_enabled
            else "detected"
            if binary_installed
            else "not_installed"
        )
        permission_mode = "draft_only" if installed and runtime_enabled else "blocked" if installed else "not_installed"
        return PCAutomationAdapterView(
            adapter_key="n8n_local",
            display_name="n8n Local",
            installed=installed,
            available=available,
            version="localhost:5678 detected" if localhost_open else None,
            permission_mode=permission_mode,
            risk_level="medium",
            allowed_actions=("n8n_workflow_draft",),
            blocked_actions=self._default_blocked_actions(),
            setup_status=setup_status,
            setup_instructions=(
                "Włącz akcje zewnętrzne w Ustawieniach, aby odblokować drafty workflowów.",
                "n8n detection checks only a local binary or localhost port.",
                "This build prepares workflow drafts only and never runs them.",
            ),
            last_checked_at=checked_at,
        )

    def _build_autohotkey_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        return PCAutomationAdapterView(
            adapter_key="autohotkey",
            display_name="AutoHotkey",
            installed=False,
            available=False,
            version=None,
            permission_mode="blocked",
            risk_level="blocked",
            allowed_actions=tuple(),
            blocked_actions=self._default_blocked_actions(),
            setup_status="unsupported_in_f_only",
            setup_instructions=(
                "AutoHotkey nie jest wspierany jako gotowy runtime Atnosa w trybie F-only.",
                "Jesli ma wrocic, potrzebuje osobnej binarki i kontrolowanego adaptera na F:.",
            ),
            last_checked_at=checked_at,
        )
        default_install_path = Path("C:/Program Files/AutoHotkey/AutoHotkey.exe")
        executable = (
            shutil.which("AutoHotkey.exe")
            or shutil.which("AutoHotkey64.exe")
            or (str(default_install_path) if default_install_path.exists() else None)
        )
        installed = executable is not None
        return PCAutomationAdapterView(
            adapter_key="autohotkey",
            display_name="AutoHotkey",
            installed=installed,
            available=False,
            version=Path(executable).name if executable else None,
            permission_mode="blocked" if installed else "not_installed",
            risk_level="blocked",
            allowed_actions=tuple(),
            blocked_actions=self._default_blocked_actions(),
            setup_status="detected_but_blocked" if installed else "not_installed",
            setup_instructions=(
                "AutoHotkey is detected only for future supervised adapter work.",
                "Script execution is blocked in this build.",
            ),
            last_checked_at=checked_at,
        )

    def _build_filesystem_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        return PCAutomationAdapterView(
            adapter_key="filesystem",
            display_name="Filesystem",
            installed=True,
            available=True,
            version="local workspace",
            permission_mode="read_only",
            risk_level="low",
            allowed_actions=("filesystem_read_plan", "folder_cleanup_plan"),
            blocked_actions=self._default_blocked_actions(),
            setup_status="ready",
            setup_instructions=(
                "Filesystem planning is read-only in this build.",
                "No delete or write execution path is enabled.",
            ),
            last_checked_at=checked_at,
        )

    def _build_obsidian_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        vault_status = self.obsidian.get_vault_status(include_note_count=False)
        return PCAutomationAdapterView(
            adapter_key="obsidian",
            display_name=f"Obsidian ({vault_status.vault_name})",
            installed=vault_status.path_configured,
            available=vault_status.readable,
            version="vault configured" if vault_status.path_configured else None,
            permission_mode="read_only" if vault_status.readable else "detected" if vault_status.path_configured else "not_installed",
            risk_level="low",
            allowed_actions=("obsidian_note_context",),
            blocked_actions=self._default_blocked_actions(),
            setup_status="ready" if vault_status.readable else "manual_setup_required" if vault_status.path_configured else "not_configured",
            setup_instructions=(
                "Configure the local path for vault obsydian in Settings > Account setup.",
                "Atnos reads only the configured vault and never scans the whole disk automatically.",
            ),
            last_checked_at=checked_at,
        )

    def _build_browser_later_adapter(self, checked_at: str) -> PCAutomationAdapterView:
        runtime_enabled = self.runtime_controls.browser_control_enabled
        return PCAutomationAdapterView(
            adapter_key="browser_later",
            display_name="Browser automation (later)",
            installed=runtime_enabled,
            available=False,
            version=None,
            permission_mode="blocked" if runtime_enabled else "not_installed",
            risk_level="blocked",
            allowed_actions=("browser_research_plan",),
            blocked_actions=self._default_blocked_actions(),
            setup_status="planned" if runtime_enabled else "disabled_in_settings",
            setup_instructions=(
                "Browser control is not enabled in this build.",
                "Use Playwright detection for planning only; execution remains disabled.",
            ),
            last_checked_at=checked_at,
        )

    def _build_planned_steps(
        self,
        *,
        adapter_key: str,
        action_type: str,
        goal: str,
    ) -> tuple[dict[str, object], ...]:
        if action_type == "filesystem_read_plan":
            return (
                {
                    "title": "Scope the folder review",
                    "detail": "Confirm which folder or workspace subset should be reviewed before any manual action.",
                    "preview_only": True,
                },
                {
                    "title": "Prepare a read-only inspection plan",
                    "detail": f"Use {adapter_key} only to list files and summarize patterns for goal: {goal}",
                    "preview_only": True,
                },
                {
                    "title": "Return a safe summary",
                    "detail": "Report findings, duplicates, large folders, and cleanup suggestions without deleting anything.",
                    "preview_only": True,
                },
            )
        if action_type == "folder_cleanup_plan":
            return (
                {
                    "title": "Review the current folder layout",
                    "detail": "Inspect names, sizes, and duplicates manually before proposing any cleanup buckets.",
                    "preview_only": True,
                },
                {
                    "title": "Prepare a cleanup proposal",
                    "detail": f"Draft categories, archive suggestions, and naming improvements for goal: {goal}",
                    "preview_only": True,
                },
                {
                    "title": "Require explicit approval for any future write path",
                    "detail": "Deletion and file moves stay blocked until a supervised execution adapter exists.",
                    "preview_only": True,
                },
            )
        if action_type == "powershell_command_draft":
            return (
                {
                    "title": "Draft a read-only PowerShell command",
                    "detail": "Prepare a command draft for manual review only.",
                    "command_draft": "Get-ChildItem -Path $HOME\\Downloads -Force | Select-Object Name,Length,LastWriteTime",
                    "preview_only": True,
                },
                {
                    "title": "Explain the command scope",
                    "detail": f"The command draft stays read-only and maps to goal: {goal}",
                    "preview_only": True,
                },
            )
        if action_type == "browser_research_plan":
            return (
                {
                    "title": "Define the research objective",
                    "detail": f"Clarify which public pages or docs should be reviewed for goal: {goal}",
                    "preview_only": True,
                },
                {
                    "title": "Prepare a browser research path",
                    "detail": "Open public pages, collect notes, and summarize findings without clicks, login, or form submission.",
                    "preview_only": True,
                },
            )
        if action_type == "obsidian_note_context":
            return (
                {
                    "title": "Review matching Obsidian notes",
                    "detail": "Use the configured obsydian vault as read-only context.",
                    "preview_only": True,
                },
                {
                    "title": "Prepare a context summary",
                    "detail": f"Extract note themes, links, and context relevant to goal: {goal}",
                    "preview_only": True,
                },
            )
        if action_type == "n8n_workflow_draft":
            return (
                {
                    "title": "Map the workflow trigger",
                    "detail": "Describe the manual trigger and source inputs without enabling runtime execution.",
                    "preview_only": True,
                },
                {
                    "title": "Draft the workflow nodes",
                    "detail": f"Prepare a local workflow draft for goal: {goal}",
                    "preview_only": True,
                },
            )
        if action_type == "power_automate_flow_draft":
            return (
                {
                    "title": "Define the supervised flow boundary",
                    "detail": "List the local apps, files, or folders involved without running any flow.",
                    "preview_only": True,
                },
                {
                    "title": "Prepare a flow draft",
                    "detail": f"Draft a manual Power Automate Desktop flow for goal: {goal}",
                    "preview_only": True,
                },
            )
        return tuple()

    def _build_audit_preview(
        self,
        *,
        adapter_key: str,
        action_type: str,
        goal: str,
        status: str,
    ) -> tuple[dict[str, object], ...]:
        return (
            {
                "event": "pc_automation_dry_run_prepared",
                "adapter_key": adapter_key,
                "action_type": action_type,
                "goal_summary": self._truncate(goal, 180),
                "status": status,
                "at": self._utcnow().isoformat(),
            },
        )

    def _derive_risk_level(self, action_type: str) -> str:
        if action_type in {"filesystem_read_plan", "obsidian_note_context"}:
            return "low"
        if action_type in {"browser_research_plan", "folder_cleanup_plan", "n8n_workflow_draft"}:
            return "medium"
        return "high"

    def _detect_blocked_goal(self, goal: str) -> tuple[str, ...]:
        lowered = goal.lower()
        reasons: list[str] = []
        for token, message in BLOCKED_ACTION_PATTERNS.items():
            if token in lowered and message not in reasons:
                reasons.append(message)
        return tuple(reasons)

    def _sanitize_goal(self, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("goal is required.")
        redacted = normalized
        redacted = re.sub(r"(?i)\bBearer\s+\S+", "[REDACTED]", redacted)
        redacted = re.sub(r"(?i)\bsk-[A-Za-z0-9_\-]{6,}\b", "[REDACTED]", redacted)
        for token in SECRET_LIKE_TOKENS:
            redacted = re.sub(rf"(?i)\b{re.escape(token)}\b", "[REDACTED]", redacted)
        return self._truncate(redacted, 800)

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3].rstrip() + "..."

    @staticmethod
    def _default_blocked_actions() -> tuple[str, ...]:
        return (
            "delete_files",
            "credential_entry",
            "login",
            "payment",
            "banking",
            "trading",
            "publish_upload",
            "send_messages",
            "change_account_settings",
            "software_install_without_confirmation",
            "arbitrary_script_execution",
            "private_site_scraping",
            "captcha_or_paywall_bypass",
        )

    @staticmethod
    def _can_connect(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
