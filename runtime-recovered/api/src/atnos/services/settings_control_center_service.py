from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import (
    ExecutionAutomationLevel,
    ExecutionVerificationMode,
    JarvisAddressAs,
    PersonaProfile,
    PluginPermissionPolicy,
    ProviderPosture,
    ResponseLengthPreference,
    ResponseStructure,
    ResponseTone,
    RoleProfileScope,
    VoiceProviderMode,
)
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.plugin_repository import PluginRepository
from atnos.repositories.provider_repository import ProviderRepository
from atnos.repositories.role_profile_repository import RoleProfileRepository
from atnos.repositories.user_repository import UserRepository
from atnos.repositories.user_style_preferences_repository import UserStylePreferencesRepository
from atnos.repositories.voice_profile_repository import VoiceProfileRepository
from atnos.repositories.decision_log_repository import DecisionLogRepository
from atnos.skills.skill_cards import build_skill_dashboard_payload
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.creative_asset_operator_service import CreativeAssetOperatorService
from atnos.services.local_host_control_service import LocalHostControlService
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeProbeService
from atnos.services.n8n_runtime_status_service import N8nRuntimeStatusService
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.access_reality_service import AccessRealityService
from atnos.services.browser_access_diagnostics_service import BrowserAccessDiagnosticsService
from atnos.services.obsidian_read_service import ObsidianReadService
from atnos.services.omega_protocol_service import OmegaProtocolService
from atnos.services.oauth_service import OAuthService
from atnos.services.pc_automation_service import PCAutomationService
from atnos.services.runtime_inventory_service import DEPRECATED_PROVIDER_KEYS, RuntimeInventoryService
from atnos.services.remote_node_service import RemoteNodeService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.trend_watch_service import TrendWatchService
from atnos.services.trusted_auto_service import TrustedAutoService
from atnos.services.ui_adapter_service import UiAdapterService
from atnos.services.voice_ambient_listener_service import VoiceAmbientListenerService
from atnos.services.voice_synthesis_service import VoiceSynthesisService


class SettingsControlCenterService:
    VOICE_PRESET_BLUEPRINTS: tuple[dict[str, object], ...] = (
        {
            "name": "Polski creator premium",
            "voice_key": VoiceSynthesisService.DEFAULT_POLISH_VOICE,
            "provider_mode": VoiceProviderMode.REMOTE,
            "style": "warm-premium",
            "language": "pl-PL",
            "speaking_rate": "-12%",
            "supports_realtime": False,
            "auto_read_responses": True,
            "is_default": True,
            "notes": "Cieplejszy, bardziej ludzki polski glos do codziennych odpowiedzi Atnosa.",
        },
        {
            "name": "Polski ekspert",
            "voice_key": "pl_piotr",
            "provider_mode": VoiceProviderMode.REMOTE,
            "style": "clear-direct",
            "language": "pl-PL",
            "speaking_rate": "-4%",
            "supports_realtime": False,
            "auto_read_responses": False,
            "is_default": False,
            "notes": "Bardziej konkretny, spokojny glos do zadan eksperckich i instrukcji.",
        },
    )

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
    ) -> None:
        self.session = session
        self.base_settings = settings
        self.provider_registry = provider_registry
        self.users = UserRepository(session)
        self.styles = UserStylePreferencesRepository(session)
        self.policies = ExecutionPolicyRepository(session)
        self.plugins = PluginRepository(session)
        self.providers = ProviderRepository(session)
        self.roles = RoleProfileRepository(session)
        self.voices = VoiceProfileRepository(session)
        self.decisions = DecisionLogRepository(session)
        self.runtime_settings = RuntimeSettingsService(session, settings)

    def get_payload(self) -> dict[str, Any]:
        owner = self._ensure_owner()
        style = self._ensure_style_preferences(owner.id)
        policy = self._ensure_execution_policy(owner.id)
        self._ensure_voice_profiles(owner.id)
        runtime_controls = self.runtime_settings.snapshot()
        effective_settings = self.runtime_settings.build_effective_settings()
        runtime_inventory = RuntimeInventoryService(
            self.session,
            provider_registry=self.provider_registry,
            settings=effective_settings,
        ).snapshot(include_live_health=False)
        provider_cards = UiAdapterService(
            self.session,
            settings=effective_settings,
            provider_registry=self.provider_registry,
        ).list_provider_cards(runtime_snapshot=runtime_inventory)
        oauth_connectors = OAuthService(self.session, self.base_settings).list_connectors()
        pc_automation = PCAutomationService(self.session, settings=self.base_settings)
        capability_snapshot = pc_automation.get_capability_snapshot()
        adapters = pc_automation.list_adapters()
        obsidian_config = IntegrationConfigService(self.session, self.base_settings).get_obsidian_config()
        vault_status = ObsidianReadService(self.session, self.base_settings).get_vault_status()
        trusted_auto = TrustedAutoService(self.session, self.base_settings).dashboard_payload()
        try:
            access_reality = AccessRealityService(
                self.session,
                settings=self.base_settings,
                provider_registry=self.provider_registry,
            ).build_snapshot(include_live_health=False)
        except Exception:
            access_reality = None
        omega_protocol = self._build_fast_omega_protocol_payload(
            runtime_controls=runtime_controls,
            trusted_auto=trusted_auto,
            obsidian_config=obsidian_config,
        )
        skill_dashboard = self._build_fast_skill_dashboard_payload()
        voice_runtime = VoiceSynthesisService(effective_settings).runtime_availability()
        local_short_runtime = LocalShortRuntimeProbeService(effective_settings).snapshot()
        ambient_runtime = VoiceAmbientListenerService.readiness_for(
            controls=runtime_controls,
            effective_settings=effective_settings,
        )
        recent_decisions = self.decisions.list_recent(limit=48)
        remote_workers = RemoteNodeService(self.session, self.base_settings).list_nodes_payload()["remote_workers"]
        local_host_service = LocalHostControlService(self.session, settings=self.base_settings)
        local_host = local_host_service.get_capability_snapshot().to_dict()
        local_host["desktop_apps"] = [item.to_dict() for item in local_host_service.list_desktop_apps()]
        local_host["chrome_diagnostics"] = BrowserAccessDiagnosticsService(
            self.session,
            settings=self.base_settings,
        ).diagnose_chrome().to_dict()
        trend_watch = TrendWatchService(self.session, self.base_settings).latest_payload()
        creative_asset_operator = self._creative_asset_operator()
        creative_asset_lanes = creative_asset_operator.build_lane_snapshot(
            allow_external_fallback=policy.allow_external_fallback,
            runtime_controls=runtime_controls,
        )
        creative_asset_lane_summary = creative_asset_operator.build_lane_summary()
        n8n_runtime = N8nRuntimeStatusService(effective_settings).snapshot(
            probe_docker=effective_settings.app_env != "test",
        )

        return {
            "profile": {
                "user_id": owner.id,
                "display_name": owner.display_name,
                "handle": owner.handle,
                "email": owner.email,
                "bio": owner.bio,
                "locale": owner.locale,
                "timezone": owner.timezone,
            },
            "style": {
                "response_length": style.response_length.value,
                "tone": style.tone.value,
                "structure": style.structure.value,
                "criticality_level": style.criticality_level,
                "supportiveness_level": style.supportiveness_level,
                "professional_default": style.professional_default,
            },
            "policy": {
                "automation_level": policy.automation_level.value,
                "verification_mode": policy.verification_mode.value,
                "allow_local_file_read": policy.allow_local_file_read,
                "allow_local_file_write": policy.allow_local_file_write,
                "allow_web_read": policy.allow_web_read,
                "allow_external_fallback": policy.allow_external_fallback,
                "allow_external_attachment_metadata": policy.allow_external_attachment_metadata,
                "allow_external_attachment_content": policy.allow_external_attachment_content,
                "prefer_local_llm": policy.prefer_local_llm,
                "default_provider_posture": policy.default_provider_posture.value,
                "default_persona_profile": policy.default_persona_profile.value,
                "jarvis_address_as": policy.jarvis_address_as.value,
                "max_external_attachment_text_chars": policy.max_external_attachment_text_chars,
                "max_daily_cost": policy.max_daily_cost,
                "max_monthly_cost": policy.max_monthly_cost,
            },
            "runtime": {
                "browser_control_enabled": runtime_controls.browser_control_enabled,
                "stealth_headless_enabled": runtime_controls.stealth_headless_enabled,
                "browser_allow_any_url": runtime_controls.browser_allow_any_url,
                "external_actions_enabled": runtime_controls.external_actions_enabled,
                "scheduler_enabled": runtime_controls.scheduler_enabled,
                "oauth_enabled": runtime_controls.oauth_enabled,
                "autonomy_mode": runtime_controls.autonomy_mode,
                "emergency_stop_enabled": runtime_controls.emergency_stop_enabled,
                "pause_all_jobs": runtime_controls.pause_all_jobs,
                "login_automation_enabled": runtime_controls.login_automation_enabled,
                "two_factor_automation_enabled": runtime_controls.two_factor_automation_enabled,
                "publish_automation_enabled": runtime_controls.publish_automation_enabled,
                "public_upload_enabled": runtime_controls.public_upload_enabled,
                "default_retry_budget": runtime_controls.default_retry_budget,
                "default_vram_floor_gb": runtime_controls.default_vram_floor_gb,
                "voice_stt_engine": runtime_controls.voice_stt_engine,
                "voice_stt_model_path": runtime_controls.voice_stt_model_path,
                "voice_stt_device": runtime_controls.voice_stt_device,
                "ambient_listening_enabled": runtime_controls.ambient_listening_enabled,
                "ambient_wake_word": runtime_controls.ambient_wake_word,
                "ambient_mic_device": runtime_controls.ambient_mic_device,
                "ambient_muted": runtime_controls.ambient_muted,
                "ambient_context_enabled": runtime_controls.ambient_context_enabled,
                "trend_watch_enabled": runtime_controls.trend_watch_enabled,
                "trend_watch_query": runtime_controls.trend_watch_query,
                "trend_watch_region": runtime_controls.trend_watch_region,
                "trend_watch_interval_minutes": runtime_controls.trend_watch_interval_minutes,
                "trend_watch_last_run_at": (
                    runtime_controls.trend_watch_last_run_at.isoformat()
                    if runtime_controls.trend_watch_last_run_at is not None
                    else None
                ),
                "voice_replies_enabled": runtime_controls.voice_replies_enabled,
                "voice_reply_volume": runtime_controls.voice_reply_volume,
                "voice_reply_voice_id": runtime_controls.voice_reply_voice_id,
                "voice_replies_available": bool(voice_runtime["available"]),
                "voice_replies_reason": str(voice_runtime["reason"]),
                "local_shorts_enabled": bool(local_short_runtime.enabled),
                "local_shorts_ready": bool(local_short_runtime.ready),
                "local_shorts_reason": str(local_short_runtime.reason),
                "local_shorts_runtime": [item.to_dict() for item in local_short_runtime.components],
                "ambient_listening_available": ambient_runtime.available,
                "ambient_listening_reason": ambient_runtime.reason,
                "last_user_interaction_at": (
                    runtime_controls.last_user_interaction_at.isoformat()
                    if runtime_controls.last_user_interaction_at is not None
                    else None
                ),
                "last_briefing_at": (
                    runtime_controls.last_briefing_at.isoformat()
                    if runtime_controls.last_briefing_at is not None
                    else None
                ),
                "execution_enabled": capability_snapshot.execution_enabled,
                "pc_operator_mode": capability_snapshot.pc_operator_mode,
                "action_broker_mode": capability_snapshot.action_broker_mode,
                "playwright_adapter_available": any(
                    item.adapter_key == "playwright" and item.available for item in adapters
                ),
            },
            "capabilities": {
                "browser_control_enabled": runtime_controls.browser_control_enabled,
                "external_actions_enabled": runtime_controls.external_actions_enabled,
                "scheduler_enabled": runtime_controls.scheduler_enabled,
                "playwright_adapter_available": any(
                    item.adapter_key == "playwright" and item.available for item in adapters
                ),
                "oauth_enabled": runtime_controls.oauth_enabled,
                "obsidian_export_enabled": obsidian_config.export_enabled,
                "autonomy_mode": runtime_controls.autonomy_mode,
                "execution_enabled": capability_snapshot.execution_enabled,
            },
            "obsidian": {
                "vault_path": str(obsidian_config.vault_path) if obsidian_config.vault_path is not None else None,
                "export_enabled": obsidian_config.export_enabled,
                "path_exists": obsidian_config.path_exists,
                "source": obsidian_config.source,
                "warnings": list(obsidian_config.warnings),
                "vault_name": vault_status.vault_name,
                "readable": vault_status.readable,
                "note_count": vault_status.note_count,
            },
            "voices": [self._serialize_voice(item) for item in self.voices.list_for_user(owner.id)],
            "roles": [self._serialize_role(item) for item in self.roles.list_active()],
            "plugins": [self._serialize_plugin(item) for item in self.plugins.list_all()],
            "provider_options": [
                {
                    "provider_id": item.id,
                    "provider_key": item.key,
                    "label": item.label,
                    "kind": item.kind.value,
                    "is_enabled": item.is_enabled,
                }
                for item in self.providers.list_all()
                if item.key not in DEPRECATED_PROVIDER_KEYS
            ],
            "provider_cards": provider_cards,
            "oauth_connectors": [
                {
                    "connector_key": item.connector_key,
                    "display_name": item.display_name,
                    "status": item.status,
                    "enabled": item.enabled,
                    "configured": item.configured,
                    "connection_id": item.connection_id,
                    "connected_at": item.connected_at.isoformat() if item.connected_at is not None else None,
                    "setup_warnings": list(item.setup_warnings),
                }
                for item in oauth_connectors
            ],
            "runtime_summary": {
                "providers_ready": runtime_inventory.summary.ready_providers,
                "enabled_models": runtime_inventory.summary.enabled_models,
                "tasks_ready": runtime_inventory.summary.tasks_ready,
                "has_local_chat": runtime_inventory.summary.has_local_chat,
                "has_external_fallback": runtime_inventory.summary.has_external_fallback,
                "voice_stt": {
                    "configured_engine": runtime_inventory.voice_stt.configured_engine,
                    "effective_engine": runtime_inventory.voice_stt.effective_engine,
                    "status": runtime_inventory.voice_stt.status,
                    "status_reason": runtime_inventory.voice_stt.status_reason,
                    "configured_model_path": runtime_inventory.voice_stt.configured_model_path,
                    "configured_device": runtime_inventory.voice_stt.configured_device,
                },
            },
            "trusted_auto": trusted_auto,
            "access_reality": access_reality.to_dict() if access_reality is not None else None,
            "omega_protocol": omega_protocol,
            "skills_dashboard": skill_dashboard,
            "decision_log": [self._serialize_decision_log(item) for item in recent_decisions],
            "local_host": local_host,
            "remote_workers": remote_workers,
            "trend_watch": {
                "enabled": runtime_controls.trend_watch_enabled,
                "query": runtime_controls.trend_watch_query,
                "region": runtime_controls.trend_watch_region,
                "interval_minutes": runtime_controls.trend_watch_interval_minutes,
                "last_run_at": (
                    runtime_controls.trend_watch_last_run_at.isoformat()
                    if runtime_controls.trend_watch_last_run_at is not None
                    else None
                ),
                "latest_snapshot": trend_watch,
            },
            "n8n_youtube_automation": {
                **n8n_runtime.to_dict(),
                "enabled": effective_settings.n8n_enabled,
                "base_url": effective_settings.n8n_base_url,
                "mode": effective_settings.n8n_youtube_automation_mode,
                "data_dir": str(effective_settings.n8n_data_dir),
            },
            "creative_asset_lanes": creative_asset_lanes,
            "creative_asset_lane_summary": creative_asset_lane_summary,
        }

    def update_profile(self, **kwargs) -> dict[str, Any]:
        owner = self._ensure_owner()
        updates = dict(kwargs)
        if updates:
            self.users.update(owner, **updates)
        return self.get_payload()

    def update_style(self, **kwargs) -> dict[str, Any]:
        owner = self._ensure_owner()
        style = self._ensure_style_preferences(owner.id)
        updates = {
            "response_length": ResponseLengthPreference(kwargs["response_length"])
            if kwargs.get("response_length") is not None
            else None,
            "tone": ResponseTone(kwargs["tone"]) if kwargs.get("tone") is not None else None,
            "structure": ResponseStructure(kwargs["structure"])
            if kwargs.get("structure") is not None
            else None,
            "criticality_level": kwargs.get("criticality_level"),
            "supportiveness_level": kwargs.get("supportiveness_level"),
            "professional_default": kwargs.get("professional_default"),
        }
        filtered_updates = {field: value for field, value in updates.items() if value is not None}
        if filtered_updates:
            self.styles.update(style, **filtered_updates)
        return self.get_payload()

    def update_policy(self, **kwargs) -> dict[str, Any]:
        owner = self._ensure_owner()
        policy = self._ensure_execution_policy(owner.id)
        updates = {
            "automation_level": ExecutionAutomationLevel(kwargs["automation_level"])
            if kwargs.get("automation_level") is not None
            else None,
            "verification_mode": ExecutionVerificationMode(kwargs["verification_mode"])
            if kwargs.get("verification_mode") is not None
            else None,
            "allow_local_file_read": kwargs.get("allow_local_file_read"),
            "allow_local_file_write": kwargs.get("allow_local_file_write"),
            "allow_web_read": kwargs.get("allow_web_read"),
            "allow_external_fallback": kwargs.get("allow_external_fallback"),
            "allow_external_attachment_metadata": kwargs.get("allow_external_attachment_metadata"),
            "allow_external_attachment_content": kwargs.get("allow_external_attachment_content"),
            "prefer_local_llm": kwargs.get("prefer_local_llm"),
            "default_provider_posture": ProviderPosture(kwargs["default_provider_posture"])
            if kwargs.get("default_provider_posture") is not None
            else None,
            "default_persona_profile": PersonaProfile(kwargs["default_persona_profile"])
            if kwargs.get("default_persona_profile") is not None
            else None,
            "jarvis_address_as": JarvisAddressAs(kwargs["jarvis_address_as"])
            if kwargs.get("jarvis_address_as") is not None
            else None,
            "max_external_attachment_text_chars": kwargs.get("max_external_attachment_text_chars"),
            "max_daily_cost": kwargs.get("max_daily_cost"),
            "max_monthly_cost": kwargs.get("max_monthly_cost"),
        }
        filtered_updates = {
            field: value
            for field, value in updates.items()
            if value is not None or field in {
                "max_external_attachment_text_chars",
                "max_daily_cost",
                "max_monthly_cost",
            }
            and field in kwargs
        }
        if filtered_updates:
            self.policies.update(policy, **filtered_updates)
        return self.get_payload()

    def update_runtime(self, **kwargs) -> dict[str, Any]:
        self.runtime_settings.update_owner_controls(**kwargs)
        return self.get_payload()

    def run_trend_watch(self) -> dict[str, Any]:
        TrendWatchService(self.session, self.base_settings).run_watch(force=True)
        return self.get_payload()

    def retest_creative_asset_lanes(self) -> dict[str, Any]:
        operator = self._creative_asset_operator()
        operator.clear_remembered_lane_blockers(
            lane_keys=(
                operator.IDEOGRAM_BROWSER_LANE,
                operator.OPENAI_IMAGE_API_LANE,
            )
        )
        return self.get_payload()

    def clear_creative_asset_lane_blockers(self) -> dict[str, Any]:
        self._creative_asset_operator().clear_remembered_lane_blockers()
        return self.get_payload()

    def update_obsidian(self, *, vault_path: str | None, export_enabled: bool) -> dict[str, Any]:
        IntegrationConfigService(self.session, self.base_settings).update_obsidian_config(
            vault_path=vault_path,
            export_enabled=export_enabled,
        )
        return self.get_payload()

    def activate_omega_protocol(self) -> dict[str, Any]:
        OmegaProtocolService(self.session, self.base_settings).activate_guarded_profile()
        return self.get_payload()

    def enable_full_access_profile(self) -> dict[str, Any]:
        """Enable broad capabilities without bypassing user confirmation boundaries."""
        owner = self._ensure_owner()
        policy = self._ensure_execution_policy(owner.id)
        snapshot = self.runtime_settings.snapshot()
        self.policies.update(
            policy,
            automation_level=ExecutionAutomationLevel.ASSISTED,
            verification_mode=ExecutionVerificationMode.STRICT,
            allow_local_file_read=True,
            allow_local_file_write=True,
            allow_web_read=True,
            allow_external_fallback=True,
            allow_external_attachment_metadata=True,
            allow_external_attachment_content=False,
            prefer_local_llm=True,
            default_provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
            default_persona_profile=PersonaProfile.JARVIS_MODE,
        )
        self.runtime_settings.update_owner_controls(
            browser_control_enabled=True,
            browser_allow_any_url=False,
            external_actions_enabled=True,
            scheduler_enabled=True,
            oauth_enabled=True,
            autonomy_mode="supervised",
            emergency_stop_enabled=False,
            pause_all_jobs=False,
            login_automation_enabled=False,
            two_factor_automation_enabled=False,
            publish_automation_enabled=False,
            public_upload_enabled=False,
            default_retry_budget=max(3, snapshot.default_retry_budget),
            default_vram_floor_gb=max(6, snapshot.default_vram_floor_gb),
        )
        for plugin in self.plugins.list_all():
            self.plugins.update(
                plugin,
                is_enabled=True,
                permission_policy=PluginPermissionPolicy.ASK,
                requires_confirmation=True,
            )
        OmegaProtocolService(self.session, self.base_settings).activate_guarded_profile()
        return self.get_payload()

    def _build_fast_omega_protocol_payload(
        self,
        *,
        runtime_controls,
        trusted_auto: dict[str, Any],
        obsidian_config,
    ) -> dict[str, Any]:
        omega_note_path, omega_note_written_at = OmegaProtocolService(
            self.session,
            self.base_settings,
        )._existing_note_metadata()
        browser_runtime = trusted_auto.get("browser_runtime") if isinstance(trusted_auto, dict) else None
        if not isinstance(browser_runtime, dict):
            browser_runtime = {
                "profile_dir": "",
                "downloads_dir": "",
                "trace_dir": "",
                "browsers_path": "",
                "browser_executable": None,
                "desktop_shell": None,
                "ready": False,
                "status_reason": "Browser runtime status is unavailable in fast settings mode.",
            }

        accounts = trusted_auto.get("accounts") if isinstance(trusted_auto, dict) else []
        grants = trusted_auto.get("trust_grants") if isinstance(trusted_auto, dict) else []
        youtube_connector = trusted_auto.get("youtube_connector") if isinstance(trusted_auto, dict) else None
        youtube_ready = bool(isinstance(youtube_connector, dict) and youtube_connector.get("ready"))

        activated = all(
            (
                runtime_controls.autonomy_mode == "supervised",
                runtime_controls.browser_control_enabled,
                runtime_controls.external_actions_enabled,
                runtime_controls.scheduler_enabled,
                not runtime_controls.login_automation_enabled,
                not runtime_controls.two_factor_automation_enabled,
            )
        )
        browser_ready = bool(browser_runtime.get("ready"))
        ready_for_browser_workflows = activated and browser_ready
        ready_for_account_login = False
        ready_for_two_factor = False
        ready_for_publish = (
            ready_for_account_login
            and runtime_controls.publish_automation_enabled
            and youtube_ready
        )

        trusted_filesystem_roots: list[str] = [
            str(self.base_settings.app_root.resolve()),
            str(self.base_settings.data_dir.resolve()),
            str(self.base_settings.uploads_dir.resolve()),
            str(self.base_settings.workspace_staging_dir.resolve()),
            str(self.base_settings.browser_profile_dir.resolve()),
        ]
        if obsidian_config.vault_path is not None and obsidian_config.path_exists:
            trusted_filesystem_roots.append(str(obsidian_config.vault_path.resolve()))

        recommended_next_steps: list[str] = []
        if not browser_ready:
            recommended_next_steps.append(
                "Napraw browser runtime, zanim ruszysz z login automation albo web workflowami."
            )
        if not accounts:
            recommended_next_steps.append(
                "Podepnij Trusted Accounts do projektow, zeby trusted auto mial realny scope."
            )
        if accounts and not grants:
            recommended_next_steps.append(
                "Dodaj trust granty do podlaczonych kont, inaczej runy beda blokowane preflightem."
            )
        if not youtube_ready:
            recommended_next_steps.append(
                "YouTube connector nadal nie jest gotowy do realnego publishu."
            )
        if not recommended_next_steps:
            recommended_next_steps.append(
                "Fast settings mode potwierdza gotowy browser-first runtime i trusted scope."
            )

        return {
            "profile_key": "omega_expanded",
            "mode": "fast-settings",
            "activated": activated,
            "ready_for_browser_workflows": ready_for_browser_workflows,
            "ready_for_account_login": ready_for_account_login,
            "ready_for_two_factor": ready_for_two_factor,
            "ready_for_publish": ready_for_publish,
            "web_advisor_fallback_enabled": (
                ready_for_browser_workflows and runtime_controls.external_actions_enabled
            ),
            "trusted_accounts_count": len(accounts) if isinstance(accounts, list) else 0,
            "trust_grants_count": len(grants) if isinstance(grants, list) else 0,
            "runtime_controls": {
                "autonomy_mode": runtime_controls.autonomy_mode,
                "browser_control_enabled": runtime_controls.browser_control_enabled,
                "external_actions_enabled": runtime_controls.external_actions_enabled,
                "scheduler_enabled": runtime_controls.scheduler_enabled,
                "oauth_enabled": runtime_controls.oauth_enabled,
                "emergency_stop_enabled": runtime_controls.emergency_stop_enabled,
                "pause_all_jobs": runtime_controls.pause_all_jobs,
                "login_automation_enabled": runtime_controls.login_automation_enabled,
                "two_factor_automation_enabled": runtime_controls.two_factor_automation_enabled,
                "publish_automation_enabled": runtime_controls.publish_automation_enabled,
                "public_upload_enabled": runtime_controls.public_upload_enabled,
                "default_retry_budget": runtime_controls.default_retry_budget,
                "default_vram_floor_gb": runtime_controls.default_vram_floor_gb,
            },
            "browser_runtime": browser_runtime,
            "trusted_filesystem_roots": trusted_filesystem_roots,
            "runtime_probes": [],
            "allowed_capabilities": [
                "Trusted Accounts i browser-first workflowy w ramach jawnie nadanego scope.",
                "Czytelny runtime control plane dla lokalnych i browserowych akcji.",
            ],
            "blocked_capabilities": [
                "Silent takeover prywatnych sesji poza jawnym trusted scope.",
                "Plaintext sekrety w notatkach albo payloadach UI.",
            ],
            "recommended_next_steps": recommended_next_steps,
            "persona_protocol": [
                "Dzialaj bezpiecznie w trusted scope i raportuj konkretne akcje.",
                "Gdy scope albo connectora brakuje, blokuj run przed wykonaniem.",
            ],
            "note_path": omega_note_path,
            "note_written_at": omega_note_written_at,
        }

    def _build_fast_skill_dashboard_payload(self) -> dict[str, Any]:
        return build_skill_dashboard_payload(fast_mode=True)

    def create_voice(self, **kwargs) -> dict[str, Any]:
        owner = self._ensure_owner()
        is_default = bool(kwargs.get("is_default"))
        if is_default:
            self.voices.clear_default_for_user(owner.id)
        self.voices.create(
            user_id=owner.id,
            provider_id=kwargs.get("provider_id"),
            name=kwargs["name"],
            voice_key=kwargs["voice_key"],
            provider_mode=VoiceProviderMode(kwargs["provider_mode"]),
            style=self._normalize_optional_text(kwargs.get("style")),
            language=self._normalize_optional_text(kwargs.get("language")) or "pl-PL",
            speaking_rate=self._normalize_optional_text(kwargs.get("speaking_rate")),
            supports_realtime=bool(kwargs.get("supports_realtime", False)),
            auto_read_responses=bool(kwargs.get("auto_read_responses", False)),
            is_default=is_default,
            notes=self._normalize_optional_text(kwargs.get("notes")),
        )
        return self.get_payload()

    def update_voice(self, voice_id: int, **kwargs) -> dict[str, Any]:
        owner = self._ensure_owner()
        voice = self.voices.get(voice_id)
        if voice is None or voice.user_id != owner.id:
            raise ValueError(f"Unknown voice_id '{voice_id}'.")
        is_default = kwargs.get("is_default")
        if is_default is True:
            self.voices.clear_default_for_user(owner.id, exclude_voice_profile_id=voice.id)
        updates = {
            "provider_id": kwargs.get("provider_id"),
            "name": kwargs.get("name"),
            "voice_key": kwargs.get("voice_key"),
            "provider_mode": VoiceProviderMode(kwargs["provider_mode"])
            if kwargs.get("provider_mode") is not None
            else None,
            "style": self._normalize_optional_text(kwargs.get("style")) if "style" in kwargs else None,
            "language": self._normalize_optional_text(kwargs.get("language")) if "language" in kwargs else None,
            "speaking_rate": self._normalize_optional_text(kwargs.get("speaking_rate"))
            if "speaking_rate" in kwargs
            else None,
            "supports_realtime": kwargs.get("supports_realtime"),
            "auto_read_responses": kwargs.get("auto_read_responses"),
            "is_default": is_default,
            "notes": self._normalize_optional_text(kwargs.get("notes")) if "notes" in kwargs else None,
        }
        filtered_updates = {field: value for field, value in updates.items() if value is not None}
        if "provider_id" in kwargs and kwargs.get("provider_id") is None:
            filtered_updates["provider_id"] = None
        if "style" in kwargs and updates["style"] is None:
            filtered_updates["style"] = None
        if "language" in kwargs and updates["language"] is None:
            filtered_updates["language"] = "pl-PL"
        if "speaking_rate" in kwargs and "speaking_rate" not in filtered_updates:
            filtered_updates["speaking_rate"] = None
        if "notes" in kwargs and "notes" not in filtered_updates:
            filtered_updates["notes"] = None
        self.voices.update(voice, **filtered_updates)
        return self.get_payload()

    def create_role(self, **kwargs) -> dict[str, Any]:
        is_default = bool(kwargs.get("is_default"))
        if is_default:
            self.roles.clear_default()
        slug = self._build_unique_role_slug(kwargs["name"])
        self.roles.create(
            name=kwargs["name"],
            slug=slug,
            description=self._normalize_optional_text(kwargs.get("description")),
            system_prompt=self._normalize_optional_text(kwargs.get("system_prompt")),
            tone=self._normalize_optional_text(kwargs.get("tone")),
            response_style=self._normalize_optional_text(kwargs.get("response_style")),
            criticality_level=int(kwargs.get("criticality_level", 3)),
            empathy_level=int(kwargs.get("empathy_level", 3)),
            firmness_level=int(kwargs.get("firmness_level", 3)),
            scope=RoleProfileScope(kwargs.get("scope", RoleProfileScope.GLOBAL.value)),
            is_default=is_default,
            is_builtin=False,
            is_archived=bool(kwargs.get("is_archived", False)),
        )
        return self.get_payload()

    def update_role(self, role_id: int, **kwargs) -> dict[str, Any]:
        role = self.roles.get(role_id)
        if role is None:
            raise ValueError(f"Unknown role_id '{role_id}'.")
        is_default = kwargs.get("is_default")
        if is_default is True:
            self.roles.clear_default(exclude_role_id=role.id)
        updates = {
            "name": kwargs.get("name"),
            "description": self._normalize_optional_text(kwargs.get("description")) if "description" in kwargs else None,
            "system_prompt": self._normalize_optional_text(kwargs.get("system_prompt")) if "system_prompt" in kwargs else None,
            "tone": self._normalize_optional_text(kwargs.get("tone")) if "tone" in kwargs else None,
            "response_style": self._normalize_optional_text(kwargs.get("response_style")) if "response_style" in kwargs else None,
            "criticality_level": kwargs.get("criticality_level"),
            "empathy_level": kwargs.get("empathy_level"),
            "firmness_level": kwargs.get("firmness_level"),
            "scope": RoleProfileScope(kwargs["scope"]) if kwargs.get("scope") is not None else None,
            "is_default": is_default,
            "is_archived": kwargs.get("is_archived"),
        }
        if kwargs.get("name"):
            updates["slug"] = self._build_unique_role_slug(kwargs["name"], excluding_role_id=role.id)
        filtered_updates = {field: value for field, value in updates.items() if value is not None}
        for nullable_field in ("description", "system_prompt", "tone", "response_style"):
            if nullable_field in kwargs and nullable_field not in filtered_updates:
                filtered_updates[nullable_field] = None
        self.roles.update(role, **filtered_updates)
        return self.get_payload()

    def update_plugin(self, plugin_key: str, **kwargs) -> dict[str, Any]:
        plugin = self.plugins.get_by_key(plugin_key)
        if plugin is None:
            raise ValueError(f"Unknown plugin_key '{plugin_key}'.")
        updates = {
            "permission_policy": PluginPermissionPolicy(kwargs["permission_policy"])
            if kwargs.get("permission_policy") is not None
            else None,
            "requires_confirmation": kwargs.get("requires_confirmation"),
            "is_enabled": kwargs.get("is_enabled"),
            "config_json": kwargs.get("config_json"),
        }
        filtered_updates = {field: value for field, value in updates.items() if value is not None}
        if "config_json" in kwargs and kwargs.get("config_json") is None:
            filtered_updates["config_json"] = None
        self.plugins.update(plugin, **filtered_updates)
        return self.get_payload()

    def _ensure_owner(self):
        owner = self.users.get_owner()
        if owner is None:
            raise ValueError("Owner user is not configured.")
        return owner

    def _creative_asset_operator(self) -> CreativeAssetOperatorService:
        return CreativeAssetOperatorService(
            self.base_settings,
            account_credentials=AccountCredentialsService(self.session, self.base_settings),
        )

    def _ensure_style_preferences(self, user_id: int):
        style = self.styles.get_for_user(user_id)
        if style is not None:
            return style
        return self.styles.create(
            user_id=user_id,
            response_length=ResponseLengthPreference.MEDIUM,
            tone=ResponseTone.NATURAL,
            structure=ResponseStructure.MIXED,
            criticality_level=3,
            supportiveness_level=3,
            professional_default=True,
        )

    def _ensure_execution_policy(self, user_id: int):
        policy = self.policies.get_for_user(user_id)
        if policy is not None:
            return policy
        return self.policies.create(
            user_id=user_id,
            automation_level=ExecutionAutomationLevel.ASSISTED,
            verification_mode=ExecutionVerificationMode.SMART,
            allow_local_file_read=True,
            allow_local_file_write=False,
            allow_web_read=True,
            allow_external_fallback=True,
            allow_external_attachment_metadata=True,
            allow_external_attachment_content=False,
            prefer_local_llm=True,
            default_provider_posture=ProviderPosture.PREFER_LOCAL,
            default_persona_profile=PersonaProfile.STRATEGIST,
            jarvis_address_as=JarvisAddressAs.NONE,
            max_external_attachment_text_chars=2000,
            max_daily_cost=25.0,
            max_monthly_cost=250.0,
        )

    def _ensure_voice_profiles(self, user_id: int) -> None:
        existing = self.voices.list_for_user(user_id)
        if not existing:
            for blueprint in self.VOICE_PRESET_BLUEPRINTS:
                self.voices.create(user_id=user_id, provider_id=None, **blueprint)
            return

        voice_by_key = {item.voice_key: item for item in existing}
        legacy_seed = next(
            (
                item
                for item in existing
                if item.name == "Polski naturalny"
                and item.voice_key == VoiceSynthesisService.DEFAULT_EDGE_POLISH_VOICE
            ),
            None,
        )

        if legacy_seed is not None and VoiceSynthesisService.DEFAULT_POLISH_VOICE not in voice_by_key:
            self.voices.update(
                legacy_seed,
                name="Polski creator premium",
                voice_key=VoiceSynthesisService.DEFAULT_POLISH_VOICE,
                provider_mode=VoiceProviderMode.REMOTE,
                style="warm-premium",
                language="pl-PL",
                speaking_rate="-12%",
                supports_realtime=False,
                auto_read_responses=True,
                notes="Cieplejszy, bardziej ludzki polski glos do codziennych odpowiedzi Atnosa.",
            )
            voice_by_key.pop(VoiceSynthesisService.DEFAULT_EDGE_POLISH_VOICE, None)
            voice_by_key[legacy_seed.voice_key] = legacy_seed

        for blueprint in self.VOICE_PRESET_BLUEPRINTS:
            voice_key = str(blueprint["voice_key"])
            if voice_key in voice_by_key:
                continue
            create_kwargs = dict(blueprint)
            # Partial unique index: only one is_default=1 per user. Never insert a
            # second default while seeding missing presets (STAB-SET-001).
            if create_kwargs.get("is_default") and any(item.is_default for item in existing):
                create_kwargs["is_default"] = False
            created = self.voices.create(user_id=user_id, provider_id=None, **create_kwargs)
            voice_by_key[voice_key] = created
            existing.append(created)

        if not any(item.is_default for item in self.voices.list_for_user(user_id)):
            default_voice = voice_by_key.get(VoiceSynthesisService.DEFAULT_POLISH_VOICE)
            if default_voice is not None:
                self.voices.clear_default_for_user(user_id, exclude_voice_profile_id=default_voice.id)
                self.voices.update(default_voice, is_default=True)

    @staticmethod
    def _serialize_voice(item) -> dict[str, Any]:
        return {
            "voice_id": item.id,
            "provider_id": item.provider_id,
            "name": item.name,
            "voice_key": item.voice_key,
            "provider_mode": item.provider_mode.value,
            "style": item.style,
            "language": item.language,
            "speaking_rate": item.speaking_rate,
            "supports_realtime": item.supports_realtime,
            "auto_read_responses": item.auto_read_responses,
            "is_default": item.is_default,
            "notes": item.notes,
        }

    @staticmethod
    def _serialize_role(item) -> dict[str, Any]:
        return {
            "role_id": item.id,
            "name": item.name,
            "slug": item.slug,
            "description": item.description,
            "system_prompt": item.system_prompt,
            "tone": item.tone,
            "response_style": item.response_style,
            "criticality_level": item.criticality_level,
            "empathy_level": item.empathy_level,
            "firmness_level": item.firmness_level,
            "scope": item.scope.value,
            "is_default": item.is_default,
            "is_builtin": item.is_builtin,
            "is_archived": item.is_archived,
        }

    @staticmethod
    def _serialize_plugin(item) -> dict[str, Any]:
        return {
            "plugin_id": item.id,
            "key": item.key,
            "name": item.name,
            "summary": item.summary,
            "source": item.source.value,
            "permission_policy": item.permission_policy.value,
            "requires_confirmation": item.requires_confirmation,
            "is_enabled": item.is_enabled,
            "config_json": item.config_json,
        }

    @staticmethod
    def _serialize_decision_log(item) -> dict[str, Any]:
        return {
            "decision_id": item.id,
            "conversation_id": item.conversation_id,
            "project_id": item.project_id,
            "kind": item.kind.value,
            "summary": item.summary,
            "made_at": item.made_at.isoformat(),
            "status": item.status.value,
            "due_at": item.due_at.isoformat() if item.due_at is not None else None,
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at is not None else None,
            "resolution_summary": item.resolution_summary,
            "evidence_message_ids": list(item.evidence_message_ids or []),
        }

    def _build_unique_role_slug(self, name: str, *, excluding_role_id: int | None = None) -> str:
        normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        base_slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "role"
        existing = {
            item.slug
            for item in self.roles.list_active()
            if excluding_role_id is None or item.id != excluding_role_id
        }
        slug = base_slug
        suffix = 2
        while slug in existing:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        return slug

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
