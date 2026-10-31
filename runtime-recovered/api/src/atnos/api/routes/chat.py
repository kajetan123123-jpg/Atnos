from __future__ import annotations

import asyncio
import json
import queue
import threading
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import TYPE_CHECKING, Any, Literal
from atnos.llm.base import LLMRunCancelled

if TYPE_CHECKING:
    pass

_ChatDeps = tuple[
    "type[_ChatOrchestrationError]",
    "type[_ChatOrchestrationProviderError]",
    "type[_ChatOrchestrationService]",
    "type[_AttachmentDraft]",
    "type[_ConversationTurnRequest]",
    "type[_ActiveDocumentAction]",
    "type[_ConversationExecutionPreferences]",
    "type[_ExternalConfirmationRequiredError]",
    "type[_ModelSelectionError]",
    "type[_AttachmentKind]",
]

AttachmentKindInput = Literal["document", "image", "audio", "video", "text", "other"]


router = APIRouter(tags=["chat"])


class ChatStreamCancelled(LLMRunCancelled):
    """Raised when the SSE client disconnects mid-turn."""


_QUEUE_TIMEOUT = object()


def _queue_get_with_timeout(event_queue: queue.Queue, timeout_s: float):
    try:
        return event_queue.get(timeout=timeout_s)
    except queue.Empty:
        return _QUEUE_TIMEOUT


def _load_chat_dependencies() -> _ChatDeps:
    """Keep heavy orchestration imports out of app factory startup."""
    from atnos.services.chat_orchestration_service import (
        ChatOrchestrationError,
        ChatOrchestrationProviderError,
        ChatOrchestrationService,
    )
    from atnos.services.conversation_service import (
        ActiveDocumentAction,
        AttachmentDraft,
        ConversationTurnRequest,
        ExecutionPreferences as ConversationExecutionPreferences,
    )
    from atnos.services.model_selection_service import (
        ExternalConfirmationRequiredError,
        ModelSelectionError,
    )
    from atnos.db.models.enums import AttachmentKind

    return (
        ChatOrchestrationError,
        ChatOrchestrationProviderError,
        ChatOrchestrationService,
        AttachmentDraft,
        ConversationTurnRequest,
        ActiveDocumentAction,
        ConversationExecutionPreferences,
        ExternalConfirmationRequiredError,
        ModelSelectionError,
        AttachmentKind,
    )


class ChatBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatAttachmentInput(ChatBaseModel):
    filename: str
    storage_path: str
    kind: AttachmentKindInput
    detected_kind: str | None = None
    understanding_status: str | None = None
    understanding_reason: str | None = None
    preview_text: str | None = None
    truncated: bool = False
    media_type: str | None = None
    file_extension: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    extracted_text: str | None = None


class ChatExecutionPreferencesInput(ChatBaseModel):
    response_style: (
        Literal["concise-direct", "professional-clear", "supportive-guide", "critical-deep"] | None
    ) = None
    persona_profile: str | None = None
    provider_posture: Literal[
        "prefer-local",
        "improve-when-needed",
        "ask-before-external",
        "strict-local",
    ] | None = None
    quality_profile: Literal["fast-turnaround", "standard", "premium"] | None = None
    memory_enabled: bool | None = None
    file_enabled: bool | None = None
    internet_enabled: bool | None = None
    active_work_mode: Literal["auto", "quick", "deep", "execute"] | None = None


class ChatActiveDocumentActionInput(ChatBaseModel):
    attachment_id: str = Field(min_length=1)
    action: Literal[
        "summarize_document",
        "extract_checklist",
        "extract_risks_decisions",
        "make_action_plan",
    ]


class ChatRequest(ChatBaseModel):
    content: str = ""
    conversation_id: int | None = Field(default=None, gt=0)
    conversation_title: str | None = None
    project_id: int | None = Field(default=None, gt=0)
    role_profile_id: int | None = Field(default=None, gt=0)
    prompt_improved: bool = False
    force_external: bool = False
    execution_preferences: ChatExecutionPreferencesInput | None = None
    active_document_action: ChatActiveDocumentActionInput | None = None
    attachments: list[ChatAttachmentInput] = Field(default_factory=list)
    # Voice companions (Okropek) mark turns so replies can attach TTS / provenance.
    message_metadata_json: dict[str, object] | None = None
    assistant_metadata_json: dict[str, object] | None = None


class PromptImproveRequest(ChatBaseModel):
    content: str = Field(min_length=1)


class PromptImproveResponse(BaseModel):
    original_content: str
    improved_content: str
    strategy_note: str
    changed: bool


class ChatTraceSummary(BaseModel):
    kind: str
    status: str
    step_name: str
    provider_key: str | None = None
    model_key: str | None = None
    metadata_json: dict | None = None


class DraftTaskSummary(BaseModel):
    title: str
    kind: str
    source: str
    confidence: str
    requires_confirmation: bool


class RuntimeErrorDetailResponse(BaseModel):
    failure_type: Literal[
        "runtime_unreachable",
        "model_missing",
        "request_timeout",
        "runtime_busy",
        "invalid_endpoint",
        "capability_mismatch",
        "context_too_large",
        "generation_error",
        "unknown",
    ]
    provider_key: str
    model_key: str
    runtime_model_name: str | None
    user_title: str
    user_detail: str
    retryable: bool
    can_fallback: bool
    suggested_actions: list[str]
    diagnostics: dict[str, object] | None = None


class SkillDecisionResponse(BaseModel):
    selected_skills: list[str]
    rejected_skills: list[str]
    reasoning_summary: str
    required_inputs: list[str]
    risks: list[str]
    next_action: str
    confidence: float


class ChatResponse(BaseModel):
    conversation_id: int
    conversation_title: str
    conversation_project_id: int | None
    user_message_id: int
    assistant_message_id: int
    detected_intent: str
    user_message_attachment_only: bool
    selected_provider_key: str
    selected_model_key: str
    routing_reason: str
    assistant_message_metadata_json: dict[str, object]
    user_message: str
    assistant_message: str
    draft_tasks: list[DraftTaskSummary]
    traces: list[ChatTraceSummary]
    runtime_error: RuntimeErrorDetailResponse | None = None
    skill_decision: SkillDecisionResponse | None = None


class BriefingResponse(BaseModel):
    conversation_id: int
    conversation_title: str
    assistant_message_id: int
    content: str
    metadata_json: dict[str, object]


class DecisionLogEntryResponse(BaseModel):
    decision_id: int
    conversation_id: int | None
    project_id: int | None
    kind: str
    summary: str
    made_at: str
    status: str
    due_at: str | None
    resolved_at: str | None
    resolution_summary: str | None
    evidence_message_ids: list[int]


class DecisionLogListResponse(BaseModel):
    entries: list[DecisionLogEntryResponse]
    overdue_count: int


class ResolveDecisionRequest(ChatBaseModel):
    resolution_summary: str | None = None


@router.post("/chat/prompt-improve", response_model=PromptImproveResponse)
def improve_chat_prompt(payload: PromptImproveRequest) -> PromptImproveResponse:
    from atnos.services.chat_prompt_improvement_service import ChatPromptImprovementService

    result = ChatPromptImprovementService().improve(payload.content)
    return PromptImproveResponse(
        original_content=result.original_content,
        improved_content=result.improved_content,
        strategy_note=result.strategy_note,
        changed=result.changed,
    )


@router.post("/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest) -> StreamingResponse:
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    cancel_event = threading.Event()

    def emit(event: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise ChatStreamCancelled("Client cancelled the chat stream.")
        event_queue.put(event)

    def worker() -> None:
        try:
            if cancel_event.is_set():
                return
            result = _run_chat_turn(request, payload, stream_event=emit)
            if cancel_event.is_set():
                emit_cancelled = {
                    "type": "error",
                    "status_code": 499,
                    "detail": "Client cancelled the chat stream.",
                    "failure_type": "cancelled",
                }
                try:
                    event_queue.put(emit_cancelled)
                except Exception:
                    pass
                return
            emit({"type": "done", "response": _serialize_chat_response(result).model_dump()})
        except ChatStreamCancelled:
            try:
                event_queue.put(
                    {
                        "type": "error",
                        "status_code": 499,
                        "detail": "Client cancelled the chat stream.",
                        "failure_type": "cancelled",
                    }
                )
            except Exception:
                pass
        except HTTPException as exc:
            if cancel_event.is_set():
                return
            detail = exc.detail
            if not isinstance(detail, (str, dict, list)):
                detail = str(detail)
            emit({"type": "error", "status_code": exc.status_code, "detail": detail})
        except Exception as exc:
            if cancel_event.is_set():
                return
            emit(
                {
                    "type": "error",
                    "status_code": 500,
                    "detail": f"{exc.__class__.__name__}: {exc}",
                }
            )
        finally:
            event_queue.put(None)

    threading.Thread(target=worker, daemon=True, name="atnos-chat-stream").start()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                item = await asyncio.to_thread(_queue_get_with_timeout, event_queue, 0.5)
                if item is _QUEUE_TIMEOUT:
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if await request.is_disconnected():
                    cancel_event.set()
                    break
        finally:
            cancel_event.set()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat", response_model=ChatResponse)
def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    result = _run_chat_turn(request, payload)
    return _serialize_chat_response(result)


def _run_chat_turn(
    request: Request,
    payload: ChatRequest,
    *,
    stream_event=None,
):
    database = request.app.state.database
    settings = request.app.state.settings
    provider_registry = request.app.state.providers
    plugin_registry = request.app.state.plugins
    summary_writeback_factory = request.app.state.summary_writeback_factory
    ambient_context_service = getattr(request.app.state, "ambient_context_service", None)
    scheduler_runtime_service = getattr(request.app.state, "scheduler_runtime", None)

    (
        ChatOrchestrationError,
        ChatOrchestrationProviderError,
        ChatOrchestrationService,
        AttachmentDraft,
        ConversationTurnRequest,
        ActiveDocumentAction,
        ConversationExecutionPreferences,
        ExternalConfirmationRequiredError,
        ModelSelectionError,
        AttachmentKind,
    ) = _load_chat_dependencies()

    try:
        with database.session_scope() as session:
            service = ChatOrchestrationService(
                session,
                settings=settings,
                provider_registry=provider_registry,
                plugin_registry=plugin_registry,
                summary_writeback_factory=summary_writeback_factory,
                ambient_context_service=ambient_context_service,
                scheduler_runtime_service=scheduler_runtime_service,
            )
            return service.handle_turn(
                ConversationTurnRequest(
                    content=payload.content,
                    conversation_id=payload.conversation_id,
                    conversation_title=payload.conversation_title,
                    project_id=payload.project_id,
                    role_profile_id=payload.role_profile_id,
                    prompt_improved=payload.prompt_improved,
                    force_external=payload.force_external,
                    execution_preferences=(
                        ConversationExecutionPreferences(
                            response_style=payload.execution_preferences.response_style,
                            persona_profile=payload.execution_preferences.persona_profile,
                            provider_posture=payload.execution_preferences.provider_posture,
                            quality_profile=payload.execution_preferences.quality_profile,
                            memory_enabled=payload.execution_preferences.memory_enabled,
                            file_enabled=payload.execution_preferences.file_enabled,
                            internet_enabled=payload.execution_preferences.internet_enabled,
                            work_mode=payload.execution_preferences.active_work_mode,
                        )
                        if payload.execution_preferences is not None
                        else None
                    ),
                    active_document_action=(
                        ActiveDocumentAction(
                            attachment_id=payload.active_document_action.attachment_id,
                            action=payload.active_document_action.action,
                        )
                        if payload.active_document_action is not None
                        else None
                    ),
                    attachments=tuple(
                        AttachmentDraft(
                            filename=attachment.filename,
                            storage_path=attachment.storage_path,
                            kind=AttachmentKind(attachment.kind),
                            detected_kind=attachment.detected_kind,
                            understanding_status=attachment.understanding_status,
                            understanding_reason=attachment.understanding_reason,
                            preview_text=attachment.preview_text,
                            truncated=attachment.truncated,
                            media_type=attachment.media_type,
                            file_extension=attachment.file_extension,
                            size_bytes=attachment.size_bytes,
                            extracted_text=attachment.extracted_text,
                        )
                        for attachment in payload.attachments
                    ),
                    message_metadata_json=payload.message_metadata_json,
                    assistant_metadata_json=payload.assistant_metadata_json,
                ),
                stream_event=stream_event,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ExternalConfirmationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": ExternalConfirmationRequiredError.code,
                "message": str(exc),
            },
        ) from exc
    except ModelSelectionError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ChatOrchestrationProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ChatOrchestrationError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


def _serialize_chat_response(result) -> ChatResponse:
    return ChatResponse(
        conversation_id=result.conversation.id,
        conversation_title=result.conversation.title,
        conversation_project_id=result.conversation.project_id,
        user_message_id=result.user_message.id,
        assistant_message_id=result.assistant_message.id,
        detected_intent=result.intent.value,
        user_message_attachment_only=result.user_message.attachment_only,
        selected_provider_key=result.selected_provider_key,
        selected_model_key=result.selected_model_key,
        routing_reason=result.routing_reason,
        assistant_message_metadata_json=result.assistant_message.metadata_json or {},
        user_message=result.user_message.content,
        assistant_message=result.assistant_message.content,
        draft_tasks=[
            DraftTaskSummary(
                title=task.title,
                kind=task.kind,
                source=task.source,
                confidence=task.confidence,
                requires_confirmation=task.requires_confirmation,
            )
            for task in result.draft_tasks
        ],
        traces=[
            ChatTraceSummary(
                kind=trace.trace_kind.value,
                status=trace.status.value,
                step_name=trace.step_name,
                provider_key=trace.provider_key,
                model_key=trace.model_key,
                metadata_json=trace.metadata_json,
            )
            for trace in result.traces
        ],
        runtime_error=(
            RuntimeErrorDetailResponse(**result.runtime_error.to_dict())
            if result.runtime_error is not None
            else None
        ),
        skill_decision=(
            SkillDecisionResponse(
                selected_skills=result.skill_decision.selected_skills,
                rejected_skills=result.skill_decision.rejected_skills,
                reasoning_summary=result.skill_decision.reasoning_summary,
                required_inputs=result.skill_decision.required_inputs,
                risks=result.skill_decision.risks,
                next_action=result.skill_decision.next_action,
                confidence=result.skill_decision.confidence,
            )
            if result.skill_decision is not None
            else None
        ),
    )


@router.post("/chat/briefing", response_model=BriefingResponse)
def create_chat_briefing(
    request: Request,
    conversation_id: int | None = None,
    project_id: int | None = None,
) -> BriefingResponse:
    from atnos.services.proactive_briefing_service import ProactiveBriefingService

    with request.app.state.database.session_scope() as session:
        result = ProactiveBriefingService(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
        ).create_briefing_message(
            conversation_id=conversation_id,
            project_id=project_id,
            trigger="manual",
        )
    return BriefingResponse(
        conversation_id=result.conversation_id,
        conversation_title=result.conversation_title,
        assistant_message_id=result.assistant_message_id,
        content=result.content,
        metadata_json=result.metadata_json,
    )


@router.get("/chat/decisions", response_model=DecisionLogListResponse)
def list_chat_decisions(
    request: Request,
    project_id: int | None = None,
) -> DecisionLogListResponse:
    from atnos.services.decision_log_service import DecisionLogService

    with request.app.state.database.session_scope() as session:
        recall = DecisionLogService(session).recall_active(project_id)
    return DecisionLogListResponse(
        entries=[_serialize_decision_entry(item) for item in recall.entries],
        overdue_count=len(recall.overdue_entries),
    )


@router.post("/chat/decisions/{decision_id}/resolve", response_model=DecisionLogEntryResponse)
def resolve_chat_decision(
    request: Request,
    decision_id: int,
    payload: ResolveDecisionRequest,
) -> DecisionLogEntryResponse:
    from atnos.services.decision_log_service import DecisionLogService

    try:
        with request.app.state.database.session_scope() as session:
            item = DecisionLogService(session).resolve(
                decision_id,
                resolution_summary=payload.resolution_summary,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _serialize_decision_entry(item)


def _serialize_decision_entry(item) -> DecisionLogEntryResponse:
    return DecisionLogEntryResponse(
        decision_id=item.id,
        conversation_id=item.conversation_id,
        project_id=item.project_id,
        kind=item.kind.value,
        summary=item.summary,
        made_at=item.made_at.isoformat(),
        status=item.status.value,
        due_at=item.due_at.isoformat() if item.due_at is not None else None,
        resolved_at=item.resolved_at.isoformat() if item.resolved_at is not None else None,
        resolution_summary=item.resolution_summary,
        evidence_message_ids=list(item.evidence_message_ids or []),
    )
