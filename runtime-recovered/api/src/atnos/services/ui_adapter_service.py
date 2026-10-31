from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.core.timestamps import as_utc
from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import ConversationStatus
from atnos.db.models.provider import Provider
from atnos.providers.base import ProviderHealthCheck
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.creator_project_repository import CreatorProjectRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.provider_repository import ProviderRepository
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.access_reality_service import AccessRealityService
from atnos.services.conversation_settings_resolver_service import (
    ConversationSettingsResolverService,
)
from atnos.services.creator_ops_service import CreatorOpsService
from atnos.services.hardware_guard_service import HardwareGuardService
from atnos.services.local_host_control_service import LocalHostControlService
from atnos.services.oauth_service import OAuthService
from atnos.services.pc_automation_service import PCAutomationService
from atnos.services.provider_runtime_secret_sync_service import ProviderRuntimeSecretSyncService
from atnos.services.runtime_inventory_service import RuntimeInventoryService
from atnos.services.remote_node_service import RemoteNodeService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.summary_cards_service import (
    SummaryCardsNotFoundError,
    SummaryCardsService,
)
from atnos.services.trusted_auto_service import TrustedAutoService

PROVIDER_CARD_NOTE_PREFIX = "atnos_provider_card:"
DEFAULT_PROVIDER_CARD_IDENTIFIER = "default"
DEFAULT_AGENT_STAGE = "idle"


class _LazyHttpxProxy:
    def __getattr__(self, name: str) -> Any:
        import httpx as httpx_module

        return getattr(httpx_module, name)


httpx = _LazyHttpxProxy()


@dataclass(frozen=True, slots=True)
class ProviderCardDefinition:
    ui_key: str
    display_name: str
    credential_provider_key: str | None
    provider_row_key: str | None
    default_base_url: str | None
    supports_api_key: bool
    supports_base_url: bool
    runtime_health_supported: bool
    oauth_connector_key: str | None = None


PROVIDER_CARD_DEFINITIONS: tuple[ProviderCardDefinition, ...] = (
    ProviderCardDefinition(
        ui_key="openai",
        display_name="OpenAI",
        credential_provider_key="openai-cloud",
        provider_row_key="openai-cloud",
        default_base_url="https://api.openai.com",
        supports_api_key=True,
        supports_base_url=True,
        runtime_health_supported=True,
    ),
    ProviderCardDefinition(
        ui_key="anthropic",
        display_name="Anthropic",
        credential_provider_key="anthropic-cloud",
        provider_row_key="anthropic-cloud",
        default_base_url="https://api.anthropic.com",
        supports_api_key=True,
        supports_base_url=True,
        runtime_health_supported=True,
    ),
    ProviderCardDefinition(
        ui_key="ollama",
        display_name="Ollama",
        credential_provider_key=None,
        provider_row_key="ollama-local",
        default_base_url=None,
        supports_api_key=False,
        supports_base_url=True,
        runtime_health_supported=True,
    ),
    ProviderCardDefinition(
        ui_key="gemini",
        display_name="Gemini",
        credential_provider_key="gemini-cloud",
        provider_row_key="gemini-cloud",
        default_base_url="https://generativelanguage.googleapis.com",
        supports_api_key=True,
        supports_base_url=True,
        runtime_health_supported=True,
    ),
)

PIPELINE_STEP_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("research", "Badanie trendów", ("research", "content_idea")),
    ("script", "Skrypt", ("script", "storyboard")),
    ("tts", "Synteza mowy", ("asset_brief",)),
    ("video", "Render wideo", ("quality_review",)),
    ("thumbnail", "Miniatura", ("thumbnail_brief",)),
    ("upload", "Publikacja YouTube", ("publish_package", "action_draft_preview")),
)


class UiAdapterService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
    ) -> None:
        self.session = session
        self.base_settings = settings
        self.runtime_settings = RuntimeSettingsService(session, settings)
        self.settings = self.runtime_settings.build_effective_settings()
        self.provider_registry = provider_registry
        self.projects = ProjectRepository(session)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.providers = ProviderRepository(session)
        self.account_repository = AccountCredentialRepository(session)
        self.attachments = AttachmentRepository(session)
        self.account_service = AccountCredentialsService(session, settings)
        self.summary_cards = SummaryCardsService(session, settings)
        self.creator_ops = CreatorOpsService(session, settings)
        self.creator_repository = CreatorProjectRepository(session)
        self.oauth = OAuthService(session, settings)
        self.runtime = RuntimeInventoryService(
            session,
            provider_registry=provider_registry,
            settings=self.settings,
        )
        self.pc_automation = PCAutomationService(session, settings=settings)
        self.trusted_auto = TrustedAutoService(session, settings)

    def get_bootstrap(self) -> dict[str, Any]:
        runtime_snapshot = self.runtime.snapshot(include_live_health=False)
        capability_snapshot = self.pc_automation.get_capability_snapshot()
        playwright_adapter_available = self.pc_automation.is_playwright_adapter_available_fast()
        local_host = LocalHostControlService(self.session, settings=self.base_settings).get_capability_snapshot()
        remote_workers = RemoteNodeService(self.session, self.base_settings).list_nodes_payload()["remote_workers"]
        try:
            access_reality = AccessRealityService(
                self.session,
                settings=self.base_settings,
                provider_registry=self.provider_registry,
            ).build_snapshot(include_live_health=False)
        except Exception:
            access_reality = None
        trusted_auto_summary = self.trusted_auto.dashboard_payload(fast_mode=True)
        if trusted_auto_summary.get("hardware_snapshot") is None:
            latest_hardware = HardwareGuardService(
                self.session,
                self.base_settings,
            ).latest()
            if latest_hardware is not None:
                trusted_auto_summary["hardware_snapshot"] = latest_hardware.to_dict()

        return {
            "ui_version": "axiom-prod-v1",
            "generated_at": self._now().isoformat(),
            "projects": [self._serialize_project(project) for project in self.projects.list_all()],
            "recent_conversations": self._list_recent_conversations(limit=24),
            "runtime_summary": {
                "providers_ready": runtime_snapshot.summary.ready_providers,
                "enabled_models": runtime_snapshot.summary.enabled_models,
                "tasks_ready": runtime_snapshot.summary.tasks_ready,
                "has_local_chat": runtime_snapshot.summary.has_local_chat,
                "has_external_fallback": runtime_snapshot.summary.has_external_fallback,
                "voice_stt": {
                    "configured_engine": runtime_snapshot.voice_stt.configured_engine,
                    "effective_engine": runtime_snapshot.voice_stt.effective_engine,
                    "status": runtime_snapshot.voice_stt.status,
                    "status_reason": runtime_snapshot.voice_stt.status_reason,
                },
            },
            "provider_cards": self.list_provider_cards(runtime_snapshot=runtime_snapshot),
            "oauth_connectors": [
                {
                    "connector_key": item.connector_key,
                    "display_name": item.display_name,
                    "status": item.status,
                    "enabled": item.enabled,
                    "configured": item.configured,
                    "connection_id": item.connection_id,
                    "connected_at": item.connected_at.isoformat()
                    if item.connected_at is not None
                    else None,
                    "setup_warnings": list(item.setup_warnings),
                }
                for item in self.oauth.list_connectors()
            ],
            "capabilities": {
                "browser_control_enabled": capability_snapshot.browser_control_enabled,
                "external_actions_enabled": capability_snapshot.external_actions_enabled,
                "scheduler_enabled": capability_snapshot.scheduler_enabled,
                "playwright_adapter_available": playwright_adapter_available,
                "oauth_enabled": self.runtime_settings.snapshot().oauth_enabled,
                "obsidian_export_enabled": self.base_settings.obsidian_export_enabled,
            },
            "trusted_auto_summary": trusted_auto_summary,
            "access_reality": access_reality.to_dict() if access_reality is not None else None,
            "local_host": local_host.to_dict(),
            "remote_workers": remote_workers,
            "pipeline": self.get_current_pipeline(),
        }

    def get_conversation_detail(self, conversation_id: int) -> dict[str, Any]:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise ValueError(f"Unknown conversation_id '{conversation_id}'.")

        resolution = ConversationSettingsResolverService(self.session).resolve(
            conversation=conversation
        )
        try:
            summary_cards = self.summary_cards.get_for_conversation(conversation_id)
        except SummaryCardsNotFoundError as exc:
            raise ValueError(str(exc)) from exc

        return {
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "project_id": conversation.project_id,
                "summary": conversation.summary,
                "last_message_at": as_utc(conversation.last_message_at).isoformat()
                if conversation.last_message_at is not None
                else None,
                "updated_at": as_utc(conversation.updated_at).isoformat(),
                "active_work_mode": resolution.active_work_mode,
            },
            "messages": [
                {
                    "id": message.id,
                    "role": message.role.value,
                    "status": message.status.value,
                    "sequence_number": message.sequence_number,
                    "content": message.content,
                    "content_format": message.content_format,
                    "prompt_improved": message.prompt_improved,
                    "attachment_only": message.attachment_only,
                    "metadata_json": message.metadata_json,
                    "attachments": [
                        {
                            "attachment_id": attachment.id,
                            "filename": attachment.filename,
                            "storage_path": attachment.storage_path,
                            "kind": attachment.kind.value,
                            "source": attachment.source.value,
                            "status": attachment.status.value,
                            "media_type": attachment.media_type,
                            "file_extension": attachment.file_extension,
                            "size_bytes": attachment.size_bytes,
                            "extracted_text": attachment.extracted_text,
                            "metadata_json": attachment.metadata_json,
                        }
                        for attachment in self.attachments.list_for_message(message.id)
                    ],
                    "created_at": as_utc(message.created_at).isoformat(),
                    "updated_at": as_utc(message.updated_at).isoformat(),
                }
                for message in self.messages.list_for_conversation(conversation_id)
            ],
            "settings": {
                "response_style": resolution.resolved_response_style,
                "memory_enabled": resolution.resolved_memory_enabled,
                "file_enabled": resolution.resolved_file_enabled,
                "internet_enabled": resolution.resolved_internet_enabled,
                "persona_profile": resolution.resolved_persona_profile.value,
                "provider_posture": resolution.resolved_provider_posture.value,
                "quality_profile": "standard",
                "sources": {
                    "response_style": resolution.resolved_response_style_source,
                    "memory_enabled": resolution.resolved_memory_enabled_source,
                    "file_enabled": resolution.resolved_file_enabled_source,
                    "internet_enabled": resolution.resolved_internet_enabled_source,
                    "persona_profile": resolution.resolved_persona_profile_source,
                    "provider_posture": resolution.resolved_provider_posture_source,
                    "quality_profile": "default",
                },
            },
            "memory_cards": {
                "conversation": {
                    "conversation_id": summary_cards.conversation.conversation_id,
                    "title": summary_cards.conversation.conversation_title,
                    "summary": summary_cards.conversation.conversation_summary,
                    "has_summary": summary_cards.conversation.has_conversation_summary,
                    "project_id": summary_cards.conversation.project_id,
                },
                "project": (
                    {
                        "project_id": summary_cards.project.project_id,
                        "name": summary_cards.project.project_name,
                        "summary": summary_cards.project.project_summary,
                        "has_summary": summary_cards.project.has_project_summary,
                    }
                    if summary_cards.project is not None
                    else None
                ),
                "obsidian": (
                    {
                        "vault_name": summary_cards.obsidian_memory.vault_name,
                        "linked_label": summary_cards.obsidian_memory.linked_label,
                        "project_linked": summary_cards.obsidian_memory.project_linked,
                        "path_configured": summary_cards.obsidian_memory.path_configured,
                        "export_enabled": summary_cards.obsidian_memory.export_enabled,
                        "ready_for_preview": summary_cards.obsidian_memory.ready_for_preview,
                        "ready_for_confirm": summary_cards.obsidian_memory.ready_for_confirm,
                        "relative_path_hint": summary_cards.obsidian_memory.relative_path_hint,
                        "helper_text": summary_cards.obsidian_memory.helper_text,
                    }
                    if summary_cards.obsidian_memory is not None
                    else None
                ),
            },
        }

    def list_provider_cards(
        self,
        *,
        runtime_snapshot=None,
    ) -> list[dict[str, Any]]:
        resolved_runtime_snapshot = runtime_snapshot or self.runtime.snapshot(
            include_live_health=True
        )
        runtime_providers = {
            item.provider_key: item for item in resolved_runtime_snapshot.providers
        }
        cards: list[dict[str, Any]] = []
        for definition in PROVIDER_CARD_DEFINITIONS:
            cards.append(
                self._build_provider_card(
                    definition,
                    runtime_provider=runtime_providers.get(definition.provider_row_key or ""),
                )
            )
        return cards

    def update_provider_card(
        self,
        provider_key: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        definition = self._provider_card_definition(provider_key)
        provider_row = self._get_provider_row(definition)

        normalized_base_url = self._normalize_optional_text(base_url)
        if definition.supports_base_url and normalized_base_url is not None and provider_row is not None:
            provider_row.endpoint_url = normalized_base_url
            self.session.add(provider_row)

        if enabled is not None and provider_row is not None:
            provider_row.is_enabled = enabled
            self.session.add(provider_row)

        account_view = self._get_provider_card_account(definition)
        metadata = self._provider_card_metadata(
            definition,
            provider_row=provider_row,
            account_view=account_view,
        )
        if normalized_base_url is not None:
            metadata["base_url"] = normalized_base_url

        if definition.credential_provider_key is not None:
            if account_view is None:
                if api_key is not None or normalized_base_url is not None or enabled is not None:
                    created = self.account_service.create_account(
                        label=definition.display_name,
                        provider_key=definition.credential_provider_key,
                        account_identifier=DEFAULT_PROVIDER_CARD_IDENTIFIER,
                        secret_type="api_key",
                        secret_value=self._normalize_optional_text(api_key),
                        risk_level="medium",
                        allowed_capabilities=["read_metadata", "draft_write"],
                        linked_integration_key=None,
                        linked_project_id=None,
                        notes=self._render_provider_card_notes(metadata),
                    )
                    account_view = created
            else:
                self.account_service.update_account(
                    account_view.id,
                    notes=self._render_provider_card_notes(metadata),
                )
                if enabled is not None:
                    next_status = "configured" if enabled and account_view.has_secret else (
                        "metadata_only" if enabled else "disabled"
                    )
                    self.account_service.update_account(account_view.id, status=next_status)
                if api_key is not None and self._normalize_optional_text(api_key) is not None:
                    account_view = self.account_service.rotate_secret(
                        account_view.id,
                        secret_value=api_key,
                    )

        ProviderRuntimeSecretSyncService(
            self.session,
            self.base_settings,
        ).sync_known_cloud_providers(prefer_vault=True)

        return self._build_provider_card(definition)

    def test_provider_card(
        self,
        provider_key: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        definition = self._provider_card_definition(provider_key)
        provider_row = self._get_provider_row(definition)
        draft_api_key = self._normalize_optional_text(api_key)
        draft_base_url = self._normalize_optional_text(base_url)
        if definition.ui_key == "ollama":
            if provider_row is None:
                return self._provider_test_response(
                    definition,
                    status="error",
                    detail="Ollama provider is not registered in this runtime.",
                )
            descriptor = self.provider_registry.get("ollama-local")
            if descriptor is None:
                return self._provider_test_response(
                    definition,
                    status="not_supported",
                    detail="Ollama runtime adapter is missing in this build.",
                )
            probe_provider = provider_row
            if draft_base_url is not None:
                probe_provider = Provider(
                    key=provider_row.key,
                    label=provider_row.label,
                    vendor=provider_row.vendor,
                    kind=provider_row.kind,
                    transport=provider_row.transport,
                    auth_mode=provider_row.auth_mode,
                    endpoint_url=draft_base_url,
                    is_enabled=provider_row.is_enabled,
                    is_builtin=provider_row.is_builtin,
                    local_first=provider_row.local_first,
                    supports_chat=provider_row.supports_chat,
                    supports_vision=provider_row.supports_vision,
                    supports_audio_in=provider_row.supports_audio_in,
                    supports_audio_out=provider_row.supports_audio_out,
                    supports_image_generation=provider_row.supports_image_generation,
                )
            health = descriptor.check_health(provider=probe_provider)
            return self._provider_test_response_from_health(definition, health)

        account_view = self._get_provider_card_account(definition)
        stored_secret_value = (
            self.account_service.resolve_secret_for_adapter(account_view.id)
            if account_view is not None and account_view.has_secret
            else None
        )
        secret_value = draft_api_key or stored_secret_value or self._runtime_env_secret(definition)
        metadata = self._provider_card_metadata(
            definition,
            provider_row=provider_row,
            account_view=account_view,
        )
        resolved_base_url = str(
            draft_base_url
            or metadata.get("base_url")
            or (provider_row.endpoint_url if provider_row is not None else None)
            or definition.default_base_url
            or ""
        ).rstrip("/")

        if definition.ui_key == "anthropic":
            secret_value = secret_value or os.getenv("ANTHROPIC_API_KEY")
            if not secret_value:
                return self._provider_test_response(
                    definition,
                    status="error",
                    detail="Anthropic API key is not configured.",
                )
            return self._probe_anthropic(definition, resolved_base_url, secret_value)

        if definition.ui_key == "openai":
            if not secret_value:
                return self._provider_test_response(
                    definition,
                    status="error",
                    detail=f"{definition.display_name} API key is not configured.",
                )
            return self._probe_openai_compatible(definition, resolved_base_url, secret_value)

        if definition.ui_key == "gemini":
            if not secret_value:
                return self._provider_test_response(
                    definition,
                    status="error",
                    detail="Gemini API key is not configured.",
                )
            return self._probe_gemini(definition, resolved_base_url, secret_value)

        return self._provider_test_response(
            definition,
            status="not_supported",
            detail="This provider card does not expose a test flow.",
        )

    def get_current_pipeline(self) -> dict[str, Any]:
        project_items = self.creator_repository.list_projects()
        if not project_items:
            return {
                "preview_only": True,
                "job": None,
                "steps": [
                    self._empty_pipeline_step(step_id, label)
                    for step_id, label, _ in PIPELINE_STEP_DEFINITIONS
                ],
                "latest_video_artifact": None,
                "awaiting_approval": False,
                "status": "idle",
                "status_reason": "No creator project is available yet.",
                "approval_package": None,
            }

        latest_project = self.creator_ops.get_project(project_items[0].id)
        if latest_project is None:
            return {
                "preview_only": True,
                "job": None,
                "steps": [
                    self._empty_pipeline_step(step_id, label)
                    for step_id, label, _ in PIPELINE_STEP_DEFINITIONS
                ],
                "latest_video_artifact": None,
                "awaiting_approval": False,
                "status": "idle",
                "status_reason": "Creator project could not be loaded.",
                "approval_package": None,
            }

        work_items = self.creator_ops.list_work_items(
            latest_project.id,
            sort="updated_at",
        )
        work_item_index = {
            step_id: self._select_relevant_work_item(work_items, work_types)
            for step_id, _, work_types in PIPELINE_STEP_DEFINITIONS
        }
        latest_video_artifact = self._extract_latest_video_artifact(work_items)

        steps: list[dict[str, Any]] = []
        awaiting_approval = False
        approval_package = None
        has_error = False
        has_active = False

        for step_id, label, _ in PIPELINE_STEP_DEFINITIONS:
            work_item = work_item_index[step_id]
            step_payload = self._serialize_pipeline_step(step_id, label, work_item)
            if step_payload["state"] == "error":
                has_error = True
            if step_payload["state"] == "active":
                has_active = True
            if (
                step_id == "upload"
                and work_item is not None
                and work_item.approval_required
                and work_item.status in {"drafted", "needs_review", "approved"}
            ):
                awaiting_approval = work_item.status in {"drafted", "needs_review"}
                approval_package = self.creator_ops.get_work_item_approval_package(
                    work_item.id
                )
                if awaiting_approval:
                    step_payload["state"] = "active"
                    step_payload["preview_only"] = True
            steps.append(step_payload)

        status = "awaiting_human" if awaiting_approval else "idle"
        if has_error:
            status = "error"
        elif has_active:
            status = "executing"

        return {
            "preview_only": True,
            "job": {
                "creator_project_id": latest_project.id,
                "project_id": latest_project.project_id,
                "title": latest_project.name,
                "project_type": latest_project.project_type,
                "status": latest_project.status,
                "platforms": list(latest_project.platforms),
                "operating_mode": latest_project.operating_mode,
                "approval_policy": latest_project.approval_policy,
                "updated_at": latest_project.updated_at,
            },
            "steps": steps,
            "latest_video_artifact": latest_video_artifact,
            "awaiting_approval": awaiting_approval,
            "status": status,
            "status_reason": (
                "Publish preview requires explicit human approval."
                if awaiting_approval
                else "Pipeline is mapped from creator planning and preview packages."
            ),
            "approval_package": (
                {
                    "work_item": dict(approval_package.work_item),
                    "project": dict(approval_package.project),
                    "risk": approval_package.risk,
                    "safety_flags": list(approval_package.safety_flags),
                    "review_notes": approval_package.review_notes,
                    "proposed_next_action": approval_package.proposed_next_action,
                    "action_broker_payload_preview": dict(
                        approval_package.action_broker_payload_preview
                    ),
                }
                if approval_package is not None
                else None
            ),
        }

    def update_pipeline_approval(
        self,
        work_item_id: int,
        *,
        action: str,
        review_notes: str | None = None,
    ) -> dict[str, Any]:
        updated = self.creator_ops.update_work_item(
            work_item_id,
            review_action=action,
            review_notes=review_notes,
        )
        if updated is None:
            raise ValueError(f"Unknown work_item_id '{work_item_id}'.")
        pipeline = self.get_current_pipeline()
        pipeline["last_review_update"] = {
            "work_item_id": work_item_id,
            "status": updated.status,
            "review_notes": updated.review_notes,
        }
        return pipeline

    def build_agent_frames(
        self,
        *,
        include_live_health: bool = True,
        fast_mode: bool = False,
    ) -> list[dict[str, Any]]:
        runtime_snapshot = self.runtime.snapshot(
            include_live_health=include_live_health
        )
        pipeline = self.get_current_pipeline()
        trusted_auto = self.trusted_auto.dashboard_payload(fast_mode=fast_mode)
        remote_service = RemoteNodeService(self.session, self.base_settings)
        remote_workers = remote_service.list_nodes_payload()["remote_workers"]
        general_chat_models = [
            item
            for item in runtime_snapshot.models
            if item.task == "general_chat" and item.is_enabled
        ]
        selected_model = next(
            (
                item
                for item in general_chat_models
                if item.provider_key == "ollama-local" and item.status == "ready"
            ),
            None,
        ) or next(
            (item for item in general_chat_models if item.status == "ready"),
            None,
        ) or next(iter(general_chat_models), None)
        latest_hardware = trusted_auto.get("hardware_snapshot")
        if latest_hardware is None:
            hardware_guard = HardwareGuardService(
                self.session,
                self.base_settings,
            )
            if fast_mode:
                latest_snapshot = hardware_guard.latest()
                latest_hardware = (
                    latest_snapshot.to_dict() if latest_snapshot is not None else None
                )
            else:
                try:
                    latest_hardware = hardware_guard.capture(persist=False).to_dict()
                except Exception:
                    latest_hardware = None

        vram_total_mb = 16384
        vram_used_mb = 0
        if isinstance(latest_hardware, dict):
            raw_total_vram = latest_hardware.get("total_vram_mb")
            raw_used_vram = latest_hardware.get("used_vram_mb")
            raw_free_vram = latest_hardware.get("free_vram_mb")
            if raw_total_vram is not None:
                try:
                    vram_total_mb = max(1, int(raw_total_vram))
                except (TypeError, ValueError):
                    pass
            if raw_used_vram is not None:
                try:
                    vram_used_mb = max(0, int(raw_used_vram))
                except (TypeError, ValueError):
                    pass
            elif raw_total_vram is not None and raw_free_vram is not None:
                try:
                    vram_used_mb = max(0, int(raw_total_vram) - int(raw_free_vram))
                except (TypeError, ValueError):
                    pass

        stage = DEFAULT_AGENT_STAGE
        if pipeline["status"] == "awaiting_human":
            stage = "awaiting_human"
        elif pipeline["status"] == "executing" and not bool(pipeline.get("preview_only")):
            stage = "executing"
        elif pipeline["status"] == "error":
            stage = "error"

        messages: list[dict[str, Any]] = [
            {
                "type": "metrics",
                "payload": {
                    "vramUsedMB": vram_used_mb,
                    "vramTotalMB": vram_total_mb,
                    "model": {
                        "name": selected_model.runtime_model_name
                        if selected_model is not None and selected_model.runtime_model_name
                        else (
                            selected_model.resolved_runtime_model_name
                            if selected_model is not None
                            and selected_model.resolved_runtime_model_name
                            else (
                                selected_model.configured_runtime_model_name
                                if selected_model is not None
                                and selected_model.configured_runtime_model_name
                                else (
                                    selected_model.model_key
                                    if selected_model is not None
                                    else "unknown"
                                )
                            )
                        ),
                        "provider": (
                            "ollama"
                            if selected_model is not None
                            and selected_model.provider_key == "ollama-local"
                            else (
                                "anthropic"
                                if selected_model is not None
                                and selected_model.provider_key == "anthropic-cloud"
                                else (
                                    "openai"
                                    if selected_model is not None
                                    and selected_model.provider_key.startswith("openai")
                                    else (
                                        "gemini"
                                        if selected_model is not None
                                        and selected_model.provider_key.startswith("gemini")
                                        else "ollama"
                                    )
                                )
                            )
                        ),
                    },
                    "chroma": "down",
                    "network": "online",
                    "rttMs": None,
                },
            },
            {
                "type": "stage",
                "payload": {
                    "stage": stage,
                },
            },
        ]
        for step in pipeline["steps"]:
            messages.append(
                {
                    "type": "pipeline.step",
                    "payload": {
                        "jobId": pipeline["job"]["creator_project_id"]
                        if pipeline["job"] is not None
                        else "preview-only",
                        "stepId": step["id"],
                        "state": step["state"],
                        "progress": step["progress"],
                        "durationMs": step["duration_ms"],
                        "errorMessage": step["error_message"],
                    },
                }
            )
        if latest_hardware is not None:
            messages.append({"type": "hardware.snapshot", "payload": latest_hardware})
        for worker in remote_workers[:8]:
            messages.append({"type": "remote.node", "payload": worker})
            for run in worker.get("recent_runs", [])[:3]:
                messages.append({"type": "remote.execution.run", "payload": run})
        for job in trusted_auto.get("scheduler_jobs", [])[:8]:
            messages.append({"type": "scheduler.job", "payload": job})
        for run in trusted_auto.get("execution_runs", [])[:8]:
            messages.append({"type": "execution.run", "payload": run})
        execution_runs = trusted_auto.get("execution_runs", [])
        latest_run = next(iter(execution_runs), None)
        active_graph_run = next(
            (
                run
                for run in execution_runs
                if isinstance(run, dict) and run.get("status") in {"running", "retrying"}
            ),
            None,
        )
        if isinstance(active_graph_run, dict):
            steps = active_graph_run.get("steps", [])
            if isinstance(steps, list) and steps:
                current_step = next(
                    (
                        step
                        for step in reversed(steps)
                        if isinstance(step, dict) and step.get("status") == "running"
                    ),
                    None,
                ) or next(
                    (step for step in reversed(steps) if isinstance(step, dict)),
                    None,
                )
                if isinstance(current_step, dict):
                    completed_steps = len(
                        [
                            step
                            for step in steps
                            if isinstance(step, dict) and step.get("status") == "done"
                        ]
                    )
                    messages.append(
                        {
                            "type": "graph.node",
                            "payload": {
                                "execution_run_id": active_graph_run.get("execution_run_id"),
                                "run_type": active_graph_run.get("run_type"),
                                "status": active_graph_run.get("status"),
                                "step_type": current_step.get("step_type"),
                                "step_name": current_step.get("step_name"),
                            },
                        }
                    )
                    messages.append(
                        {
                            "type": "graph.progress",
                            "payload": {
                                "execution_run_id": active_graph_run.get("execution_run_id"),
                                "run_type": active_graph_run.get("run_type"),
                                "status": active_graph_run.get("status"),
                                "current_step": current_step.get("step_name"),
                                "completed_steps": completed_steps,
                                "total_steps": len(steps),
                            },
                        }
                    )
        if isinstance(latest_run, dict):
            artifacts = latest_run.get("artifacts_json")
            if isinstance(artifacts, dict):
                for artifact_key, artifact_value in artifacts.items():
                    messages.append(
                        {
                            "type": "artifact.ready",
                            "payload": {
                                "execution_run_id": latest_run.get("execution_run_id"),
                                "kind": artifact_key,
                                "artifact": artifact_value,
                            },
                        }
                    )
            output_json = latest_run.get("output_json")
            if isinstance(output_json, dict):
                approval_package = output_json.get("approval_package")
                approval_required = bool(output_json.get("approval_required"))
                if approval_required and isinstance(approval_package, dict):
                    messages.append(
                        {
                            "type": "approval.required",
                            "payload": {
                                "execution_run_id": latest_run.get("execution_run_id"),
                                "run_type": latest_run.get("run_type"),
                                "approval_package": approval_package,
                            },
                        }
                    )
                publish_result = output_json.get("publish_result")
                if isinstance(publish_result, dict):
                    messages.append({"type": "publish.result", "payload": publish_result})
                analytics_summary = output_json.get("analytics_summary")
                if isinstance(analytics_summary, dict):
                    messages.append({"type": "analytics.summary", "payload": analytics_summary})
                fallback_used = output_json.get("fallback_used")
                if isinstance(fallback_used, dict):
                    messages.append({"type": "fallback.used", "payload": fallback_used})
            if latest_run.get("status") == "failed" and self._should_emit_run_failure_alert(latest_run):
                messages.append(
                    {
                        "type": "alert",
                        "payload": {
                            "severity": "error",
                            "title": self._run_failure_alert_title(latest_run),
                            "message": self._run_failure_alert_message(latest_run),
                        },
                    }
                )
        messages.append(
            {
                "type": "connector.state",
                "payload": {
                    "autonomy_mode": trusted_auto["runtime_controls"]["autonomy_mode"],
                    "scheduler_enabled": trusted_auto["runtime_controls"]["scheduler_enabled"],
                    "emergency_stop_enabled": trusted_auto["runtime_controls"]["emergency_stop_enabled"],
                    "pause_all_jobs": trusted_auto["runtime_controls"]["pause_all_jobs"],
                    "browser_runtime": trusted_auto.get("browser_runtime"),
                },
            }
        )
        if isinstance(trusted_auto.get("content_readiness"), dict):
            messages.append(
                {
                    "type": "content.readiness",
                    "payload": trusted_auto["content_readiness"],
                }
            )
        return messages

    @staticmethod
    def _should_emit_run_failure_alert(run: dict[str, Any]) -> bool:
        finished_at = UiAdapterService._parse_runtime_datetime(run.get("finished_at"))
        if finished_at is None:
            return True
        return (datetime.now(UTC) - finished_at).total_seconds() <= 180

    @staticmethod
    def _run_failure_alert_title(run: dict[str, Any]) -> str:
        message = str(run.get("error_message") or run.get("goal") or "").casefold()
        if "vram" in message or "gpu" in message:
            return "VRAM blokuje workflow"
        return "Workflow zatrzymany"

    @staticmethod
    def _run_failure_alert_message(run: dict[str, Any]) -> str:
        message = str(run.get("error_message") or run.get("goal") or "Workflow zatrzymal sie bez dodatkowej diagnozy.")
        lowered = message.casefold()
        if "vram" in lowered or "gpu" in lowered:
            return (
                f"{message} Przy kolejnym uruchomieniu Atnos najpierw sprobuje automatycznie zwolnic GPU."
            )
        return message

    @staticmethod
    def _parse_runtime_datetime(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _list_recent_conversations(self, *, limit: int) -> list[dict[str, Any]]:
        statement = (
            select(Conversation)
            .where(Conversation.status == ConversationStatus.ACTIVE)
            .order_by(
                Conversation.last_message_at.desc().nullslast(),
                Conversation.updated_at.desc(),
                Conversation.id.desc(),
            )
            .limit(limit)
        )
        conversations = list(self.session.scalars(statement))
        return [
            {
                "id": item.id,
                "title": item.title,
                "project_id": item.project_id,
                "summary": item.summary,
                "last_message_at": as_utc(item.last_message_at).isoformat()
                if item.last_message_at is not None
                else None,
                "updated_at": as_utc(item.updated_at).isoformat(),
            }
            for item in conversations
        ]

    def _serialize_project(self, project) -> dict[str, Any]:
        return {
            "id": project.id,
            "name": project.name,
            "slug": project.slug,
            "description": project.description,
            "summary": project.summary,
            "status": project.status.value,
            "is_pinned": project.is_pinned,
            "provider_posture": project.provider_posture.value
            if project.provider_posture is not None
            else None,
            "persona_profile": project.persona_profile.value
            if project.persona_profile is not None
            else None,
        }

    def _build_provider_card(
        self,
        definition: ProviderCardDefinition,
        *,
        runtime_provider=None,
    ) -> dict[str, Any]:
        provider_row = self._get_provider_row(definition)
        account_view = self._get_provider_card_account(definition)
        metadata = self._provider_card_metadata(
            definition,
            provider_row=provider_row,
            account_view=account_view,
        )
        base_url = (
            str(metadata.get("base_url")).strip()
            if metadata.get("base_url")
            else None
        )
        if not base_url and provider_row is not None and provider_row.endpoint_url:
            base_url = provider_row.endpoint_url
        if not base_url:
            base_url = definition.default_base_url or (
                self.settings.ollama_base_url if definition.ui_key == "ollama" else None
            )

        runtime_item = runtime_provider
        runtime_adapter_available = runtime_item is not None and runtime_item.has_runtime_adapter
        provider_enabled = provider_row.is_enabled if provider_row is not None else True
        resolved_secret = (
            self.account_service.resolve_secret_for_adapter(account_view.id)
            if account_view is not None and account_view.has_secret
            else None
        )
        runtime_secret = self._runtime_env_secret(definition)
        secret_for_sanity = resolved_secret or runtime_secret
        runtime_secret_configured = bool(runtime_secret)
        if definition.ui_key == "ollama":
            if runtime_item is None:
                status = "disconnected"
                status_reason = "Ollama provider is not registered in this runtime."
            elif runtime_item.health_status == "healthy":
                status = "connected"
                status_reason = runtime_item.health_reason
            elif provider_enabled:
                status = "configured"
                status_reason = runtime_item.health_reason
            else:
                status = "disabled"
                status_reason = "Ollama provider is disabled."
        else:
            if account_view is None:
                if runtime_secret_configured:
                    status = "configured"
                    status_reason = "Runtime environment secret is configured and ready for explicit test."
                else:
                    status = "disconnected"
                    status_reason = "No secret or metadata has been configured yet."
            elif account_view.status == "disabled":
                status = "disabled"
                status_reason = "Provider card is disabled."
            elif account_view.has_secret:
                status = "configured"
                status_reason = "Secret is stored and ready for explicit test."
            else:
                status = "metadata_only"
                status_reason = "Only metadata is configured. Add a secret to enable live tests."

            if runtime_item is not None and runtime_item.health_status == "healthy":
                status = "connected"
                status_reason = runtime_item.health_reason

        sanity_status, sanity_reason = self._provider_secret_sanity(
            definition.ui_key,
            secret_for_sanity,
        )
        readiness_status, readiness_reason = self._provider_readiness_state(
            provider_key=definition.ui_key,
            status=status,
            runtime_item=runtime_item,
            provider_enabled=provider_enabled,
            sanity_status=sanity_status,
            sanity_reason=sanity_reason,
        )

        return {
            "provider_key": definition.ui_key,
            "display_name": definition.display_name,
            "status": status,
            "status_reason": status_reason,
            "supports_api_key": definition.supports_api_key,
            "supports_base_url": definition.supports_base_url,
            "provider_enabled": provider_enabled,
            "base_url": base_url,
            "account_id": account_view.id if account_view is not None else None,
            "has_secret": (account_view.has_secret if account_view is not None else False) or runtime_secret_configured,
            "masked_secret_state": account_view.masked_secret_state
            if account_view is not None
            else ("Runtime environment secret configured" if runtime_secret_configured else "Not configured"),
            "runtime_adapter_available": runtime_adapter_available,
            "runtime_health_status": runtime_item.health_status if runtime_item is not None else "unknown",
            "runtime_health_reason": runtime_item.health_reason if runtime_item is not None else "No runtime adapter.",
            "sanity_status": sanity_status,
            "sanity_reason": sanity_reason,
            "readiness_status": readiness_status,
            "readiness_reason": readiness_reason,
            "oauth_connector_key": definition.oauth_connector_key,
        }

    def _provider_test_response_from_health(
        self,
        definition: ProviderCardDefinition,
        health: ProviderHealthCheck,
    ) -> dict[str, Any]:
        connection_state = "connected" if health.status == "healthy" else "error"
        return {
            "provider_key": definition.ui_key,
            "status": connection_state,
            "connection_state": connection_state,
            "detail": health.status_reason,
            "message": health.status_reason,
            "health_status": health.status,
            "latency_ms": health.latency_ms,
            "installed_models": list(health.installed_models),
            "reset_in_sec": None,
            "tested_at": self._now().isoformat(),
        }

    def _provider_test_response(
        self,
        definition: ProviderCardDefinition,
        *,
        status: str,
        detail: str,
        latency_ms: int | None = None,
        reset_in_sec: int | None = None,
    ) -> dict[str, Any]:
        return {
            "provider_key": definition.ui_key,
            "status": status,
            "connection_state": status,
            "detail": detail,
            "message": detail,
            "health_status": "healthy" if status == "connected" else "unknown",
            "latency_ms": latency_ms,
            "installed_models": [],
            "reset_in_sec": reset_in_sec,
            "tested_at": self._now().isoformat(),
        }

    @staticmethod
    def _provider_secret_sanity(provider_key: str, secret_value: str | None) -> tuple[str, str]:
        if not secret_value:
            return "unknown", "Brak sekretu do weryfikacji."
        normalized = secret_value.strip()
        if provider_key == "openai":
            if normalized.startswith("sk-ant-"):
                return "warning", "Sekret wyglada jak klucz Anthropic, a nie OpenAI."
            if not normalized.startswith("sk-"):
                return "warning", "Format klucza nie wyglada jak typowy sekret OpenAI."
        if provider_key == "anthropic":
            if not normalized.startswith("sk-ant-"):
                return "warning", "Format klucza nie wyglada jak typowy sekret Anthropic."
        return "ok", "Format sekretu wyglada spojnie z tym providerem."

    def _runtime_env_secret(self, definition: ProviderCardDefinition) -> str | None:
        if definition.ui_key == "gemini":
            value = self.settings.gemini_api_key.strip()
            return value or None
        return None

    @staticmethod
    def _provider_readiness_state(
        *,
        provider_key: str,
        status: str,
        runtime_item,
        provider_enabled: bool,
        sanity_status: str,
        sanity_reason: str,
    ) -> tuple[str, str]:
        if not provider_enabled or status == "disabled":
            return "blocked", "Provider jest wylaczony w runtime."
        if sanity_status == "warning":
            return "blocked", sanity_reason
        if provider_key == "ollama":
            if runtime_item is not None and runtime_item.health_status == "healthy":
                return "ready", "Lokalny runtime odpowiada i moze wejsc do routingu."
            if status in {"configured", "connected"}:
                return "fallback_only", "Konfiguracja jest zapisana, ale runtime nie potwierdzil zdrowia."
            return "blocked", "Lokalny runtime nie jest gotowy."
        if status == "connected":
            return "ready", "Provider przeszedl probe i jest gotowy do routingu."
        if status == "configured":
            return "configured", "Sekret jest zapisany, ale provider nie przeszedl jeszcze live probe."
        if status == "metadata_only":
            return "fallback_only", "Masz tylko metadane. Provider nie ma jeszcze sekretu do wykonania."
        return "blocked", "Provider nie jest gotowy do bezpiecznego uzycia."

    def _probe_anthropic(
        self,
        definition: ProviderCardDefinition,
        base_url: str,
        secret_value: str,
    ) -> dict[str, Any]:
        started_at = self._now()
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    f"{base_url}/v1/models",
                    headers={
                        "x-api-key": secret_value,
                        "anthropic-version": "2023-06-01",
                    },
                )
        except httpx.HTTPError as exc:
            return self._provider_test_response(
                definition,
                status="error",
                detail=f"Anthropic probe failed: {exc}",
            )

        latency_ms = max(
            1,
            int((self._now() - started_at).total_seconds() * 1000),
        )
        if response.status_code >= 400:
            reset_in_sec = self._retry_after_seconds(response)
            detail = (
                "Anthropic returned 429. This often means missing credits or a quota/rate-limit block."
                if response.status_code == 429
                else f"Anthropic returned {response.status_code}."
            )
            return self._provider_test_response(
                definition,
                status="rate_limited" if response.status_code == 429 else "error",
                detail=detail,
                latency_ms=latency_ms,
                reset_in_sec=reset_in_sec,
            )
        return self._provider_test_response(
            definition,
            status="connected",
            detail="Anthropic responded to /v1/models.",
            latency_ms=latency_ms,
        )

    def _probe_openai_compatible(
        self,
        definition: ProviderCardDefinition,
        base_url: str,
        secret_value: str,
    ) -> dict[str, Any]:
        started_at = self._now()
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    f"{base_url}/v1/models",
                    headers={"Authorization": f"Bearer {secret_value}"},
                )
        except httpx.HTTPError as exc:
            return self._provider_test_response(
                definition,
                status="error",
                detail=f"{definition.display_name} probe failed: {exc}",
            )

        latency_ms = max(
            1,
            int((self._now() - started_at).total_seconds() * 1000),
        )
        if response.status_code >= 400:
            reset_in_sec = self._retry_after_seconds(response)
            detail = (
                f"{definition.display_name} returned 429. This often means missing credits or a quota/rate-limit block."
                if response.status_code == 429
                else f"{definition.display_name} returned {response.status_code}."
            )
            return self._provider_test_response(
                definition,
                status="rate_limited" if response.status_code == 429 else "error",
                detail=detail,
                latency_ms=latency_ms,
                reset_in_sec=reset_in_sec,
            )
        return self._provider_test_response(
            definition,
            status="connected",
            detail=f"{definition.display_name} responded to /v1/models.",
            latency_ms=latency_ms,
        )

    def _probe_gemini(
        self,
        definition: ProviderCardDefinition,
        base_url: str,
        secret_value: str,
    ) -> dict[str, Any]:
        started_at = self._now()
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    f"{base_url}/v1beta/models",
                    params={"key": secret_value},
                )
        except httpx.HTTPError as exc:
            return self._provider_test_response(
                definition,
                status="error",
                detail=f"Gemini probe failed: {exc}",
            )

        latency_ms = max(
            1,
            int((self._now() - started_at).total_seconds() * 1000),
        )
        if response.status_code >= 400:
            reset_in_sec = self._retry_after_seconds(response)
            detail = (
                "Gemini returned 429. This often means missing credits or a quota/rate-limit block."
                if response.status_code == 429
                else f"Gemini returned {response.status_code}."
            )
            return self._provider_test_response(
                definition,
                status="rate_limited" if response.status_code == 429 else "error",
                detail=detail,
                latency_ms=latency_ms,
                reset_in_sec=reset_in_sec,
            )
        return self._provider_test_response(
            definition,
            status="connected",
            detail="Gemini responded to /v1beta/models.",
            latency_ms=latency_ms,
        )

    @staticmethod
    def _retry_after_seconds(response) -> int | None:
        raw_retry_after = response.headers.get("retry-after")
        if raw_retry_after is None:
            return None
        try:
            seconds = int(raw_retry_after)
        except ValueError:
            return None
        return seconds if seconds >= 0 else None

    def _select_relevant_work_item(self, items, work_types: tuple[str, ...]):
        for item in items:
            if item.work_type in work_types:
                return item
        return None

    def _serialize_pipeline_step(self, step_id: str, label: str, work_item) -> dict[str, Any]:
        if work_item is None:
            return self._empty_pipeline_step(step_id, label)

        state = "pending"
        progress = 0.0
        error_message = None
        if work_item.status == "blocked":
            state = "error"
            error_message = work_item.review_notes or "This preview item is blocked."
        elif work_item.status in {"drafted", "needs_review"}:
            state = "active"
            progress = 0.6 if work_item.status == "needs_review" else 0.35
        elif work_item.status in {"approved", "done"}:
            state = "done"
            progress = 1.0
        elif work_item.status == "backlog":
            state = "pending"

        return {
            "id": step_id,
            "label": label,
            "state": state,
            "progress": progress,
            "duration_ms": None,
            "error_message": error_message,
            "preview_only": step_id == "upload",
            "work_item_id": work_item.id,
            "work_type": work_item.work_type,
            "review_notes": work_item.review_notes,
        }

    @staticmethod
    def _empty_pipeline_step(step_id: str, label: str) -> dict[str, Any]:
        return {
            "id": step_id,
            "label": label,
            "state": "pending",
            "progress": 0.0,
            "duration_ms": None,
            "error_message": None,
            "preview_only": step_id == "upload",
            "work_item_id": None,
            "work_type": None,
            "review_notes": None,
        }

    def _extract_latest_video_artifact(self, work_items) -> dict[str, Any] | None:
        for work_item in work_items:
            candidate = self._video_artifact_from_payload(
                getattr(work_item, "draft_payload", None),
                fallback_title=getattr(work_item, "title", None),
                updated_at=getattr(work_item, "updated_at", None),
            )
            if candidate is not None:
                return candidate
        return None

    def _video_artifact_from_payload(
        self,
        payload: dict[str, Any] | None,
        *,
        fallback_title: str | None,
        updated_at: str | None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        storage_path = self._find_first_string(
            payload,
            (
                "storage_path",
                "video_storage_path",
                "asset_storage_path",
                "output_path",
                "video_path",
                "file_path",
            ),
        )
        external_url = self._find_first_string(
            payload,
            (
                "external_url",
                "video_url",
                "youtube_url",
                "share_url",
                "watch_url",
                "url",
            ),
        )
        title = self._find_first_string(
            payload,
            (
                "title",
                "video_title",
                "asset_title",
                "name",
                "headline",
            ),
        ) or fallback_title or "Ostatnie wideo"

        if storage_path is None and external_url is None:
            return None

        return {
            "title": title,
            "storage_path": storage_path,
            "external_url": external_url,
            "updated_at": updated_at,
        }

    def _find_first_string(
        self,
        payload: dict[str, Any],
        keys: tuple[str, ...],
    ) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for value in payload.values():
            if isinstance(value, dict):
                nested = self._find_first_string(value, keys)
                if nested is not None:
                    return nested
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        nested = self._find_first_string(item, keys)
                        if nested is not None:
                            return nested
        return None

    def _provider_card_definition(self, provider_key: str) -> ProviderCardDefinition:
        for definition in PROVIDER_CARD_DEFINITIONS:
            if definition.ui_key == provider_key:
                return definition
        raise ValueError(f"Unknown provider card '{provider_key}'.")

    def _get_provider_row(self, definition: ProviderCardDefinition) -> Provider | None:
        if definition.provider_row_key is None:
            return None
        return self.providers.get_by_key(definition.provider_row_key)

    def _get_provider_card_account(self, definition: ProviderCardDefinition):
        if definition.credential_provider_key is None:
            return None
        account = self.account_repository.find_by_provider_and_identifier(
            provider_key=definition.credential_provider_key,
            account_identifier=DEFAULT_PROVIDER_CARD_IDENTIFIER,
        )
        if account is None:
            return None
        return self.account_service.get_account(account.id)

    def _provider_card_metadata(
        self,
        definition: ProviderCardDefinition,
        *,
        provider_row: Provider | None,
        account_view,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if account_view is not None:
            repository_model = self.account_repository.get_by_id(account_view.id)
            metadata = self._parse_provider_card_notes(
                repository_model.notes if repository_model is not None else None
            )
        if not metadata.get("base_url"):
            if provider_row is not None and provider_row.endpoint_url:
                metadata["base_url"] = provider_row.endpoint_url
            elif definition.ui_key == "ollama":
                metadata["base_url"] = self.settings.ollama_base_url
            elif definition.default_base_url is not None:
                metadata["base_url"] = definition.default_base_url
        return metadata

    @staticmethod
    def _parse_provider_card_notes(notes: str | None) -> dict[str, Any]:
        if notes is None:
            return {}
        normalized = notes.strip()
        if not normalized.startswith(PROVIDER_CARD_NOTE_PREFIX):
            return {}
        raw_payload = normalized[len(PROVIDER_CARD_NOTE_PREFIX) :]
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _render_provider_card_notes(metadata: dict[str, Any]) -> str:
        return f"{PROVIDER_CARD_NOTE_PREFIX}{json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))}"

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
