from __future__ import annotations
from atnos.llm.base import LLMRunCancelled

import asyncio
from dataclasses import dataclass, replace
import json
import logging
import re
from time import perf_counter
from typing import Callable
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from atnos.agentic import AgentContext, AgentResult, OrchestratorAgent
from atnos.agentic.orchestrator import ToolCallOutcome
from atnos.core.config import Settings
from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import (
    AttachmentKind,
    AttachmentSource,
    AttachmentStatus,
    ExecutionTraceKind,
    ExecutionTraceStatus,
    MessageRole,
    MessageStatus,
    ModelTask,
    ProviderPosture,
)
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.db.models.message import Message
from atnos.plugins.registry import PluginRegistry
from atnos.tools.chat_agent_tools import (
    build_chat_agent_registry,
    build_chat_agent_system_prompt,
)
from atnos.tools.chat_approval_store import ChatApprovalStore
from atnos.tools.chat_hard_stop import looks_like_confirmation
from atnos.tools.executor import ToolExecutor
from atnos.tools.registry import ToolRegistry
from atnos.llm.base import ToolCall
from atnos.llm.ollama import OllamaChatBackend
from atnos.llm.router import LLMRouter, PrivacyMode, TaskType
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.execution_trace_repository import ExecutionTraceRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.user_repository import UserRepository
from atnos.repositories.voice_profile_repository import VoiceProfileRepository
from atnos.providers.base import (
    LocalModelFailureDetail,
    LocalProviderRuntimeError,
    ProviderExecutionResult,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from atnos.services.context_builder_service import ContextBuilderService
from atnos.services.context_builder_service import SelectedReviewNoteContext
from atnos.services.conversation_settings_resolver_service import (
    ConversationSettingsResolverService,
)
from atnos.services.conversation_service import (
    ActiveDocumentAction,
    ConversationService,
    ConversationTurnResult,
    ConversationTurnRequest,
    ExecutionPreferences,
)
from atnos.services.document_action_service import DocumentActionResult
from atnos.services.intent_service import ChatIntent, IntentDetectionService
from atnos.services.capability_action_service import CapabilityActionError, CapabilityActionService
from atnos.services.access_reality_service import AccessRealityService
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.model_selection_service import (
    ExternalConfirmationRequiredError,
    ModelSelectionError,
    ModelSelectionResult,
    ModelSelectionService,
)
from atnos.services.runtime_context_preferences import RuntimeContextPreferences
from atnos.services.runtime_inventory_service import RuntimeInventoryService
from atnos.services.pc_automation_service import PCAutomationService
from atnos.services.decision_log_service import DecisionContradiction, DecisionLogService
from atnos.services.knowledge_library_service import KnowledgeLibraryService
from atnos.services.prompt_optimizer_service import PromptOptimizerService
from atnos.services.browser_chat_operator_service import BrowserChatOperatorService
from atnos.services.browser_access_diagnostics_service import BrowserAccessDiagnosticsService
from atnos.services.channel_ops_chat_service import ChannelOpsChatService
from atnos.services.creative_asset_operator_service import CreativeAssetOperatorService
from atnos.services.creative_video_operator_service import CreativeVideoOperatorService
from atnos.services.desktop_chat_operator_service import DesktopChatOperatorService
from atnos.services.external_llm_operator_service import ExternalLlmOperatorService
from atnos.services.local_host_control_service import LocalHostControlService
from atnos.services.quick_chat_operator_service import QuickChatOperatorService
from atnos.services.chat_tool_focus import natural_tool_focus, format_background_status, direct_read_request, format_direct_read, memory_opt_out, named_note_source, named_note_sources
from atnos.services.chat_tool_focus import ground_file_first_line
from atnos.services.chat_tool_focus import format_obsidian_read, obsidian_read_complete, requests_note_comparison
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.self_improvement_notes_service import SelfImprovementNotesService
from atnos.services.summary_writeback_service import SummaryWritebackService
from atnos.services.task_extraction_service import DraftTask, TaskExtractionService
from atnos.services.voice_synthesis_service import VoiceSynthesisError, VoiceSynthesisService
from atnos.skills.logging import log_skill_decision
from atnos.skills.models import SkillRouteDecision
from atnos.skills.router import route_task
from atnos.skills.skill_prompt import load_skill_prompt_context
from atnos.skills.youtube_skill import (
    build_youtube_upload_preview,
    detects_youtube_private_upload_request,
    detects_youtube_upload_confirmation,
    execute_youtube_upload,
    format_upload_preview,
    format_upload_result,
)
from agent.ubuntu_agent_adapter import UbuntuAgentAdapter

logger = logging.getLogger(__name__)


class ChatOrchestrationError(RuntimeError):
    pass


class ChatOrchestrationProviderError(ChatOrchestrationError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True, frozen=True)
class ChatOrchestrationResult:
    conversation: Conversation
    user_message: Message
    assistant_message: Message
    intent: ChatIntent
    selected_model_key: str
    selected_provider_key: str
    routing_reason: str
    draft_tasks: tuple[DraftTask, ...]
    traces: tuple[ExecutionTrace, ...]
    runtime_error: LocalModelFailureDetail | None = None
    skill_decision: SkillRouteDecision | None = None


@dataclass(slots=True, frozen=True)
class AutoExternalRoutingPlan:
    target: str
    task_type: str
    mode_preference: str | None
    reason: str
    fallback_policy: str = "normal"


@dataclass(slots=True, frozen=True)
class ProviderExecutionAttempt:
    selection: ModelSelectionResult
    provider_context: object
    draft_tasks: tuple[DraftTask, ...]
    provider_trace_input_summary: str
    trace_metadata: dict
    execution_started: float


class QuestionOption(BaseModel):
    id: str = Field(..., min_length=1, max_length=20)
    label: str = Field(..., min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=160)


class StructuredQuestion(BaseModel):
    question_id: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=3)
    options: list[QuestionOption] = Field(..., min_length=2, max_length=6)
    multi_select: bool = False
    required: bool = True
    helper_text: str | None = Field(default=None, max_length=240)

    @field_validator("options")
    @classmethod
    def unique_option_ids(cls, options: list[QuestionOption]) -> list[QuestionOption]:
        ids = [option.id for option in options]
        if len(ids) != len(set(ids)):
            raise ValueError("Option ids must be unique")
        return options


class ChatOrchestrationService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry,
        summary_writeback_factory: type[SummaryWritebackService] = SummaryWritebackService,
        ambient_context_service=None,
        scheduler_runtime_service=None,
    ) -> None:
        self.session = session
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry
        self.settings = settings
        self.conversation_service = ConversationService(session, settings=settings)
        self.model_selection = ModelSelectionService(
            session,
            provider_registry=provider_registry,
            settings=settings,
        )
        self.llm_router = LLMRouter(settings)
        self.intent_detection = IntentDetectionService()
        self.context_builder = ContextBuilderService()
        self.pc_automation = PCAutomationService(session, settings=settings)
        self.summary_writeback = summary_writeback_factory(session)
        self.conversation_settings = ConversationSettingsResolverService(session)
        self.task_extraction = TaskExtractionService()
        self.runtime_settings = RuntimeSettingsService(session, settings)
        self.account_credentials = AccountCredentialsService(session, settings)
        self.users = UserRepository(session)
        self.attachments = AttachmentRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.messages = MessageRepository(session)
        self.execution_traces = ExecutionTraceRepository(session)
        self.voice_profiles = VoiceProfileRepository(session)
        self.decisions = DecisionLogService(
            session,
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
        )
        self.capability_actions = CapabilityActionService(session, settings)
        self.voice_synthesis = VoiceSynthesisService(settings)
        self.knowledge = KnowledgeLibraryService(settings)
        self.prompt_optimizer = PromptOptimizerService()
        self.browser_operator = BrowserChatOperatorService(
            session,
            settings=settings,
            provider_registry=provider_registry,
        )
        self.external_llm_operator = ExternalLlmOperatorService(
            session,
            settings=settings,
            provider_registry=provider_registry,
        )
        self.channel_ops_chat = ChannelOpsChatService(settings)
        self.creative_video_operator = CreativeVideoOperatorService(
            session,
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
        )
        self.creative_asset_operator = CreativeAssetOperatorService(
            settings,
            account_credentials=self.account_credentials,
        )
        self.desktop_operator = DesktopChatOperatorService(
            session,
            settings=settings,
            provider_registry=provider_registry,
        )
        self.quick_chat_operator = QuickChatOperatorService()
        self.ambient_context_service = ambient_context_service
        self.scheduler_runtime_service = scheduler_runtime_service

    def handle_turn(
        self,
        request: ConversationTurnRequest,
        *,
        stream_event: Callable[[dict[str, object]], None] | None = None,
    ) -> ChatOrchestrationResult:
        owner = self.users.get_owner()
        if owner is None:
            raise ChatOrchestrationError("Owner user is not configured.")

        execution_policy = self.execution_policies.get_for_user(owner.id)
        if execution_policy is None:
            raise ChatOrchestrationError("Execution policy is not configured for the owner user.")
        runtime_controls = self.runtime_settings.mark_user_interaction()

        if stream_event is not None:
            try:
                stream_event(
                    {
                        "type": "status",
                        "stage": "thinking",
                        "title": "Atnos myśli",
                        "detail": "Przyjmuję polecenie…",
                    }
                )
            except Exception:
                pass

        pre_resolved_settings = (
            self.conversation_settings.resolve(conversation_id=request.conversation_id)
            if request.conversation_id is not None
            else None
        )
        effective_request = self._apply_conversation_toggles_to_request(
            request,
            pre_resolved_settings=pre_resolved_settings,
        )
        intent = self.intent_detection.detect(
            content=effective_request.content,
            attachments=effective_request.attachments,
        )
        if effective_request.active_document_action is not None:
            intent = replace(
                intent,
                document_action=DocumentActionResult(
                    action=effective_request.active_document_action.action,
                    source="active_document_action",
                    reason="Explicit active document action provided by the chat client.",
                ),
            )
        conversation_result = self.conversation_service.process_user_turn(effective_request)
        if stream_event is not None:
            # Persist the accepted turn before model work. A disconnect must not
            # lose its identity or cause the next message to create another chat.
            self.session.commit()
            stream_event({
                "type": "conversation",
                "conversation_id": conversation_result.conversation.id,
                "conversation_title": conversation_result.conversation.title,
                "user_message_id": conversation_result.user_message.id,
            })
        return self._complete_turn(
            request=effective_request,
            original_request=request,
            conversation_result=conversation_result,
            owner_id=owner.id,
            execution_policy=execution_policy,
            runtime_controls=runtime_controls,
            intent=intent,
            stream_event=stream_event,
        )

    def continue_with_answer(
        self,
        *,
        conversation_id: int,
        answered_question_id: str,
        selected_ids: list[str],
    ) -> Message:
        owner = self.users.get_owner()
        if owner is None:
            raise ChatOrchestrationError("Owner user is not configured.")

        execution_policy = self.execution_policies.get_for_user(owner.id)
        if execution_policy is None:
            raise ChatOrchestrationError("Execution policy is not configured for the owner user.")
        runtime_controls = self.runtime_settings.mark_user_interaction()

        conversation = self.conversation_service.conversations.get(conversation_id)
        if conversation is None:
            raise ChatOrchestrationError(f"Conversation {conversation_id} does not exist.")

        messages = self.messages.list_for_conversation(conversation_id)
        question_message = self._find_structured_question_message(
            messages=messages,
            question_id=answered_question_id,
        )
        if question_message is None:
            raise ChatOrchestrationError(
                f"Structured question '{answered_question_id}' does not exist in conversation {conversation_id}."
            )

        answer_message = self._find_choice_response_message(
            messages=messages,
            question_id=answered_question_id,
            selected_ids=selected_ids,
        )
        if answer_message is None:
            raise ChatOrchestrationError(
                f"Choice response for question '{answered_question_id}' does not exist in conversation {conversation_id}."
            )

        question_metadata = dict(question_message.metadata_json or {})
        answer_request = ConversationTurnRequest(
            content=answer_message.content,
            conversation_id=conversation_id,
            project_id=conversation.project_id,
            role_profile_id=conversation.role_profile_id,
            message_metadata_json=answer_message.metadata_json,
        )
        intent = self.intent_detection.detect(
            content=answer_request.content,
            attachments=answer_request.attachments,
        )
        traces = self._create_follow_up_traces(
            conversation=conversation,
            user_message=answer_message,
            selected_ids=selected_ids,
        )
        result = self._complete_turn(
            request=answer_request,
            original_request=answer_request,
            conversation_result=ConversationTurnResult(
                conversation=conversation,
                user_message=answer_message,
                traces=traces,
            ),
            owner_id=owner.id,
            execution_policy=execution_policy,
            runtime_controls=runtime_controls,
            intent=intent,
            extra_system_sections=(
                self._build_answered_question_context_section(
                    question_metadata=question_metadata,
                    selected_ids=selected_ids,
                ),
            ),
        )
        return result.assistant_message

    def _complete_turn(
        self,
        *,
        request: ConversationTurnRequest,
        original_request: ConversationTurnRequest,
        conversation_result: ConversationTurnResult,
        owner_id: int,
        execution_policy,
        runtime_controls,
        intent,
        extra_system_sections: tuple[str, ...] = (),
        stream_event: Callable[[dict[str, object]], None] | None = None,
    ) -> ChatOrchestrationResult:
        conversation_settings = self._apply_request_execution_overrides(
            self.conversation_settings.resolve(conversation=conversation_result.conversation),
            request_execution_preferences=original_request.execution_preferences,
        )
        runtime_preferences = self._build_runtime_context_preferences(
            conversation=conversation_result.conversation,
            conversation_settings=conversation_settings,
            request_execution_preferences=original_request.execution_preferences,
        )
        if (
            not original_request.force_external
            and not request.attachments
            and request.active_document_action is None
        ):
            early_quick_chat = self.quick_chat_operator.maybe_handle(content=request.content)
            if early_quick_chat.activated and early_quick_chat.assistant_content is not None:
                skill_decision = self._build_quick_chat_skill_decision(request.content)
                skill_trace = self.execution_traces.create(
                    step_name="skill-routing",
                    trace_kind=ExecutionTraceKind.INTENT,
                    status=ExecutionTraceStatus.SKIPPED,
                    conversation_id=conversation_result.conversation.id,
                    message_id=conversation_result.user_message.id,
                    input_summary=f"quick_chat_fast_path task_chars={len(request.content)}",
                    output_summary="Skipped full skill routing for deterministic local quick-chat.",
                    metadata_json={
                        "quick_chat_fast_path": True,
                        "skill_decision": self._serialize_skill_decision(skill_decision),
                    },
                )
                return self._build_quick_chat_message_result(
                    conversation_result=conversation_result,
                    content=early_quick_chat.assistant_content,
                    metadata_json={
                        **(early_quick_chat.metadata or {}),
                        "quick_chat_fast_path": True,
                    },
                    execution_policy=execution_policy,
                    skill_decision=skill_decision,
                    skill_trace=skill_trace,
                )
        contradiction = self.decisions.detect_contradiction(
            project_id=conversation_result.conversation.project_id,
            user_message=request.content,
        )
        skill_decision = route_task(self._build_skill_routing_task_text(request))
        log_skill_decision(skill_decision)
        skill_trace = self.execution_traces.create(
            step_name="skill-routing",
            trace_kind=ExecutionTraceKind.INTENT,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=conversation_result.user_message.id,
            input_summary=f"task_chars={len(request.content)} attachments={len(request.attachments)}",
            output_summary=", ".join(skill_decision.selected_skills)[:240],
            metadata_json={"skill_decision": self._serialize_skill_decision(skill_decision)},
        )
        early_capability_result = self._handle_capability_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if early_capability_result is not None:
            return early_capability_result
        if skill_decision.required_inputs and self._has_missing_obvious_skill_inputs(
            skill_decision=skill_decision,
            request=request,
        ):
            return self._build_skill_routing_message_result(
                conversation_result=conversation_result,
                intent=intent.intent,
                content=self._build_missing_skill_inputs_message(skill_decision),
                execution_policy=execution_policy,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )
        youtube_confirmation_result = self._handle_youtube_upload_confirmation_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if youtube_confirmation_result is not None:
            return youtube_confirmation_result
        if not original_request.force_external:
            quick_chat_result = self._handle_quick_chat_turn(
                request=request,
                conversation_result=conversation_result,
                execution_policy=execution_policy,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )
            if quick_chat_result is not None:
                return quick_chat_result
        prompt_optimizer_result = self._handle_prompt_optimizer_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if prompt_optimizer_result is not None:
            return prompt_optimizer_result
        channel_ops_result = self._handle_channel_ops_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if channel_ops_result is not None:
            return channel_ops_result
        external_llm_result = self._handle_external_llm_operator_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            runtime_controls=runtime_controls,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if external_llm_result is not None:
            return external_llm_result
        # Clear local file/task requests must not be stolen by media detectors
        # matching words inside code or filenames (e.g. clean_prompt).
        if not original_request.force_external and natural_tool_focus(request.content) is not None:
            focused_result = self._handle_agentic_tool_loop_turn(
                request=request, conversation_result=conversation_result,
                execution_policy=execution_policy, intent=intent,
                skill_decision=skill_decision, skill_trace=skill_trace,
                runtime_preferences=runtime_preferences, stream_event=stream_event,
            )
            if focused_result is not None:
                return focused_result
        creative_video_result = self._handle_creative_video_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            runtime_controls=runtime_controls,
            runtime_preferences=runtime_preferences,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if creative_video_result is not None:
            return creative_video_result
        creative_asset_result = self._handle_creative_asset_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            runtime_controls=runtime_controls,
            runtime_preferences=runtime_preferences,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if creative_asset_result is not None:
            return creative_asset_result
        youtube_preview_result = self._handle_youtube_upload_preview_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if youtube_preview_result is not None:
            return youtube_preview_result
        # Route higher-level research/quality requests before the always-on local
        # tool loop. The loop is the default for ordinary local turns, not a
        # replacement for explicit provider routing.
        auto_external_result = self._handle_auto_external_quality_turn(
            request=request,
            intent=intent,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            conversation_settings=conversation_settings,
            runtime_preferences=runtime_preferences,
            runtime_controls=runtime_controls,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if auto_external_result is not None:
            return auto_external_result
        desktop_operator_result = self._handle_desktop_operator_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
            runtime_preferences=runtime_preferences,
        )
        if desktop_operator_result is not None:
            return desktop_operator_result
        # Browser before always-on agentic so karty/Chrome nie giną w tool loop.
        browser_operator_result = self._handle_browser_operator_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
            runtime_preferences=runtime_preferences,
        )
        if browser_operator_result is not None:
            return browser_operator_result
        ubuntu_agent_result = self._handle_ubuntu_agent_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if ubuntu_agent_result is not None:
            return ubuntu_agent_result
        # force_external is an explicit routing control and must never be
        # swallowed by the local always-on agentic tool loop.
        agentic_result = None
        if not original_request.force_external:
            agentic_result = self._handle_agentic_tool_loop_turn(
                request=request,
                conversation_result=conversation_result,
                execution_policy=execution_policy,
                intent=intent,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
                runtime_preferences=runtime_preferences,
                stream_event=stream_event,
            )
        if agentic_result is not None:
            return agentic_result
        capability_result = self._handle_capability_turn(
            request=request,
            conversation_result=conversation_result,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if capability_result is not None:
            return capability_result
        attachments = self.attachments.list_for_message(conversation_result.user_message.id)
        has_valid_active_document_action = self._resolve_active_document_action(
            request.active_document_action,
            attachments,
        ) is not None
        intent_trace = self.execution_traces.create(
            step_name="intent-classification",
            trace_kind=ExecutionTraceKind.INTENT,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=conversation_result.user_message.id,
            input_summary=f"attachments={len(attachments)}",
            output_summary=f"intent={intent.intent.value} task={intent.model_task.value}",
            metadata_json=(
                {"active_document_action_skip_reason": "active_document_attachment_not_visible"}
                if request.active_document_action is not None
                and not has_valid_active_document_action
                else None
            ),
        )

        selection_trace_input_summary = (
            f"intent={intent.intent.value} "
            f"task={intent.model_task.value} "
            f"prefer_local_llm={execution_policy.prefer_local_llm} "
            f"provider_posture={conversation_settings.resolved_provider_posture.value} "
            f"provider_posture_source={conversation_settings.resolved_provider_posture_source} "
            f"internet_enabled={conversation_settings.resolved_internet_enabled} "
            f"quality_profile={runtime_preferences.quality_profile} "
            f"force_external={original_request.force_external}"
        )
        try:
            selection = self.model_selection.select_for_task(
                task=intent.model_task,
                execution_policy=execution_policy,
                provider_posture=conversation_settings.resolved_provider_posture,
                internet_enabled=conversation_settings.resolved_internet_enabled,
                force_external=original_request.force_external,
            )
        except ExternalConfirmationRequiredError:
            raise
        except ModelSelectionError as exc:
            self.execution_traces.create(
                step_name="provider-selection",
                trace_kind=ExecutionTraceKind.PROVIDER,
                status=ExecutionTraceStatus.FAILED,
                conversation_id=conversation_result.conversation.id,
                message_id=conversation_result.user_message.id,
                input_summary=selection_trace_input_summary,
                error_message=str(exc),
                metadata_json={
                    "failure_kind": getattr(exc, "failure_kind", "provider_unavailable"),
                },
            )
            self.session.commit()
            raise

        selection_trace = self.execution_traces.create(
            step_name="provider-selection",
            trace_kind=ExecutionTraceKind.PROVIDER,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=conversation_result.user_message.id,
            provider_key=selection.provider.key,
            model_key=selection.model_config.key,
            input_summary=selection_trace_input_summary,
            output_summary=selection.reason,
        )
        llm_router_metadata = self._build_llm_router_metadata(
            content=request.content,
            has_image=any(attachment.kind == AttachmentKind.IMAGE for attachment in attachments),
            intent_model_task=intent.model_task,
            provider_posture=conversation_settings.resolved_provider_posture.value,
            selected_provider_key=selection.provider.key,
            selected_model_key=selection.model_config.key,
            runtime_model_name=getattr(selection.model_config, "runtime_model_name", None),
        )
        logger.info(
            "provider_selected=%s reason=%s",
            selection.provider.key,
            selection.selection_reason_code,
        )

        selected_review_note = self._build_selected_review_note_context()
        lightweight_chat_turn = self._is_lightweight_chat_turn(
            intent=intent,
            runtime_preferences=runtime_preferences,
            attachments=attachments,
            active_document_action=request.active_document_action,
            selected_review_note=selected_review_note,
            user_message=request.content,
        )
        capability_snapshot = self._build_capability_snapshot(
            runtime_preferences=runtime_preferences,
            active_document_available=(
                request.active_document_action is not None
                or any(bool(getattr(attachment, "extracted_text", None)) for attachment in attachments)
            ),
            user_message=request.content,
        )
        additional_system_sections = self._build_additional_system_sections(
            project_id=conversation_result.conversation.project_id,
            contradiction=contradiction,
            runtime_controls=runtime_controls,
            execution_policy=execution_policy,
            conversation_settings=conversation_settings,
            user_message=request.content,
            intent=intent,
            lightweight_chat_turn=lightweight_chat_turn,
        )
        knowledge_section = None
        if not lightweight_chat_turn:
            skill_prompt_context = load_skill_prompt_context(request.content, skill_decision)
            skill_trace.metadata_json = {
                **(skill_trace.metadata_json or {}),
                "instruction_context": skill_prompt_context.metadata,
                "instruction_context_applied_to": "provider_system_prompt",
            }
            loaded_ids = [
                item["skill_id"]
                for item in skill_prompt_context.metadata.get("loaded_instructions", [])
                if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
            ]
            skill_trace.output_summary = (
                f"selected={','.join(skill_decision.selected_skills) or 'none'}; "
                f"instructions_loaded={','.join(loaded_ids) or 'none'}"
            )[:240]
            self.session.add(skill_trace)
            knowledge_section = self.knowledge.build_system_section(
                request.content,
                max_docs=8,
                max_chars=12_000,
            )
            if knowledge_section is not None:
                additional_system_sections = (*additional_system_sections, knowledge_section)
            additional_system_sections = (
                *additional_system_sections,
                *extra_system_sections,
                self._build_structured_questions_system_section(),
                skill_prompt_context.prompt,
            )
        else:
            additional_system_sections = (*additional_system_sections, *extra_system_sections)

        fallback_chain = self.model_selection.build_execution_fallback_chain(
            task=intent.model_task,
            selected_provider_key=selection.provider.key,
            internet_enabled=conversation_settings.resolved_internet_enabled,
            # An explicit force_external turn already authorized this cloud
            # request; strict-local posture must not silently collapse its
            # provider retry chain to Ollama after the first cloud failure.
            provider_posture=(
                ProviderPosture.PREFER_LOCAL
                if original_request.force_external
                else conversation_settings.resolved_provider_posture
            ),
            probe_health=False,
            allow_external_fallback=execution_policy.allow_external_fallback,
        )
        provider_execution = self._execute_provider_with_fallback(
            initial_selection=selection,
            fallback_chain=fallback_chain,
            conversation_result=conversation_result,
            request=request,
            attachments=attachments,
            execution_policy=execution_policy,
            conversation_settings=conversation_settings,
            runtime_preferences=runtime_preferences,
            intent=intent,
            selected_review_note=selected_review_note,
            capability_snapshot=capability_snapshot,
            additional_system_sections=additional_system_sections,
            knowledge_section_used=knowledge_section is not None,
            assistant_metadata_json=request.assistant_metadata_json,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
            intent_trace=intent_trace,
            selection_trace=selection_trace,
        )
        if isinstance(provider_execution, ChatOrchestrationResult):
            return provider_execution
        (
            selection,
            provider_context,
            draft_tasks,
            provider_trace_input_summary,
            trace_metadata,
            execution_started,
            provider_result,
        ) = provider_execution

        assistant_content = provider_result.content
        if stream_event is not None:
            # The ordinary provider path is buffered. Honor a disconnected
            # client before persisting a response from an abandoned request.
            stream_event({"type": "status", "stage": "answering",
                          "title": "Atnos odpowiada", "detail": "Odpowiedź modelu gotowa."})
        if contradiction is not None:
            assistant_content = f"{contradiction.build_prefix()}\n\n{assistant_content}".strip()

        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=assistant_content,
            metadata_json=request.assistant_metadata_json,
            skill_decision=skill_decision,
            selected_provider_key=selection.provider.key,
            selected_model_key=selection.model_config.key,
            routing_reason=selection.reason,
            provider_posture=conversation_settings.resolved_provider_posture.value,
            task_type=selection.model_config.task.value,
            llm_router_metadata=llm_router_metadata,
        )
        assistant_content = assistant_message.content
        self._finalize_answer_placeholder_trace(
            traces=conversation_result.traces,
            status=ExecutionTraceStatus.COMPLETED,
            output_summary="Provider response saved to the conversation.",
        )
        self._attach_voice_reply_best_effort(
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            owner_id=owner_id,
            assistant_content=assistant_content,
            runtime_controls=runtime_controls,
            request_metadata_json=conversation_result.user_message.metadata_json,
        )
        provider_duration_ms = int((perf_counter() - execution_started) * 1000)
        metadata_json = trace_metadata.copy()
        if provider_result.raw_metadata:
            metadata_json.update(provider_result.raw_metadata)
        if provider_result.provider_message_id is not None:
            metadata_json["provider_message_id"] = provider_result.provider_message_id
        if provider_result.finish_reason is not None:
            metadata_json["finish_reason"] = provider_result.finish_reason
        if provider_result.usage is not None:
            metadata_json["usage"] = provider_result.usage.to_dict()
        metadata_json["assistant_output_chars"] = len(assistant_content)
        metadata_json["provider_duration_ms"] = provider_duration_ms
        metadata_json["skill_decision"] = self._serialize_skill_decision(skill_decision)
        metadata_json["llm_router"] = llm_router_metadata
        if contradiction is not None:
            metadata_json["decision_contradiction"] = {
                "entry_id": contradiction.entry_id,
                "summary": contradiction.summary,
                "prior_date": contradiction.prior_date.isoformat(),
                "evidence_message_ids": list(contradiction.evidence_message_ids),
                "current_claim": contradiction.current_claim,
            }
        provider_execution_trace = self.execution_traces.create(
            step_name="provider-execution",
            trace_kind=ExecutionTraceKind.PROVIDER,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key=selection.provider.key,
            model_key=selection.model_config.key,
            input_summary=provider_trace_input_summary,
            output_summary=(
                f"provider_result=completed "
                f"assistant_output_chars={len(assistant_content)} "
                f"finish_reason={provider_result.finish_reason or 'none'}"
            ),
            duration_ms=provider_duration_ms,
            metadata_json=metadata_json or None,
        )

        self.session.flush()
        self.session.commit()
        if not runtime_preferences.memory_enabled or memory_opt_out(original_request.content):
            memory_trace = self.execution_traces.create(
                step_name="memory-writeback",
                trace_kind=ExecutionTraceKind.MEMORY,
                status=ExecutionTraceStatus.SKIPPED,
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                input_summary=(
                    "memory_enabled=false "
                    f"source={runtime_preferences.memory_enabled_source} "
                    f"work_mode={runtime_preferences.work_mode} "
                    f"quality_profile={runtime_preferences.quality_profile} "
                    f"response_style={runtime_preferences.response_style or 'none'} "
                    f"persona_profile={runtime_preferences.persona_profile.value if runtime_preferences.persona_profile is not None else 'none'}"
                ),
                output_summary="Skipped because conversation memory is disabled.",
                metadata_json={
                    "memory_layer": "summary_writeback",
                    "memory_enabled": False,
                    "memory_enabled_source": runtime_preferences.memory_enabled_source,
                    "work_mode": runtime_preferences.work_mode,
                    "work_mode_source": runtime_preferences.work_mode_source,
                    "quality_profile": runtime_preferences.quality_profile,
                    "quality_profile_source": runtime_preferences.quality_profile_source,
                    "response_style": runtime_preferences.response_style,
                    "response_style_source": runtime_preferences.response_style_source,
                    "persona_profile": (
                        runtime_preferences.persona_profile.value
                        if runtime_preferences.persona_profile is not None
                        else None
                    ),
                    "persona_profile_source": runtime_preferences.persona_profile_source,
                },
            )
            self.session.commit()
        else:
            memory_trace = self._write_memory_best_effort(
                conversation=conversation_result.conversation,
                user_message=conversation_result.user_message,
                assistant_message=assistant_message,
                attachments=attachments,
                runtime_preferences=runtime_preferences,
                project_context_applied=provider_context.project_context_applied,
            )
        self._extract_decisions_best_effort(conversation_result.conversation.id)

        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=intent.intent,
            selected_model_key=selection.model_config.key,
            selected_provider_key=selection.provider.key,
            routing_reason=selection.reason,
            draft_tasks=draft_tasks,
            traces=conversation_result.traces
            + (skill_trace, intent_trace, selection_trace, provider_execution_trace, memory_trace),
            runtime_error=None,
            skill_decision=skill_decision,
        )

    def _execute_provider_with_fallback(
        self,
        *,
        initial_selection: ModelSelectionResult,
        fallback_chain: tuple[ModelSelectionResult, ...],
        conversation_result: ConversationTurnResult,
        request: ConversationTurnRequest,
        attachments,
        execution_policy,
        conversation_settings,
        runtime_preferences: RuntimeContextPreferences,
        intent: ChatIntent,
        selected_review_note: SelectedReviewNoteContext | None,
        capability_snapshot: dict,
        additional_system_sections: tuple[str, ...],
        knowledge_section_used: bool,
        assistant_metadata_json: dict | None,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
        intent_trace: ExecutionTrace,
        selection_trace: ExecutionTrace,
    ) -> tuple[
        ModelSelectionResult,
        object,
        tuple[DraftTask, ...],
        str,
        dict,
        float,
        ProviderExecutionResult,
    ] | ChatOrchestrationResult:
        selections = (initial_selection, *fallback_chain)
        for attempt_index, selection in enumerate(selections):
            attempt = self._build_provider_execution_attempt(
                selection=selection,
                conversation_result=conversation_result,
                request=request,
                attachments=attachments,
                execution_policy=execution_policy,
                runtime_preferences=runtime_preferences,
                intent=intent,
                selected_review_note=selected_review_note,
                capability_snapshot=capability_snapshot,
                additional_system_sections=additional_system_sections,
                knowledge_section_used=knowledge_section_used,
            )
            provider_adapter = self.provider_registry.get(selection.provider.key)
            if provider_adapter is None:
                error_message = f"No runtime adapter registered for provider '{selection.provider.key}'."
                self._persist_failed_provider_trace(
                    conversation_id=conversation_result.conversation.id,
                    user_message_id=conversation_result.user_message.id,
                    provider_key=selection.provider.key,
                    model_key=selection.model_config.key,
                    provider_trace_input_summary=attempt.provider_trace_input_summary,
                    execution_started=attempt.execution_started,
                    error_message=error_message,
                    metadata_json={**attempt.trace_metadata, "failure_kind": "provider_unavailable"},
                )
                if attempt_index < len(selections) - 1:
                    continue
                raise ChatOrchestrationError(error_message)

            try:
                provider_result = provider_adapter.execute(
                    task=selection.model_config.task,
                    context=attempt.provider_context,
                    provider=selection.provider,
                    model_config=selection.model_config,
                )
            except LocalProviderRuntimeError as exc:
                if attempt_index < len(selections) - 1:
                    self._persist_failed_provider_trace(
                        conversation_id=conversation_result.conversation.id,
                        user_message_id=conversation_result.user_message.id,
                        provider_key=selection.provider.key,
                        model_key=selection.model_config.key,
                        provider_trace_input_summary=attempt.provider_trace_input_summary,
                        execution_started=attempt.execution_started,
                        error_message=exc.failure_detail.user_detail,
                        metadata_json={**attempt.trace_metadata, "failure_kind": "local_runtime"},
                    )
                    continue
                return self._build_local_runtime_failure_result(
                    conversation_result=conversation_result,
                    intent=intent.intent,
                    selection=selection,
                    execution_policy=execution_policy,
                    conversation_settings=conversation_settings,
                    trace_metadata=attempt.trace_metadata,
                    provider_trace_input_summary=attempt.provider_trace_input_summary,
                    execution_started=attempt.execution_started,
                    failure_detail=exc.failure_detail,
                    assistant_metadata_json=assistant_metadata_json,
                    skill_decision=skill_decision,
                    skill_trace=skill_trace,
                    intent_trace=intent_trace,
                    selection_trace=selection_trace,
                )
            except (ProviderTimeoutError, ProviderConfigurationError, ProviderUnavailableError, ProviderResponseError) as exc:
                error_message, status_code, failure_kind = self._normalize_provider_execution_error(
                    selection=selection,
                    exc=exc,
                )
                self._persist_failed_provider_trace(
                    conversation_id=conversation_result.conversation.id,
                    user_message_id=conversation_result.user_message.id,
                    provider_key=selection.provider.key,
                    model_key=selection.model_config.key,
                    provider_trace_input_summary=attempt.provider_trace_input_summary,
                    execution_started=attempt.execution_started,
                    error_message=error_message,
                    metadata_json={**attempt.trace_metadata, "failure_kind": failure_kind},
                )
                if attempt_index < len(selections) - 1:
                    continue
                raise ChatOrchestrationProviderError(
                    error_message,
                    status_code=status_code,
                ) from exc

            if attempt_index > 0:
                logger.info(
                    "provider_selected=%s reason=policy_fallback",
                    selection.provider.key,
                )
                provider_result = self._with_provider_switch_notice(
                    provider_result,
                    provider_key=selection.provider.key,
                    fallback_from=initial_selection.provider.key,
                )
                attempt.trace_metadata["fallback_from_provider"] = initial_selection.provider.key
                attempt.trace_metadata["fallback_provider"] = selection.provider.key
                attempt.trace_metadata["fallback_attempt_index"] = attempt_index

            return (
                selection,
                attempt.provider_context,
                attempt.draft_tasks,
                attempt.provider_trace_input_summary,
                attempt.trace_metadata,
                attempt.execution_started,
                provider_result,
            )

        raise ChatOrchestrationProviderError(
            "No provider execution path succeeded.",
            status_code=503,
        )

    def _build_provider_execution_attempt(
        self,
        *,
        selection: ModelSelectionResult,
        conversation_result: ConversationTurnResult,
        request: ConversationTurnRequest,
        attachments,
        execution_policy,
        runtime_preferences: RuntimeContextPreferences,
        intent: ChatIntent,
        selected_review_note: SelectedReviewNoteContext | None,
        capability_snapshot: dict,
        additional_system_sections: tuple[str, ...],
        knowledge_section_used: bool,
    ) -> ProviderExecutionAttempt:
        runtime_model_name = getattr(selection.model_config, "runtime_model_name", None)
        obsidian_memory_used = False
        if runtime_preferences.memory_enabled and selection.provider.local_first:
            try:
                from atnos.services.chat_session_memory import load_recent_memory_brief

                project_name = (
                    conversation_result.conversation.project.name
                    if conversation_result.conversation.project is not None
                    else ""
                )
                memory_brief = load_recent_memory_brief(
                    settings=self.settings,
                    limit=4,
                    query=" ".join(part for part in (request.content, project_name) if part),
                    session=self.session,
                )
                if memory_brief:
                    additional_system_sections = (*additional_system_sections, memory_brief)
                    obsidian_memory_used = True
            except Exception:
                logger.debug("chat Obsidian memory retrieval skipped", exc_info=True)
        if isinstance(runtime_model_name, str) and runtime_model_name.strip():
            additional_system_sections = (
                *additional_system_sections,
                (
                    "AUTHORITATIVE RUNTIME IDENTITY\n"
                    f"Provider: {selection.provider.key}\n"
                    f"Runtime model: {runtime_model_name.strip()}\n"
                    "If the user asks which model handled this request, use this exact runtime model name. "
                    "Do not infer or invent a different model identity."
                ),
            )
        provider_context = self.context_builder.build(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            attachments=attachments,
            execution_policy=execution_policy,
            intent=intent,
            provider=selection.provider,
            runtime_preferences=runtime_preferences,
            conversation_history=self._build_provider_conversation_history(
                conversation_id=conversation_result.conversation.id,
                current_user_message=conversation_result.user_message,
            ),
            active_document_action=request.active_document_action,
            selected_review_note=selected_review_note,
            capability_snapshot=capability_snapshot,
            additional_system_sections=additional_system_sections,
        )
        draft_tasks = self.task_extraction.extract(
            content=request.content,
            document_action=intent.document_action,
            visible_attachments=provider_context.visible_attachments,
        )
        trace_metadata = {
            "task": selection.model_config.task.value,
            "draft_tasks": [task.to_dict() for task in draft_tasks],
            "provider_prompt_chars": provider_context.provider_prompt_chars,
            "active_document_preview_chars": provider_context.active_document_preview_chars,
            "active_document_preview_limit": provider_context.active_document_preview_limit,
            "knowledge_library_used": knowledge_section_used,
            "obsidian_memory_context_used": obsidian_memory_used,
            "quality_profile": runtime_preferences.quality_profile,
            "quality_profile_source": runtime_preferences.quality_profile_source,
        }
        if selected_review_note is not None:
            trace_metadata["selected_review_note_id"] = selected_review_note.id
            trace_metadata["selected_review_note_decision"] = selected_review_note.decision
            trace_metadata["selected_review_note_view"] = selected_review_note.view
        trace_metadata["capability_snapshot"] = capability_snapshot
        return ProviderExecutionAttempt(
            selection=selection,
            provider_context=provider_context,
            draft_tasks=draft_tasks,
            provider_trace_input_summary=f"{provider_context.summary} draft_tasks={len(draft_tasks)}",
            trace_metadata=trace_metadata,
            execution_started=perf_counter(),
        )

    def _build_provider_conversation_history(
        self,
        *,
        conversation_id: int,
        current_user_message: Message,
        max_messages: int = ContextBuilderService.CURRENT_CONVERSATION_HISTORY_MAX_MESSAGES,
        max_message_chars: int = ContextBuilderService.CURRENT_CONVERSATION_HISTORY_MAX_MESSAGE_CHARS,
    ) -> tuple[dict[str, str], ...]:
        """Load bounded completed turns from this conversation only."""
        prior_messages = self.messages.list_before_sequence(
            conversation_id,
            current_user_message.sequence_number,
            limit=max_messages,
        )
        history: list[dict[str, str]] = []
        for message in prior_messages:
            if message.status is not MessageStatus.COMPLETED:
                continue
            role = message.role.value if hasattr(message.role, "value") else str(message.role)
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(message.content or "").split())
            if content:
                history.append({"role": role, "content": content[:max_message_chars]})
        return tuple(history)

    def _normalize_provider_execution_error(
        self,
        *,
        selection: ModelSelectionResult,
        exc: Exception,
    ) -> tuple[str, int, str]:
        if isinstance(exc, ProviderTimeoutError):
            return self._build_provider_timeout_message(selection), 504, "timeout"
        if isinstance(exc, ProviderConfigurationError):
            return str(exc), 503, "provider_configuration"
        if isinstance(exc, ProviderUnavailableError):
            return str(exc), 503, "provider_unavailable"
        if isinstance(exc, ProviderResponseError):
            return str(exc), 502, "provider_response"
        return str(exc), 503, "provider_unavailable"

    @staticmethod
    def _with_provider_switch_notice(
        provider_result: ProviderExecutionResult,
        *,
        provider_key: str,
        fallback_from: str,
    ) -> ProviderExecutionResult:
        raw_metadata = dict(provider_result.raw_metadata or {})
        raw_metadata["fallback_from_provider"] = fallback_from
        raw_metadata["fallback_provider"] = provider_key
        return replace(
            provider_result,
            content=f"(prze\u0142\u0105czono na {provider_key})\n\n{provider_result.content}".strip(),
            raw_metadata=raw_metadata,
        )

    def _write_memory_best_effort(
        self,
        *,
        conversation: Conversation,
        user_message: Message,
        assistant_message: Message,
        attachments: tuple | list,
        runtime_preferences: RuntimeContextPreferences,
        project_context_applied: bool,
    ) -> ExecutionTrace:
        try:
            memory_trace = self.summary_writeback.refresh_after_turn(
                conversation=conversation,
                user_message=user_message,
                assistant_message=assistant_message,
                attachments=attachments,
                runtime_preferences=runtime_preferences,
                project_context_applied=project_context_applied,
            )
            self.session.commit()
            return memory_trace
        except Exception as exc:
            self.session.rollback()
            failed_trace = self.execution_traces.create(
                step_name="memory-writeback",
                trace_kind=ExecutionTraceKind.MEMORY,
                status=ExecutionTraceStatus.FAILED,
                conversation_id=conversation.id,
                message_id=assistant_message.id,
                input_summary=(
                    f"messages={assistant_message.sequence_number} "
                    f"attachments={len(tuple(attachments))} "
                    f"work_mode={runtime_preferences.work_mode} "
                    f"quality_profile={runtime_preferences.quality_profile} "
                    f"response_style={runtime_preferences.response_style or 'none'} "
                    f"persona_profile={runtime_preferences.persona_profile.value if runtime_preferences.persona_profile is not None else 'none'} "
                    f"project_context_applied={project_context_applied}"
                ),
                error_message=str(exc),
                metadata_json={
                    "memory_layer": "summary_writeback",
                    "project_context_applied": project_context_applied,
                    "work_mode": runtime_preferences.work_mode,
                    "work_mode_source": runtime_preferences.work_mode_source,
                    "quality_profile": runtime_preferences.quality_profile,
                    "quality_profile_source": runtime_preferences.quality_profile_source,
                    "response_style": runtime_preferences.response_style,
                    "response_style_source": runtime_preferences.response_style_source,
                    "persona_profile": (
                        runtime_preferences.persona_profile.value
                        if runtime_preferences.persona_profile is not None
                        else None
                    ),
                    "persona_profile_source": runtime_preferences.persona_profile_source,
                    "memory_enabled": runtime_preferences.memory_enabled,
                    "memory_enabled_source": runtime_preferences.memory_enabled_source,
                },
            )
            self.session.commit()
            return failed_trace

    def _build_local_runtime_failure_result(
        self,
        *,
        conversation_result,
        intent: ChatIntent,
        selection: ModelSelectionResult,
        execution_policy,
        conversation_settings,
        trace_metadata: dict[str, object],
        provider_trace_input_summary: str,
        execution_started: float,
        failure_detail: LocalModelFailureDetail,
        assistant_metadata_json: dict | None,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
        intent_trace: ExecutionTrace,
        selection_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        normalized_failure = self._refresh_local_runtime_failure_detail(
            selection=selection,
            execution_policy=execution_policy,
            conversation_settings=conversation_settings,
            failure_detail=failure_detail,
        )
        runtime_error_payload = normalized_failure.to_dict()
        metadata_json = dict(assistant_metadata_json or {})
        metadata_json["runtime_error"] = runtime_error_payload
        metadata_json["skill_decision"] = self._serialize_skill_decision(skill_decision)

        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content="",
            skill_decision=skill_decision,
            metadata_json=metadata_json,
            selected_provider_key=selection.provider.key,
            selected_model_key=selection.model_config.key,
            routing_reason=selection.reason,
            provider_posture=conversation_settings.resolved_provider_posture.value,
            task_type=selection.model_config.task.value,
            llm_router_metadata=self._build_llm_router_metadata(
                content=conversation_result.user_message.content,
                has_image=False,
                intent_model_task=selection.model_config.task,
                provider_posture=conversation_settings.resolved_provider_posture.value,
                selected_provider_key=selection.provider.key,
                selected_model_key=selection.model_config.key,
                runtime_model_name=getattr(selection.model_config, "runtime_model_name", None),
            ),
        )
        assistant_message.status = MessageStatus.FAILED
        self.session.add(assistant_message)
        self._finalize_answer_placeholder_trace(
            traces=conversation_result.traces,
            status=ExecutionTraceStatus.FAILED,
            output_summary="Provider execution failed and the turn was stored as failed.",
            error_message=normalized_failure.user_detail,
        )

        provider_trace_metadata = trace_metadata.copy()
        provider_trace_metadata["failure_type"] = normalized_failure.failure_type
        provider_trace_metadata["runtime_error"] = runtime_error_payload
        provider_trace = self.execution_traces.create(
            step_name="provider-execution",
            trace_kind=ExecutionTraceKind.PROVIDER,
            status=ExecutionTraceStatus.FAILED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key=selection.provider.key,
            model_key=selection.model_config.key,
            input_summary=provider_trace_input_summary,
            error_message=normalized_failure.user_detail,
            duration_ms=int((perf_counter() - execution_started) * 1000),
            metadata_json=provider_trace_metadata,
        )
        self.session.commit()
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=intent,
            selected_model_key=selection.model_config.key,
            selected_provider_key=selection.provider.key,
            routing_reason=selection.reason,
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, intent_trace, selection_trace, provider_trace),
            runtime_error=normalized_failure,
            skill_decision=skill_decision,
        )

    def _refresh_local_runtime_failure_detail(
        self,
        *,
        selection: ModelSelectionResult,
        execution_policy,
        conversation_settings,
        failure_detail: LocalModelFailureDetail,
    ) -> LocalModelFailureDetail:
        diagnostics = dict(failure_detail.diagnostics or {})
        normalized_kind = self._normalize_local_failure_kind(failure_detail.failure_type)
        if normalized_kind != failure_detail.failure_type:
            diagnostics["normalized_from"] = failure_detail.failure_type

        can_fallback = bool(
            execution_policy.allow_external_fallback
            and conversation_settings.resolved_internet_enabled
            and bool(getattr(self.settings, "cloud_llm_allowed", True))
        )
        diagnostics["fallback_requires_confirmation"] = can_fallback

        runtime_model_name = failure_detail.runtime_model_name
        try:
            inventory = RuntimeInventoryService(
                self.session,
                provider_registry=self.provider_registry,
                settings=self.settings,
            ).snapshot(include_live_health=True)
            provider_item = next(
                (
                    item
                    for item in inventory.providers
                    if item.provider_key == selection.provider.key
                ),
                None,
            )
            model_item = next(
                (
                    item
                    for item in inventory.models
                    if item.model_key == selection.model_config.key
                ),
                None,
            )
            if provider_item is not None:
                diagnostics["health_status"] = provider_item.health_status
                diagnostics["health_reason"] = provider_item.health_reason
                if provider_item.installed_models:
                    diagnostics["installed_models_sample"] = list(
                        provider_item.installed_models[:5]
                    )
            if model_item is not None:
                diagnostics["model_present"] = bool(model_item.runtime_model_present)
                runtime_model_name = (
                    runtime_model_name
                    or model_item.resolved_runtime_model_name
                    or model_item.runtime_model_name
                )
            elif provider_item is not None and runtime_model_name is not None:
                diagnostics["model_present"] = runtime_model_name in provider_item.installed_models
            else:
                diagnostics["model_present"] = False
        except Exception as exc:
            diagnostics.setdefault("health_status", "unknown")
            diagnostics["health_refresh_error"] = exc.__class__.__name__
            diagnostics.setdefault("model_present", False)

        return replace(
            failure_detail,
            failure_type=normalized_kind,
            runtime_model_name=runtime_model_name,
            can_fallback=can_fallback,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _normalize_local_failure_kind(raw_kind: str | None) -> str:
        normalized = (raw_kind or "").strip().lower()
        aliases = {
            "runtime_unreachable": "runtime_unreachable",
            "provider_unavailable": "runtime_unreachable",
            "unreachable": "runtime_unreachable",
            "connection_error": "runtime_unreachable",
            "network_error": "runtime_unreachable",
            "model_missing": "model_missing",
            "missing_model": "model_missing",
            "model_not_found": "model_missing",
            "request_timeout": "request_timeout",
            "timeout": "request_timeout",
            "runtime_busy": "runtime_busy",
            "busy": "runtime_busy",
            "overloaded": "runtime_busy",
            "invalid_endpoint": "invalid_endpoint",
            "invalid_url": "invalid_endpoint",
            "capability_mismatch": "capability_mismatch",
            "unsupported_capability": "capability_mismatch",
            "context_too_large": "context_too_large",
            "context_limit": "context_too_large",
            "generation_error": "generation_error",
            "response_error": "generation_error",
            "unknown": "unknown",
        }
        return aliases.get(normalized, "unknown")

    def _finalize_answer_placeholder_trace(
        self,
        *,
        traces: tuple[ExecutionTrace, ...],
        status: ExecutionTraceStatus,
        output_summary: str,
        error_message: str | None = None,
    ) -> None:
        for trace in traces:
            if (
                trace.step_name == "assistant-response-placeholder"
                and trace.status == ExecutionTraceStatus.PENDING
            ):
                trace.status = status
                trace.output_summary = output_summary
                trace.error_message = error_message
                self.session.add(trace)

    def _build_additional_system_sections(
        self,
        *,
        project_id: int | None,
        contradiction: DecisionContradiction | None,
        runtime_controls,
        execution_policy,
        conversation_settings,
        user_message: str,
        intent,
        lightweight_chat_turn: bool = False,
    ) -> tuple[str, ...]:
        sections: list[str] = []
        if not lightweight_chat_turn:
            decision_block = self.decisions.build_context_block(project_id=project_id)
            if decision_block:
                sections.append(decision_block)
            if contradiction is not None:
                sections.append(
                    "Potential contradiction with earlier decision memory:\n"
                    f"{contradiction.build_prefix()}"
                )
            ambient_block = self._build_ambient_context_block(
                enabled=runtime_controls.ambient_context_enabled,
            )
            if ambient_block:
                sections.append(ambient_block)
        travel_block = self._build_travel_planning_block(
            intent=intent,
            user_message=user_message,
            execution_policy=execution_policy,
            conversation_settings=conversation_settings,
            runtime_controls=runtime_controls,
        )
        if travel_block:
            sections.append(travel_block)
        # A live access snapshot is useful for questions about accounts, permissions
        # or connected tools. Sending it on every normal chat turn makes the local
        # model re-read thousands of irrelevant tokens and turns a short reply into
        # a slow one.
        if AccessRealityService.should_expand_for_message(user_message):
            access_block = self._build_access_reality_block(
                user_message=user_message,
            )
            if access_block:
                sections.append(access_block)
        return tuple(sections)

    @staticmethod
    def _is_lightweight_chat_turn(
        *,
        intent,
        runtime_preferences: RuntimeContextPreferences,
        attachments,
        active_document_action,
        selected_review_note: SelectedReviewNoteContext | None,
        user_message: str,
    ) -> bool:
        """Wylacza kosztowny kontekst tylko dla jawnie szybkiej, zwyklej rozmowy."""
        return (
            intent.model_task is ModelTask.GENERAL_CHAT
            and runtime_preferences.work_mode == "quick"
            and runtime_preferences.quality_profile == "fast-turnaround"
            and not attachments
            and active_document_action is None
            and selected_review_note is None
            and not AccessRealityService.should_expand_for_message(user_message)
        )

    def _build_access_reality_block(
        self,
        *,
        user_message: str,
    ) -> str | None:
        try:
            return AccessRealityService(
                self.session,
                settings=self.settings,
                provider_registry=self.provider_registry,
            ).build_chat_block(
                user_message=user_message,
                include_live_health=True,
            )
        except Exception:
            return None

    @staticmethod
    def _build_travel_planning_block(
        *,
        intent,
        user_message: str,
        execution_policy,
        conversation_settings,
        runtime_controls,
    ) -> str | None:
        if getattr(intent, "intent", None) != ChatIntent.TRAVEL_PLANNING:
            return None

        live_web_allowed = bool(
            execution_policy.allow_web_read and conversation_settings.resolved_internet_enabled
        )
        browser_research_available = bool(runtime_controls.browser_control_enabled)
        normalized = user_message.lower()
        local_route_request = any(
            token in normalized
            for token in ("trasa", "dojazd", "jak dojade", "jak dojadę", "google maps", "nawigac", "korki")
        )
        return (
            "Travel planning guidance:\n"
            f"- live_web_allowed: {live_web_allowed}\n"
            f"- browser_research_available: {browser_research_available}\n"
            "- Treat route, departure, arrival, weather and trip-planning requests as current-data tasks.\n"
            "- Use current public web and browser research when this runtime allows it. Do not default to a generic disclaimer that you lack route or traffic access when web read is enabled.\n"
            "- Do not claim that you already checked live traffic, weather, departures, prices or route status unless an explicit live browser result is present in the current context for this turn.\n"
            "- If no explicit live browser result is present in this turn, never write phrases such as 'sprawdzam aktualny ruch', 'widze korki', 'ruch jest stabilny', 'pogoda jest teraz' or 'dostepne dane w czasie rzeczywistym'.\n"
            "- In that case say plainly: 'Nie mam potwierdzonego live odczytu w tej turze, ale najlepszy praktyczny plan jest taki:' and then continue with the plan.\n"
            "- If exact live traffic, transit or booking data still cannot be verified from the current runtime, state precisely what is known, what still needs live confirmation, and then give the best practical plan anyway.\n"
            "- Do not invent exact road numbers, traffic conditions, departure boards or weather states when they are not explicitly present in the current context.\n"
            "- When no live route snapshot is present, prefer high-level route wording such as 'najprostsza trasa przez Lodz' instead of named road numbers.\n"
            "- For domestic travel inside Poland, do not mention passports, border checks or international-travel documents unless the user explicitly asks about a foreign trip.\n"
            + (
                "- For local Polish travel, give concrete departure timing, route variants, parking or transit notes, weather checks and what to verify in the map app.\n"
                if local_route_request
                else ""
            )
            + "- Ask follow-up questions only for missing hard constraints such as origin, destination, date, time window or transport mode."
        )

    def _build_ambient_context_block(self, *, enabled: bool) -> str | None:
        if not enabled or self.ambient_context_service is None:
            return None
        try:
            snapshot = self.ambient_context_service.get_snapshot()
        except Exception:
            return None
        if snapshot is None:
            return None
        foreground = snapshot.foreground_window_title or "unknown"
        app_name = snapshot.active_app_name or "unknown"
        project_name = snapshot.active_project_name or "none"
        return (
            "Ambient desktop context (privacy-safe, title only):\n"
            f"- time: {snapshot.time_of_day} {snapshot.day_of_week}\n"
            f"- foreground_app: {app_name}\n"
            f"- foreground_title: {foreground}\n"
            f"- active_project: {project_name}\n"
            f"- network_online: {snapshot.network_online}\n"
            f"- cpu_percent: {snapshot.cpu_percent if snapshot.cpu_percent is not None else 'n/a'}\n"
            f"- ram_used_mb: {snapshot.ram_used_mb if snapshot.ram_used_mb is not None else 'n/a'}\n"
            f"- free_vram_mb: {snapshot.free_vram_mb if snapshot.free_vram_mb is not None else 'n/a'}"
        )

    def _attach_voice_reply_best_effort(
        self,
        *,
        conversation_id: int,
        message_id: int,
        owner_id: int,
        assistant_content: str,
        runtime_controls,
        request_metadata_json: dict | None = None,
    ) -> None:
        if (
            not runtime_controls.voice_replies_enabled
            or not self._should_attach_voice_reply(request_metadata_json)
        ):
            return
        voice_name = None
        if runtime_controls.voice_reply_voice_id is not None:
            profile = self.voice_profiles.get(runtime_controls.voice_reply_voice_id)
            if profile is not None:
                voice_name = profile.voice_key
        else:
            default_voice = self.voice_profiles.get_default_for_user(owner_id)
            if default_voice is not None:
                voice_name = default_voice.voice_key
        from atnos.services.voice_synthesis_service import VoiceSynthesisService

        voice_name = VoiceSynthesisService.resolve_persona_voice(
            wake_word=getattr(runtime_controls, "ambient_wake_word", None),
            voice_name=voice_name,
        )
        try:
            descriptor = self.voice_synthesis.synthesize(
                text=assistant_content,
                conversation_id=conversation_id,
                voice_name=voice_name,
            )
        except VoiceSynthesisError:
            return
        self.attachments.create(
            filename=descriptor.filename,
            storage_path=descriptor.storage_path,
            kind=AttachmentKind.AUDIO,
            conversation_id=conversation_id,
            message_id=message_id,
            source=AttachmentSource.GENERATED,
            status=AttachmentStatus.AVAILABLE,
            media_type=descriptor.media_type,
            file_extension=descriptor.file_extension,
            size_bytes=descriptor.size_bytes,
            extracted_text=None,
            metadata_json={"voice_reply": True},
        )

    @staticmethod
    def _should_attach_voice_reply(request_metadata_json: dict | None) -> bool:
        if not isinstance(request_metadata_json, dict):
            return False
        if bool(request_metadata_json.get("voice_reply_requested")):
            return True
        source = request_metadata_json.get("source")
        if not isinstance(source, str):
            return False
        return source in {"voice_ambient", "voice_input", "push_to_talk"}

    def _extract_decisions_best_effort(self, conversation_id: int) -> None:
        try:
            # Keep decision logging cheap on the chat hot path.
            self.decisions.extract_from_conversation(conversation_id, allow_llm=False)
            self.session.commit()
        except Exception:
            self.session.rollback()

    def _persist_failed_provider_trace(
        self,
        *,
        conversation_id: int,
        user_message_id: int,
        provider_key: str,
        model_key: str,
        provider_trace_input_summary: str,
        execution_started: float,
        error_message: str,
        metadata_json: dict | None,
    ) -> None:
        self.execution_traces.create(
            step_name="provider-execution",
            trace_kind=ExecutionTraceKind.PROVIDER,
            status=ExecutionTraceStatus.FAILED,
            conversation_id=conversation_id,
            message_id=user_message_id,
            provider_key=provider_key,
            model_key=model_key,
            input_summary=provider_trace_input_summary,
            error_message=error_message,
            duration_ms=int((perf_counter() - execution_started) * 1000),
            metadata_json=metadata_json,
        )
        self.session.commit()

    @staticmethod
    def _build_provider_timeout_message(selection: ModelSelectionResult) -> str:
        if selection.provider.local_first:
            return "Model lokalny nie odpowiedzial w czasie. Sprawdz Ollama/model albo sprobuj ponownie."
        return "Provider nie odpowiedzial w czasie. Sprobuj ponownie za chwile."

    def _apply_conversation_toggles_to_request(
        self,
        request: ConversationTurnRequest,
        *,
        pre_resolved_settings,
    ) -> ConversationTurnRequest:
        if pre_resolved_settings is None or pre_resolved_settings.resolved_file_enabled:
            return request

        if not request.attachments:
            return request

        if not request.content.strip():
            raise ValueError("Files are disabled for this conversation.")

        return replace(request, attachments=tuple())

    @staticmethod
    def _resolve_active_document_action(
        active_document_action: ActiveDocumentAction | None,
        attachments: list,
    ) -> ActiveDocumentAction | None:
        if active_document_action is None:
            return None

        for attachment in attachments:
            if attachment.storage_path == active_document_action.attachment_id:
                return active_document_action
        return None

    @staticmethod
    def _build_runtime_context_preferences(
        *,
        conversation: Conversation,
        conversation_settings,
        request_execution_preferences: ExecutionPreferences | None,
    ) -> RuntimeContextPreferences:
        request_response_style = None
        request_memory_enabled = None
        request_files_enabled = None
        request_internet_enabled = None
        request_work_mode = None
        if request_execution_preferences is not None:
            normalized = " ".join((request_execution_preferences.response_style or "").split())
            request_response_style = normalized or None
            request_memory_enabled = request_execution_preferences.memory_enabled
            request_files_enabled = request_execution_preferences.file_enabled
            request_internet_enabled = request_execution_preferences.internet_enabled
            normalized_work_mode = " ".join((request_execution_preferences.work_mode or "").split()).lower()
            request_work_mode = normalized_work_mode or None

        if conversation_settings.response_style_override is not None:
            resolved_response_style = conversation_settings.response_style_override
            response_style_source = "conversation"
        elif request_response_style is not None:
            resolved_response_style = request_response_style
            response_style_source = "global"
        else:
            resolved_response_style = conversation_settings.resolved_response_style
            response_style_source = conversation_settings.resolved_response_style_source

        request_quality_profile = ChatOrchestrationService._normalize_quality_profile(
            request_execution_preferences.quality_profile if request_execution_preferences is not None else None
        )
        if request_quality_profile is not None:
            resolved_quality_profile = request_quality_profile
            quality_profile_source = "global"
        else:
            resolved_quality_profile = "standard"
            quality_profile_source = "default"

        if request_memory_enabled is not None:
            resolved_memory_enabled = request_memory_enabled
            memory_enabled_source = "global"
        else:
            resolved_memory_enabled = conversation_settings.resolved_memory_enabled
            memory_enabled_source = conversation_settings.resolved_memory_enabled_source

        if request_files_enabled is not None:
            resolved_files_enabled = request_files_enabled
            files_enabled_source = "global"
        else:
            resolved_files_enabled = conversation_settings.resolved_file_enabled
            files_enabled_source = conversation_settings.resolved_file_enabled_source

        if request_internet_enabled is not None:
            resolved_internet_enabled = request_internet_enabled
            internet_enabled_source = "global"
        else:
            resolved_internet_enabled = conversation_settings.resolved_internet_enabled
            internet_enabled_source = conversation_settings.resolved_internet_enabled_source

        if request_work_mode in {"auto", "quick", "deep", "execute"}:
            resolved_work_mode = request_work_mode
            work_mode_source = "global"
        else:
            resolved_work_mode = conversation_settings.active_work_mode
            work_mode_source = conversation_settings.active_work_mode_source

        return RuntimeContextPreferences(
            conversation_id=conversation.id,
            project_id=conversation.project_id,
            response_style=resolved_response_style,
            response_style_source=response_style_source,
            persona_profile=conversation_settings.resolved_persona_profile,
            persona_profile_source=conversation_settings.resolved_persona_profile_source,
            provider_posture=conversation_settings.resolved_provider_posture.value,
            provider_posture_source=conversation_settings.resolved_provider_posture_source,
            memory_enabled=resolved_memory_enabled,
            memory_enabled_source=memory_enabled_source,
            files_enabled=resolved_files_enabled,
            files_enabled_source=files_enabled_source,
            internet_enabled=resolved_internet_enabled,
            internet_enabled_source=internet_enabled_source,
            work_mode=resolved_work_mode,
            work_mode_source=work_mode_source,
            quality_profile=resolved_quality_profile,
            quality_profile_source=quality_profile_source,
        )

    @staticmethod
    def _apply_request_execution_overrides(
        conversation_settings,
        *,
        request_execution_preferences: ExecutionPreferences | None,
    ):
        """Nadaje ustawieniom pojedynczej wiadomosci najwyzszy priorytet.

        UI przekazuje te wartosci na kazdy turn. Bez tego selektor modelu widzial
        jedynie ustawienie projektu/globalne, co moglo rozjechac opis w interfejsie
        z faktycznym providerem.
        """
        if request_execution_preferences is None:
            return conversation_settings

        updates: dict[str, object] = {}
        raw_posture = " ".join((request_execution_preferences.provider_posture or "").split()).lower()
        try:
            request_posture = ProviderPosture(raw_posture) if raw_posture else None
        except ValueError:
            request_posture = None
        if request_posture is not None:
            updates["resolved_provider_posture"] = request_posture
            updates["resolved_provider_posture_source"] = "request"

        if request_posture is ProviderPosture.STRICT_LOCAL:
            # Strict local is a privacy guarantee, not a visual preference.
            updates["resolved_internet_enabled"] = False
            updates["resolved_internet_enabled_source"] = "request"
        elif request_execution_preferences.internet_enabled is not None:
            updates["resolved_internet_enabled"] = request_execution_preferences.internet_enabled
            updates["resolved_internet_enabled_source"] = "request"

        return replace(conversation_settings, **updates) if updates else conversation_settings

    def _build_selected_review_note_context(self) -> SelectedReviewNoteContext | None:
        selected_note = SelfImprovementNotesService(self.session).get_selected_note()
        if selected_note is None:
            return None

        return SelectedReviewNoteContext(
            id=selected_note.id,
            source=selected_note.source,
            view=selected_note.view,
            severity=selected_note.severity.value,
            decision=selected_note.decision.value,
            friction=selected_note.friction,
            proposed_fix=selected_note.proposed_fix,
            chosen_next_patch=selected_note.chosen_next_patch,
        )

    def _build_capability_snapshot(
        self,
        *,
        runtime_preferences: RuntimeContextPreferences,
        active_document_available: bool,
        user_message: str,
    ) -> dict[str, object]:
        snapshot = self.pc_automation.get_capability_snapshot(
            runtime_preferences=runtime_preferences,
            active_document_available=active_document_available,
        ).to_dict()
        snapshot.update(
            {
                "skill_router_available": True,
                "tool_selection_available": True,
                "prompt_optimizer_available": True,
                "quick_chat_operator_available": True,
                "browser_operator_available": True,
                "desktop_operator_available": True,
                "external_llm_operator_available": True,
            }
        )

        try:
            runtime_inventory = RuntimeInventoryService(
                self.session,
                provider_registry=self.provider_registry,
                settings=self.settings,
            ).snapshot(include_live_health=False)
            ready_providers = [item for item in runtime_inventory.providers if item.status == "ready"]
            snapshot["local_chat_ready"] = runtime_inventory.summary.has_local_chat
            snapshot["external_fallback_ready"] = runtime_inventory.summary.has_external_fallback
            snapshot["ready_tasks"] = [
                item.task for item in runtime_inventory.tasks if item.ready
            ][:8]
            snapshot["ready_provider_labels"] = [item.label for item in ready_providers][:8]
            snapshot["ready_local_provider_labels"] = [
                item.label for item in ready_providers if item.local_first
            ][:6]
            snapshot["ready_external_provider_labels"] = [
                self._runtime_provider_display_name(item.provider_key, item.label)
                for item in ready_providers
                if not item.local_first
            ][:6]
            snapshot["external_api_llms_ready"] = [
                self._runtime_provider_display_name(item.provider_key, item.label)
                for item in ready_providers
                if not item.local_first and self._is_external_llm_provider(item.provider_key)
            ][:6]
        except Exception:
            pass

        try:
            configured_accounts = AccountCredentialsService(self.session, self.settings).list_accounts()
            account_backed_external_llms = [
                self._runtime_provider_display_name(account.provider_key, account.label)
                for account in configured_accounts
                if account.status == "configured"
                and account.has_secret
                and self._is_external_llm_provider(account.provider_key)
            ]
            snapshot["account_backed_external_llms"] = self._dedupe_preserve_order(
                account_backed_external_llms
            )[:6]
            merged_external_api = [
                *self._coerce_string_list(snapshot.get("external_api_llms_ready")),
                *self._coerce_string_list(snapshot.get("account_backed_external_llms")),
            ]
            snapshot["external_api_llms_ready"] = self._dedupe_preserve_order(merged_external_api)[:6]
        except Exception:
            pass

        try:
            local_host = LocalHostControlService(self.session, settings=self.settings).get_capability_snapshot()
            snapshot["powershell_available"] = local_host.powershell_available
            snapshot["desktop_input_available"] = local_host.desktop_input_available
            snapshot["browser_recipe_available"] = local_host.browser_recipe_available
            snapshot["playwright_adapter_available"] = local_host.playwright_adapter_available
            snapshot["local_host_trusted_auto_enabled"] = local_host.trusted_auto_enabled
        except Exception:
            pass

        try:
            chrome = BrowserAccessDiagnosticsService(
                self.session,
                settings=self.settings,
            ).diagnose_chrome()
            snapshot["chrome_mode"] = chrome.mode
            snapshot["chrome_live_tabs_visible"] = chrome.mode == "devtools_live"
            snapshot["chrome_detected_apps"] = list(chrome.detected_apps[:6])
            snapshot["chrome_visible_llm_tabs"] = [
                app for app in chrome.detected_apps if app in {"ChatGPT", "Claude"}
            ][:4]
            snapshot["browser_llm_targets_supported"] = ["Claude", "ChatGPT"]
        except Exception:
            pass

        if self._looks_like_tool_awareness_request(user_message):
            try:
                desktop_apps = LocalHostControlService(self.session, settings=self.settings).list_desktop_apps()
                snapshot["ready_desktop_apps"] = [
                    item.label
                    for item in desktop_apps
                    if item.installed and (item.launch_supported or item.focus_supported)
                ][:8]
                snapshot["running_desktop_apps"] = [
                    item.label for item in desktop_apps if item.running
                ][:8]
            except Exception:
                pass

        return snapshot

    @staticmethod
    def _is_external_llm_provider(provider_key: str) -> bool:
        normalized = str(provider_key or "").strip().lower()
        return normalized.startswith(("anthropic", "openai", "grok", "gemini"))

    @staticmethod
    def _runtime_provider_display_name(provider_key: str, fallback_label: str) -> str:
        normalized = str(provider_key or "").strip().lower()
        if normalized.startswith("anthropic"):
            return "Claude"
        if normalized.startswith("openai"):
            return "OpenAI"
        if normalized.startswith("grok"):
            return "Grok"
        if normalized.startswith("gemini"):
            return "Gemini"
        if normalized.startswith("ollama"):
            return "Ollama"
        return fallback_label

    @staticmethod
    def _looks_like_tool_awareness_request(user_message: str) -> bool:
        normalized = " ".join((user_message or "").lower().split())
        normalized = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
        if not normalized:
            return False
        keywords = (
            "narzedz",
            "mozliwos",
            "potraf",
            "co umiesz",
            "co mozesz",
            "dostep",
            "chrome",
            "obsidian",
            "plik",
            "konto",
            "app",
            "aplikac",
            "program",
            "otworz",
            "uruchom",
            "llm",
        )
        return any(token in normalized for token in keywords)

    @staticmethod
    def _looks_like_local_app_request(user_message: str) -> bool:
        normalized = " ".join((user_message or "").lower().split())
        normalized = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
        if not normalized:
            return False
        if any(token in normalized for token in ("http://", "https://", "www.", "strona")):
            return False
        return any(
            token in normalized
            for token in (
                "aplikac",
                "program",
                "softwar",
                "otworz",
                "uruchom",
                "odpal",
                "wlacz",
                "zainstaluj",
                "install",
                "launch",
                "start ",
                ".exe",
                ".lnk",
            )
        )

    @staticmethod
    def _coerce_string_list(value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _handle_capability_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        if (
            "atnos:ubuntu-full-control" in skill_decision.selected_skills
            and self._has_explicit_ubuntu_context(request.content)
        ):
            return None
        pending_preview = self._find_pending_capability_preview(conversation_result.conversation.id)
        if pending_preview is not None and self._looks_like_confirmation(request.content):
            return self._execute_pending_capability_preview(
                request=request,
                conversation_result=conversation_result,
                execution_policy=execution_policy,
                pending_preview=pending_preview,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )

        if not self.capability_actions.might_build_preview_from_chat(request.content):
            return None

        try:
            preview = self.capability_actions.build_preview_from_chat(content=request.content)
        except CapabilityActionError as exc:
            return self._build_capability_message_result(
                conversation_result=conversation_result,
                content=f"{exc}\n\nNapisz wprost, czy chcesz Word, PowerPoint, Excel czy PDF.",
                metadata_json={"capability_error": str(exc)},
                execution_policy=execution_policy,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )

        if preview is None:
            return None

        if preview.action == "schedule_recurring_task":
            return self._build_capability_message_result(
                conversation_result=conversation_result,
                content=(
                    f"Potwierdź zaplanowane zadanie:\n"
                    f"- harmonogram: {preview.payload['human_label']}\n"
                    f"- cron: `{preview.payload['cron_spec']}`\n"
                    f"- prompt: `{preview.payload['prompt_template']}`\n\n"
                    f"Odpowiedz `potwierdz`, aby zapisać zadanie cykliczne."
                ),
                metadata_json={"pending_capability_action": preview.to_dict()},
                execution_policy=execution_policy,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )

        try:
            execution = self.capability_actions.execute(
                action=preview.action,
                conversation_id=conversation_result.conversation.id,
                project_id=conversation_result.conversation.project_id,
                payload=preview.payload,
            )
        except CapabilityActionError as exc:
            return self._build_capability_message_result(
                conversation_result=conversation_result,
                content=str(exc),
                metadata_json={"capability_error": str(exc)},
                execution_policy=execution_policy,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )

        artifact = execution.get("artifact")
        assistant_content = "Wykonałem lokalną akcję dokumentową."
        if isinstance(artifact, dict):
            assistant_content = (
                f"Wygenerowałem plik `{artifact['filename']}`. "
                "Możesz go pobrać z załącznika i otworzyć lokalnie."
            )
        result = self._build_capability_message_result(
            conversation_result=conversation_result,
            content=assistant_content,
            metadata_json={
                "capability_action": preview.action,
                "capability_payload": preview.payload,
            },
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if artifact is not None:
            self.capability_actions.attach_generated_artifact(
                conversation_id=conversation_result.conversation.id,
                message_id=result.assistant_message.id,
                artifact=artifact,
            )
            self.session.commit()
        return result

    def _handle_prompt_optimizer_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        if not self.prompt_optimizer.should_optimize(request.content):
            return None

        knowledge_section = self.knowledge.build_system_section(
            request.content,
            max_docs=1,
            max_chars=1_200,
        )
        optimization = self.prompt_optimizer.maybe_optimize(
            content=request.content,
            memory_context=knowledge_section,
        )
        if not optimization.activated or optimization.response_text is None or optimization.brief is None:
            return None

        return self._build_prompt_optimizer_message_result(
            conversation_result=conversation_result,
            content=optimization.response_text,
            metadata_json={
                "prompt_optimizer": {
                    "profile_key": optimization.metadata.get("profile_key"),
                    "profile_label": optimization.metadata.get("profile_label"),
                    "workload": optimization.metadata.get("workload"),
                    "quality": optimization.metadata.get("quality"),
                    "clarifying_question_count": optimization.metadata.get("clarifying_question_count"),
                    "memory_context_used": optimization.metadata.get("memory_context_used"),
                    "needs_clarification": optimization.brief.needs_clarification,
                    "goal": optimization.brief.goal,
                    "output_format": optimization.brief.output_format,
                    "constraints": list(optimization.brief.constraints),
                    "success_criteria": list(optimization.brief.success_criteria),
                }
            },
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    def _handle_quick_chat_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        operation = self.quick_chat_operator.maybe_handle(content=request.content)
        if not operation.activated or operation.assistant_content is None:
            return None

        return self._build_quick_chat_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    def _handle_agentic_tool_loop_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        intent,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
        runtime_preferences: RuntimeContextPreferences | None = None,
        stream_event: Callable[[dict[str, object]], None] | None = None,
    ) -> ChatOrchestrationResult | None:
        agentic_prompt = self._resolve_agentic_tool_loop_prompt(request.content)
        if agentic_prompt is None:
            return None

        skill_prompt_context = load_skill_prompt_context(request.content, skill_decision)
        skill_trace.metadata_json = {
            **(skill_trace.metadata_json or {}),
            "instruction_context": skill_prompt_context.metadata,
            "instruction_context_applied_to": "agentic_tool_loop_prompt",
        }
        loaded_ids = [
            item["skill_id"]
            for item in skill_prompt_context.metadata.get("loaded_instructions", [])
            if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
        ]
        skill_trace.output_summary = (
            f"selected={','.join(skill_decision.selected_skills) or 'none'}; "
            f"instructions_loaded={','.join(loaded_ids) or 'none'}"
        )[:240]
        self.session.add(skill_trace)
        agentic_prompt = f"{agentic_prompt}\n\n{skill_prompt_context.prompt}"

        # Vision/image turns must reach the vision provider with image bytes.
        # Always-on tool loop has no multimodal input — skip when images are attached.
        has_image_attachment = any(
            getattr(attachment, "kind", None) == AttachmentKind.IMAGE
            or str(getattr(attachment, "kind", "") or "").lower() == "image"
            or str(getattr(attachment, "detected_kind", "") or "").lower() == "image"
            for attachment in (request.attachments or ())
        )
        if has_image_attachment:
            return None

        # Quick conversational turns: skip tool loop, use normal local chat.
        if self._should_skip_agentic_for_quick_chat(
            content=request.content,
            intent=intent,
            runtime_preferences=runtime_preferences,
        ):
            return None

        if not execution_policy.allow_local_file_read and not (
            runtime_preferences is not None
            and (runtime_preferences.memory_enabled or runtime_preferences.internet_enabled)
        ):
            return self._build_agentic_tool_loop_message_result(
                conversation_result=conversation_result,
                content="Agentic tool loop jest zablokowany przez policy: brak zgody na lokalne operacje narzędziowe.",
                metadata_json={"agentic_tool_loop": {"status": "blocked_by_policy"}},
                intent=intent.intent,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
                status=ExecutionTraceStatus.FAILED,
                routing_reason="Agentic tool loop blocked by execution policy.",
                runtime_model_name=self._resolve_agentic_tool_loop_model(work_mode="auto"),
            )

        conversation_id = conversation_result.conversation.id
        approval_store = ChatApprovalStore(self.settings)
        pending = approval_store.get(conversation_id)
        if pending is not None and looks_like_confirmation(request.content):
            confirmed_result = self._execute_pending_chat_approval(
                pending=pending,
                conversation_id=conversation_id,
                files_enabled=(
                    bool(execution_policy.allow_local_file_read)
                    and (bool(runtime_preferences.files_enabled) if runtime_preferences else True)
                ),
                local_file_write_enabled=bool(execution_policy.allow_local_file_write),
                memory_enabled=bool(runtime_preferences.memory_enabled) if runtime_preferences else True,
                internet_enabled=(
                    bool(execution_policy.allow_web_read)
                    and (bool(runtime_preferences.internet_enabled) if runtime_preferences else True)
                ),
            )
            if confirmed_result.get("status") == "needs_confirmation":
                content = (
                    "Potwierdzenie nie wystarczyło albo akcja nadal wymaga twardego stopu.\n"
                    f"{confirmed_result.get('summary') or confirmed_result.get('reason') or ''}"
                ).strip()
                return self._build_agentic_tool_loop_message_result(
                    conversation_result=conversation_result,
                    content=content,
                    metadata_json={
                        "agentic_tool_loop": {
                            "status": "needs_confirmation",
                            "pending_approval": confirmed_result,
                            "requires_confirmation": True,
                        }
                    },
                    intent=intent.intent,
                    skill_decision=skill_decision,
                    skill_trace=skill_trace,
                    status=ExecutionTraceStatus.COMPLETED,
                    routing_reason="Hard-stop still requires confirmation.",
                    runtime_model_name=self._resolve_agentic_tool_loop_model(work_mode="auto"),
                )
            agentic_prompt = (
                "Użytkownik potwierdził wcześniej zablokowaną akcję. "
                f"Wynik wykonania: {json.dumps(confirmed_result, ensure_ascii=False)}. "
                "Podsumuj rezultat po polsku i kontynuuj zadanie, jeśli coś jeszcze zostało."
            )

        files_enabled = True
        memory_enabled = True
        internet_enabled = True
        work_mode = "auto"
        if runtime_preferences is not None:
            files_enabled = bool(runtime_preferences.files_enabled)
            memory_enabled = bool(runtime_preferences.memory_enabled)
            internet_enabled = bool(runtime_preferences.internet_enabled)
            work_mode = str(runtime_preferences.work_mode or "auto")
        if not bool(getattr(execution_policy, "allow_local_file_read", True)):
            files_enabled = False
        if not bool(getattr(execution_policy, "allow_web_read", True)):
            internet_enabled = False
        local_file_write_enabled = bool(getattr(execution_policy, "allow_local_file_write", False))
        memory_write_enabled = not memory_opt_out(request.content)
        runtime_model_name = self._resolve_agentic_tool_loop_model(work_mode=work_mode)

        capability_snapshot: dict[str, object] = {}
        if runtime_preferences is not None:
            try:
                capability_snapshot = self._build_capability_snapshot(
                    runtime_preferences=runtime_preferences,
                    active_document_available=False,
                    user_message=request.content,
                )
            except Exception:
                capability_snapshot = {}
        registry = build_chat_agent_registry(
            settings=self.settings,
            session=self.session,
            conversation_id=conversation_id,
            provider_registry=self.provider_registry,
            capability_snapshot=capability_snapshot,
            files_enabled=files_enabled,
            local_file_write_enabled=local_file_write_enabled,
            memory_enabled=memory_enabled,
            memory_write_enabled=memory_write_enabled,
            internet_enabled=internet_enabled,
        )
        available_tool_names = [tool.name for tool in registry.list_all()]
        requested_tool_names = [
            name
            for name in available_tool_names
            if re.search(rf"(?<![\w]){re.escape(name.casefold())}(?![\w])", request.content.casefold())
        ]
        explicit_tool_request = bool(
            requested_tool_names
            and (
                re.search(
                    r"\b(użyj|wywołaj|wykonaj|uruchom|zastosuj|zrób|use|call|run|execute|invoke)\b",
                    request.content.casefold(),
                )
                or re.match(r"\s*(agentic:|agent loop:|tool loop:|narzędzie:|narzedzie:)\s*", request.content.casefold())
            )
        )
        natural_focus = natural_tool_focus(request.content)
        if natural_focus is not None:
            missing = [name for name in natural_focus.required if name not in available_tool_names]
            if missing:
                return self._build_agentic_tool_loop_message_result(
                    conversation_result=conversation_result,
                    content="Nie wykonałem zadania: w tej rozmowie wyłączono potrzebny dostęp do lokalnych plików lub narzędzi. Sprawdź aktywne ustawienia rozmowy.",
                    metadata_json={"agentic_tool_loop": {"status": "blocked_by_policy", "unavailable_tools": missing, "natural_tool_focus": natural_focus.purpose, "tool_calls": []}},
                    intent=intent.intent, skill_decision=skill_decision, skill_trace=skill_trace,
                    status=ExecutionTraceStatus.FAILED, routing_reason="Natural request requires unavailable tools.",
                )
            requested_tool_names = list(natural_focus.required)
            explicit_tool_request = bool(requested_tool_names)
        if explicit_tool_request or natural_focus is not None:
            # Explicitly named tool requests are easier for a small local model to
            # execute reliably when its choices aren't diluted by unrelated tools.
            narrowed_registry = ToolRegistry()
            for tool in registry.list_all():
                if tool.name in (natural_focus.tools if natural_focus else requested_tool_names):
                    narrowed_registry.register_tool(tool)
            registry = narrowed_registry
        available_tools = [tool.name for tool in registry.list_all()]
        ready_desktop_apps: list[str] = []
        snapshot_apps = capability_snapshot.get("ready_desktop_apps")
        if isinstance(snapshot_apps, list):
            ready_desktop_apps = [str(item) for item in snapshot_apps if str(item).strip()]
        if not ready_desktop_apps and files_enabled and self._looks_like_local_app_request(request.content):
            try:
                ready_desktop_apps = [
                    item.label
                    for item in LocalHostControlService(self.session, settings=self.settings).list_desktop_apps()
                    if item.installed and (item.launch_supported or item.focus_supported)
                ][:12]
            except Exception:
                ready_desktop_apps = []
        ready_external_llms: list[str] = []
        for key in ("external_api_llms_ready", "account_backed_external_llms", "ready_external_provider_labels"):
            raw = capability_snapshot.get(key)
            if isinstance(raw, list):
                for item in raw:
                    label = str(item).strip()
                    if label and label not in ready_external_llms:
                        ready_external_llms.append(label)
        memory_brief = ""
        if memory_enabled:
            try:
                from atnos.services.chat_session_memory import load_recent_memory_brief

                memory_brief = load_recent_memory_brief(
                    settings=self.settings,
                    limit=4,
                    query=request.content,
                    session=self.session,
                )
            except Exception:
                memory_brief = ""
        system_prompt = build_chat_agent_system_prompt(
            files_enabled=files_enabled,
            memory_enabled=memory_enabled,
            internet_enabled=internet_enabled,
            work_mode=work_mode,
            available_tools=available_tools,
            ready_desktop_apps=ready_desktop_apps,
            ready_external_llms=ready_external_llms[:8],
            persona_profile=(
                runtime_preferences.persona_profile.value
                if runtime_preferences is not None and runtime_preferences.persona_profile is not None
                else "jarvis-mode"
            ),
            memory_brief=memory_brief or None,
            focus_purpose=natural_focus.purpose if natural_focus else None,
        )
        if natural_focus is not None:
            system_prompt += f"\nKatalog repozytorium Atnosa: {self.settings.app_root}. Ścieżki źródeł do delegacji podawaj względem tego katalogu."
        system_prompt += (
            "\n\nZasada jawnie zażądanego narzędzia: jeśli użytkownik wskazuje nazwę narzędzia z listy dostępnych i prosi o jego użycie, "
            "wywołaj to narzędzie jako tool call. Nie naśladuj go opisem, kodem ani poleceniem shell i nie twierdź, że wykonałeś "
            "operację bez wyniku wywołania. Jeśli nie potrafisz wywołać narzędzia, powiedz wprost, że zadanie nie zostało wykonane."
        )
        if explicit_tool_request:
            system_prompt += (
                "\nUżytkownik jawnie wskazał konkretne narzędzie. Lista dostępnych narzędzi została ograniczona do wskazanych nazw; "
                "wywołaj je teraz i nie zastępuj wywołania instrukcją ani symulacją."
            )
        history_messages = self._build_agentic_history_messages(
            conversation_id=conversation_id,
            current_user_message_id=conversation_result.user_message.id,
        )
        if work_mode == "deep":
            max_turns = 12
        elif work_mode == "quick":
            max_turns = 4
        else:
            max_turns = 6
        # Release SQLite locks before the long Ollama/tool loop so scheduler and
        # concurrent chat requests are not blocked on "database is locked".
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        direct_read = direct_read_request(request.content, natural_focus, conversation_id=conversation_id)
        backend = None if direct_read else self._build_agentic_tool_loop_backend(
            think=(work_mode == "deep"),
            work_mode=work_mode,
        )
        runtime_model_name = str(getattr(backend, "model", None) or runtime_model_name or "").strip() or runtime_model_name
        try:
            if direct_read:
                name, arguments = direct_read
                call = ToolCall(name=name, arguments=arguments, id=f"direct-read-{conversation_result.user_message.id}")
                started_at = perf_counter()
                if stream_event:
                    stream_event({"type": "tool_start", "name": name, "arguments": arguments})
                response = asyncio.run(ToolExecutor(registry, timeout_seconds=30.0).execute_tool_call(call))
                data = response.data if response.success and isinstance(response.data, dict) else None
                outcome_status = str(data.get("status") or "ok") if data is not None else "error"
                summary = ("Zapis potwierdzony." if name == "obsidian_write" else "Odczyt zakończony.") if data is not None else str(response.error or "Brak wyniku")
                if stream_event:
                    stream_event({"type": "tool_result", "name": name, "status": outcome_status, "summary": summary})
                result = AgentResult(
                    final_response=(self._format_self_state_summary(data) if name == 'self_state' else format_direct_read(name, data)) if data is not None else summary,
                    turns_used=0, tool_calls_made=(call,), stopped_reason="completed" if data is not None else "tool_error",
                    total_latency_s=perf_counter()-started_at,
                    tool_outcomes=(ToolCallOutcome(name=name, arguments=arguments, id=call.id, status=outcome_status, summary=summary, data=data),),
                )
                runtime_model_name = "local-tools/executor" if name == "obsidian_write" else "local-tools/readonly"
            else:
                result = asyncio.run(
                    OrchestratorAgent(executor=ToolExecutor(registry, timeout_seconds=90.0)).run(
                        agentic_prompt,
                        AgentContext(
                            system_prompt=system_prompt,
                            backend=backend,
                            registry=registry,
                            max_turns=max_turns,
                            required_tool_names=tuple(name for name in requested_tool_names if name != "self_state") if explicit_tool_request else (),
                        ),
                        history_messages=history_messages,
                        on_event=stream_event,
                    )
                )
        except LLMRunCancelled:
            raise
        except Exception as exc:
            return self._build_agentic_tool_loop_message_result(
                conversation_result=conversation_result,
                content=f"Agentic tool loop nie wykonał się poprawnie: {exc.__class__.__name__}: {exc}",
                metadata_json={
                    "agentic_tool_loop": {
                        "status": "error",
                        "error_type": exc.__class__.__name__,
                        "settings": {
                            "files_enabled": files_enabled,
                            "memory_enabled": memory_enabled,
                            "internet_enabled": internet_enabled,
                            "work_mode": work_mode,
                        },
                    }
                },
                intent=intent.intent,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
                status=ExecutionTraceStatus.FAILED,
                routing_reason="Agentic tool loop failed before completion.",
                runtime_model_name=runtime_model_name,
            )

        remaining_read_dispatch = False
        from atnos.services.chat_tool_focus import explicit_followup_read
        followup_read = explicit_followup_read(request.content, natural_focus)
        committed_note = any(item.name == 'obsidian_write' and item.status in {'ok', 'written'}
            and isinstance(item.data, dict) and item.data.get('status') == 'written'
            for item in result.tool_outcomes)
        if (followup_read and committed_note and internet_enabled and 'browser_learn' in available_tools
                and not approval_store.get(conversation_id) and result.stopped_reason == 'completed'
                and result.turns_used < max_turns and len(result.tool_calls_made) < max_turns
                and not any(call.name == 'browser_learn' for call in result.tool_calls_made)):
            # Complete one explicit read omitted by a prematurely finished model.
            # Do not repeat the durable write, exceed the budget, retry a failed
            # browser call, infer a URL, or bypass tool permissions/approval.
            call = ToolCall(name='browser_learn', arguments=followup_read,
                id=f'followup-read-{conversation_result.user_message.id}')
            if stream_event:
                stream_event({'type': 'tool_start', 'name': call.name, 'arguments': call.arguments})
            response = asyncio.run(ToolExecutor(registry, timeout_seconds=90.0).execute_tool_call(call))
            data = response.data if response.success and isinstance(response.data, dict) else None
            outcome = ToolCallOutcome(name=call.name, arguments=call.arguments, id=call.id,
                status=str(data.get('status') or 'ok') if data is not None else 'error',
                summary=str(data.get('summary') or '') if data is not None else str(response.error), data=data)
            if stream_event:
                stream_event({'type': 'tool_result', 'name': call.name, 'status': outcome.status, 'summary': outcome.summary})
            payload = data.get('result') if data else None
            analysis = payload.get('analysis') if isinstance(payload, dict) else None
            receipt = next(item.data for item in reversed(result.tool_outcomes)
                if item.name == 'obsidian_write' and item.status in {'ok', 'written'}
                and isinstance(item.data, dict) and item.data.get('status') == 'written')
            result = replace(result, tool_calls_made=(*result.tool_calls_made, call),
                tool_outcomes=(*result.tool_outcomes, outcome),
                final_response=(f"Zapisałem notatkę „{receipt.get('title')}”. Ścieżka: {receipt.get('path')}. "
                    f"SHA-256: {receipt.get('sha256') or 'UNKNOWN'}.\n\n"
                    + 'Odczyt strony: ' + followup_read['url'] + '\n' + str(analysis or outcome.summary)))
            remaining_read_dispatch = True

        source_read_dispatch = False
        if natural_focus and natural_focus.purpose == 'memory_read' and 'obsidian_read' in available_tools:
            reads = [item for item in result.tool_outcomes if item.name == 'obsidian_read' and item.status in {'ok', 'incomplete'} and isinstance(item.data, dict)]
            searches = [item.data for item in result.tool_outcomes if item.name == 'obsidian_search' and item.status == 'ok' and isinstance(item.data, dict)]
            # A real read can arrive before search. Verify its named title via
            # the normal search tool rather than rejecting correct content or
            # accepting an arbitrary model-guessed path without ambiguity checks.
            titles = {str(item.data.get('title') or '').strip() for item in reads}
            named_titles = [title for title in titles if len(title) >= 3 and title.casefold() in request.content.casefold()]
            if not searches and len(named_titles) == 1 and 'obsidian_search' in available_tools:
                call = ToolCall(name='obsidian_search', arguments={'query': named_titles[0]}, id=f'source-search-{conversation_result.user_message.id}')
                if stream_event:
                    stream_event({'type': 'tool_start', 'name': call.name, 'arguments': call.arguments})
                response = asyncio.run(ToolExecutor(registry, timeout_seconds=30.0).execute_tool_call(call))
                data = response.data if response.success and isinstance(response.data, dict) else None
                outcome = ToolCallOutcome(name=call.name, arguments=call.arguments, id=call.id,
                    status='ok' if data is not None else 'error', summary='Weryfikacja źródeł wskazanej notatki.' if data is not None else str(response.error), data=data)
                if stream_event:
                    stream_event({'type': 'tool_result', 'name': call.name, 'status': outcome.status, 'summary': outcome.summary})
                result = replace(result, tool_calls_made=(*result.tool_calls_made, call), tool_outcomes=(*result.tool_outcomes, outcome))
                if data is not None:
                    searches.append(data)
            source = named_note_source(request.content, searches[-1]) if searches else None
            sources = named_note_sources(request.content, searches[-1]) if searches else []
            matching = [item.data for item in reads if item.data.get('relative_path') == source]
            if len(sources) > 1:
                result = replace(result, final_response='W pamięci są różne notatki o tej samej wskazanej nazwie. Nie mogę jednoznacznie ustalić aktualnego źródła; nie połączę ich etapów. Wskaż właściwą ścieżkę:\n' + '\n'.join(f'- `{path}`' for path in sources), stopped_reason='source_conflict')
                if requests_note_comparison(request.content) and not approval_store.get(conversation_id):
                    # Read only search-backed sources. Keep the original ambiguity
                    # status, tool permissions, and call budget; never ask a model
                    # to pick canonical from two competing notes.
                    compared = []
                    for index, path in enumerate(sources[:2]):
                        existing = next((item.data for item in reads if item.data.get('relative_path') == path), None)
                        if existing is None and len(result.tool_calls_made) < max_turns:
                            call = ToolCall(name='obsidian_read', arguments={'relative_path': path},
                                id=f'conflict-read-{conversation_result.user_message.id}-{index}')
                            if stream_event:
                                stream_event({'type': 'tool_start', 'name': call.name, 'arguments': call.arguments})
                            response = asyncio.run(ToolExecutor(registry, timeout_seconds=30.0).execute_tool_call(call))
                            existing = response.data if response.success and isinstance(response.data, dict) else None
                            outcome = ToolCallOutcome(name=call.name, arguments=call.arguments, id=call.id,
                                status=str(existing.get('status') or 'ok') if existing is not None else 'error',
                                summary='Porównanie znalezionych źródeł.' if existing is not None else str(response.error), data=existing)
                            if stream_event:
                                stream_event({'type': 'tool_result', 'name': call.name, 'status': outcome.status, 'summary': outcome.summary})
                            result = replace(result, tool_calls_made=(*result.tool_calls_made, call),
                                tool_outcomes=(*result.tool_outcomes, outcome))
                            source_read_dispatch = True
                        if existing is not None:
                            compared.append((path, existing))
                    full = len(compared) == len(sources) and all(obsidian_read_complete(data) for _, data in compared)
                    lines = ['Porównanie źródeł o tej samej nazwie. Nie ustalam aktualnego etapu produkcji na podstawie tych notatek.']
                    if full:
                        same = len({data['markdown'] for _, data in compared}) == 1
                        lines.append('Odczytane treści są identyczne; różne ścieżki nie dowodzą sprzeczności.' if same
                            else 'Odczytane treści różnią się. Poniżej cytaty z obu źródeł; nie łączę ich w jeden stan.')
                    else:
                        lines.append('Porównanie częściowe: nie wszystkie źródła odczytano w całości. Nie rozstrzygam zgodności ani aktualności.')
                    lines.extend(format_obsidian_read(data) for _, data in compared)
                    unread = [path for path in sources if path not in {p for p, _ in compared}]
                    if unread:
                        lines.append('Źródła nieodczytane (limit operacji lub błąd):\n' + '\n'.join(f'- `{path}`' for path in unread))
                    result = replace(result, final_response='\n\n'.join(lines))
            elif source is not None and matching:
                data = matching[-1]
                result = replace(result, final_response=format_obsidian_read(data),
                    stopped_reason='completed' if obsidian_read_complete(data) else 'incomplete')
            elif source is not None:
                call = ToolCall(name='obsidian_read', arguments={'relative_path': source}, id=f'source-read-{conversation_result.user_message.id}')
                if stream_event:
                    stream_event({'type': 'tool_start', 'name': call.name, 'arguments': call.arguments})
                response = asyncio.run(ToolExecutor(registry, timeout_seconds=30.0).execute_tool_call(call))
                data = response.data if response.success and isinstance(response.data, dict) else None
                outcome = ToolCallOutcome(name=call.name, arguments=call.arguments, id=call.id,
                    status=str(data.get('status') or 'ok') if data is not None else 'error', summary='Odczyt jednoznacznie wskazanej notatki.' if data is not None else str(response.error), data=data)
                if stream_event:
                    stream_event({'type': 'tool_result', 'name': call.name, 'status': outcome.status, 'summary': outcome.summary})
                result = replace(result, tool_calls_made=(*result.tool_calls_made, call), tool_outcomes=(*result.tool_outcomes, outcome),
                    final_response=format_obsidian_read(data) if data is not None else outcome.summary,
                    stopped_reason=('completed' if obsidian_read_complete(data) else 'incomplete') if data is not None else 'tool_error')
                source_read_dispatch = True
            elif direct_read and direct_read[0] == 'obsidian_search':
                hits = searches[-1].get('hits', []) if searches else []
                result = replace(result,
                    final_response=format_direct_read('obsidian_search', {'hits': hits}) + (
                        '\n' + '\n'.join(f"- {hit.get('title')}: `{hit.get('relative_path')}`"
                                         for hit in hits if isinstance(hit, dict)) if hits else ''),
                    stopped_reason='source_conflict' if hits else 'source_not_found')
        bounded_coding_dispatch = False
        if (
            natural_focus is not None and natural_focus.purpose == "coding_proposal"
            and "ai_delegate_background" not in {call.name for call in result.tool_calls_made}
            and {call.name for call in result.tool_calls_made} == {"fs_read"}
            and all(outcome.status == "ok" for outcome in result.tool_outcomes)
        ):
            reads = [item.data for item in result.tool_outcomes if isinstance(item.data, dict) and item.data.get("path")]
            if len(reads) == 1:
                source = Path(reads[0]["path"]).resolve()
                user_text = request.content.replace("\\", "/").casefold()
                exact_mention = re.search(r"(?<![\w/])" + re.escape(source.as_posix().casefold()) + r'''(?=$|[\s"'`])''', user_text)
                if exact_mention and source.is_relative_to(self.settings.app_root.resolve()):
                    args = {"prompt": request.content, "task_type": "coding", "source_paths": [source.relative_to(self.settings.app_root.resolve()).as_posix()], "preferred_worker": "ollama", "privacy_mode": "strict_local"}
                    call = ToolCall(name="ai_delegate_background", arguments=args, id=f"bounded-coding-{conversation_result.user_message.id}")
                    if stream_event:
                        stream_event({"type": "tool_start", "name": call.name, "arguments": args})
                    dispatched = asyncio.run(ToolExecutor(registry, timeout_seconds=30.0).execute_tool_call(call))
                    data = dispatched.data if dispatched.success and isinstance(dispatched.data, dict) else None
                    outcome_status = str(data.get("status") or "error") if data else "error"
                    summary = "Zlecono lokalną propozycję kodu; wynik niegotowy." if outcome_status == "started_background" else str(dispatched.error or "Nie uruchomiono zadania.")
                    if stream_event:
                        stream_event({"type": "tool_result", "name": call.name, "status": outcome_status, "summary": summary})
                    result = replace(result, final_response=summary, tool_calls_made=(*result.tool_calls_made, call), tool_outcomes=(*result.tool_outcomes, ToolCallOutcome(name=call.name, arguments=args, id=call.id, status=outcome_status, summary=summary, data=data)), stopped_reason="completed" if outcome_status == "started_background" else "tool_error")
                    bounded_coding_dispatch = True
        pending_after = approval_store.get(conversation_id)
        explicit_readonly_dispatch: str | None = direct_read[0] if direct_read and direct_read[0] != "obsidian_write" else None
        if explicit_tool_request and requested_tool_names == ["self_state"]:
            states = [item.data for item in result.tool_outcomes if item.name == "self_state" and item.status == "ok" and isinstance(item.data, dict)]
            if states:
                # Render the actual snapshot; a model paraphrase must not turn
                # registered provider labels into successful connections.
                result = replace(result, final_response=self._format_self_state_summary(states[-1]))
        elif natural_focus is not None and natural_focus.purpose == "background_status":
            states = [item.data for item in result.tool_outcomes if item.name == "ai_delegate_status" and item.status == "ok" and isinstance(item.data, dict)]
            if states:
                result = replace(result, final_response=format_background_status(states[-1]))
        elif natural_focus is not None and natural_focus.purpose == "coding_proposal":
            started = [item.data for item in result.tool_outcomes if item.name == "ai_delegate_background" and item.status == "started_background" and isinstance(item.data, dict)]
            if started:
                result = replace(result, final_response=f"Zleciłem lokalnemu modelowi przygotowanie propozycji poprawki. Identyfikator: {started[-1].get('job_token')}. Kod nie został jeszcze zmieniony ani przetestowany. Możesz zapytać o stan zadania.")
        elif explicit_tool_request and requested_tool_names == ["fs_write"]:
            writes = [item.data for item in result.tool_outcomes if item.name == "fs_write" and item.status == "written" and isinstance(item.data, dict)]
            if writes:
                result = replace(result, final_response="\n".join(f"Zapisano plik: `{item.get('path')}`." for item in writes))
        if (
            explicit_tool_request
            and (requested_tool_names == ["self_state"] or (natural_focus is not None and natural_focus.purpose == "background_status"))
            and not result.tool_calls_made
        ):
            # self_state is an explicitly requested, read-only tool with an empty
            # argument schema. Dispatch it deterministically when a local model
            # returns prose instead of the requested tool call.
            readonly_name = requested_tool_names[0]
            direct_call = ToolCall(
                name=readonly_name,
                arguments={},
                id=f"direct-self-state-{conversation_result.user_message.id}",
            )
            direct_result = asyncio.run(
                ToolExecutor(registry, timeout_seconds=30.0).execute_tool_call(direct_call)
            )
            if direct_result.success and isinstance(direct_result.data, dict):
                state = direct_result.data
                result = replace(
                    result,
                    final_response=self._format_self_state_summary(state) if readonly_name == "self_state" else format_background_status(state),
                    tool_calls_made=(*result.tool_calls_made, direct_call),
                    tool_outcomes=(
                        *result.tool_outcomes,
                        ToolCallOutcome(
                            name=readonly_name,
                            arguments={},
                            id=direct_call.id,
                            status=str(state.get("status") or "ok"),
                            summary=str(state.get("summary") or "Read-only capability snapshot")[:240],
                        ),
                    ),
                    stopped_reason="completed",
                )
                explicit_readonly_dispatch = readonly_name
            else:
                result = replace(
                    result,
                    final_response=(
                        f"Nie udało się odczytać stanu Atnosa przez {readonly_name}: "
                        f"{direct_result.error or 'brak wyniku narzędzia'}."
                    ),
                    tool_calls_made=(*result.tool_calls_made, direct_call),
                    tool_outcomes=(
                        *result.tool_outcomes,
                        ToolCallOutcome(
                            name=readonly_name,
                            arguments={},
                            id=direct_call.id,
                            status="error",
                            summary=str(direct_result.error or "No tool result")[:240],
                        ),
                    ),
                    stopped_reason="tool_error",
                )
                explicit_readonly_dispatch = readonly_name

        quote_grounding = None
        if result.stopped_reason == 'completed':
            quote_check = ground_file_first_line(request.content, natural_focus,
                [item.data for item in result.tool_outcomes if item.name == 'fs_read'
                 and item.status == 'ok' and isinstance(item.data, dict)], result.final_response)
            if quote_check:
                corrected_answer, quote_grounding = quote_check
                if corrected_answer is not None:
                    result = replace(result, final_response=corrected_answer, stopped_reason='incomplete')

        tool_names_used = [call.name for call in result.tool_calls_made]
        missing_requested_tools = [name for name in requested_tool_names if name not in tool_names_used]
        unfulfilled_tool_request = bool(
            missing_requested_tools and explicit_tool_request
            and result.stopped_reason not in {'source_not_found', 'source_conflict'}
        )
        outcomes_by_index = list(result.tool_outcomes)
        tool_call_payloads: list[dict[str, object]] = []
        for index, call in enumerate(result.tool_calls_made):
            outcome = outcomes_by_index[index] if index < len(outcomes_by_index) else None
            entry: dict[str, object] = {
                "name": call.name,
                "arguments": call.arguments,
                "id": call.id,
            }
            if outcome is not None:
                entry["status"] = outcome.status
                entry["summary"] = outcome.summary
            tool_call_payloads.append(entry)
        latest_outcomes = {str(item["name"]): item for item in tool_call_payloads}
        incomplete_tools = [item for item in latest_outcomes.values() if item.get("status") == "incomplete"]
        failed_tools = [item for item in latest_outcomes.values() if item.get("status") in {"error", "failed", "blocked", "not_found"}]
        metadata_json = {
            "chat_intent": intent.intent.value,
            "intent_reason": intent.reason,
            "actual_runtime_model_name": runtime_model_name,
            "executed_provider": "local-tools" if direct_read else "ollama-local",
            "executed_model": runtime_model_name,
            "fallback_used": False,
            "agentic_tool_loop": {
                "status": "source_conflict" if result.stopped_reason == 'source_conflict' else "unfulfilled_tool_request" if unfulfilled_tool_request else "incomplete" if incomplete_tools else "failed" if failed_tools else result.stopped_reason,
                "requested_tools_not_called": missing_requested_tools if unfulfilled_tool_request else [],
                "turns_used": result.turns_used,
                "always_on": True,
                "tool_calls": tool_call_payloads,
                "tools_used": tool_names_used,
                "explicit_readonly_dispatch": explicit_readonly_dispatch,
                "available_tools": available_tools,
                "natural_tool_focus": natural_focus.purpose if natural_focus else None,
                "quote_grounding": quote_grounding,
                "bounded_coding_dispatch": bounded_coding_dispatch,
                "source_read_dispatch": source_read_dispatch,
                "remaining_read_dispatch": remaining_read_dispatch,
                "settings": {
                    "files_enabled": files_enabled,
                    "memory_enabled": memory_enabled,
                    "internet_enabled": internet_enabled,
                    "memory_write_enabled": memory_write_enabled,
                    "work_mode": work_mode,
                    "max_turns": max_turns,
                    "runtime_model_name": runtime_model_name,
                },
                "pending_approval": pending_after.to_dict() if pending_after else None,
                "requires_confirmation": result.stopped_reason == "needs_confirmation"
                or pending_after is not None,
            }
        }
        content = result.final_response
        if failed_tools:
            content = "Nie ukończyłem zadania: narzędzia zgłosiły błąd.\n\n" + "\n".join(
                f"{item['name']}: {item.get('summary') or item.get('status')}" for item in failed_tools
            )
        if incomplete_tools:
            content = "Nie ukończyłem analizy źródła.\n\n" + "\n".join(
                str(item.get("summary") or item["name"]) for item in incomplete_tools
            )
            partial_reads = [item.data for item in result.tool_outcomes if item.name == 'obsidian_read'
                             and item.status == 'incomplete' and isinstance(item.data, dict)]
            if partial_reads:
                content += '\n\n' + '\n\n'.join(format_obsidian_read(item) for item in partial_reads)
        if unfulfilled_tool_request:
            content = (
                "Nie wykonałem żądanego narzędzia: model nie wywołał "
                + ", ".join(missing_requested_tools)
                + ". Zadanie pozostaje niewykonane."
            )
        if result.stopped_reason == 'source_conflict':
            content = result.final_response
        if not (unfulfilled_tool_request or failed_tools or incomplete_tools):
            queued = [item.data for item in result.tool_outcomes if item.name == "ai_queue_tasks" and item.status in {"ok", "queued_for_manual_execution"} and isinstance(item.data, dict)]
            if queued:
                content = "Zapisane zadania — oczekują, niczego nie uruchomiłem:\n" + "\n".join(
                    f"{row['position']}. #{row['id']}: {row['task']}" for row in queued[-1].get("tasks", []))
            writes = [item for item in result.tool_outcomes if item.name == "obsidian_write" and item.status in {"ok", "written"} and isinstance(item.data, dict) and item.data.get("status") == "written"]
            if writes and not (natural_focus and "browser_learn" in natural_focus.required):
                receipt = writes[-1].data
                content = f"Zapisałem notatkę „{receipt.get('title')}”.\nŚcieżka: {receipt.get('path')}\nSHA-256: {receipt.get('sha256', 'brak w starszym receipcie')}"
            elif writes:
                receipt = writes[-1].data
                acknowledgment = format_direct_read('obsidian_write', receipt)
                if acknowledgment not in content:
                    content = acknowledgment + '\n\n' + content
            pages = [item.data.get('result') for item in result.tool_outcomes
                if item.name == 'browser_learn' and item.status == 'learned' and isinstance(item.data, dict)
                and isinstance(item.data.get('result'), dict)]
            for page in pages:
                title = page.get('page_title')
                if isinstance(title, str) and title.strip() and title not in content:
                    content = f"Tytuł odczytanej strony: {title.strip()}\nŹródło: {page.get('final_url') or page.get('target_url') or 'UNKNOWN'}\n\n" + content
        if not content:
            content = f"Agentic tool loop zakończył się bez finalnej odpowiedzi: {result.stopped_reason}."
        committed_writes = [item.data for item in result.tool_outcomes if item.name == 'obsidian_write'
            and item.status in {'ok', 'written'} and isinstance(item.data, dict) and item.data.get('status') == 'written']
        if committed_writes and (unfulfilled_tool_request or incomplete_tools or failed_tools or result.stopped_reason not in {'completed', 'needs_confirmation'}):
            metadata_json['agentic_tool_loop']['status'] = 'partially_completed'
            metadata_json['agentic_tool_loop']['committed_writes'] = [
                {key: item.get(key) for key in ('title', 'path', 'sha256')} for item in committed_writes]
            content = '\n'.join(f"Zapis notatki „{item.get('title')}” jest potwierdzony. Ścieżka: {item.get('path')}. SHA-256: {item.get('sha256') or 'UNKNOWN'}." for item in committed_writes) + '\n\nPozostałe sprawdzenia nie zostały ukończone. Nie ponawiaj zapisu w ciemno; najpierw sprawdź istniejący plik.\n\n' + content
        if memory_enabled and not memory_opt_out(request.content) and not unfulfilled_tool_request and not incomplete_tools and not failed_tools and result.stopped_reason in {"completed", "needs_confirmation"}:
            try:
                from atnos.services.chat_session_memory import persist_agentic_session_memory

                persist_agentic_session_memory(
                    settings=self.settings,
                    user_message=request.content,
                    assistant_message=content,
                    tools_used=tool_names_used,
                    enabled=True,
                    session=self.session,
                )
            except Exception:
                pass
        trace_status = (
            ExecutionTraceStatus.COMPLETED
            if not unfulfilled_tool_request and not incomplete_tools and not failed_tools and result.stopped_reason in {"completed", "needs_confirmation"}
            else ExecutionTraceStatus.FAILED
        )

        return self._build_agentic_tool_loop_message_result(
            conversation_result=conversation_result,
            content=content,
            metadata_json=metadata_json,
            intent=intent.intent,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
            status=trace_status,
            routing_reason=(
                "Requested tool was not called by the local model."
                if unfulfilled_tool_request
                else "Handled by the always-on Atnos chat tool loop."
            ),
            runtime_model_name=runtime_model_name,
        )

    @staticmethod
    def _format_self_state_summary(state: dict[str, object]) -> str:
        lines = [str(state.get("summary") or "Odczytałem bieżący stan Atnosa.").strip()]
        capabilities = state.get("can_do")
        if isinstance(capabilities, dict):
            internet_access = capabilities.get('realtime_internet')
            file_access = capabilities.get('local_files_without_chat_attachment')
            internet = "skonfigurowany" if internet_access is True else "wyłączony lub niedostępny" if internet_access is False else "niepotwierdzony"
            files = "włączone w rozmowie" if file_access is True else "wyłączone w rozmowie" if file_access is False else "status nieznany"
            external = capabilities.get("external_llm_labels")
            external_text = ", ".join(str(item) for item in external) if isinstance(external, list) else ""
            lines.append(f"Internet: {internet}; pliki lokalne: {files}.")
            available = capabilities.get("external_llms_claude_openai_gemini")
            access = "dostępny według konfiguracji" if available is True else "wyłączony lub niepodłączony" if available is False else "niepotwierdzony"
            lines.append(f"Dostęp do zewnętrznych LLM: {access}.")
            if external_text:
                lines.append(f"Zarejestrowane etykiety dostawców: {external_text} (same nazwy nie potwierdzają dostępu).")
        obsidian = state.get("obsidian")
        if isinstance(obsidian, dict):
            readable = obsidian.get("readable")
            obsidian_status = ("wyłączony w tej rozmowie" if obsidian.get('access') == 'disabled_in_conversation'
                else "czytelny" if readable is True else "ograniczony" if readable is False else "status nieznany")
            lines.append(f"Obsidian: {obsidian_status}.")
        tools = state.get('registered_tools')
        if isinstance(tools, list):
            labels = {'fs_read': 'odczyt plików', 'fs_write': 'zapis plików', 'obsidian_search': 'wyszukiwanie notatek',
                'obsidian_write': 'zapisywanie notatek', 'browser_learn': 'odczyt stron',
                'ai_delegate_status': 'sprawdzanie kolejki', 'ai_queue_tasks': 'kolejkowanie zadań',
                'ai_delegate_background': 'delegowanie zadań', 'shell_powershell': 'polecenia lokalne'}
            enabled = [label for name, label in labels.items() if name in tools]
            if enabled:
                lines.append('Narzędzia włączone w tej rozmowie: ' + ', '.join(enabled) + '.')
        return "\n".join(line for line in lines if line)

    def _build_agentic_history_messages(
        self,
        *,
        conversation_id: int,
        current_user_message_id: int,
        max_messages: int = 8,
    ) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        try:
            prior = self.messages.list_for_conversation(conversation_id)
        except Exception:
            return history
        for message in prior:
            if getattr(message, "id", None) == current_user_message_id:
                continue
            role = getattr(getattr(message, "role", None), "value", None) or str(
                getattr(message, "role", "") or ""
            )
            role_normalized = str(role).strip().lower()
            if role_normalized not in {"user", "assistant"}:
                continue
            content = str(getattr(message, "content", "") or "").strip()
            if not content:
                continue
            # Keep history compact for local models.
            if len(content) > 1200:
                content = content[:1200] + "…"
            history.append({"role": role_normalized, "content": content})
        if len(history) > max_messages:
            history = history[-max_messages:]
        return history

    def _execute_pending_chat_approval(
        self,
        *,
        pending,
        conversation_id: int,
        files_enabled: bool = True,
        local_file_write_enabled: bool = True,
        memory_enabled: bool = True,
        internet_enabled: bool = True,
    ) -> dict:
        registry = build_chat_agent_registry(
            settings=self.settings,
            session=self.session,
            conversation_id=conversation_id,
            provider_registry=self.provider_registry,
            files_enabled=files_enabled,
            local_file_write_enabled=local_file_write_enabled,
            memory_enabled=memory_enabled,
            internet_enabled=internet_enabled,
        )
        tool = registry.get(pending.tool_name)
        if tool is None:
            ChatApprovalStore(self.settings).clear(conversation_id)
            return {
                "status": "error",
                "error": f"Unknown pending tool: {pending.tool_name}",
            }
        arguments = dict(pending.arguments)
        arguments["confirmed"] = True
        arguments["approval_id"] = pending.approval_id
        authorize = getattr(tool, "authorize_pending_approval", None)
        if callable(authorize):
            authorize(pending.approval_id)

        async def _run():
            return await ToolExecutor(registry, timeout_seconds=90.0).execute_tool_call(
                ToolCall(name=pending.tool_name, arguments=arguments, id=pending.approval_id)
            )

        tool_result = asyncio.run(_run())
        if tool_result.success:
            ChatApprovalStore(self.settings).clear(conversation_id)
            data = tool_result.data if isinstance(tool_result.data, dict) else {"data": tool_result.data}
            return {"status": "executed", **data}
        return {
            "status": "error",
            "error": tool_result.error or "pending approval execution failed",
        }

    def _resolve_agentic_tool_loop_model(self, *, work_mode: str = "auto") -> str:
        from atnos.services.ollama_model_roles_service import OllamaModelRolesService
        from atnos.repositories.model_config_repository import ModelConfigRepository

        roles = OllamaModelRolesService(self.settings)
        normalized = str(work_mode or "auto").strip().lower()
        # Reserve the larger model for analysis. PC tool execution needs the
        # lower-latency model so a simple local action does not time out.
        # Shorts keep DOCUMENT/SYNTHESIS/VISION from env (unchanged).
        if normalized == "deep":
            return roles.main_text_model

        model = roles.fast_text_model or roles.main_text_model
        # The UI switches the default local chat model in the database.
        # The always-on tool loop must use the same selection as regular chat.
        session = getattr(self, "session", None)
        if session is None:
            return model
        try:
            candidates = [
                item
                for item in ModelConfigRepository(session).list_enabled_for_task(ModelTask.GENERAL_CHAT)
                if item.provider is not None
                and item.provider.is_enabled
                and item.provider.key == "ollama-local"
            ]
            selected = next((item for item in candidates if item.is_default), None) or (
                candidates[0] if candidates else None
            )
            if selected is not None and selected.supports_tools:
                resolved = str(selected.runtime_model_name or model).strip() or model
                return resolved
        except Exception:
            logger.warning(
                "Failed to resolve stored local chat model for agentic tool loop; using role default %s",
                model,
                exc_info=True,
            )
        return model

    def _build_agentic_tool_loop_backend(self, *, think: bool = False, work_mode: str = "auto"):
        model = ChatOrchestrationService._resolve_agentic_tool_loop_model(self, work_mode=work_mode)
        return OllamaChatBackend(settings=self.settings, model=model, think=think)

    @staticmethod
    def _resolve_agentic_tool_loop_prompt(content: str) -> str | None:
        stripped = (content or "").strip()
        if not stripped:
            return None
        normalized = stripped.casefold()
        prefixes = (
            "agentic:",
            "agent loop:",
            "tool loop:",
            "narzędzie:",
            "narzedzie:",
        )
        for prefix in prefixes:
            if normalized.startswith(prefix):
                prompt = stripped[len(prefix) :].strip()
                return prompt or stripped
        # Ordinary conversation stays on the normal local/provider chat path.
        # Use the tool loop only when the wording indicates a local action or a
        # direct tool request; this avoids tool-loop latency on every turn.
        if ChatOrchestrationService._looks_like_agentic_tool_request(stripped):
            return stripped
        if re.search(
            r"\b(?:use|call|run|execute|invoke|wywołaj|wywolaj|użyj|uzyj)\s+"
            r"(?:the\s+)?(?:echo|[a-z][a-z0-9]*_[a-z0-9_-]+|[a-z][\w-]*\s+(?:tool|narzędzie|narzedzie))\b",
            normalized,
        ):
            return stripped
        return None

    def _should_skip_agentic_for_quick_chat(
        self,
        *,
        content: str,
        intent,
        runtime_preferences: RuntimeContextPreferences | None,
    ) -> bool:
        work_mode = "auto"
        if runtime_preferences is not None:
            work_mode = str(runtime_preferences.work_mode or "auto")
        if work_mode != "quick":
            return False
        intent_value = getattr(getattr(intent, "intent", None), "value", None) or str(
            getattr(intent, "intent", "") or ""
        )
        if intent_value and intent_value not in {
            ChatIntent.GENERAL_CHAT.value,
            "general_chat",
        }:
            return False
        return not self._looks_like_agentic_tool_request(content)

    @staticmethod
    def _looks_like_agentic_tool_request(content: str) -> bool:
        if natural_tool_focus(content) is not None:
            return True
        normalized = " ".join((content or "").casefold().split())
        if not normalized:
            return False
        for src, dst in (
            ("ą", "a"),
            ("ć", "c"),
            ("ę", "e"),
            ("ł", "l"),
            ("ń", "n"),
            ("ó", "o"),
            ("ś", "s"),
            ("ź", "z"),
            ("ż", "z"),
        ):
            normalized = normalized.replace(src, dst)
        tool_hints = (
            "otworz",
            "uruchom",
            "odpal",
            "wlacz",
            "sprawdz plik",
            "przeczytaj",
            "zapisz",
            "obsidian",
            "notatk",
            "shell",
            "powershell",
            "terminal",
            "przegladark",
            "chrome",
            "brave",
            "strone",
            "strona",
            "www.",
            "http://",
            "https://",
            " nip",
            "nip ",
            "kontakt",
            "desktop",
            "aplikacj",
            "comfy",
            "comfort",
            "ubuntu",
            "ssh",
            "folder",
            "katalog",
            "sciezke",
            "sciezka",
            "agentic",
            "narzedzie:",
            "tool loop",
            "stan lokalny",
            "jaki jest twoj stan",
            "local state",
            "self_state",
            "system state",
            "karty",
            "taby",
            "screenshot",
            "zrzut",
        )
        return any(hint in normalized for hint in tool_hints)

    def _handle_creative_asset_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        runtime_controls,
        runtime_preferences: RuntimeContextPreferences,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        operation = self.creative_asset_operator.maybe_handle(
            content=request.content,
            conversation_id=conversation_result.conversation.id,
            allow_local_file_write=execution_policy.allow_local_file_write,
            allow_external_fallback=execution_policy.allow_external_fallback,
            runtime_controls=runtime_controls,
            quality_profile=runtime_preferences.quality_profile,
        )
        if not operation.activated or operation.assistant_content is None:
            return None

        result = self._build_creative_asset_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            selected_provider_key=operation.selected_provider_key or "creative-asset-operator",
            selected_model_key=operation.selected_model_key or "creative-asset-operator",
            routing_reason=operation.routing_reason or "Handled by the creative asset operator.",
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if operation.artifact is not None:
            self._attach_generated_image_artifact(
                conversation_id=conversation_result.conversation.id,
                message_id=result.assistant_message.id,
                artifact=operation.artifact,
            )
            self.session.commit()
        return result

    def _handle_creative_video_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        runtime_controls,
        runtime_preferences: RuntimeContextPreferences,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        operation = self.creative_video_operator.maybe_handle(
            content=request.content,
            conversation_id=conversation_result.conversation.id,
            project_id=conversation_result.conversation.project_id,
            allow_local_file_write=execution_policy.allow_local_file_write,
            quality_profile=runtime_preferences.quality_profile,
            runtime_controls=runtime_controls,
        )
        if not operation.activated or operation.assistant_content is None:
            return None

        result = self._build_creative_video_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            selected_provider_key=operation.selected_provider_key or "creative-video-operator",
            selected_model_key=operation.selected_model_key or "creative-video-operator",
            routing_reason=operation.routing_reason or "Handled by the creative video operator.",
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if operation.kick_scheduler and self.scheduler_runtime_service is not None:
            self.scheduler_runtime_service.kick_trusted_auto()
        if operation.artifacts:
            self._attach_generated_video_artifacts(
                conversation_id=conversation_result.conversation.id,
                message_id=result.assistant_message.id,
                artifacts=operation.artifacts,
            )
            self.session.commit()
        return result

    def _handle_channel_ops_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        operation = self.channel_ops_chat.maybe_handle(request.content)
        if not operation.activated or operation.assistant_content is None:
            return None
        return self._build_channel_ops_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            selected_provider_key=operation.selected_provider_key,
            selected_model_key=operation.selected_model_key,
            routing_reason=operation.routing_reason,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    @staticmethod
    def _normalize_quality_profile(value: str | None) -> str | None:
        normalized = " ".join((value or "").split())
        if normalized not in {"fast-turnaround", "standard", "premium"}:
            return None
        return normalized

    def _handle_auto_external_quality_turn(
        self,
        *,
        request: ConversationTurnRequest,
        intent: IntentDetectionResult,
        conversation_result,
        execution_policy,
        conversation_settings,
        runtime_preferences: RuntimeContextPreferences,
        runtime_controls,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        plan = self._plan_auto_external_quality_route(
            request=request,
            intent=intent,
            execution_policy=execution_policy,
            conversation_settings=conversation_settings,
            runtime_preferences=runtime_preferences,
        )
        if plan is None:
            return None

        operation = self.external_llm_operator.dispatch_auto(
            prompt=request.content,
            target=plan.target,  # type: ignore[arg-type]
            task_type=plan.task_type,  # type: ignore[arg-type]
            mode_preference=plan.mode_preference,  # type: ignore[arg-type]
            allow_external_fallback=execution_policy.allow_external_fallback,
            runtime_controls=runtime_controls,
            routing_reason=plan.reason,
            fallback_policy=plan.fallback_policy,  # type: ignore[arg-type]
        )
        if not operation.activated or operation.assistant_content is None:
            return None
        return self._build_external_llm_operator_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            selected_provider_key=operation.selected_provider_key or "external-llm-operator",
            selected_model_key=operation.selected_model_key or "external-llm-operator",
            routing_reason=operation.routing_reason or plan.reason,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    def _plan_auto_external_quality_route(
        self,
        *,
        request: ConversationTurnRequest,
        intent: IntentDetectionResult,
        execution_policy,
        conversation_settings,
        runtime_preferences: RuntimeContextPreferences,
    ) -> AutoExternalRoutingPlan | None:
        if intent.intent not in {ChatIntent.GENERAL_CHAT, ChatIntent.BROWSER_OPERATOR}:
            return None
        if not execution_policy.allow_external_fallback:
            return None
        if not conversation_settings.resolved_internet_enabled:
            return None
        normalized = self._normalize_routing_text(request.content)
        if not normalized or len(normalized) < 18:
            return None

        live_research_request = self._looks_live_research_request(normalized)
        if intent.intent == ChatIntent.BROWSER_OPERATOR and not live_research_request:
            return None

        task_type = self.external_llm_operator._classify_task_type(normalized)
        if live_research_request:
            return AutoExternalRoutingPlan(
                target="claude",
                task_type="research",
                mode_preference="browser",
                reason=(
                    "Auto-routed this turn to Claude browser flow because the request needs live research, "
                    "current links or browsing-aware synthesis."
                ),
                fallback_policy="browser_only",
            )

        premium_requested = runtime_preferences.quality_profile == "premium"
        deep_mode = runtime_preferences.work_mode == "deep"
        if not premium_requested and not deep_mode:
            return None

        if task_type == "coding":
            return AutoExternalRoutingPlan(
                target="claude",
                task_type="coding",
                mode_preference="api",
                reason=(
                    "Auto-routed this turn to Claude because the conversation asked for a higher-quality coding pass."
                ),
            )
        if task_type == "writing":
            return AutoExternalRoutingPlan(
                target="claude",
                task_type="writing",
                mode_preference="api",
                reason=(
                    "Auto-routed this turn to Claude because the conversation asked for a stronger writing or synthesis pass."
                ),
            )
        if task_type == "research":
            return AutoExternalRoutingPlan(
                target="claude",
                task_type="research",
                mode_preference="browser" if deep_mode else "api",
                reason=(
                    "Auto-routed this turn to Claude because the conversation asked for a deeper research-quality answer."
                ),
            )
        if self._looks_quality_sensitive_request(normalized):
            return AutoExternalRoutingPlan(
                target="claude",
                task_type="general",
                mode_preference="api",
                reason=(
                    "Auto-routed this turn to Claude because the prompt asks for a higher-quality analytical or strategic answer."
                ),
            )
        return None

    @staticmethod
    def _normalize_routing_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
        return " ".join(ascii_only.split())

    @staticmethod
    def _looks_live_research_request(normalized: str) -> bool:
        live_research_hints = (
            "sprawdz w necie",
            "sprawdz online",
            "sprawdz aktualne",
            "aktualne informacje",
            "daj linki",
            "podaj linki",
            "zrodla",
            "sources",
            "latest",
            "current",
            "news",
            "research",
            "compare",
            "porownaj",
            "porownanie",
            "dokumentacja",
            "documentation",
            "docs",
        )
        return any(token in normalized for token in live_research_hints)

    @staticmethod
    def _looks_quality_sensitive_request(normalized: str) -> bool:
        quality_hints = (
            "dokladnie",
            "porzadnie",
            "gleboko",
            "doglebnie",
            "krytycznie",
            "strategi",
            "roadmap",
            "architektur",
            "specyfikac",
            "prezentacj",
            "oferta",
            "proposal",
            "plan",
            "analiz",
        )
        return any(token in normalized for token in quality_hints)

    def _handle_ubuntu_agent_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        if "atnos:ubuntu-full-control" not in skill_decision.selected_skills:
            return None
        if not self._has_explicit_ubuntu_context(request.content):
            return None

        adapter = UbuntuAgentAdapter(
            settings=self.settings,
            session=self.session,
            provider_registry=self.provider_registry,
            plugin_registry=self.plugin_registry,
        )
        result = adapter.maybe_run(request.content)
        if result is None:
            return None

        return self._build_ubuntu_agent_message_result(
            conversation_result=conversation_result,
            content=result.response,
            metadata_json={"agent_result": result.to_dict()},
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    def _handle_youtube_upload_preview_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        if not detects_youtube_private_upload_request(request.content):
            return None
        try:
            preview = build_youtube_upload_preview(self.settings)
            content = format_upload_preview(preview)
            metadata = {
                "youtube_upload_preview": preview.to_dict(),
                "approval_required": True,
                "status": "awaiting_upload_approval",
            }
        except Exception as exc:
            content = f"Nie moge przygotowac uploadu YouTube: {type(exc).__name__}: {exc}"
            metadata = {"youtube_upload_error": str(exc), "status": "upload_preview_failed"}
        return self._build_youtube_message_result(
            conversation_result=conversation_result,
            content=content,
            metadata_json=metadata,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
            selected_provider_key="youtube-skill",
            selected_model_key="youtube-upload-preview",
            routing_reason="Prepared supervised YouTube private upload preview.",
        )

    def _handle_youtube_upload_confirmation_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        if not detects_youtube_upload_confirmation(request.content):
            return None
        preview = self._find_pending_youtube_upload_preview(conversation_result.conversation.id)
        if preview is None:
            return None
        try:
            result = execute_youtube_upload(self.settings, preview)
            content = format_upload_result(result)
            metadata = {"youtube_upload_result": result.to_dict(), "status": "uploaded_private"}
        except Exception as exc:
            content = f"Upload YouTube nie powiodl sie: {type(exc).__name__}: {exc}"
            metadata = {"youtube_upload_error": str(exc), "status": "upload_failed"}
        return self._build_youtube_message_result(
            conversation_result=conversation_result,
            content=content,
            metadata_json=metadata,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
            selected_provider_key="youtube-skill",
            selected_model_key="youtube-private-upload",
            routing_reason="Executed approved private YouTube upload.",
        )

    def _handle_browser_operator_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
        runtime_preferences: RuntimeContextPreferences | None = None,
    ) -> ChatOrchestrationResult | None:
        if self._is_explicit_agentic_browser_request(request.content):
            return None
        memory_enabled = bool(runtime_preferences.memory_enabled) if runtime_preferences is not None else True
        operation = self.browser_operator.maybe_handle(
            content=request.content,
            memory_enabled=memory_enabled,
        )
        if not operation.activated or operation.assistant_content is None:
            return None

        return self._build_browser_operator_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    @staticmethod
    def _is_explicit_agentic_browser_request(content: str) -> bool:
        normalized = (content or "").strip().casefold()
        prefixes = ("agentic:", "agent loop:", "tool loop:", "narzędzie:", "narzedzie:")
        return normalized.startswith(prefixes) or bool(
            re.search(r"\b(?:browser_open|browser_tabs|browser_learn)\b", normalized)
        )

    def _handle_external_llm_operator_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        runtime_controls,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult | None:
        operation = self.external_llm_operator.maybe_handle(
            content=request.content,
            allow_external_fallback=execution_policy.allow_external_fallback,
            runtime_controls=runtime_controls,
        )
        if not operation.activated or operation.assistant_content is None:
            return None

        return self._build_external_llm_operator_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            selected_provider_key=operation.selected_provider_key or "external-llm-operator",
            selected_model_key=operation.selected_model_key or "external-llm-operator",
            routing_reason=operation.routing_reason or "Handled by the external LLM operator.",
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    def _handle_desktop_operator_turn(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
        runtime_preferences: RuntimeContextPreferences | None = None,
    ) -> ChatOrchestrationResult | None:
        # An explicit browser tool request belongs to the agentic browser loop.
        # Do not let generic words such as "open" route it to a desktop app.
        if self._is_explicit_agentic_browser_request(request.content):
            return None
        operation = self.desktop_operator.maybe_handle(content=request.content)
        if not operation.activated or operation.assistant_content is None:
            return None

        memory_enabled = True
        if runtime_preferences is not None:
            memory_enabled = bool(runtime_preferences.memory_enabled)
        if memory_enabled and not memory_opt_out(request.content) and operation.action == "desktop_app_open":
            meta = operation.metadata.get("desktop_operator") if isinstance(operation.metadata, dict) else None
            if isinstance(meta, dict):
                from atnos.services.chat_session_memory import persist_desktop_alias_memory

                persist_desktop_alias_memory(
                    settings=self.settings,
                    query=str(meta.get("query") or meta.get("app_alias") or request.content),
                    resolved_alias=str(meta.get("resolved_alias") or "") or None,
                    app_key=str(meta.get("app_key") or "") or None,
                    label=str(meta.get("label") or "") or None,
                    enabled=True,
                    session=self.session,
                )

        return self._build_desktop_operator_message_result(
            conversation_result=conversation_result,
            content=operation.assistant_content,
            metadata_json=operation.metadata or None,
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )

    def _execute_pending_capability_preview(
        self,
        *,
        request: ConversationTurnRequest,
        conversation_result,
        execution_policy,
        pending_preview: dict,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        action = str(pending_preview.get("action"))
        payload = pending_preview.get("payload")
        if not isinstance(payload, dict):
            raise ChatOrchestrationError("Pending capability preview payload is invalid.")
        try:
            execution = self.capability_actions.execute(
                action=action,
                conversation_id=conversation_result.conversation.id,
                project_id=conversation_result.conversation.project_id,
                payload=payload,
            )
        except CapabilityActionError as exc:
            return self._build_capability_message_result(
                conversation_result=conversation_result,
                content=str(exc),
                metadata_json={"capability_error": str(exc)},
                execution_policy=execution_policy,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )

        scheduled_task = execution.get("scheduled_task")
        if isinstance(scheduled_task, dict):
            return self._build_capability_message_result(
                conversation_result=conversation_result,
                content=(
                    f"Zapisałem zadanie cykliczne `{scheduled_task['prompt_template']}`.\n"
                    f"Następny bieg: {scheduled_task['next_run_at'] or 'scheduler wyliczy przy starcie joba'}.\n"
                    f"Cron: `{scheduled_task['cron_spec']}`."
                ),
                metadata_json={
                    "capability_action": action,
                    "triggered_by": scheduled_task["scheduled_task_id"],
                    "scheduled_task": scheduled_task,
                },
                execution_policy=execution_policy,
                skill_decision=skill_decision,
                skill_trace=skill_trace,
            )

        artifact = execution.get("artifact")
        result = self._build_capability_message_result(
            conversation_result=conversation_result,
            content="Wykonałem oczekującą akcję capability.",
            metadata_json={"capability_action": action, "capability_payload": payload},
            execution_policy=execution_policy,
            skill_decision=skill_decision,
            skill_trace=skill_trace,
        )
        if artifact is not None:
            self.capability_actions.attach_generated_artifact(
                conversation_id=conversation_result.conversation.id,
                message_id=result.assistant_message.id,
                artifact=artifact,
            )
            self.session.commit()
        return result

    def _build_capability_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key="local-capability",
            selected_model_key="local-capability",
            routing_reason="Handled by local capability execution.",
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by a local capability."
                self.session.add(trace)
        capability_trace = self.execution_traces.create(
            step_name="capability-execution",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            input_summary=f"local_capability role={conversation_result.user_message.role.value}",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.DOCUMENT_WORKFLOW,
            selected_model_key="local-capability",
            selected_provider_key="local-capability",
            routing_reason="Handled by local capability execution.",
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, capability_trace),
            skill_decision=skill_decision,
        )

    def _build_quick_chat_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key="local-quick-chat",
            selected_model_key="local-quick-chat",
            routing_reason="Handled by the local quick-chat operator.",
        )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the local quick-chat operator."
                self.session.add(trace)
        quick_trace = self.execution_traces.create(
            step_name="quick-chat-operator",
            trace_kind=ExecutionTraceKind.ANSWER,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            input_summary="local_quick_chat",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.GENERAL_CHAT,
            selected_model_key="local-quick-chat",
            selected_provider_key="local-quick-chat",
            routing_reason="Handled by the local quick-chat operator.",
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, quick_trace),
            skill_decision=skill_decision,
        )

    def _build_agentic_tool_loop_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        intent: ChatIntent,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
        status: ExecutionTraceStatus,
        routing_reason: str,
        runtime_model_name: str | None = None,
    ) -> ChatOrchestrationResult:
        prepared_metadata = dict(metadata_json or {})
        agentic_payload = prepared_metadata.get("agentic_tool_loop")
        agentic_payload = agentic_payload if isinstance(agentic_payload, dict) else {}
        direct_local = prepared_metadata.get("executed_provider") == "local-tools"
        result_provider = "local-tools" if direct_local else "agentic-ollama-tools"
        result_model = (runtime_model_name or "local-tools/readonly") if direct_local else "ollama-local/agentic-tools"
        raw_tool_calls = agentic_payload.get("tool_calls")
        tool_names = [
            str(item.get("name"))
            for item in raw_tool_calls
            if isinstance(item, dict) and item.get("name")
        ] if isinstance(raw_tool_calls, list) else []
        skill_trace.metadata_json = {
            **(skill_trace.metadata_json or {}),
            "actual_tool_invocations": tool_names,
            "tool_invocation_count": len(tool_names),
            "tool_loop_status": agentic_payload.get("status", status.value),
        }
        self.session.add(skill_trace)
        if runtime_model_name:
            prepared_metadata.setdefault("actual_runtime_model_name", runtime_model_name)
            prepared_metadata.setdefault("executed_model", runtime_model_name)
            prepared_metadata.setdefault("executed_provider", "ollama-local")
            prepared_metadata.setdefault("fallback_used", False)
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=prepared_metadata,
            skill_decision=skill_decision,
            selected_provider_key=result_provider,
            selected_model_key=result_model,
            routing_reason=routing_reason,
        )
        if status == ExecutionTraceStatus.FAILED:
            assistant_message.status = MessageStatus.FAILED
            self.session.add(assistant_message)
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the agentic tool loop."
                self.session.add(trace)
        agentic_trace = self.execution_traces.create(
            step_name="agentic-tool-loop",
            trace_kind=ExecutionTraceKind.PROVIDER,
            status=status,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key="local-tools" if direct_local else "ollama-local",
            model_key=runtime_model_name or "ollama-local/agentic-tools",
            input_summary="explicit_agentic_tool_loop",
            output_summary=content[:240],
            metadata_json=prepared_metadata,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=intent,
            selected_model_key=result_model,
            selected_provider_key=result_provider,
            routing_reason=routing_reason,
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, agentic_trace),
            skill_decision=skill_decision,
        )

    def _build_creative_asset_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        selected_provider_key: str,
        selected_model_key: str,
        routing_reason: str,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key=selected_provider_key,
            selected_model_key=selected_model_key,
            routing_reason=routing_reason,
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the creative asset operator."
                self.session.add(trace)
        creative_trace = self.execution_traces.create(
            step_name="creative-asset-operator",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key=selected_provider_key,
            model_key=selected_model_key,
            input_summary="creative_asset_operator",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.CREATIVE_ASSET,
            selected_model_key=selected_model_key,
            selected_provider_key=selected_provider_key,
            routing_reason=routing_reason,
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, creative_trace),
            skill_decision=skill_decision,
        )

    def _build_creative_video_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        selected_provider_key: str,
        selected_model_key: str,
        routing_reason: str,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key=selected_provider_key,
            selected_model_key=selected_model_key,
            routing_reason=routing_reason,
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the creative video operator."
                self.session.add(trace)
        creative_trace = self.execution_traces.create(
            step_name="creative-video-operator",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key=selected_provider_key,
            model_key=selected_model_key,
            input_summary="creative_video_operator",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.CREATIVE_VIDEO,
            selected_model_key=selected_model_key,
            selected_provider_key=selected_provider_key,
            routing_reason=routing_reason,
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, creative_trace),
            skill_decision=skill_decision,
        )

    def _build_browser_operator_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        browser_meta = (metadata_json or {}).get("browser_operator", {})
        browser_failed = isinstance(browser_meta, dict) and (browser_meta.get("status") in {"incomplete", "error", "learn_failed", "not_found"} or bool(browser_meta.get("error")))
        instruction_context = browser_meta.get("instruction_context") if isinstance(browser_meta, dict) else None
        if isinstance(instruction_context, dict):
            skill_trace.metadata_json = {
                **(skill_trace.metadata_json or {}),
                "instruction_context": instruction_context,
                "instruction_context_applied_to": "native_browser_summary_prompt",
            }
            self.session.add(skill_trace)
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key="local-browser-operator",
            selected_model_key="local-browser-operator",
            routing_reason="Handled by the local browser operator.",
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the local browser operator."
                self.session.add(trace)
        browser_trace = self.execution_traces.create(
            step_name="browser-operator",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.FAILED if browser_failed else ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            input_summary="local_browser_operator",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.BROWSER_OPERATOR,
            selected_model_key="local-browser-operator",
            selected_provider_key="local-browser-operator",
            routing_reason="Handled by the local browser operator.",
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, browser_trace),
            skill_decision=skill_decision,
        )

    def _build_ubuntu_agent_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key="ubuntu-sandbox-agent",
            selected_model_key="agent-orchestrator",
            routing_reason="Handled by Ubuntu sandbox agent through SSH.",
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the Ubuntu sandbox agent."
                self.session.add(trace)
        ubuntu_trace = self.execution_traces.create(
            step_name="ubuntu-sandbox-agent",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key="ubuntu-sandbox-agent",
            model_key="agent-orchestrator",
            input_summary="ubuntu_full_access_skill",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.GENERAL_CHAT,
            selected_model_key="agent-orchestrator",
            selected_provider_key="ubuntu-sandbox-agent",
            routing_reason="Handled by Ubuntu sandbox agent through SSH.",
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, ubuntu_trace),
            skill_decision=skill_decision,
        )

    def _build_youtube_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
        selected_provider_key: str,
        selected_model_key: str,
        routing_reason: str,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key=selected_provider_key,
            selected_model_key=selected_model_key,
            routing_reason=routing_reason,
        )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the YouTube skill."
                self.session.add(trace)
        youtube_trace = self.execution_traces.create(
            step_name="youtube-skill",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key=selected_provider_key,
            model_key=selected_model_key,
            input_summary="youtube_private_upload",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.GENERAL_CHAT,
            selected_model_key=selected_model_key,
            selected_provider_key=selected_provider_key,
            routing_reason=routing_reason,
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, youtube_trace),
            skill_decision=skill_decision,
        )

    def _build_channel_ops_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        selected_provider_key: str,
        selected_model_key: str,
        routing_reason: str,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key=selected_provider_key,
            selected_model_key=selected_model_key,
            routing_reason=routing_reason,
        )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by ChannelOps."
                self.session.add(trace)
        channel_ops_trace = self.execution_traces.create(
            step_name="channel-ops",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key=selected_provider_key,
            model_key=selected_model_key,
            input_summary="channel_ops",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.GENERAL_CHAT,
            selected_model_key=selected_model_key,
            selected_provider_key=selected_provider_key,
            routing_reason=routing_reason,
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, channel_ops_trace),
            skill_decision=skill_decision,
        )

    def _build_desktop_operator_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        desktop_meta = (metadata_json or {}).get('desktop_operator', {})
        desktop_failed = isinstance(desktop_meta, dict) and (
            desktop_meta.get('status') in {'error', 'failed', 'not_found', 'blocked', 'ambiguous', 'incomplete'}
            or bool(desktop_meta.get('error'))
            or (isinstance(desktop_meta.get('command_exit_code'), int)
                and desktop_meta['command_exit_code'] != 0)
        )
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key="local-desktop-operator",
            selected_model_key="local-desktop-operator",
            routing_reason="Handled by the local desktop operator.",
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the local desktop operator."
                self.session.add(trace)
        desktop_trace = self.execution_traces.create(
            step_name="desktop-operator",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.FAILED if desktop_failed else ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            input_summary="local_desktop_operator",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.DESKTOP_OPERATOR,
            selected_model_key="local-desktop-operator",
            selected_provider_key="local-desktop-operator",
            routing_reason="Handled by the local desktop operator.",
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, desktop_trace),
            skill_decision=skill_decision,
        )

    def _build_external_llm_operator_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        selected_provider_key: str,
        selected_model_key: str,
        routing_reason: str,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key=selected_provider_key,
            selected_model_key=selected_model_key,
            routing_reason=routing_reason,
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the external LLM operator."
                self.session.add(trace)
        operator_trace = self.execution_traces.create(
            step_name="external-llm-operator",
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            provider_key=selected_provider_key,
            model_key=selected_model_key,
            input_summary="external_llm_operator",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.EXTERNAL_LLM_OPERATOR,
            selected_model_key=selected_model_key,
            selected_provider_key=selected_provider_key,
            routing_reason=routing_reason,
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, operator_trace),
            skill_decision=skill_decision,
        )

    def _build_prompt_optimizer_message_result(
        self,
        *,
        conversation_result,
        content: str,
        metadata_json: dict | None,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key="local-prompt-optimizer",
            selected_model_key="local-prompt-optimizer",
            routing_reason="Handled by the local prompt optimizer.",
        )
        owner = self.users.get_owner()
        runtime_controls = self.runtime_settings.snapshot()
        if owner is not None:
            self._attach_voice_reply_best_effort(
                conversation_id=conversation_result.conversation.id,
                message_id=assistant_message.id,
                owner_id=owner.id,
                assistant_content=content,
                runtime_controls=runtime_controls,
                request_metadata_json=conversation_result.user_message.metadata_json,
            )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because the response was handled by the local prompt optimizer."
                self.session.add(trace)
        prompt_trace = self.execution_traces.create(
            step_name="prompt-optimizer",
            trace_kind=ExecutionTraceKind.ANSWER,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            input_summary="local_prompt_optimizer",
            output_summary=content[:240],
            metadata_json=metadata_json,
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=ChatIntent.PROMPT_OPTIMIZATION,
            selected_model_key="local-prompt-optimizer",
            selected_provider_key="local-prompt-optimizer",
            routing_reason="Handled by the local prompt optimizer.",
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, prompt_trace),
            skill_decision=skill_decision,
        )

    def _build_skill_routing_message_result(
        self,
        *,
        conversation_result,
        intent: ChatIntent,
        content: str,
        execution_policy,
        skill_decision: SkillRouteDecision,
        skill_trace: ExecutionTrace,
    ) -> ChatOrchestrationResult:
        assistant_message = self._create_assistant_message(
            conversation_id=conversation_result.conversation.id,
            parent_message_id=conversation_result.user_message.id,
            content=content,
            metadata_json=None,
            skill_decision=skill_decision,
            selected_provider_key="local-skill-router",
            selected_model_key="local-skill-router",
            routing_reason="Paused at deterministic skill routing because required inputs are missing.",
        )
        for trace in conversation_result.traces:
            if trace.step_name == "assistant-response-placeholder" and trace.status == ExecutionTraceStatus.PENDING:
                trace.status = ExecutionTraceStatus.SKIPPED
                trace.output_summary = "Skipped because skill routing is waiting for missing user input."
                self.session.add(trace)
        routing_trace = self.execution_traces.create(
            step_name="skill-routing-response",
            trace_kind=ExecutionTraceKind.ANSWER,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation_result.conversation.id,
            message_id=assistant_message.id,
            input_summary="skill_router_missing_inputs",
            output_summary=content[:240],
            metadata_json={"skill_decision": self._serialize_skill_decision(skill_decision)},
        )
        self.session.commit()
        self._extract_decisions_best_effort(conversation_result.conversation.id)
        return ChatOrchestrationResult(
            conversation=conversation_result.conversation,
            user_message=conversation_result.user_message,
            assistant_message=assistant_message,
            intent=intent,
            selected_model_key="local-skill-router",
            selected_provider_key="local-skill-router",
            routing_reason="Paused at deterministic skill routing because required inputs are missing.",
            draft_tasks=tuple(),
            traces=conversation_result.traces + (skill_trace, routing_trace),
            skill_decision=skill_decision,
        )

    def _build_skill_routing_task_text(self, request: ConversationTurnRequest) -> str:
        if request.attachments:
            attachment_hints = ", ".join(
                filter(
                    None,
                    (
                        f"{attachment.kind.value}:{attachment.filename}"
                        for attachment in request.attachments
                    ),
                )
            )
            return f"{request.content}\nattachments: {attachment_hints}".strip()
        return request.content

    @staticmethod
    def _build_quick_chat_skill_decision(content: str) -> SkillRouteDecision:
        return SkillRouteDecision(
            task=content,
            # This fast path is deterministic application code. It skips skill
            # routing and does not load local-LLM instructions, so it must not
            # claim a skill was selected or executed.
            selected_skills=[],
            rejected_skills=[],
            reasoning_summary="No skill loaded; deterministic local quick-chat handled the message.",
            required_inputs=[],
            risks=[],
            next_action="respond via deterministic quick-chat operator without provider call",
            confidence=1.0,
        )

    @staticmethod
    def _parse_agentic_tool_loop_prompt(content: str) -> str | None:
        return ChatOrchestrationService._resolve_agentic_tool_loop_prompt(content)

    def _has_missing_obvious_skill_inputs(
        self,
        *,
        skill_decision: SkillRouteDecision,
        request: ConversationTurnRequest,
    ) -> bool:
        if not skill_decision.required_inputs:
            return False
        normalized = request.content.casefold()
        if "atnos:ubuntu-full-control" in skill_decision.selected_skills:
            return not self._has_explicit_ubuntu_context(normalized)
        if "atnos:youtube-automation" in skill_decision.selected_skills and detects_youtube_private_upload_request(
            request.content
        ):
            return False
        if request.attachments:
            return False
        if re.search(r"\b[a-z]:[\\/]", normalized):
            return False
        return any(
            self._required_input_needs_explicit_file(item)
            for item in skill_decision.required_inputs
        )

    @staticmethod
    def _has_explicit_ubuntu_context(content: str) -> bool:
        normalized = content.casefold()
        return any(
            token in normalized
            for token in ("ssh", "host", "ubuntu", "user", "klucz", "key", "laptop", "sandbox", "198.51.100.216")
        )

    def _build_missing_skill_inputs_message(self, skill_decision: SkillRouteDecision) -> str:
        selected = ", ".join(skill_decision.selected_skills)
        inputs = ", ".join(skill_decision.required_inputs)
        risks = ", ".join(skill_decision.risks) or "brak"
        return (
            f"Do tego zadania wybralbym: {selected}.\n"
            f"Brakuje mi jeszcze inputow: {inputs}.\n"
            f"Ryzyka: {risks}.\n"
            f"Nastepny krok: {skill_decision.next_action}"
        )

    def _merge_skill_metadata(
        self,
        metadata_json: dict | None,
        skill_decision: SkillRouteDecision,
    ) -> dict:
        payload = dict(metadata_json or {})
        payload["skill_decision"] = self._serialize_skill_decision(skill_decision)
        return payload

    @staticmethod
    def _serialize_skill_decision(skill_decision: SkillRouteDecision) -> dict[str, object]:
        return skill_decision.model_dump()

    def _build_llm_router_metadata(
        self,
        *,
        content: str,
        has_image: bool,
        intent_model_task: ModelTask,
        provider_posture: str,
        selected_provider_key: str,
        selected_model_key: str,
        runtime_model_name: str | None,
    ) -> dict[str, object]:
        router_task_type = self._resolve_llm_router_task_type(
            content=content,
            has_image=has_image,
            intent_model_task=intent_model_task,
        )
        router_backend = self.llm_router.select_backend(
            router_task_type,
            privacy_mode=self._resolve_llm_router_privacy_mode(provider_posture),
        )
        payload: dict[str, object] = {
            "task_type": router_task_type.value,
            "privacy_mode": provider_posture,
            "recommended_provider_key": router_backend.provider,
            "recommended_model": router_backend.model,
            "recommendation_reason": router_backend.routing_reason,
            "recommended_supports_tools": router_backend.supports_tools,
            "recommended_supports_vision": router_backend.supports_vision,
            "actual_provider_key": selected_provider_key,
            "actual_model_key": selected_model_key,
        }
        if runtime_model_name:
            payload["actual_runtime_model_name"] = runtime_model_name
        return payload

    def _resolve_llm_router_task_type(
        self,
        *,
        content: str,
        has_image: bool,
        intent_model_task: ModelTask,
    ) -> TaskType:
        if has_image or intent_model_task == ModelTask.VISION:
            return TaskType.VISION
        if intent_model_task == ModelTask.SYNTHESIS:
            return TaskType.CONTENT
        if intent_model_task in {ModelTask.ORCHESTRATION, ModelTask.CODING}:
            return TaskType.AGENT
        if intent_model_task == ModelTask.SEARCH_RESEARCH:
            return TaskType.PREMIUM
        return self.llm_router.detect_task_type(content, has_image=False)

    @staticmethod
    def _resolve_llm_router_privacy_mode(provider_posture: str) -> PrivacyMode:
        if provider_posture == "strict-local":
            return PrivacyMode.STRICT_LOCAL
        return PrivacyMode.ALLOW_CLOUD

    def _create_assistant_message(
        self,
        *,
        conversation_id: int,
        parent_message_id: int,
        content: str,
        skill_decision: SkillRouteDecision,
        metadata_json: dict | None = None,
        selected_provider_key: str | None = None,
        selected_model_key: str | None = None,
        routing_reason: str | None = None,
        provider_posture: str | None = None,
        task_type: str | None = None,
        llm_router_metadata: dict[str, object] | None = None,
    ) -> Message:
        prepared_content, prepared_metadata_json = self._prepare_assistant_message_payload(
            content=content,
            metadata_json=metadata_json,
            skill_decision=skill_decision,
            selected_provider_key=selected_provider_key,
            selected_model_key=selected_model_key,
            routing_reason=routing_reason,
            provider_posture=provider_posture,
            task_type=task_type,
            llm_router_metadata=llm_router_metadata,
        )
        # Persist provenance with the answer, so reloading history retains the
        # same evidence as the streaming response. Only this turn's server-side
        # routing trace is authoritative; client-supplied metadata is not.
        routing_trace = next((
            trace for trace in reversed(self.execution_traces.list_for_message(parent_message_id))
            if trace.step_name == "skill-routing" and trace.conversation_id == conversation_id
        ), None)
        context = (routing_trace.metadata_json or {}).get("instruction_context", {}) if routing_trace else {}
        context = context if isinstance(context, dict) else {}
        loaded = [item for item in context.get("loaded_instructions", []) if isinstance(item, dict)]
        prepared_metadata_json["atnos_skill_execution"] = {
            "selectedSkills": list(skill_decision.selected_skills),
            "reasoningSummary": skill_decision.reasoning_summary,
            "instructionStatus": context.get("instruction_status", "not_loaded"),
            "loadedInstructionSkills": [item["skill_id"] for item in loaded if isinstance(item.get("skill_id"), str)],
            "loadedInstructionSources": loaded,
            "missingInstructionSkills": context.get("missing_instruction_skills", []),
        }
        return self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=prepared_content,
            parent_message_id=parent_message_id,
            metadata_json=prepared_metadata_json,
        )

    def _prepare_assistant_message_payload(
        self,
        *,
        content: str,
        metadata_json: dict | None,
        skill_decision: SkillRouteDecision,
        selected_provider_key: str | None = None,
        selected_model_key: str | None = None,
        routing_reason: str | None = None,
        provider_posture: str | None = None,
        task_type: str | None = None,
        llm_router_metadata: dict[str, object] | None = None,
    ) -> tuple[str, dict]:
        cleaned_content, structured_question_metadata = self._parse_structured_question(content)
        payload = dict(metadata_json or {})
        if structured_question_metadata is not None:
            payload.update(structured_question_metadata)
        payload["skill_decision"] = self._serialize_skill_decision(skill_decision)
        if selected_provider_key is not None:
            payload["selected_provider_key"] = selected_provider_key
        if selected_model_key is not None:
            payload["selected_model_key"] = selected_model_key
        if routing_reason is not None:
            payload["routing_reason"] = routing_reason
        if provider_posture is not None:
            payload["provider_posture"] = provider_posture
        if task_type is not None:
            payload["task_type"] = task_type
        if llm_router_metadata is not None:
            payload["llm_router"] = llm_router_metadata
            router_task_type = llm_router_metadata.get("task_type")
            router_provider_key = llm_router_metadata.get("recommended_provider_key")
            router_model = llm_router_metadata.get("recommended_model")
            router_reason = llm_router_metadata.get("recommendation_reason")
            actual_runtime_model = llm_router_metadata.get("actual_runtime_model_name")
            if isinstance(router_task_type, str):
                payload["llm_router_task_type"] = router_task_type
            if isinstance(router_provider_key, str):
                payload["llm_router_provider_key"] = router_provider_key
            if isinstance(router_model, str):
                payload["llm_router_model"] = router_model
            if isinstance(router_reason, str):
                payload["llm_router_reason"] = router_reason
            if isinstance(actual_runtime_model, str):
                payload["actual_runtime_model_name"] = actual_runtime_model
        return cleaned_content, payload

    @staticmethod
    def _parse_structured_question(content: str) -> tuple[str, dict | None]:
        pattern = r"```atnos-question\s*\n(.*?)\n```"
        match = re.search(pattern, content, re.DOTALL)
        if match is None:
            return content, None
        try:
            parsed = json.loads(match.group(1))
            question = StructuredQuestion.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Malformed atnos-question block: %s", exc)
            return content, None
        cleaned = f"{content[:match.start()]}{content[match.end():]}".strip()
        metadata = {
            "kind": "question_choice",
            "question_id": question.question_id,
            "prompt": question.prompt,
            "options": [option.model_dump(exclude_none=True) for option in question.options],
            "multi_select": question.multi_select,
            "required": question.required,
            "helper_text": question.helper_text,
        }
        return cleaned, metadata

    @staticmethod
    def _build_structured_questions_system_section() -> str:
        return (
            "STRUCTURED_QUESTIONS:\n"
            "Gdy potrzebujesz wyboru od uzytkownika z 2-6 opcji, zamiast pytac plain text emit JSON code block z language `atnos-question`.\n"
            "Schema:\n"
            "```atnos-question\n"
            "{\n"
            '  "question_id": "<uuid v4>",\n'
            '  "prompt": "<pytanie po polsku>",\n'
            '  "options": [\n'
            '    {"id": "a", "label": "<krotka opcja>", "description": "<opcjonalnie>"},\n'
            '    {"id": "b", "label": "<krotka opcja>", "description": "<opcjonalnie>"}\n'
            "  ],\n"
            '  "multi_select": false,\n'
            '  "required": true,\n'
            '  "helper_text": "<opcjonalnie podpowiedz>"\n'
            "}\n"
            "```\n"
            "Wymagania:\n"
            "- minimum 2 opcje, maksimum 6\n"
            "- ids unikalne, krotkie ('a', 'b', 'c', ...)\n"
            "- labels krotkie (max ~40 znakow)\n"
            "- descriptions tylko gdy realnie pomagaja zrozumiec wybor\n"
            "- multi_select=true tylko gdy opcje sa kompatybilne (mozna wybrac kilka)\n"
            "NIE uzywaj structured questions dla:\n"
            "- free-form answer\n"
            "- jednoznacznej opinii typu tak/nie\n"
            "- sytuacji, gdy chcesz aby user pomyslal samodzielnie\n"
            "Atnos UI zamieni taki blok na interaktywna karte i moze kontynuowac rozmowe na podstawie wybranego wariantu."
        )

    @staticmethod
    def _find_structured_question_message(
        *,
        messages: list[Message],
        question_id: str,
    ) -> Message | None:
        for message in reversed(messages):
            if message.role != MessageRole.ASSISTANT or not isinstance(message.metadata_json, dict):
                continue
            if (
                message.metadata_json.get("kind") == "question_choice"
                and message.metadata_json.get("question_id") == question_id
            ):
                return message
        return None

    @staticmethod
    def _find_choice_response_message(
        *,
        messages: list[Message],
        question_id: str,
        selected_ids: list[str],
    ) -> Message | None:
        expected = tuple(selected_ids)
        for message in reversed(messages):
            if message.role != MessageRole.USER or not isinstance(message.metadata_json, dict):
                continue
            if message.metadata_json.get("kind") != "choice_response":
                continue
            if message.metadata_json.get("question_id") != question_id:
                continue
            response_ids = message.metadata_json.get("selected_ids")
            if list(response_ids or []) == list(expected):
                return message
        return None

    def _build_answered_question_context_section(
        self,
        *,
        question_metadata: dict[str, object],
        selected_ids: list[str],
    ) -> str:
        options = question_metadata.get("options")
        if not isinstance(options, list):
            options = []
        labels = [
            str(option.get("label"))
            for option in options
            if isinstance(option, dict) and str(option.get("id")) in selected_ids
        ]
        option_lines = [
            f"- {str(option.get('id'))}: {str(option.get('label'))}"
            for option in options
            if isinstance(option, dict)
        ]
        prompt = str(question_metadata.get("prompt") or "").strip()
        return "\n".join(
            [
                "STRUCTURED_QUESTION_ANSWER:",
                f"question_id={str(question_metadata.get('question_id') or '')}",
                f"prompt={prompt}",
                f"user_answer_labels={', '.join(labels) if labels else ', '.join(selected_ids)}",
                f"user_answer_ids={', '.join(selected_ids)}",
                "available_options:",
                *option_lines,
                "Continue the conversation using this answer as explicit user input. Do not ask the same question again unless the user changes course or asks for alternatives.",
            ]
        )

    def _create_follow_up_traces(
        self,
        *,
        conversation: Conversation,
        user_message: Message,
        selected_ids: list[str],
    ) -> tuple[ExecutionTrace, ExecutionTrace]:
        context_trace = self.execution_traces.create(
            step_name="conversation-turn-ingest",
            trace_kind=ExecutionTraceKind.CONTEXT,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation.id,
            message_id=user_message.id,
            input_summary=f"choice_response selected={len(selected_ids)}",
            output_summary="conversation context assembled",
        )
        answer_trace = self.execution_traces.create(
            step_name="assistant-response-placeholder",
            trace_kind=ExecutionTraceKind.ANSWER,
            status=ExecutionTraceStatus.PENDING,
            conversation_id=conversation.id,
            message_id=user_message.id,
            input_summary="awaiting provider execution",
        )
        self.conversation_service.conversations.touch_last_message(conversation)
        self.session.flush()
        return (context_trace, answer_trace)

    @staticmethod
    def _required_input_needs_explicit_file(required_input: str) -> bool:
        normalized = ChatOrchestrationService._normalize_skill_input_text(required_input)
        if not normalized:
            return False
        file_tokens = ("plik", "sciezka", "pdf", "docx", "pptx", "xlsx", "csv")
        flexible_tokens = (
            "brief",
            "opis",
            "cel",
            "zakres",
            "nazwa",
            "vault",
            "katalog",
            "repozytorium",
            "repo",
            "link",
            "url",
            "screenshot",
            "zrzut",
            "makieta",
            "flow",
            "html",
            "markdown",
            "notat",
        )
        if not any(
            ChatOrchestrationService._contains_skill_input_token(normalized, token)
            for token in file_tokens
        ):
            return False
        if any(
            ChatOrchestrationService._contains_skill_input_token(normalized, token)
            for token in flexible_tokens
        ):
            return False
        return True

    def _attach_generated_image_artifact(
        self,
        *,
        conversation_id: int,
        message_id: int,
        artifact: dict[str, object],
    ) -> None:
        self.attachments.create(
            filename=str(artifact["filename"]),
            storage_path=str(artifact["storage_path"]),
            kind=AttachmentKind.IMAGE,
            conversation_id=conversation_id,
            message_id=message_id,
            source=AttachmentSource.GENERATED,
            status=AttachmentStatus.AVAILABLE,
            media_type=str(artifact["mime"]),
            file_extension=str(artifact["file_extension"]),
            size_bytes=int(artifact["size_bytes"]),
            extracted_text=None,
            metadata_json={"generated": True, "pipeline": "creative_asset_operator"},
        )

    def _attach_generated_video_artifacts(
        self,
        *,
        conversation_id: int,
        message_id: int,
        artifacts: tuple[dict[str, object], ...],
    ) -> None:
        for artifact in artifacts:
            extension = str(artifact["file_extension"])
            self.attachments.create(
                filename=str(artifact["filename"]),
                storage_path=str(artifact["storage_path"]),
                kind=self._attachment_kind_for_extension(extension),
                conversation_id=conversation_id,
                message_id=message_id,
                source=AttachmentSource.GENERATED,
                status=AttachmentStatus.AVAILABLE,
                media_type=str(artifact["mime"]),
                file_extension=extension,
                size_bytes=int(artifact["size_bytes"]),
                extracted_text=None,
                metadata_json={
                    "generated": True,
                    "pipeline": "creative_video_operator",
                    "artifact_kind": str(artifact.get("kind") or "artifact"),
                },
            )

    @staticmethod
    def _attachment_kind_for_extension(extension: str) -> AttachmentKind:
        normalized = extension.lower().strip()
        if normalized in {".png", ".jpg", ".jpeg", ".webp"}:
            return AttachmentKind.IMAGE
        if normalized in {".wav", ".mp3"}:
            return AttachmentKind.AUDIO
        if normalized in {".mp4", ".mov"}:
            return AttachmentKind.VIDEO
        if normalized in {".md", ".txt", ".json", ".srt"}:
            return AttachmentKind.TEXT
        return AttachmentKind.DOCUMENT

    @staticmethod
    def _contains_skill_input_token(normalized_text: str, token: str) -> bool:
        pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None

    @staticmethod
    def _normalize_skill_input_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(value))
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        collapsed = " ".join(ascii_only.casefold().split())
        collapsed = re.sub(r"[^a-z0-9:_./\\ -]+", " ", collapsed)
        return " ".join(collapsed.split())

    def _find_pending_capability_preview(self, conversation_id: int) -> dict[str, object] | None:
        messages = self.messages.list_for_conversation(conversation_id)
        for message in reversed(messages):
            if message.role != MessageRole.ASSISTANT:
                continue
            if not isinstance(message.metadata_json, dict):
                continue
            preview = message.metadata_json.get("pending_capability_action")
            if isinstance(preview, dict):
                return preview
        return None

    def _find_pending_youtube_upload_preview(self, conversation_id: int) -> dict[str, object] | None:
        messages = self.messages.list_for_conversation(conversation_id)
        for message in reversed(messages):
            if message.role != MessageRole.ASSISTANT:
                continue
            if not isinstance(message.metadata_json, dict):
                continue
            if message.metadata_json.get("status") not in {"awaiting_upload_approval", "upload_preview_failed"}:
                continue
            preview = message.metadata_json.get("youtube_upload_preview")
            if isinstance(preview, dict):
                return preview
        return None

    @staticmethod
    def _looks_like_confirmation(content: str) -> bool:
        normalized = " ".join((content or "").lower().split())
        return normalized in {
            "potwierdz",
            "potwierdzam",
            "tak",
            "tak, potwierdzam",
            "ok",
            "ok, potwierdz",
        }
