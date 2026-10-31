from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.local_runtime_probe_service import LocalRuntimeProbeService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.youtube_connector_service import YouTubeConnectorService


@dataclass(frozen=True, slots=True)
class OmegaProtocolStatusView:
    profile_key: str
    mode: str
    activated: bool
    ready_for_browser_workflows: bool
    ready_for_account_login: bool
    ready_for_two_factor: bool
    ready_for_publish: bool
    web_advisor_fallback_enabled: bool
    trusted_accounts_count: int
    trust_grants_count: int
    runtime_controls: dict[str, object]
    browser_runtime: dict[str, object]
    trusted_filesystem_roots: tuple[str, ...]
    runtime_probes: tuple[dict[str, object], ...]
    allowed_capabilities: tuple[str, ...]
    blocked_capabilities: tuple[str, ...]
    recommended_next_steps: tuple[str, ...]
    persona_protocol: tuple[str, ...]
    note_path: str | None
    note_written_at: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_key": self.profile_key,
            "mode": self.mode,
            "activated": self.activated,
            "ready_for_browser_workflows": self.ready_for_browser_workflows,
            "ready_for_account_login": self.ready_for_account_login,
            "ready_for_two_factor": self.ready_for_two_factor,
            "ready_for_publish": self.ready_for_publish,
            "web_advisor_fallback_enabled": self.web_advisor_fallback_enabled,
            "trusted_accounts_count": self.trusted_accounts_count,
            "trust_grants_count": self.trust_grants_count,
            "runtime_controls": dict(self.runtime_controls),
            "browser_runtime": dict(self.browser_runtime),
            "trusted_filesystem_roots": list(self.trusted_filesystem_roots),
            "runtime_probes": [dict(item) for item in self.runtime_probes],
            "allowed_capabilities": list(self.allowed_capabilities),
            "blocked_capabilities": list(self.blocked_capabilities),
            "recommended_next_steps": list(self.recommended_next_steps),
            "persona_protocol": list(self.persona_protocol),
            "note_path": self.note_path,
            "note_written_at": self.note_written_at,
        }


class OmegaProtocolService:
    NOTE_RELATIVE_PATH = Path("Atnos") / "System" / "Omega-Expanded-Protocol.md"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.runtime_settings = RuntimeSettingsService(session, settings)
        self.browser_runtime = BrowserRuntimeService(settings)
        self.local_probes = LocalRuntimeProbeService(settings)
        self.integration_config = IntegrationConfigService(session, settings)

    def get_status(self) -> OmegaProtocolStatusView:
        controls = self.runtime_settings.snapshot()
        browser_runtime = self.browser_runtime.describe()
        runtime_probes = tuple(self.local_probes.probe_all())
        probe_index = {item.key: item for item in runtime_probes}
        trusted_accounts_count = self._count_table("account_credentials")
        trust_grants_count = self._count_table("trust_grants")
        youtube_status = self._safe_youtube_status()

        activated = all(
            (
                controls.autonomy_mode == "supervised",
                controls.browser_control_enabled,
                controls.external_actions_enabled,
                controls.scheduler_enabled,
                not controls.login_automation_enabled,
                not controls.two_factor_automation_enabled,
            )
        )
        ready_for_browser_workflows = activated and browser_runtime.ready
        ready_for_account_login = False
        ready_for_two_factor = False
        ready_for_publish = ready_for_account_login and controls.publish_automation_enabled and youtube_status.get("ready") is True
        web_advisor_fallback_enabled = ready_for_browser_workflows and controls.external_actions_enabled

        note_path, note_written_at = self._existing_note_metadata()

        return OmegaProtocolStatusView(
            profile_key="omega_expanded",
            mode="expanded",
            activated=activated,
            ready_for_browser_workflows=ready_for_browser_workflows,
            ready_for_account_login=ready_for_account_login,
            ready_for_two_factor=ready_for_two_factor,
            ready_for_publish=ready_for_publish,
            web_advisor_fallback_enabled=web_advisor_fallback_enabled,
            trusted_accounts_count=trusted_accounts_count,
            trust_grants_count=trust_grants_count,
            runtime_controls={
                "autonomy_mode": controls.autonomy_mode,
                "browser_control_enabled": controls.browser_control_enabled,
                "external_actions_enabled": controls.external_actions_enabled,
                "scheduler_enabled": controls.scheduler_enabled,
                "oauth_enabled": controls.oauth_enabled,
                "emergency_stop_enabled": controls.emergency_stop_enabled,
                "pause_all_jobs": controls.pause_all_jobs,
                "login_automation_enabled": controls.login_automation_enabled,
                "two_factor_automation_enabled": controls.two_factor_automation_enabled,
                "publish_automation_enabled": controls.publish_automation_enabled,
                "default_retry_budget": controls.default_retry_budget,
                "default_vram_floor_gb": controls.default_vram_floor_gb,
            },
            browser_runtime=browser_runtime.to_dict(),
            trusted_filesystem_roots=self._trusted_filesystem_roots(),
            runtime_probes=tuple(
                {
                    "key": item.key,
                    "label": item.label,
                    "status": item.status,
                    "ready": item.ready,
                    "reason": item.reason,
                }
                for item in runtime_probes
            ),
            allowed_capabilities=(
                "Vault-backed Trusted Accounts with direct browser-first login and recovery flows for connected user-owned services.",
                "Read and write work inside trusted ATNOS roots on F:, including app data, staging, browser artifacts and the configured Obsidian vault.",
                "Dedicated ATNOS browser profile with screenshots, traces, retries and auditable browser recipes across approved websites and app workflows.",
                "Supervised scheduler, interrupted-run recovery and web-advisor fallback with auditable confirmation boundaries.",
                "Structured Obsidian memory and runtime notes inside the configured vault, without pushing secrets into notes.",
            ),
            blocked_capabilities=(
                "Silent takeover of unrelated private Opera tabs, cookies or non-ATNOS sessions.",
                "Plaintext passwords, tokens or recovery secrets in Obsidian notes.",
                "CAPTCHA, paywall or account-security challenge bypass.",
                "Live broker execution or autonomous financial orders.",
            ),
            recommended_next_steps=self._build_next_steps(
                probe_index=probe_index,
                browser_ready=browser_runtime.ready,
                ready_for_account_login=ready_for_account_login,
                youtube_ready=youtube_status.get("ready") is True,
            ),
            persona_protocol=(
                "Use first person singular and report concrete actions directly.",
                "Within trusted scope, act directly, log evidence and avoid unnecessary confirmation loops.",
                "When blocked by missing runtime or trust scope, say that execution cannot continue without that dependency.",
                "Never promise unrestricted private-session takeover or invisible credential handling.",
            ),
            note_path=note_path,
            note_written_at=note_written_at,
        )

    def activate_guarded_profile(self) -> OmegaProtocolStatusView:
        snapshot = self.runtime_settings.snapshot()
        self.runtime_settings.update_owner_controls(
            autonomy_mode="supervised",
            browser_control_enabled=True,
            browser_allow_any_url=False,
            external_actions_enabled=True,
            scheduler_enabled=True,
            emergency_stop_enabled=False,
            pause_all_jobs=False,
            login_automation_enabled=False,
            two_factor_automation_enabled=False,
            publish_automation_enabled=False,
            public_upload_enabled=False,
            default_retry_budget=max(3, snapshot.default_retry_budget),
            default_vram_floor_gb=max(6, snapshot.default_vram_floor_gb),
        )
        status = self.get_status()
        self._write_note(status)
        return self.get_status()

    def _build_next_steps(
        self,
        *,
        probe_index: dict[str, object],
        browser_ready: bool,
        ready_for_account_login: bool,
        youtube_ready: bool,
    ) -> tuple[str, ...]:
        steps: list[str] = []
        if not browser_ready:
            steps.append("Napraw browser runtime na F:, zanim ruszysz z web advisor fallback albo login automation.")
        comfy = probe_index.get("comfyui")
        if comfy is not None and getattr(comfy, "ready", False) is not True:
            steps.append("Podlacz ComfyUI jako pierwszy runtime assetowy dla stills, thumbnaili i identity-lock.")
        kokoro = probe_index.get("kokoro-local")
        if kokoro is not None and getattr(kokoro, "ready", False) is not True:
            steps.append("Podlacz Kokoro local, zeby polski voice pipeline nie zalezal od browserowych obejsc.")
        if not ready_for_account_login:
            steps.append("Podepnij konta i granty do konkretnych projektow, zamiast zostawiac sam runtime bez scope.")
        if not youtube_ready:
            steps.append("Skonfiguruj YouTube OAuth, jesli chcesz przejsc z manual-ready publish package do realnego uploadu.")
        if not steps:
            steps.append("Runtime jest gotowy do szerokich trusted workflowow na F: i browser-first execution dla podlaczonych kont.")
        return tuple(steps[:5])

    def _trusted_filesystem_roots(self) -> tuple[str, ...]:
        roots = [
            self.settings.app_root,
            self.settings.data_dir,
            self.settings.uploads_dir,
            self.settings.workspace_staging_dir,
            self.settings.browser_profile_dir,
            self.settings.browser_downloads_dir,
            self.settings.browser_trace_dir,
        ]
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is not None:
            roots.append(config.vault_path)
        unique: list[str] = []
        seen: set[str] = set()
        for root in roots:
            if root is None:
                continue
            value = str(root.resolve())
            if value not in seen:
                seen.add(value)
                unique.append(value)
        return tuple(unique)

    def _safe_youtube_status(self) -> dict[str, object]:
        try:
            return YouTubeConnectorService(self.session, self.settings).get_status().to_dict()
        except Exception:
            return {"ready": False}

    def _existing_note_metadata(self) -> tuple[str | None, str | None]:
        config = self.integration_config.get_obsidian_config()
        if not config.export_enabled or config.vault_path is None or not config.path_exists:
            return None, None
        target = (config.vault_path / self.NOTE_RELATIVE_PATH).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError:
            return None, None
        if not target.exists():
            return self.NOTE_RELATIVE_PATH.as_posix(), None
        written_at = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc).isoformat()
        return self.NOTE_RELATIVE_PATH.as_posix(), written_at

    def _write_note(self, status: OmegaProtocolStatusView) -> None:
        config = self.integration_config.get_obsidian_config()
        if not config.export_enabled or config.vault_path is None or not config.path_exists:
            return
        vault_root = config.vault_path.resolve()
        target = (vault_root / self.NOTE_RELATIVE_PATH).resolve()
        target.relative_to(vault_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._build_note_markdown(status), encoding="utf-8")

    @staticmethod
    def _build_note_markdown(status: OmegaProtocolStatusView) -> str:
        exported_at = datetime.now(timezone.utc).isoformat()
        probe_lines = [
            f"- {item['label']}: {item['status']} — {item['reason']}"
            for item in status.runtime_probes
        ]
        allowed_lines = [f"- {item}" for item in status.allowed_capabilities]
        blocked_lines = [f"- {item}" for item in status.blocked_capabilities]
        next_step_lines = [f"- {item}" for item in status.recommended_next_steps]

        return "\n".join(
            [
                "---",
                "type: atnos-runtime-profile",
                "profile_key: omega_expanded",
                f"exported_at: {exported_at}",
                f"activated: {'true' if status.activated else 'false'}",
                "---",
                "",
                "# Omega Expanded",
                "",
                "## Status",
                "",
                f"- Browser workflows ready: {'tak' if status.ready_for_browser_workflows else 'nie'}",
                f"- Account login ready: {'tak' if status.ready_for_account_login else 'nie'}",
                f"- Two-factor ready: {'tak' if status.ready_for_two_factor else 'nie'}",
                f"- Publish ready: {'tak' if status.ready_for_publish else 'nie'}",
                f"- Trusted Accounts: {status.trusted_accounts_count}",
                f"- Trust grants: {status.trust_grants_count}",
                "",
                "## Runtime probes",
                "",
                *probe_lines,
                "",
                "## Trusted filesystem roots",
                "",
                *[f"- {item}" for item in status.trusted_filesystem_roots],
                "",
                "## Allowed capabilities",
                "",
                *allowed_lines,
                "",
                "## Explicitly blocked",
                "",
                *blocked_lines,
                "",
                "## Next steps",
                "",
                *next_step_lines,
                "",
                "## Notes",
                "",
                "This is an expanded autonomy preset. It unlocks broader trusted execution with Vault, browser recipes, trusted F: roots and runtime recovery.",
                "It still does not authorize silent takeover of private sessions or storing plaintext secrets in Obsidian.",
                "",
            ]
        )

    def _count_table(self, table_name: str) -> int:
        statement = f"SELECT COUNT(*) FROM {table_name}"
        return int(self.session.connection().exec_driver_sql(statement).scalar() or 0)
