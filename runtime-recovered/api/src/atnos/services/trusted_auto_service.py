from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import json
import struct
from typing import TYPE_CHECKING, Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from atnos.core.config import Settings
    from atnos.plugins.registry import PluginRegistry
    from atnos.providers.registry import ProviderRegistry


TRUST_MODES = {"blocked", "supervised", "trusted_auto"}
JOB_STATUSES = {"queued", "planned", "running", "waiting_for_input", "blocked", "retrying", "interrupted", "done", "failed", "cancelled"}
DEFAULT_ACCOUNT_PROBE_URLS = {
    "airtable": "https://airtable.com/",
    "anthropic": "https://console.anthropic.com/",
    "anthropic-cloud": "https://console.anthropic.com/",
    "artlist": "https://artlist.io/",
    "better_stack": "https://betterstack.com/",
    "betterstack": "https://betterstack.com/",
    "canva": "https://www.canva.com/login",
    "capcut": "https://www.capcut.com/",
    "elevenlabs": "https://elevenlabs.io/",
    "figma": "https://www.figma.com/",
    "github": "https://github.com/login",
    "gmail": "https://mail.google.com/",
    "google": "https://accounts.google.com/",
    "hubspot": "https://app.hubspot.com/login",
    "instagram": "https://www.instagram.com/",
    "int_mail": "https://int.pl/",
    "int-mail": "https://int.pl/",
    "lovable": "https://lovable.dev/",
    "metricool": "https://metricool.com/",
    "onlyfans": "https://onlyfans.com/",
    "openai": "https://platform.openai.com/",
    "openai-cloud": "https://platform.openai.com/",
    "slack": "https://slack.com/signin",
    "stripe": "https://dashboard.stripe.com/login",
    "tiktok": "https://www.tiktok.com/",
    "vercel": "https://vercel.com/login",
    "youtube": "https://studio.youtube.com/",
}


@dataclass(frozen=True, slots=True)
class TrustResolution:
    allowed: bool
    reason: str
    mode: str
    grant_id: int | None


@dataclass(frozen=True, slots=True)
class TrustedContextResolution:
    account_id: int | None
    connector_key: str | None
    trust_grant_id: int | None


class ContentCampaignService:
    """Lazy proxy kept at module level so tests and integrations can patch it."""

    def __init__(self, *args, **kwargs) -> None:
        from atnos.services.content_campaign_service import ContentCampaignService as _Service

        self._impl = _Service(*args, **kwargs)

    def run_youtube_short_campaign(self, *args, **kwargs):
        return self._impl.run_youtube_short_campaign(*args, **kwargs)

    def publish_approved_campaign(self, *args, **kwargs):
        return self._impl.publish_approved_campaign(*args, **kwargs)

    @staticmethod
    def _attachment_kind_for_extension(extension: str):
        from atnos.services.content_campaign_service import ContentCampaignService as _Service

        return _Service._attachment_kind_for_extension(extension)


class SyntheticCreatorWorkflowService:
    """Lazy proxy kept at module level so tests and integrations can patch it."""

    def __init__(self, *args, **kwargs) -> None:
        from atnos.services.synthetic_creator_workflow_service import (
            SyntheticCreatorWorkflowService as _Service,
        )

        self._impl = _Service(*args, **kwargs)

    def generate_preview(self, *args, **kwargs):
        return self._impl.generate_preview(*args, **kwargs)

    def generate_preview_batch(self, *args, **kwargs):
        return self._impl.generate_preview_batch(*args, **kwargs)

    def publish_approved_batch(self, *args, **kwargs):
        return self._impl.publish_approved_batch(*args, **kwargs)


class PublishAnalyticsFollowupService:
    """Lazy proxy kept at module level so tests and integrations can patch it."""

    def __init__(self, *args, **kwargs) -> None:
        from atnos.services.publish_analytics_followup_service import (
            PublishAnalyticsFollowupService as _Service,
        )

        self._impl = _Service(*args, **kwargs)

    def run_followup(self, *args, **kwargs):
        return self._impl.run_followup(*args, **kwargs)


class HardwareGuardService:
    """Lazy proxy kept at module level so tests and integrations can patch it."""

    def __init__(self, *args, **kwargs) -> None:
        from atnos.services.hardware_guard_service import HardwareGuardService as _Service

        self._impl = _Service(*args, **kwargs)

    def capture(self, *args, **kwargs):
        return self._impl.capture(*args, **kwargs)

    def latest(self, *args, **kwargs):
        return self._impl.latest(*args, **kwargs)

    def release_gpu_pressure(self, *args, **kwargs):
        return self._impl.release_gpu_pressure(*args, **kwargs)


class TrustedAutoService:
    def __init__(
        self,
        session: "Session",
        settings: "Settings",
        provider_registry: "ProviderRegistry | None" = None,
        plugin_registry: "PluginRegistry | None" = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry or self._build_provider_registry()
        self.plugin_registry = plugin_registry or self._build_plugin_registry()
        self._runtime_settings = None
        self._account_credentials = None
        self._accounts = None
        self._creator_ops = None
        self._projects = None
        self._trust_grants = None
        self._credential_profiles = None
        self._scheduler_jobs = None
        self._execution_runs = None
        self._messages = None
        self._attachments = None
        self._pipeline_presets = None
        self._hardware_guard = None
        self._browser_runtime = None
        self._browser_recipe_runner = None
        self._vault = None
        self._analytics_learning = None
        self._synthetic_project_strategy = None

    def _build_provider_registry(self):
        from atnos.providers.registry import build_default_provider_registry, build_stub_provider_registry

        return (
            build_stub_provider_registry()
            if self.settings.app_env == "test"
            else build_default_provider_registry(app_root=self.settings.app_root, settings=self.settings)
        )

    @staticmethod
    def _build_plugin_registry():
        from atnos.plugins.registry import build_default_plugin_registry

        return build_default_plugin_registry()

    def _cached(self, attr_name: str, factory):
        value = getattr(self, attr_name)
        if value is None:
            value = factory()
            setattr(self, attr_name, value)
        return value

    @property
    def runtime_settings(self):
        def factory():
            from atnos.services.runtime_settings_service import RuntimeSettingsService

            return RuntimeSettingsService(self.session, self.settings)

        return self._cached("_runtime_settings", factory)

    @property
    def account_credentials(self):
        def factory():
            from atnos.services.account_credentials_service import AccountCredentialsService

            return AccountCredentialsService(self.session, self.settings)

        return self._cached("_account_credentials", factory)

    @property
    def accounts(self):
        def factory():
            from atnos.repositories.account_credential_repository import AccountCredentialRepository

            return AccountCredentialRepository(self.session)

        return self._cached("_accounts", factory)

    @property
    def creator_ops(self):
        def factory():
            from atnos.services.creator_ops_service import CreatorOpsService

            return CreatorOpsService(self.session, self.settings)

        return self._cached("_creator_ops", factory)

    @property
    def projects(self):
        def factory():
            from atnos.repositories.project_repository import ProjectRepository

            return ProjectRepository(self.session)

        return self._cached("_projects", factory)

    @property
    def trust_grants(self):
        def factory():
            from atnos.repositories.trust_grant_repository import TrustGrantRepository

            return TrustGrantRepository(self.session)

        return self._cached("_trust_grants", factory)

    @property
    def credential_profiles(self):
        def factory():
            from atnos.repositories.credential_profile_repository import CredentialProfileRepository

            return CredentialProfileRepository(self.session)

        return self._cached("_credential_profiles", factory)

    @property
    def scheduler_jobs(self):
        def factory():
            from atnos.repositories.scheduler_job_repository import SchedulerJobRepository

            return SchedulerJobRepository(self.session)

        return self._cached("_scheduler_jobs", factory)

    @property
    def execution_runs(self):
        def factory():
            from atnos.repositories.execution_run_repository import ExecutionRunRepository

            return ExecutionRunRepository(self.session)

        return self._cached("_execution_runs", factory)

    @property
    def messages(self):
        def factory():
            from atnos.repositories.message_repository import MessageRepository

            return MessageRepository(self.session)

        return self._cached("_messages", factory)

    @property
    def attachments(self):
        def factory():
            from atnos.repositories.attachment_repository import AttachmentRepository

            return AttachmentRepository(self.session)

        return self._cached("_attachments", factory)

    @property
    def pipeline_presets(self):
        def factory():
            from atnos.repositories.media_pipeline_preset_repository import MediaPipelinePresetRepository

            return MediaPipelinePresetRepository(self.session)

        return self._cached("_pipeline_presets", factory)

    @property
    def hardware_guard(self):
        def factory():
            return HardwareGuardService(self.session, self.settings)

        return self._cached("_hardware_guard", factory)

    @property
    def browser_runtime(self):
        def factory():
            from atnos.services.browser_runtime_service import BrowserRuntimeService

            return BrowserRuntimeService(self.settings)

        return self._cached("_browser_runtime", factory)

    @property
    def browser_recipe_runner(self):
        def factory():
            from atnos.services.browser_recipe_runner_service import BrowserRecipeRunnerService

            return BrowserRecipeRunnerService(self.settings)

        return self._cached("_browser_recipe_runner", factory)

    @property
    def vault(self):
        def factory():
            from atnos.services.secret_vault_service import SecretVaultService

            return SecretVaultService(self.settings)

        return self._cached("_vault", factory)

    @property
    def analytics_learning(self):
        def factory():
            from atnos.services.synthetic_analytics_learning_service import (
                SyntheticAnalyticsLearningService,
            )

            return SyntheticAnalyticsLearningService(self.session)

        return self._cached("_analytics_learning", factory)

    @property
    def synthetic_project_strategy(self):
        def factory():
            from atnos.services.synthetic_project_strategy_service import SyntheticProjectStrategyService

            return SyntheticProjectStrategyService()

        return self._cached("_synthetic_project_strategy", factory)

    def list_accounts(self) -> list[dict[str, Any]]:
        profile_map = {
            item.account_id: item
            for item in self.credential_profiles.list_all()
        }
        trust_by_account: dict[int, list[dict[str, Any]]] = {}
        for grant in self.trust_grants.list_all():
            trust_by_account.setdefault(grant.account_id, []).append(self._serialize_trust_grant(grant))
        items: list[dict[str, Any]] = []
        for account in self.account_credentials.list_accounts():
            profile = profile_map.get(account.id)
            items.append(
                {
                    "account_id": account.id,
                    "label": account.label,
                    "provider_key": account.provider_key,
                    "account_identifier": account.account_identifier,
                    "secret_type": account.secret_type,
                    "status": account.status,
                    "has_secret": account.has_secret,
                    "masked_secret_state": account.masked_secret_state,
                    "linked_project_id": account.linked_project_id,
                    "linked_integration_key": account.linked_integration_key,
                    "allowed_capabilities": list(account.allowed_capabilities),
                    "credential_profile": self._serialize_credential_profile(profile),
                    "trust_grants": trust_by_account.get(account.id, []),
                }
            )
        return items

    def probe_account_feedback(self, *, timeout_seconds: float = 12.0) -> list[dict[str, Any]]:
        accounts = self.list_accounts()
        timeout = max(1.0, min(float(timeout_seconds), 30.0))
        checked_at = self._now().isoformat()
        results: list[dict[str, Any]] = []

        for index, account in enumerate(accounts, start=1):
            profile = account.get("credential_profile")
            connector_mapping_json = (
                dict(profile.get("connector_mapping_json") or {})
                if isinstance(profile, dict)
                else {}
            )
            primary_url = self._resolve_account_probe_url(
                account=account,
                connector_mapping_json=connector_mapping_json,
            )
            feedback = {
                "sequence": index,
                "account_id": account["account_id"],
                "label": account["label"],
                "provider_key": account["provider_key"],
                "account_identifier": account["account_identifier"],
                "url": primary_url,
                "has_secret": bool(account["has_secret"]),
                "grant_count": len(account.get("trust_grants") or []),
                "checked_at": checked_at,
            }
            if primary_url is None:
                results.append(
                    {
                        **feedback,
                        "status": "config_missing",
                        "http_status": None,
                        "reason": "Brak URL-a strony do sprawdzenia. Ustaw primary_url albo login_url w connector mapping.",
                    }
                )
                continue
            results.append(
                {
                    **feedback,
                    **self._probe_feedback_url(primary_url, timeout_seconds=timeout),
                }
            )
        return results

    def upsert_account(
        self,
        *,
        account_id: int | None,
        label: str,
        provider_key: str,
        account_identifier: str,
        secret_type: str,
        secret_value: str | None,
        linked_project_id: int | None,
        linked_integration_key: str | None,
        notes: str | None,
        allowed_capabilities: list[str],
        login_identifier: str | None,
        password_value: str | None,
        totp_value: str | None,
        backup_codes_value: str | None,
        recovery_email: str | None,
        recovery_email_secret_value: str | None,
        connector_mapping_json: dict[str, object] | None,
    ) -> dict[str, Any]:
        if account_id is None:
            view = self.account_credentials.create_account(
                label=label,
                provider_key=provider_key,
                account_identifier=account_identifier,
                secret_type=secret_type,
                secret_value=secret_value,
                risk_level="high",
                allowed_capabilities=allowed_capabilities,
                linked_integration_key=linked_integration_key,
                linked_project_id=linked_project_id,
                notes=notes,
            )
            account_id = view.id
        else:
            view = self.account_credentials.update_account(
                account_id,
                label=label,
                provider_key=provider_key,
                account_identifier=account_identifier,
                secret_type=secret_type,
                linked_integration_key=linked_integration_key,
                linked_project_id=linked_project_id,
                notes=notes,
                allowed_capabilities=allowed_capabilities,
            )
            if view is None:
                raise ValueError("Account was not found.")
            if secret_value:
                view = self.account_credentials.rotate_secret(account_id, secret_value=secret_value)
                if view is None:
                    raise ValueError("Account secret could not be rotated.")

        profile = self.credential_profiles.get_by_account_id(account_id)
        profile_payload = self._build_credential_profile_payload(
            login_identifier=login_identifier,
            password_value=password_value,
            totp_value=totp_value,
            backup_codes_value=backup_codes_value,
            recovery_email=recovery_email,
            recovery_email_secret_value=recovery_email_secret_value,
            existing=profile,
            connector_mapping_json=connector_mapping_json or {},
        )
        if profile is None:
            self.credential_profiles.create(account_id=account_id, **profile_payload)
        else:
            for key, value in profile_payload.items():
                setattr(profile, key, value)
            self.credential_profiles.save(profile)
        return next(item for item in self.list_accounts() if item["account_id"] == account_id)

    def list_trust_grants(self) -> list[dict[str, Any]]:
        return [self._serialize_trust_grant(item) for item in self.trust_grants.list_all()]

    def upsert_trust_grant(
        self,
        *,
        trust_grant_id: int | None,
        project_id: int,
        account_id: int,
        connector_key: str,
        mode: str,
        domain_allowlist: list[str],
        action_families: list[str],
        file_write_scope: list[str],
        login_allowed: bool,
        two_factor_allowed: bool,
        publish_allowed: bool,
        browser_automation_allowed: bool,
        connector_write_allowed: bool,
        local_render_allowed: bool,
        max_retries: int,
        cooldown_minutes: int,
        is_enabled: bool,
        notes: str | None,
    ) -> dict[str, Any]:
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in TRUST_MODES:
            raise ValueError("Unsupported trust grant mode.")
        item = self.trust_grants.get_by_id(trust_grant_id) if trust_grant_id is not None else None
        if item is None:
            item = self.trust_grants.get_by_scope(
                project_id=project_id,
                account_id=account_id,
                connector_key=connector_key,
            )
        payload = {
            "project_id": project_id,
            "account_id": account_id,
            "connector_key": connector_key.strip(),
            "mode": normalized_mode,
            "domain_allowlist_json": self._normalize_string_list(domain_allowlist),
            "action_families_json": self._normalize_string_list(action_families),
            "file_write_scope_json": self._normalize_string_list(file_write_scope),
            "login_allowed": bool(login_allowed),
            "two_factor_allowed": bool(two_factor_allowed),
            "publish_allowed": bool(publish_allowed),
            "browser_automation_allowed": bool(browser_automation_allowed),
            "connector_write_allowed": bool(connector_write_allowed),
            "local_render_allowed": bool(local_render_allowed),
            "max_retries": max(0, int(max_retries)),
            "cooldown_minutes": max(0, int(cooldown_minutes)),
            "is_enabled": bool(is_enabled),
            "notes": self._normalize_optional_text(notes),
        }
        if item is None:
            item = self.trust_grants.create(**payload)
        else:
            for key, value in payload.items():
                setattr(item, key, value)
            self.trust_grants.save(item)
        return self._serialize_trust_grant(item)

    def list_scheduler_jobs(self) -> list[dict[str, Any]]:
        return [self._serialize_scheduler_job(item) for item in self.scheduler_jobs.list_all()]

    def update_scheduler_controls(
        self,
        *,
        autonomy_mode: str | None = None,
        scheduler_enabled: bool | None = None,
        emergency_stop_enabled: bool | None = None,
        pause_all_jobs: bool | None = None,
        login_automation_enabled: bool | None = None,
        two_factor_automation_enabled: bool | None = None,
        publish_automation_enabled: bool | None = None,
        default_retry_budget: int | None = None,
        default_vram_floor_gb: int | None = None,
    ) -> dict[str, Any]:
        snapshot = self.runtime_settings.update_owner_controls(
            autonomy_mode=autonomy_mode,
            scheduler_enabled=scheduler_enabled,
            emergency_stop_enabled=emergency_stop_enabled,
            pause_all_jobs=pause_all_jobs,
            login_automation_enabled=login_automation_enabled,
            two_factor_automation_enabled=two_factor_automation_enabled,
            publish_automation_enabled=publish_automation_enabled,
            default_retry_budget=default_retry_budget,
            default_vram_floor_gb=default_vram_floor_gb,
        )
        return {
            "autonomy_mode": snapshot.autonomy_mode,
            "scheduler_enabled": snapshot.scheduler_enabled,
            "emergency_stop_enabled": snapshot.emergency_stop_enabled,
            "pause_all_jobs": snapshot.pause_all_jobs,
            "login_automation_enabled": snapshot.login_automation_enabled,
            "two_factor_automation_enabled": snapshot.two_factor_automation_enabled,
            "publish_automation_enabled": snapshot.publish_automation_enabled,
            "default_retry_budget": snapshot.default_retry_budget,
            "default_vram_floor_gb": snapshot.default_vram_floor_gb,
        }

    def upsert_scheduler_job(
        self,
        *,
        job_id: int | None,
        project_id: int,
        job_type: str,
        trigger_mode: str,
        connector_key: str | None,
        platform_key: str | None,
        scheduled_for: datetime | None,
        cron_expression: str | None,
        payload_json: dict[str, object],
        retry_budget: int | None,
        requires_trusted_auto: bool,
        cooldown_key: str | None,
    ) -> dict[str, Any]:
        normalized_job_type = self._normalize_required_text(job_type, "job_type")
        item = self.scheduler_jobs.get_by_id(job_id) if job_id is not None else None
        snapshot = self.runtime_settings.snapshot()
        payload = {
            "project_id": project_id,
            "job_type": normalized_job_type,
            "trigger_mode": self._normalize_required_text(trigger_mode, "trigger_mode"),
            "status": "queued" if item is None else item.status,
            "connector_key": self._normalize_optional_text(connector_key),
            "platform_key": self._normalize_optional_text(platform_key),
            "scheduled_for": scheduled_for,
            "cron_expression": self._normalize_optional_text(cron_expression),
            "payload_json": payload_json or {},
            "retry_budget": snapshot.default_retry_budget if retry_budget is None else max(0, int(retry_budget)),
            "requires_trusted_auto": bool(requires_trusted_auto),
            "cooldown_key": self._normalize_optional_text(cooldown_key),
        }
        if item is None:
            item = self.scheduler_jobs.create(
                retry_count=0,
                last_error=None,
                last_run_at=None,
                next_run_at=scheduled_for,
                locked_at=None,
                cancelled_at=None,
                **payload,
            )
        else:
            for key, value in payload.items():
                setattr(item, key, value)
            item.next_run_at = scheduled_for
            self.scheduler_jobs.save(item)
        return self._serialize_scheduler_job(item)

    def list_media_pipeline_presets(self) -> list[dict[str, Any]]:
        return [self._serialize_media_preset(item) for item in self.pipeline_presets.list_all()]

    def upsert_media_pipeline_preset(
        self,
        *,
        preset_id: int | None,
        project_id: int | None,
        name: str,
        target_platform: str,
        tts_engine: str,
        image_provider: str,
        video_renderer: str,
        subtitle_engine: str,
        prompt_style: str | None,
        target_duration_sec: int,
        vram_floor_gb: int,
        publish_mode: str,
        cooldown_hours: int,
        steps_json: dict[str, object],
        is_default: bool,
    ) -> dict[str, Any]:
        item = self.pipeline_presets.get_by_id(preset_id) if preset_id is not None else None
        payload = {
            "project_id": project_id,
            "name": self._normalize_required_text(name, "name"),
            "target_platform": self._normalize_required_text(target_platform, "target_platform"),
            "tts_engine": self._normalize_required_text(tts_engine, "tts_engine"),
            "image_provider": self._normalize_required_text(image_provider, "image_provider"),
            "video_renderer": self._normalize_required_text(video_renderer, "video_renderer"),
            "subtitle_engine": self._normalize_required_text(subtitle_engine, "subtitle_engine"),
            "prompt_style": self._normalize_optional_text(prompt_style),
            "target_duration_sec": max(1, int(target_duration_sec)),
            "vram_floor_gb": max(1, int(vram_floor_gb)),
            "publish_mode": self._normalize_required_text(publish_mode, "publish_mode"),
            "cooldown_hours": max(0, int(cooldown_hours)),
            "steps_json": steps_json or {},
            "is_default": bool(is_default),
        }
        if item is None:
            item = self.pipeline_presets.create(**payload)
        else:
            for key, value in payload.items():
                setattr(item, key, value)
            self.pipeline_presets.save(item)
        return self._serialize_media_preset(item)

    def run_job_now(
        self,
        *,
        project_id: int,
        job_type: str,
        goal: str,
        connector_key: str | None,
        platform_key: str | None,
        account_id: int | None,
        trust_grant_id: int | None,
        payload_json: dict[str, object],
        requires_trusted_auto: bool,
        execute_now: bool = True,
    ) -> dict[str, Any]:
        resolved_context = self._resolve_trusted_context(
            project_id=project_id,
            connector_key=connector_key,
            account_id=account_id,
            trust_grant_id=trust_grant_id,
            requires_trusted_auto=requires_trusted_auto,
        )
        job = self.scheduler_jobs.create(
            project_id=project_id,
            job_type=self._normalize_required_text(job_type, "job_type"),
            trigger_mode="manual",
            status="queued",
            connector_key=resolved_context.connector_key,
            platform_key=self._normalize_optional_text(platform_key),
            scheduled_for=None,
            cron_expression=None,
            payload_json={
                **payload_json,
                "goal": goal,
                "account_id": resolved_context.account_id,
                "trust_grant_id": resolved_context.trust_grant_id,
            },
            retry_count=0,
            retry_budget=self.runtime_settings.snapshot().default_retry_budget,
            requires_trusted_auto=requires_trusted_auto,
            cooldown_key=self._normalize_optional_text(platform_key) or resolved_context.connector_key,
            last_error=None,
            last_run_at=None,
            next_run_at=None,
            locked_at=None,
            cancelled_at=None,
        )
        self.session.commit()
        run = self.process_scheduler_job(job.id) if execute_now else None
        return {
            "job": self._serialize_scheduler_job(job),
            "run": self._serialize_execution_run(run) if run is not None else None,
        }

    def cancel_job(self, job_id: int) -> dict[str, Any]:
        item = self.scheduler_jobs.get_by_id(job_id)
        if item is None:
            raise ValueError("Scheduler job was not found.")
        item.status = "cancelled"
        item.cancelled_at = self._now()
        self.scheduler_jobs.save(item)
        return self._serialize_scheduler_job(item)

    def retry_job(self, job_id: int) -> dict[str, Any]:
        item = self.scheduler_jobs.get_by_id(job_id)
        if item is None:
            raise ValueError("Scheduler job was not found.")
        item.status = "retrying"
        item.next_run_at = self._now()
        item.last_error = None
        self.scheduler_jobs.save(item)
        return self._serialize_scheduler_job(item)

    def process_due_jobs(self) -> list[dict[str, Any]]:
        controls = self.runtime_settings.snapshot()
        if not controls.scheduler_enabled or controls.emergency_stop_enabled or controls.pause_all_jobs:
            return []
        results: list[dict[str, Any]] = []
        for item in self.scheduler_jobs.list_due(now=self._now()):
            run = self.process_scheduler_job(item.id)
            if run is not None:
                results.append(self._serialize_execution_run(run))
        return results

    def recover_interrupted_runs(self, *, reason: str = "Interrupted by runtime restart.") -> dict[str, int]:
        now = self._now()
        recovered_jobs = 0
        requeued_jobs = 0
        recovered_runs = 0
        recovered_steps = 0
        retried_job_ids: set[int] = set()

        for job in self.scheduler_jobs.list_running():
            should_retry = job.retry_count < job.retry_budget
            if should_retry:
                job.retry_count += 1
                job.status = "retrying"
                job.next_run_at = now
                retried_job_ids.add(job.id)
                requeued_jobs += 1
            else:
                job.status = "failed"
                job.next_run_at = None
            job.locked_at = None
            job.last_error = reason
            self.scheduler_jobs.save(job)
            recovered_jobs += 1

        for run in self.execution_runs.list_running_runs():
            retry_scheduled = (
                run.scheduler_job_id is not None
                and run.scheduler_job_id in retried_job_ids
            )
            run.status = "interrupted"
            run.error_message = reason
            run.output_json = {
                **dict(run.output_json or {}),
                "recovery": {
                    "status": "interrupted_by_runtime_restart",
                    "retry_scheduled": retry_scheduled,
                    "reason": reason,
                    "recovered_at": now.isoformat(),
                },
            }
            run.finished_at = now
            self.execution_runs.save_run(run)
            recovered_runs += 1
            self._append_campaign_background_status_message(
                run=run,
                job=self.scheduler_jobs.get_by_id(run.scheduler_job_id) if run.scheduler_job_id is not None else None,
                final_status="interrupted",
            )

            for step in self.execution_runs.list_steps(run.id):
                if step.status != "running":
                    continue
                step.status = "interrupted"
                step.error_message = reason
                step.finished_at = now
                self.execution_runs.save_step(step)
                recovered_steps += 1

        if recovered_jobs or recovered_runs or recovered_steps:
            self.session.commit()

        return {
            "recovered_jobs": recovered_jobs,
            "requeued_jobs": requeued_jobs,
            "recovered_runs": recovered_runs,
            "recovered_steps": recovered_steps,
        }

    def process_scheduler_job(self, job_id: int):
        controls = self.runtime_settings.snapshot()
        job = self.scheduler_jobs.get_by_id(job_id)
        if job is None:
            raise ValueError("Scheduler job was not found.")
        if job.status in {"cancelled", "done", "running"}:
            return None
        if controls.emergency_stop_enabled or controls.pause_all_jobs:
            job.status = "blocked"
            job.last_error = "Runtime is paused by kill switch."
            self.scheduler_jobs.save(job)
            return None

        payload = dict(job.payload_json or {})
        goal = str(payload.get("goal") or f"{job.job_type} for project {job.project_id}")
        account_id = payload.get("account_id") if isinstance(payload.get("account_id"), int) else None
        trust_grant_id = payload.get("trust_grant_id") if isinstance(payload.get("trust_grant_id"), int) else None
        try:
            resolved_context = self._resolve_trusted_context(
                project_id=job.project_id,
                connector_key=job.connector_key,
                account_id=account_id,
                trust_grant_id=trust_grant_id,
                requires_trusted_auto=job.requires_trusted_auto,
            )
        except RuntimeError as exc:
            reason = str(exc)
            job.status = "blocked"
            job.last_error = reason
            self.scheduler_jobs.save(job)
            run = self._create_blocked_run(
                job=job,
                goal=goal,
                account_id=account_id,
                trust=TrustResolution(False, reason, controls.autonomy_mode, trust_grant_id),
                reason=reason,
            )
            self._append_campaign_background_status_message(
                run=run,
                job=job,
                final_status="blocked",
            )
            self.session.commit()
            return run
        account_id = resolved_context.account_id
        trust_grant_id = resolved_context.trust_grant_id
        if job.connector_key != resolved_context.connector_key:
            job.connector_key = resolved_context.connector_key
        if payload.get("account_id") != account_id or payload.get("trust_grant_id") != trust_grant_id:
            payload["account_id"] = account_id
            payload["trust_grant_id"] = trust_grant_id
            job.payload_json = payload
        trust = self._resolve_trust(
            project_id=job.project_id,
            account_id=account_id,
            trust_grant_id=trust_grant_id,
            connector_key=resolved_context.connector_key,
            requires_trusted_auto=job.requires_trusted_auto,
        )
        if not trust.allowed:
            job.status = "blocked"
            job.last_error = trust.reason
            self.scheduler_jobs.save(job)
            return self._create_blocked_run(job=job, goal=goal, account_id=account_id, trust=trust, reason=trust.reason)

        job.status = "running"
        job.locked_at = self._now()
        job.last_run_at = self._now()
        self.scheduler_jobs.save(job)

        run = self.execution_runs.create_run(
            project_id=job.project_id,
            scheduler_job_id=job.id,
            account_id=account_id,
            trust_grant_id=trust.grant_id,
            run_type=job.job_type,
            status="running",
            goal=goal,
            connector_key=job.connector_key,
            platform_key=job.platform_key,
            risk_level="high" if job.requires_trusted_auto else "medium",
            input_json=payload,
            output_json={},
            artifacts_json={},
            error_message=None,
            started_at=self._now(),
            finished_at=None,
        )
        self.session.commit()

        try:
            self._execute_job(run=run, job=job, controls=controls)
            domain_result = run.output_json if isinstance(run.output_json, dict) else {}
            domain_status = str(domain_result.get("status") or "")
            unsuccessful = domain_status in {"runtime_not_ready", "blocked", "requires_setup", "requires_data", "failed", "FAILED_WITH_REASON"}
            followup_job = None if unsuccessful else self._schedule_analytics_followup_if_needed(run=run, controls=controls)
            if followup_job is not None:
                run.output_json = {**dict(run.output_json or {}), "analytics_followup_job": followup_job}
            awaiting_approval = (
                isinstance(run.output_json, dict)
                and str(run.output_json.get("status") or "") == "awaiting_approval"
            )
            final_status = ("failed" if domain_status in {"failed", "FAILED_WITH_REASON"} else "blocked") if unsuccessful else "waiting_for_input" if awaiting_approval else "done"
            run.status = final_status
            if unsuccessful:
                publish_result = domain_result.get("publish_result")
                reason = str((publish_result.get("message") if isinstance(publish_result, dict) else None) or domain_status)
                run.error_message = reason
                job.last_error = reason
            run.finished_at = self._now()
            job.status = final_status
            job.locked_at = None
            job.next_run_at = None
            self.execution_runs.save_run(run)
            self.scheduler_jobs.save(job)
            self.session.commit()
        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = self._now()
            self.execution_runs.save_run(run)
            job.retry_count += 1
            job.locked_at = None
            job.last_error = str(exc)
            if job.retry_count <= job.retry_budget:
                job.status = "retrying"
                job.next_run_at = self._now() + timedelta(minutes=5)
            else:
                job.status = "failed"
            self.scheduler_jobs.save(job)
            if job.status == "failed":
                self._append_campaign_background_status_message(
                    run=run,
                    job=job,
                    final_status="failed",
                )
            self.session.commit()
        return run

    def _append_campaign_background_status_message(
        self,
        *,
        run,
        job,
        final_status: str,
    ) -> None:
        from atnos.db.models.enums import AttachmentSource, AttachmentStatus, MessageRole, MessageStatus

        if str(getattr(run, "run_type", "")).strip().lower() != "campaign":
            return
        conversation_id = self._extract_conversation_id_for_run(run=run, job=job)
        if conversation_id is None:
            return

        blocker = self._resolve_campaign_blocker(run=run, job=job)
        next_step = self._resolve_campaign_next_step(
            blocker=blocker,
            run=run,
            job=job,
            final_status=final_status,
        )
        current_node = self._resolve_run_current_node(run)
        attachment_payloads = self._collect_generated_attachment_payloads(
            dict(getattr(run, "artifacts_json", {}) or {}),
            dict(getattr(run, "output_json", {}) or {}),
        )
        quality_profile = "standard"
        if isinstance(getattr(run, "input_json", None), dict):
            quality_profile = str(run.input_json.get("quality_profile") or "standard")

        content = (
            "Workflow shorta zostal przerwany w tle.\n"
            if final_status == "interrupted"
            else "Workflow shorta zatrzymal sie w tle.\n"
        )
        content += f"Blokada: {blocker}\nNastepny krok: {next_step}"

        message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=content,
            metadata_json={
                "triggered_by_run_id": getattr(run, "id", None),
                "creative_video_operator": {
                    "workflow_key": "content_campaign",
                    "platform_hint": getattr(run, "platform_key", None) or "youtube",
                    "quality_profile": quality_profile,
                    "workflow_state": {
                        "current_node": current_node,
                        "status": final_status,
                    },
                    "blocked": True,
                    "blocker": blocker,
                    "approval_required": False,
                    "artifact_count": len(attachment_payloads),
                    "next_step": next_step,
                    "scheduler_job_id": getattr(run, "scheduler_job_id", None),
                    "execution_run_id": getattr(run, "id", None),
                    "job_status": getattr(job, "status", None),
                },
            },
        )
        for artifact in attachment_payloads:
            self.attachments.create(
                filename=str(artifact["filename"]),
                storage_path=str(artifact["storage_path"]),
                kind=ContentCampaignService._attachment_kind_for_extension(str(artifact["file_extension"])),
                conversation_id=conversation_id,
                message_id=message.id,
                source=AttachmentSource.GENERATED,
                status=AttachmentStatus.AVAILABLE,
                media_type=str(artifact["media_type"]),
                file_extension=str(artifact["file_extension"]),
                size_bytes=int(artifact["size_bytes"]),
                extracted_text=None,
                metadata_json={"generated_by": "background-campaign", "run_id": getattr(run, "id", None)},
            )

    @staticmethod
    def _extract_conversation_id_for_run(*, run, job) -> int | None:
        for payload in (
            getattr(run, "input_json", None),
            getattr(job, "payload_json", None),
        ):
            if isinstance(payload, dict) and isinstance(payload.get("conversation_id"), int):
                return int(payload["conversation_id"])
        return None

    def _resolve_campaign_blocker(self, *, run, job) -> str:
        hardware_snapshot = dict(getattr(run, "artifacts_json", {}) or {}).get("hardware_snapshot")
        if isinstance(hardware_snapshot, dict):
            notes = str(hardware_snapshot.get("notes") or "").strip()
            if notes:
                return notes
        for candidate in (
            getattr(run, "error_message", None),
            getattr(job, "last_error", None),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return "Workflow zatrzymal sie bez dodatkowej diagnozy z runtime."

    def _resolve_campaign_next_step(
        self,
        *,
        blocker: str,
        run,
        job,
        final_status: str,
    ) -> str:
        lowered = blocker.casefold()
        if "free vram below" in lowered or "vram" in lowered or "gpu" in lowered:
            gpu_release = dict(getattr(run, "artifacts_json", {}) or {}).get("gpu_release")
            if isinstance(gpu_release, dict):
                release_status = str(gpu_release.get("status") or "").strip()
                stopped_models = gpu_release.get("stopped_models")
                if release_status in {"success", "partial"} and isinstance(stopped_models, list) and stopped_models:
                    return (
                        "Zwolnij GPU: Atnos probowal to zrobic przez zatrzymanie aktywnych modeli Ollama, "
                        "ale VRAM nadal jest ponizej progu. Zamknij ComfyUI/generatory obrazu albo obniz tryb jakosci na `Szybciej`, potem uruchom short ponownie."
                    )
                return (
                    "Zwolnij GPU: Atnos probowal automatycznie, ale to nie wystarczylo. "
                    "Zamknij ComfyUI/Ollama/gry albo przelacz rozmowe na `Szybciej`, potem uruchom short ponownie."
                )
            return "Zwolnij GPU albo przelacz rozmowe na `Szybciej`, potem uruchom short ponownie."
        if "runtime is paused" in lowered or "scheduler" in lowered or "emergency stop" in lowered:
            return "Wznow scheduler i runtime w Ustawieniach, potem uruchom short ponownie."
        if "connect an account" in lowered or "trust grant" in lowered or "oauth" in lowered:
            return "Skonfiguruj konto i grant trusted automation w Ustawieniach, potem uruchom short ponownie."
        if final_status == "interrupted":
            return "Uruchom short ponownie po restarcie runtime i sprawdz, czy workflow rusza od poczatku."
        return "Sprawdz blocker, popraw warunki runtime i uruchom short ponownie."

    def _resolve_run_current_node(self, run) -> str:
        steps = self.execution_runs.list_steps(run.id)
        if steps:
            latest = steps[-1]
            step_type = str(getattr(latest, "step_type", "") or "").strip()
            if step_type:
                return step_type
            step_name = str(getattr(latest, "step_name", "") or "").strip()
            if step_name:
                return step_name.replace(" ", "_").lower()
        return "graph.workflow"

    def _collect_generated_attachment_payloads(self, *payloads: dict[str, Any]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        def _walk(value: Any) -> None:
            if isinstance(value, dict):
                if self._is_attachment_descriptor(value):
                    storage_path = str(value["storage_path"])
                    if storage_path not in seen_paths:
                        seen_paths.add(storage_path)
                        collected.append(
                            {
                                "filename": str(value["filename"]),
                                "storage_path": storage_path,
                                "media_type": str(value["media_type"]),
                                "file_extension": str(value["file_extension"]),
                                "size_bytes": int(value["size_bytes"]),
                            }
                        )
                    return
                for item in value.values():
                    _walk(item)
                return
            if isinstance(value, list):
                for item in value:
                    _walk(item)

        for payload in payloads:
            _walk(payload)
        return collected

    @staticmethod
    def _is_attachment_descriptor(value: dict[str, Any]) -> bool:
        required = ("filename", "storage_path", "media_type", "file_extension", "size_bytes")
        return all(key in value for key in required)

    def dashboard_payload(self, *, fast_mode: bool = False) -> dict[str, Any]:
        controls = self.runtime_settings.snapshot()
        jobs = [self._serialize_scheduler_job(item) for item in self.scheduler_jobs.list_all()[:12]]
        runs = [self._serialize_execution_run(item) for item in self.execution_runs.list_runs(limit=12)]
        if fast_mode:
            latest_hardware = self.hardware_guard.latest()
        else:
            try:
                latest_hardware = self.hardware_guard.capture(
                    snapshot_type="trusted-auto-dashboard",
                    persist=False,
                )
            except Exception:
                latest_hardware = self.hardware_guard.latest()
        from atnos.services.content_access_reality_service import ContentAccessRealityService

        content_readiness = ContentAccessRealityService(self.session, self.settings).build_snapshot()
        return {
            "runtime_controls": {
                "autonomy_mode": controls.autonomy_mode,
                "scheduler_enabled": controls.scheduler_enabled,
                "emergency_stop_enabled": controls.emergency_stop_enabled,
                "pause_all_jobs": controls.pause_all_jobs,
                "login_automation_enabled": controls.login_automation_enabled,
                "two_factor_automation_enabled": controls.two_factor_automation_enabled,
                "publish_automation_enabled": controls.publish_automation_enabled,
                "default_retry_budget": controls.default_retry_budget,
                "default_vram_floor_gb": controls.default_vram_floor_gb,
            },
            "accounts": self.list_accounts(),
            "trust_grants": self.list_trust_grants(),
            "scheduler_jobs": jobs,
            "execution_runs": runs,
            "media_pipeline_presets": self.list_media_pipeline_presets(),
            "hardware_snapshot": latest_hardware.to_dict() if latest_hardware is not None else None,
            "browser_runtime": self.browser_runtime.describe().to_dict(),
            "youtube_connector": self._safe_youtube_connector_status(),
            "content_readiness": content_readiness.to_dict(),
        }

    def approve_campaign_publish(self, run_id: int) -> dict[str, Any]:
        controls = self.runtime_settings.snapshot()
        source_run = self.execution_runs.get_run(run_id)
        if source_run is None:
            raise ValueError("Execution run was not found.")
        if source_run.run_type not in {"campaign", "synthetic_creator_preview_batch"}:
            raise ValueError("Only campaign and synthetic creator batch runs can be approved for publish.")
        if source_run.status != "waiting_for_input":
            raise ValueError("This run is not waiting for approval.")
        source_output = dict(source_run.output_json or {})
        if str(source_output.get("status") or "") != "awaiting_approval":
            raise ValueError("This run has no pending approval gate.")

        if source_run.run_type == "campaign":
            publish_run_type = "campaign_publish"
            publish_connector_key = source_run.connector_key
            publish_platform_key = source_run.platform_key
        else:
            release_pack = dict(source_output.get("release_pack") or {})
            publish_run_type = "synthetic_creator_publish"
            publish_connector_key = str(release_pack.get("connector_key") or source_run.connector_key or "youtube-studio")
            publish_platform_key = str(release_pack.get("platform_key") or source_run.platform_key or "youtube")

        publish_run = self.execution_runs.create_run(
            project_id=source_run.project_id,
            scheduler_job_id=source_run.scheduler_job_id,
            account_id=source_run.account_id,
            trust_grant_id=source_run.trust_grant_id,
            run_type=publish_run_type,
            status="running",
            goal=f"Publish approval for {source_run.goal}",
            connector_key=publish_connector_key,
            platform_key=publish_platform_key,
            risk_level=source_run.risk_level,
            input_json={"source_run_id": source_run.id},
            output_json={},
            artifacts_json=dict(source_run.artifacts_json or {}),
            error_message=None,
            started_at=self._now(),
            finished_at=None,
        )
        self.session.commit()

        try:
            if source_run.run_type == "campaign":
                trust = self._resolve_trust(
                    project_id=source_run.project_id,
                    account_id=source_run.account_id,
                    trust_grant_id=source_run.trust_grant_id,
                    connector_key=source_run.connector_key,
                    requires_trusted_auto=True,
                )
                if not trust.allowed:
                    raise RuntimeError(trust.reason)
                grant = self.trust_grants.get_by_id(trust.grant_id) if trust.grant_id is not None else None
                campaign = ContentCampaignService(
                    self.session,
                    settings=self.settings,
                    provider_registry=self.provider_registry,
                    plugin_registry=self.plugin_registry,
                )
                publish_run.output_json = campaign.publish_approved_campaign(
                    source_run=source_run,
                    publish_run=publish_run,
                    controls=controls,
                    publish_allowed=bool(grant.publish_allowed) if grant is not None else False,
                )
            else:
                preview = SyntheticCreatorWorkflowService(
                    self.session,
                    settings=self.settings,
                    provider_registry=self.provider_registry,
                    plugin_registry=self.plugin_registry,
                )
                publish_run.output_json = preview.publish_approved_batch(
                    source_run=source_run,
                    publish_run=publish_run,
                    controls=controls,
                )
            publish_artifacts = publish_run.output_json.get("artifacts", {})
            if isinstance(publish_artifacts, dict):
                publish_run.artifacts_json = {
                    **dict(publish_run.artifacts_json or {}),
                    **publish_artifacts,
                }
            followup_job = self._schedule_analytics_followup_if_needed(run=publish_run, controls=controls)
            if followup_job is not None:
                publish_run.output_json = {
                    **dict(publish_run.output_json or {}),
                    "analytics_followup_job": followup_job,
                }
            publish_run.status = "done"
            publish_run.finished_at = self._now()
            self.execution_runs.save_run(publish_run)

            source_run.status = "done"
            source_run.finished_at = self._now()
            source_run.output_json = {
                **source_output,
                "approval_status": "approved",
                "approved_at": self._now().isoformat(),
            }
            self.execution_runs.save_run(source_run)

            source_job = (
                self.scheduler_jobs.get_by_id(source_run.scheduler_job_id)
                if source_run.scheduler_job_id is not None
                else None
            )
            if source_job is not None and source_job.status == "waiting_for_input":
                source_job.status = "done"
                source_job.last_error = None
                source_job.locked_at = None
                self.scheduler_jobs.save(source_job)
            self.session.commit()
        except Exception as exc:
            publish_run.status = "failed"
            publish_run.error_message = str(exc)
            publish_run.finished_at = self._now()
            self.execution_runs.save_run(publish_run)
            self.session.commit()
            raise

        return self._serialize_execution_run(publish_run)

    def _execute_job(self, *, run, job, controls) -> None:
        step_index = 0
        self.execution_runs.create_step(
            execution_run_id=run.id,
            step_index=step_index,
            step_type="hardware_guard",
            step_name="Hardware guard",
            status="running",
            selector_hint=None,
            input_json={"vram_floor_gb": controls.default_vram_floor_gb},
            output_json={},
            error_message=None,
            screenshot_path=None,
            started_at=self._now(),
            finished_at=None,
        )
        snapshot = self.hardware_guard.capture(
            snapshot_type=f"job:{job.job_type}",
            vram_floor_gb=controls.default_vram_floor_gb,
        )
        step = self.execution_runs.list_steps(run.id)[-1]
        step.status = "done"
        step_output: dict[str, Any] = {
            "hardware_snapshot": snapshot.to_dict(),
            "hardware_gate_mode": "advisory_only",
        }
        step.output_json = step_output
        step.finished_at = self._now()
        self.execution_runs.save_step(step)
        run.artifacts_json = {
            **dict(run.artifacts_json or {}),
            "hardware_snapshot": snapshot.to_dict(),
            "hardware_gate_mode": "advisory_only",
        }
        self.execution_runs.save_run(run)
        self.session.commit()

        if job.job_type in {"research", "script", "memory_update"}:
            run.output_json = {
                "status": "completed",
                "message": f"{job.job_type} completed under trusted automation.",
            }
            return

        if job.job_type == "analytics_sync":
            followup = PublishAnalyticsFollowupService(self.session, self.settings)
            conversation_id = (
                job.payload_json.get("conversation_id")
                if isinstance(job.payload_json.get("conversation_id"), int)
                else None
            )
            source_run_id = (
                job.payload_json.get("source_run_id")
                if isinstance(job.payload_json.get("source_run_id"), int)
                else None
            )
            video_id = str(job.payload_json.get("video_id") or "").strip()
            if not video_id:
                raise RuntimeError("Analytics sync job requires a video_id payload.")
            run.output_json = followup.run_followup(
                project_name=str(job.payload_json.get("project_name") or f"project-{job.project_id}"),
                conversation_id=conversation_id,
                source_run_id=source_run_id,
                video_id=video_id,
                title=str(job.payload_json.get("title") or "").strip() or None,
                publish_result=dict(job.payload_json.get("publish_result") or {}),
                review_window_hours=max(1, int(job.payload_json.get("review_window_hours") or 48)),
            )
            feedback_artifact = run.output_json.get("feedback_artifact", {})
            run.artifacts_json = {
                **dict(run.artifacts_json or {}),
                "analytics_followup": {
                    "feedback_artifact": dict(feedback_artifact) if isinstance(feedback_artifact, dict) else {},
                    "vault_note_path": run.output_json.get("vault_note_path"),
                    "analytics_summary": dict(run.output_json.get("analytics_summary") or {}),
                },
            }
            self._update_source_run_analytics_followup(
                source_run_id=source_run_id,
                followup_job_id=job.id,
                followup_payload=run.output_json,
            )
            self.execution_runs.save_run(run)
            self.session.commit()
            return

        if job.job_type in {"generate_assets", "render_video"}:
            run.output_json = {
                "status": "completed",
                "message": f"{job.job_type} completed with local pipeline prerequisites satisfied.",
            }
            return

        if job.job_type == "peer_consultation":
            from atnos.services.peer_consultation_service import PeerConsultationService

            consultation = PeerConsultationService(
                self.session,
                settings=self.settings,
                provider_registry=self.provider_registry,
                plugin_registry=self.plugin_registry,
            )
            run.output_json = consultation.run_project_consultation(
                project_id=job.project_id,
                conversation_id=job.payload_json.get("conversation_id")
                if isinstance(job.payload_json.get("conversation_id"), int)
                else None,
                goal=str(job.payload_json.get("goal") or f"peer consultation for project {job.project_id}"),
                topic_hint=str(job.payload_json.get("topic") or job.payload_json.get("goal") or "").strip() or None,
            )
            consultation_artifact = run.output_json.get("artifact", {})
            run.artifacts_json = {
                **dict(run.artifacts_json or {}),
                "peer_consultation": dict(consultation_artifact) if isinstance(consultation_artifact, dict) else {},
            }
            self.execution_runs.save_run(run)
            self.session.commit()
            return

        if job.job_type == "learning_ingest":
            from atnos.services.learning_ingest_service import LearningIngestService

            ingest = LearningIngestService(self.session, self.settings)
            run.output_json = ingest.run_learning_ingest(run=run, job=job)
            note_artifact = run.output_json.get("artifact", {})
            downloads = run.output_json.get("downloads", [])
            run.artifacts_json = {
                **dict(run.artifacts_json or {}),
                "learning_ingest": {
                    "artifact": dict(note_artifact) if isinstance(note_artifact, dict) else {},
                    "downloads": list(downloads) if isinstance(downloads, list) else [],
                    "vault_note_path": run.output_json.get("vault_note_path"),
                },
            }
            self.execution_runs.save_run(run)
            self.session.commit()
            return

        if job.job_type == "synthetic_creator_preview":
            preview = SyntheticCreatorWorkflowService(
                self.session,
                settings=self.settings,
                provider_registry=self.provider_registry,
                plugin_registry=self.plugin_registry,
            )
            run.output_json = preview.generate_preview(
                creator_project_id=job.payload_json.get("creator_project_id")
                if isinstance(job.payload_json.get("creator_project_id"), int)
                else None,
                canonical_project_id=job.project_id,
                conversation_id=job.payload_json.get("conversation_id")
                if isinstance(job.payload_json.get("conversation_id"), int)
                else None,
                goal=str(job.payload_json.get("goal") or job.goal or "").strip() or None,
                style_key=str(job.payload_json.get("style_key") or "").strip() or None,
                reel_type=str(job.payload_json.get("reel_type") or "").strip() or None,
                quality_profile=str(job.payload_json.get("quality_profile") or "").strip() or "standard",
                execution_run=run,
            )
            preview_artifacts = run.output_json.get("artifacts", {})
            run.artifacts_json = {
                **dict(run.artifacts_json or {}),
                "synthetic_creator_preview": (
                    dict(preview_artifacts)
                    if isinstance(preview_artifacts, dict)
                    else {}
                ),
                "vault_note_path": run.output_json.get("vault_note_path"),
            }
            self.execution_runs.save_run(run)
            self.session.commit()
            return

        if job.job_type == "synthetic_creator_preview_batch":
            preview = SyntheticCreatorWorkflowService(
                self.session,
                settings=self.settings,
                provider_registry=self.provider_registry,
                plugin_registry=self.plugin_registry,
            )
            style_keys = job.payload_json.get("style_keys")
            reel_types = job.payload_json.get("reel_types")
            run.output_json = preview.generate_preview_batch(
                creator_project_id=job.payload_json.get("creator_project_id")
                if isinstance(job.payload_json.get("creator_project_id"), int)
                else None,
                canonical_project_id=job.project_id,
                conversation_id=job.payload_json.get("conversation_id")
                if isinstance(job.payload_json.get("conversation_id"), int)
                else None,
                goal=str(job.payload_json.get("goal") or job.goal or "").strip() or None,
                style_keys=[str(item) for item in style_keys if str(item).strip()] if isinstance(style_keys, list) else None,
                reel_types=[str(item) for item in reel_types if str(item).strip()] if isinstance(reel_types, list) else None,
                quality_profile=str(job.payload_json.get("quality_profile") or "").strip() or "standard",
                max_items=int(job.payload_json.get("max_items") or 4),
                execution_run=run,
            )
            run.artifacts_json = {
                **dict(run.artifacts_json or {}),
                "synthetic_creator_preview_batch": {
                    "batch_report": dict(run.output_json.get("batch_report") or {}),
                    "release_pack": dict(run.output_json.get("release_pack") or {}),
                    "release_pack_artifact": dict(run.output_json.get("release_pack_artifact") or {}),
                    "release_pack_json_artifact": dict(run.output_json.get("release_pack_json_artifact") or {}),
                    "release_pack_vault_note_path": run.output_json.get("release_pack_vault_note_path"),
                    "vault_note_path": run.output_json.get("vault_note_path"),
                    "variants": list(run.output_json.get("variants") or []),
                },
            }
            self.execution_runs.save_run(run)
            self.session.commit()
            return

        if job.job_type == "publish":
            recipe = job.payload_json.get("browser_recipe")
            if not isinstance(recipe, dict):
                raise RuntimeError("Publish job requires a browser_recipe payload.")
            recipe_result = self._run_browser_recipe(
                recipe=recipe,
                account_id=job.payload_json.get("account_id") if isinstance(job.payload_json.get("account_id"), int) else None,
                allow_login=controls.login_automation_enabled,
                allow_two_factor=controls.two_factor_automation_enabled,
            )
            run.output_json = {"status": "completed", "browser_result": recipe_result}
            run.artifacts_json = {**dict(run.artifacts_json or {}), **recipe_result.get("artifacts", {})}
            return

        if job.job_type == "campaign":
            trust = self._resolve_trust(
                project_id=job.project_id,
                account_id=job.payload_json.get("account_id") if isinstance(job.payload_json.get("account_id"), int) else None,
                trust_grant_id=job.payload_json.get("trust_grant_id") if isinstance(job.payload_json.get("trust_grant_id"), int) else None,
                connector_key=job.connector_key,
                requires_trusted_auto=job.requires_trusted_auto,
            )
            grant = self.trust_grants.get_by_id(trust.grant_id) if trust.grant_id is not None else None
            campaign = ContentCampaignService(
                self.session,
                settings=self.settings,
                provider_registry=self.provider_registry,
                plugin_registry=self.plugin_registry,
            )
            run.output_json = campaign.run_youtube_short_campaign(
                run=run,
                job=job,
                controls=controls,
                publish_allowed=bool(grant.publish_allowed) if grant is not None else False,
            )
            campaign_artifacts = run.output_json.get("artifacts", {})
            run.artifacts_json = {
                **dict(run.artifacts_json or {}),
                "artifacts": (
                    dict(campaign_artifacts)
                    if isinstance(campaign_artifacts, dict)
                    else list(campaign_artifacts)
                    if isinstance(campaign_artifacts, list)
                    else {}
                ),
            }
            self.execution_runs.save_run(run)
            self.session.commit()
            return

        raise RuntimeError(f"Unsupported job_type '{job.job_type}'.")

    def _schedule_analytics_followup_if_needed(self, *, run, controls) -> dict[str, Any] | None:
        if not controls.scheduler_enabled:
            return None
        output_json = dict(run.output_json or {})
        publish_result = dict(output_json.get("publish_result") or {})
        if str(publish_result.get("status") or "") != "uploaded":
            return None
        video_id = str(publish_result.get("video_id") or "").strip()
        if not video_id:
            return None
        analytics_summary = dict(output_json.get("analytics_summary") or {})
        if str(analytics_summary.get("status") or "").strip().lower() == "ready":
            return None
        review_window_hours = self._resolve_review_window_hours(output_json=output_json)
        scheduled_for = self._now() + timedelta(hours=review_window_hours)
        payload_json = {
            "goal": f"Analytics follow-up for {run.goal}",
            "source_run_id": run.id,
            "conversation_id": (
                run.input_json.get("conversation_id")
                if isinstance(run.input_json, dict) and isinstance(run.input_json.get("conversation_id"), int)
                else None
            ),
            "video_id": video_id,
            "publish_result": publish_result,
            "project_name": output_json.get("project_name") or output_json.get("topic") or run.goal,
            "title": self._resolve_publish_title(output_json=output_json),
            "review_window_hours": review_window_hours,
        }
        job = self.scheduler_jobs.create(
            project_id=run.project_id,
            job_type="analytics_sync",
            trigger_mode="scheduled",
            status="queued",
            connector_key=run.connector_key,
            platform_key=run.platform_key,
            scheduled_for=scheduled_for,
            cron_expression=None,
            payload_json=payload_json,
            retry_count=0,
            retry_budget=max(1, controls.default_retry_budget),
            requires_trusted_auto=False,
            cooldown_key=f"analytics:{video_id}",
            last_error=None,
            last_run_at=None,
            next_run_at=scheduled_for,
            locked_at=None,
            cancelled_at=None,
        )
        self.scheduler_jobs.save(job)
        return self._serialize_scheduler_job(job)

    def _update_source_run_analytics_followup(
        self,
        *,
        source_run_id: int | None,
        followup_job_id: int,
        followup_payload: dict[str, Any],
    ) -> None:
        if source_run_id is None:
            return
        source_run = self.execution_runs.get_run(source_run_id)
        if source_run is None:
            return
        source_output = dict(source_run.output_json or {})
        source_output["analytics_summary"] = dict(followup_payload.get("analytics_summary") or {})
        source_output["analytics_strategy_snapshot"] = dict(followup_payload.get("strategy_snapshot") or {})
        source_output["analytics_followup_completed_at"] = self._now().isoformat()
        source_output["analytics_followup_job_id"] = followup_job_id
        source_run.output_json = source_output
        source_artifacts = dict(source_run.artifacts_json or {})
        followup_artifact = dict(followup_payload.get("feedback_artifact") or {})
        if followup_artifact:
            source_artifacts["analytics_followup"] = {
                "feedback_artifact": followup_artifact,
                "vault_note_path": followup_payload.get("vault_note_path"),
                "analytics_summary": dict(followup_payload.get("analytics_summary") or {}),
                "strategy_snapshot": dict(followup_payload.get("strategy_snapshot") or {}),
            }
            source_run.artifacts_json = source_artifacts
        self.execution_runs.save_run(source_run)
        self._refresh_creator_project_strategy_profile(canonical_project_id=source_run.project_id)

    def _refresh_creator_project_strategy_profile(self, *, canonical_project_id: int | None) -> None:
        if canonical_project_id is None:
            return
        creator_project = self.creator_ops.get_project_by_canonical_project_id(canonical_project_id)
        if creator_project is None:
            return
        if creator_project.project_type not in {"synthetic_influencer", "synthetic_model"}:
            return
        calibration = self.analytics_learning.build_calibration(project_id=canonical_project_id)
        strategy_profile = self.synthetic_project_strategy.build_profile(
            project_name=creator_project.name,
            calibration=calibration,
        )
        self.creator_ops.update_strategy_profile_by_canonical_project_id(
            canonical_project_id,
            strategy_profile=strategy_profile,
        )

    @staticmethod
    def _resolve_review_window_hours(*, output_json: dict[str, Any]) -> int:
        release_pack = dict(output_json.get("release_pack") or {})
        analytics_followup = dict(release_pack.get("analytics_followup") or {})
        value = analytics_followup.get("recommended_review_window_hours")
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
        analytics_summary = dict(output_json.get("analytics_summary") or {})
        direct_value = analytics_summary.get("recommended_review_window_hours")
        if isinstance(direct_value, int) and direct_value > 0:
            return direct_value
        return 48

    @staticmethod
    def _resolve_publish_title(*, output_json: dict[str, Any]) -> str | None:
        release_pack = dict(output_json.get("release_pack") or {})
        metadata = dict(release_pack.get("metadata") or {})
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        approval_package = dict(output_json.get("approval_package") or {})
        approval_metadata = dict(approval_package.get("metadata") or {})
        fallback_title = approval_metadata.get("title")
        if isinstance(fallback_title, str) and fallback_title.strip():
            return fallback_title.strip()
        return None

    def _run_browser_recipe(
        self,
        *,
        recipe: dict[str, object],
        account_id: int | None,
        allow_login: bool,
        allow_two_factor: bool,
    ) -> dict[str, Any]:
        browser_state = self.browser_runtime.describe()
        if not browser_state.ready:
            raise RuntimeError(browser_state.status_reason)
        resolved_recipe = self._resolve_recipe_secrets(
            recipe=recipe,
            account_id=account_id,
            allow_login=allow_login,
            allow_two_factor=allow_two_factor,
        )
        return self.browser_recipe_runner.run(resolved_recipe)

    def _resolve_recipe_secrets(
        self,
        *,
        recipe: dict[str, object],
        account_id: int | None,
        allow_login: bool,
        allow_two_factor: bool,
    ) -> dict[str, object]:
        if account_id is None:
            return recipe
        profile = self.credential_profiles.get_by_account_id(account_id)
        if profile is None:
            return recipe
        payload = json.loads(json.dumps(recipe))
        password = self._resolve_secret_ref(profile.password_secret_ref) if allow_login else None
        totp_code = self._generate_totp_code(profile.totp_secret_ref) if allow_two_factor else None
        backup_code = self._resolve_backup_code(profile.backup_codes_secret_ref) if allow_two_factor else None
        replacements = {
            "{{credential.login}}": profile.login_identifier or "",
            "{{secret.password}}": password or "",
            "{{totp.code}}": totp_code or "",
            "{{backup.code}}": backup_code or "",
            "{{recovery.email}}": profile.recovery_email or "",
        }
        return self._deep_replace(payload, replacements)

    def _resolve_trust(
        self,
        *,
        project_id: int,
        account_id: int | None,
        trust_grant_id: int | None,
        connector_key: str | None,
        requires_trusted_auto: bool,
    ) -> TrustResolution:
        controls = self.runtime_settings.snapshot()
        if controls.autonomy_mode == "blocked":
            return TrustResolution(False, "Autonomy mode is blocked.", controls.autonomy_mode, None)
        if requires_trusted_auto and controls.autonomy_mode != "trusted_auto":
            return TrustResolution(False, "Trusted auto mode is not enabled.", controls.autonomy_mode, None)
        if account_id is None or connector_key is None:
            if requires_trusted_auto:
                return TrustResolution(False, "Trusted auto jobs require account and connector context.", controls.autonomy_mode, None)
            return TrustResolution(True, "No trust grant required.", controls.autonomy_mode, None)
        grant = self.trust_grants.get_by_id(trust_grant_id) if trust_grant_id is not None else None
        if grant is None:
            grant = self.trust_grants.get_by_scope(project_id=project_id, account_id=account_id, connector_key=connector_key)
        if grant is None:
            return TrustResolution(False, "No trust grant exists for this project/account/connector.", controls.autonomy_mode, None)
        if not grant.is_enabled:
            return TrustResolution(False, "Trust grant is disabled.", grant.mode, grant.id)
        if requires_trusted_auto and grant.mode != "trusted_auto":
            return TrustResolution(False, "Trust grant is not in trusted_auto mode.", grant.mode, grant.id)
        return TrustResolution(True, "Trusted automation allowed.", grant.mode, grant.id)

    def _resolve_trusted_context(
        self,
        *,
        project_id: int,
        connector_key: str | None,
        account_id: int | None,
        trust_grant_id: int | None,
        requires_trusted_auto: bool,
    ) -> TrustedContextResolution:
        normalized_connector_key = self._normalize_optional_text(connector_key)

        if trust_grant_id is not None:
            grant = self.trust_grants.get_by_id(trust_grant_id)
            if grant is None:
                raise RuntimeError("Selected trust grant was not found.")
            if grant.project_id != project_id:
                raise RuntimeError("Selected trust grant does not belong to the active project.")
            if account_id is not None and grant.account_id != account_id:
                raise RuntimeError("Selected trust grant does not match the selected account.")
            if normalized_connector_key is not None and grant.connector_key != normalized_connector_key:
                raise RuntimeError("Selected trust grant does not match the requested connector.")
            return TrustedContextResolution(
                account_id=grant.account_id,
                connector_key=grant.connector_key,
                trust_grant_id=grant.id,
            )

        if not requires_trusted_auto:
            return TrustedContextResolution(
                account_id=account_id,
                connector_key=normalized_connector_key,
                trust_grant_id=None,
            )

        if account_id is not None and normalized_connector_key is not None:
            return TrustedContextResolution(
                account_id=account_id,
                connector_key=normalized_connector_key,
                trust_grant_id=None,
            )

        candidate_grants = [
            grant
            for grant in self.trust_grants.list_for_project(project_id)
            if grant.is_enabled and grant.mode == "trusted_auto"
        ]
        if normalized_connector_key is not None:
            candidate_grants = [
                grant for grant in candidate_grants if grant.connector_key == normalized_connector_key
            ]
        if account_id is not None:
            candidate_grants = [
                grant for grant in candidate_grants if grant.account_id == account_id
            ]

        if len(candidate_grants) == 1:
            grant = candidate_grants[0]
            return TrustedContextResolution(
                account_id=grant.account_id,
                connector_key=grant.connector_key,
                trust_grant_id=grant.id,
            )

        if len(candidate_grants) > 1:
            raise RuntimeError(
                "Multiple trusted auto grants match this project. Select one account/grant in Settings first."
            )

        raise RuntimeError(
            "Run blocked. Connect an account and enable one trusted auto grant for this project in Settings."
        )

    def _create_blocked_run(self, *, job, goal: str, account_id: int | None, trust: TrustResolution, reason: str):
        return self.execution_runs.create_run(
            project_id=job.project_id,
            scheduler_job_id=job.id,
            account_id=account_id,
            trust_grant_id=trust.grant_id,
            run_type=job.job_type,
            status="blocked",
            goal=goal,
            connector_key=job.connector_key,
            platform_key=job.platform_key,
            risk_level="high" if job.requires_trusted_auto else "medium",
            input_json=dict(job.payload_json or {}),
            output_json={},
            artifacts_json={},
            error_message=reason,
            started_at=self._now(),
            finished_at=self._now(),
        )

    def _serialize_trust_grant(self, item) -> dict[str, Any]:
        project = self.projects.get(item.project_id)
        account = self.accounts.get_by_id(item.account_id)
        return {
            "trust_grant_id": item.id,
            "project_id": item.project_id,
            "project_name": project.name if project is not None else None,
            "account_id": item.account_id,
            "account_label": account.label if account is not None else None,
            "connector_key": item.connector_key,
            "mode": item.mode,
            "domain_allowlist": list(item.domain_allowlist_json or []),
            "action_families": list(item.action_families_json or []),
            "file_write_scope": list(item.file_write_scope_json or []),
            "login_allowed": item.login_allowed,
            "two_factor_allowed": item.two_factor_allowed,
            "publish_allowed": item.publish_allowed,
            "browser_automation_allowed": item.browser_automation_allowed,
            "connector_write_allowed": item.connector_write_allowed,
            "local_render_allowed": item.local_render_allowed,
            "max_retries": item.max_retries,
            "cooldown_minutes": item.cooldown_minutes,
            "is_enabled": item.is_enabled,
            "notes": item.notes,
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_credential_profile(item) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "credential_profile_id": item.id,
            "login_identifier": item.login_identifier,
            "has_password": bool(item.password_secret_ref),
            "has_totp": bool(item.totp_secret_ref),
            "has_backup_codes": bool(item.backup_codes_secret_ref),
            "recovery_email": item.recovery_email,
            "has_recovery_email_secret": bool(item.recovery_email_secret_ref),
            "connector_mapping_json": dict(item.connector_mapping_json or {}),
            "status": item.status,
            "notes": item.notes,
        }

    def _serialize_scheduler_job(self, item) -> dict[str, Any]:
        project = self.projects.get(item.project_id)
        return {
            "job_id": item.id,
            "project_id": item.project_id,
            "project_name": project.name if project is not None else None,
            "job_type": item.job_type,
            "trigger_mode": item.trigger_mode,
            "status": item.status,
            "connector_key": item.connector_key,
            "platform_key": item.platform_key,
            "scheduled_for": item.scheduled_for.isoformat() if item.scheduled_for is not None else None,
            "cron_expression": item.cron_expression,
            "payload_json": dict(item.payload_json or {}),
            "retry_count": item.retry_count,
            "retry_budget": item.retry_budget,
            "requires_trusted_auto": item.requires_trusted_auto,
            "cooldown_key": item.cooldown_key,
            "last_error": item.last_error,
            "last_run_at": item.last_run_at.isoformat() if item.last_run_at is not None else None,
            "next_run_at": item.next_run_at.isoformat() if item.next_run_at is not None else None,
            "locked_at": item.locked_at.isoformat() if item.locked_at is not None else None,
            "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at is not None else None,
        }

    def _serialize_execution_run(self, item) -> dict[str, Any]:
        project = self.projects.get(item.project_id)
        steps = [
            {
                "execution_step_id": step.id,
                "step_index": step.step_index,
                "step_type": step.step_type,
                "step_name": step.step_name,
                "status": step.status,
                "selector_hint": step.selector_hint,
                "error_message": step.error_message,
                "screenshot_path": step.screenshot_path,
                "started_at": step.started_at.isoformat() if step.started_at is not None else None,
                "finished_at": step.finished_at.isoformat() if step.finished_at is not None else None,
            }
            for step in self.execution_runs.list_steps(item.id)
        ]
        return {
            "execution_run_id": item.id,
            "project_id": item.project_id,
            "project_name": project.name if project is not None else None,
            "scheduler_job_id": item.scheduler_job_id,
            "account_id": item.account_id,
            "trust_grant_id": item.trust_grant_id,
            "run_type": item.run_type,
            "status": item.status,
            "goal": item.goal,
            "connector_key": item.connector_key,
            "platform_key": item.platform_key,
            "risk_level": item.risk_level,
            "input_json": dict(item.input_json or {}),
            "output_json": dict(item.output_json or {}),
            "artifacts_json": dict(item.artifacts_json or {}),
            "error_message": item.error_message,
            "started_at": item.started_at.isoformat() if item.started_at is not None else None,
            "finished_at": item.finished_at.isoformat() if item.finished_at is not None else None,
            "steps": steps,
        }

    def _safe_youtube_connector_status(self) -> dict[str, Any]:
        try:
            from atnos.services.youtube_connector_service import YouTubeConnectorService

            return YouTubeConnectorService(self.session, self.settings).get_status().to_dict()
        except Exception as exc:
            return {
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": False,
                "connected": False,
                "ready": False,
                "warnings": [str(exc)],
                "account_id": None,
                "channel_title": None,
                "granted_scopes": [],
            }

    def _resolve_account_probe_url(
        self,
        *,
        account: dict[str, Any],
        connector_mapping_json: dict[str, object],
    ) -> str | None:
        for key in ("primary_url", "login_url", "base_url", "url", "host"):
            normalized = self._normalize_probe_url(connector_mapping_json.get(key))
            if normalized is not None:
                return normalized

        domains = connector_mapping_json.get("domains")
        if isinstance(domains, list):
            for raw in domains:
                normalized = self._normalize_probe_url(raw)
                if normalized is not None:
                    return normalized

        for grant in account.get("trust_grants") or []:
            if not isinstance(grant, dict):
                continue
            for raw in grant.get("domain_allowlist") or []:
                normalized = self._normalize_probe_url(raw)
                if normalized is not None:
                    return normalized

        fallback = DEFAULT_ACCOUNT_PROBE_URLS.get(str(account.get("provider_key") or "").strip().lower())
        return self._normalize_probe_url(fallback)

    def _probe_feedback_url(self, url: str, *, timeout_seconds: float) -> dict[str, Any]:
        request = urllib_request.Request(
            url,
            headers={
                "User-Agent": "AtnosTrustedAccountProbe/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
                status_code = getattr(response, "status", None) or response.getcode()
            if 200 <= int(status_code) < 400:
                return {
                    "status": "ok",
                    "http_status": int(status_code),
                    "reason": f"Strona odpowiada HTTP {int(status_code)}.",
                }
            return {
                "status": "http_error",
                "http_status": int(status_code),
                "reason": f"Strona zwrocila HTTP {int(status_code)}.",
            }
        except urllib_error.HTTPError as exc:
            if exc.code in {401, 403}:
                return {
                    "status": "http_restricted",
                    "http_status": int(exc.code),
                    "reason": f"Strona odpowiada HTTP {int(exc.code)}, ale blokuje anonimowy probe.",
                }
            return {
                "status": "http_error",
                "http_status": int(exc.code),
                "reason": f"Strona zwrocila HTTP {int(exc.code)}.",
            }
        except urllib_error.URLError as exc:
            reason_text = str(exc.reason or exc).strip() or "Unknown connection error."
            if "timed out" in reason_text.lower():
                return {
                    "status": "timeout",
                    "http_status": None,
                    "reason": f"Timeout podczas laczenia: {reason_text}",
                }
            return {
                "status": "connection_error",
                "http_status": None,
                "reason": f"Blad polaczenia: {reason_text}",
            }
        except TimeoutError as exc:
            return {
                "status": "timeout",
                "http_status": None,
                "reason": f"Timeout podczas laczenia: {exc}",
            }
        except OSError as exc:
            return {
                "status": "connection_error",
                "http_status": None,
                "reason": f"Blad polaczenia: {exc}",
            }

    @staticmethod
    def _normalize_probe_url(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.startswith("//"):
            candidate = f"https:{candidate}"
        elif "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urllib_parse.urlparse(candidate)
        if not parsed.netloc:
            return None
        return urllib_parse.urlunparse(parsed._replace(fragment=""))

    @staticmethod
    def _serialize_media_preset(item) -> dict[str, Any]:
        return {
            "preset_id": item.id,
            "project_id": item.project_id,
            "name": item.name,
            "target_platform": item.target_platform,
            "tts_engine": item.tts_engine,
            "image_provider": item.image_provider,
            "video_renderer": item.video_renderer,
            "subtitle_engine": item.subtitle_engine,
            "prompt_style": item.prompt_style,
            "target_duration_sec": item.target_duration_sec,
            "vram_floor_gb": item.vram_floor_gb,
            "publish_mode": item.publish_mode,
            "cooldown_hours": item.cooldown_hours,
            "steps_json": dict(item.steps_json or {}),
            "is_default": item.is_default,
        }

    def _build_credential_profile_payload(
        self,
        *,
        login_identifier: str | None,
        password_value: str | None,
        totp_value: str | None,
        backup_codes_value: str | None,
        recovery_email: str | None,
        recovery_email_secret_value: str | None,
        existing,
        connector_mapping_json: dict[str, object],
    ) -> dict[str, Any]:
        return {
            "login_identifier": self._normalize_optional_text(login_identifier),
            "password_secret_ref": self._store_or_keep_secret(password_value, getattr(existing, "password_secret_ref", None)),
            "totp_secret_ref": self._store_or_keep_secret(totp_value, getattr(existing, "totp_secret_ref", None)),
            "backup_codes_secret_ref": self._store_or_keep_secret(
                backup_codes_value,
                getattr(existing, "backup_codes_secret_ref", None),
            ),
            "recovery_email": self._normalize_optional_text(recovery_email),
            "recovery_email_secret_ref": self._store_or_keep_secret(
                recovery_email_secret_value,
                getattr(existing, "recovery_email_secret_ref", None),
            ),
            "connector_mapping_json": connector_mapping_json,
            "status": "configured",
            "notes": getattr(existing, "notes", None),
        }

    def _store_or_keep_secret(self, value: str | None, existing_ref: str | None) -> str | None:
        normalized = self._normalize_optional_text(value)
        if normalized is None:
            return existing_ref
        backend, secret_ref = self.vault.rotate_secret(secret_value=normalized, existing_secret_ref=existing_ref)
        if backend == "metadata_only":
            return None
        return secret_ref

    def _resolve_secret_ref(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        try:
            return self.vault.resolve_secret(secret_ref)
        except Exception as exc:
            from atnos.services.secret_vault_service import SecretVaultResolutionError

            if not isinstance(exc, SecretVaultResolutionError):
                raise
            return None

    def _generate_totp_code(self, secret_ref: str | None) -> str | None:
        secret = self._resolve_secret_ref(secret_ref)
        if not secret:
            return None
        normalized = secret.strip().replace(" ", "").upper()
        key = base64.b32decode(normalized, casefold=True)
        timestep = int(datetime.now(tz=UTC).timestamp() // 30)
        counter = struct.pack(">Q", timestep)
        digest = hmac.new(key, counter, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{truncated % 1000000:06d}"

    def _resolve_backup_code(self, secret_ref: str | None) -> str | None:
        secret = self._resolve_secret_ref(secret_ref)
        if not secret:
            return None
        lines = [line.strip() for line in secret.replace(",", "\n").splitlines() if line.strip()]
        return lines[0] if lines else None

    @classmethod
    def _deep_replace(cls, value, replacements: dict[str, str]):
        if isinstance(value, dict):
            return {key: cls._deep_replace(item, replacements) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._deep_replace(item, replacements) for item in value]
        if isinstance(value, str):
            result = value
            for placeholder, resolved in replacements.items():
                result = result.replace(placeholder, resolved)
            return result
        return value

    @staticmethod
    def _normalize_required_text(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @classmethod
    def _normalize_string_list(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = cls._normalize_optional_text(raw)
            if value is None:
                continue
            if value in seen:
                continue
            output.append(value)
            seen.add(value)
        return output

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC)
