from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from atnos.api.routes.local_host import require_local_capability

router = APIRouter(tags=["agent"])

ScreenCaptureService = None
VisualQAService = None


class AgentBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentCapabilityRequest(AgentBaseModel):
    action: str = Field(min_length=1, max_length=120)
    conversation_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)
    payload: dict[str, object] = Field(default_factory=dict)


class ScheduledTaskToggleRequest(AgentBaseModel):
    is_active: bool


class AgentActionPreviewRequest(AgentBaseModel):
    kind: str = Field(min_length=1, max_length=64)
    project_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    impact_summary: str = Field(default="", max_length=2000)
    preview_payload: dict[str, object] = Field(default_factory=dict)
    required_approval_level: str = Field(default="single", pattern="^(single|double)$")
    connector_key: str | None = Field(default=None, max_length=120)
    platform_key: str | None = Field(default=None, max_length=120)
    risk_level: str = Field(default="medium", max_length=24)


class AgentActionPreviewRejectRequest(AgentBaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class AgentBrowserRecipeRunRequest(AgentBaseModel):
    project_id: int | None = Field(default=None, gt=0)
    recipe: dict[str, object] = Field(default_factory=dict)
    connector_key: str | None = Field(default="browser", max_length=120)
    platform_key: str | None = Field(default="browser", max_length=120)
    risk_level: str = Field(default="medium", max_length=24)


class AnswerQuestionRequest(AgentBaseModel):
    conversation_id: int = Field(gt=0)
    assistant_message_id: int = Field(gt=0)
    question_id: str = Field(min_length=1, max_length=120)
    selected_ids: list[str] = Field(default_factory=list)


class AnswerQuestionResponse(AgentBaseModel):
    ok: bool
    assistant_message_id: int
    user_message_id: int
    next_assistant_message_id: int | None = None


class AgentApprovalRequest(AgentBaseModel):
    run_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=1000)


class AgentQualityCheckRequest(AgentBaseModel):
    name: str = Field(min_length=1, max_length=160)
    status: str = Field(pattern="^(pass|warn|fail)$")
    evidence: str = Field(min_length=1, max_length=2000)
    score: float | None = Field(default=None, ge=0, le=10)
    required: bool = True


class AgentQualityRunRequest(AgentBaseModel):
    task: str = Field(min_length=3, max_length=2000)
    area: str = Field(default="agent", min_length=1, max_length=160)
    checks: list[AgentQualityCheckRequest] = Field(min_length=1, max_length=24)
    artifacts: list[str] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=4000)
    min_score: float | None = Field(default=None, ge=0, le=10)


class VisualQAScreenshotRequest(AgentBaseModel):
    label: str = Field(default="atnos-screen", min_length=1, max_length=120)


class VisualQARecordRequest(AgentBaseModel):
    label: str = Field(default="atnos-screen-recording", min_length=1, max_length=120)
    duration_seconds: int = Field(default=10, ge=1, le=120)


class VisualQAReviewRequest(AgentBaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    area: str = Field(default="Atnos UI", min_length=1, max_length=200)
    extra_context: str = Field(default="", max_length=4000)
    screenshot_path: str | None = Field(default=None, max_length=1000)
    recording_path: str | None = Field(default=None, max_length=1000)
    capture_screenshot: bool = True
    allow_cloud_review: bool = False


def _normalize_question_options(metadata_json: dict[str, object]) -> list[dict[str, object]]:
    options = metadata_json.get("options")
    if not isinstance(options, list):
        return []
    normalized: list[dict[str, object]] = []
    for option in options:
        if isinstance(option, dict):
            normalized.append(option)
    return normalized


@router.get("/agent/status")
def get_agent_status(request: Request) -> dict[str, object]:
    from atnos.services.agent_control_panel_service import AgentControlPanelService

    with request.app.state.database.session_scope() as session:
        return AgentControlPanelService(request.app.state.settings, session).status()


@router.get("/agent/logs/recent")
def get_recent_agent_logs(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
    from atnos.services.agent_control_panel_service import AgentControlPanelService

    return AgentControlPanelService(request.app.state.settings).recent_logs(limit=limit)


@router.get("/agent/tools")
def get_agent_tools(request: Request) -> dict[str, object]:
    from atnos.services.agent_control_panel_service import AgentControlPanelService

    return AgentControlPanelService(request.app.state.settings).tools()


@router.get("/agent/pending-approvals")
def get_agent_pending_approvals(request: Request) -> dict[str, object]:
    from atnos.services.agent_control_panel_service import AgentControlPanelService

    with request.app.state.database.session_scope() as session:
        approvals = AgentControlPanelService(request.app.state.settings, session).pending_approvals()
    return {"pending_approvals": approvals}


@router.get("/agent/quality-runs/recent")
def get_recent_agent_quality_runs(request: Request, limit: int = Query(default=5, ge=1, le=50)) -> dict[str, object]:
    from atnos.services.agent_quality_runtime_service import AgentQualityRuntimeService

    return {"quality_runs": AgentQualityRuntimeService(request.app.state.settings).latest_runs(limit=limit)}


@router.post("/agent/quality-runs")
def record_agent_quality_run(request: Request, payload: AgentQualityRunRequest) -> dict[str, object]:
    from atnos.services.agent_quality_runtime_service import AgentQualityRuntimeService, QualityCheck

    checks = [
        QualityCheck(
            name=check.name,
            status=check.status,  # type: ignore[arg-type]
            evidence=check.evidence,
            score=check.score,
            required=check.required,
        )
        for check in payload.checks
    ]
    result = AgentQualityRuntimeService(request.app.state.settings).record_run(
        task=payload.task,
        area=payload.area,
        checks=checks,
        artifacts=payload.artifacts,
        notes=payload.notes,
        min_score=payload.min_score,
    )
    return {"quality_run": result}


@router.post("/agent/emergency-stop")
def emergency_stop_agent(request: Request) -> dict[str, object]:
    from atnos.services.agent_control_panel_service import AgentControlPanelService

    with request.app.state.database.session_scope() as session:
        result = AgentControlPanelService(request.app.state.settings, session).emergency_stop()
    request.app.state.agent_emergency_stop = result
    return result


@router.post("/agent/approve-action")
def approve_agent_action(request: Request, payload: AgentApprovalRequest) -> dict[str, object]:
    from atnos.services.agent_action_preview_service import AgentActionPreviewError
    from atnos.services.agent_control_panel_service import AgentControlPanelService
    from atnos.services.agent_execution_service import AgentExecutionError

    try:
        with request.app.state.database.session_scope() as session:
            return AgentControlPanelService(request.app.state.settings, session).approve_action(payload.run_id)
    except AgentActionPreviewError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/agent/reject-action")
def reject_agent_action(request: Request, payload: AgentApprovalRequest) -> dict[str, object]:
    from atnos.services.agent_action_preview_service import AgentActionPreviewError
    from atnos.services.agent_control_panel_service import AgentControlPanelService

    try:
        with request.app.state.database.session_scope() as session:
            return AgentControlPanelService(request.app.state.settings, session).reject_action(
                payload.run_id,
                reason=payload.reason,
            )
    except AgentActionPreviewError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/agent/visual-qa/screenshot")
def capture_visual_qa_screenshot(request: Request, payload: VisualQAScreenshotRequest) -> dict[str, object]:
    service_cls, ScreenCaptureError = _load_screen_capture_dependencies()

    try:
        artifact = service_cls(request.app.state.settings).capture_screenshot(label=payload.label)
    except ScreenCaptureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"artifact": artifact.to_dict()}


@router.post("/agent/visual-qa/record")
def record_visual_qa_screen(request: Request, payload: VisualQARecordRequest) -> dict[str, object]:
    service_cls, ScreenCaptureError = _load_screen_capture_dependencies()

    try:
        artifact = service_cls(request.app.state.settings).record_screen(
            label=payload.label,
            duration_seconds=payload.duration_seconds,
        )
    except ScreenCaptureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"artifact": artifact.to_dict()}


@router.post("/agent/visual-qa/review")
def run_visual_qa_review(request: Request, payload: VisualQAReviewRequest) -> dict[str, object]:
    from atnos.services.screen_capture_service import ScreenCaptureError

    service_cls, VisualQAError = _load_visual_qa_dependencies()

    try:
        screenshot_path = Path(payload.screenshot_path) if payload.screenshot_path else None
        recording_path = Path(payload.recording_path) if payload.recording_path else None
        with request.app.state.database.session_scope() as session:
            result = service_cls(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
                plugin_registry=request.app.state.plugins,
            ).run_review(
                goal=payload.goal,
                area=payload.area,
                extra_context=payload.extra_context,
                screenshot_path=screenshot_path,
                capture_screenshot=payload.capture_screenshot,
                include_recording_path=recording_path,
                allow_cloud_review=payload.allow_cloud_review,
            )
    except (VisualQAError, ScreenCaptureError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"visual_qa": result.to_dict()}


@router.post("/agent/answer-question", response_model=AnswerQuestionResponse)
def answer_question(request: Request, payload: AnswerQuestionRequest) -> AnswerQuestionResponse:
    from atnos.db.models.enums import MessageRole, MessageStatus
    from atnos.db.models.message import Message
    from atnos.services.chat_orchestration_service import (
        ChatOrchestrationError,
        ChatOrchestrationProviderError,
        ChatOrchestrationService,
    )

    database = request.app.state.database
    settings = request.app.state.settings
    provider_registry = request.app.state.providers
    plugin_registry = request.app.state.plugins
    summary_writeback_factory = request.app.state.summary_writeback_factory
    ambient_context_service = getattr(request.app.state, "ambient_context_service", None)
    scheduler_runtime_service = getattr(request.app.state, "scheduler_runtime", None)

    with database.session_scope() as session:
        conversation_service = ChatOrchestrationService(
            session,
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
            summary_writeback_factory=summary_writeback_factory,
            ambient_context_service=ambient_context_service,
            scheduler_runtime_service=scheduler_runtime_service,
        )

        conversation = conversation_service.conversation_service.conversations.get(payload.conversation_id)
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {payload.conversation_id} does not exist.",
            )

        assistant_message = session.get(Message, payload.assistant_message_id)
        if assistant_message is None or assistant_message.conversation_id != payload.conversation_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Structured question message does not exist in this conversation.",
            )
        if assistant_message.role != MessageRole.ASSISTANT:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Selected message is not an assistant structured question.",
            )

        metadata_json = dict(assistant_message.metadata_json or {})
        if metadata_json.get("kind") != "question_choice":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant message is not a question_choice card.",
            )
        if metadata_json.get("question_id") != payload.question_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Structured question '{payload.question_id}' was not found on this message.",
            )
        if metadata_json.get("resolved_selected_ids") is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This structured question has already been answered.",
            )

        options = _normalize_question_options(metadata_json)
        allowed_option_ids = {
            str(option.get("id"))
            for option in options
            if option.get("id") is not None
        }
        if not payload.selected_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="selected_ids must contain at least one option.",
            )
        if len(payload.selected_ids) != len(set(payload.selected_ids)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="selected_ids must not contain duplicates.",
            )
        invalid_ids = [option_id for option_id in payload.selected_ids if option_id not in allowed_option_ids]
        if invalid_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown option ids: {', '.join(invalid_ids)}",
            )

        multi_select = bool(metadata_json.get("multi_select", False))
        required = bool(metadata_json.get("required", True))
        if not multi_select and len(payload.selected_ids) != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="This question accepts exactly one selected option.",
            )
        if required and multi_select and len(payload.selected_ids) < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="At least one option must be selected.",
            )

        metadata_json["resolved_selected_ids"] = list(payload.selected_ids)
        assistant_message.metadata_json = metadata_json
        session.add(assistant_message)
        session.flush()

        selected_options = [
            option
            for option in options
            if str(option.get("id")) in payload.selected_ids
        ]
        selected_labels = [
            str(option.get("label")).strip()
            for option in selected_options
            if str(option.get("label")).strip()
        ]
        user_message = conversation_service.messages.create(
            conversation_id=payload.conversation_id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content=f"Wybralem: {', '.join(selected_labels)}",
            parent_message_id=assistant_message.id,
            metadata_json={
                "kind": "choice_response",
                "question_id": payload.question_id,
                "selected_ids": list(payload.selected_ids),
            },
        )

        try:
            next_assistant_message = conversation_service.continue_with_answer(
                conversation_id=payload.conversation_id,
                answered_question_id=payload.question_id,
                selected_ids=list(payload.selected_ids),
            )
        except ChatOrchestrationProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except ChatOrchestrationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        return AnswerQuestionResponse(
            ok=True,
            assistant_message_id=assistant_message.id,
            user_message_id=user_message.id,
            next_assistant_message_id=next_assistant_message.id if next_assistant_message is not None else None,
        )


@router.post("/agent/preview")
def preview_agent_capability(request: Request, payload: AgentCapabilityRequest) -> dict[str, object]:
    from atnos.services.capability_action_service import CapabilityActionError, CapabilityActionService
    from atnos.services.scheduled_task_service import ScheduledTaskError

    try:
        with request.app.state.database.session_scope() as session:
            preview = CapabilityActionService(session, request.app.state.settings).preview(
                action=payload.action,
                conversation_id=payload.conversation_id,
                project_id=payload.project_id,
                payload=payload.payload,
            )
        return {"preview": preview.to_dict()}
    except (CapabilityActionError, ScheduledTaskError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/agent/execute")
def execute_agent_capability(request: Request, payload: AgentCapabilityRequest) -> dict[str, object]:
    from atnos.services.capability_action_service import CapabilityActionError, CapabilityActionService
    from atnos.services.scheduled_task_service import ScheduledTaskError

    try:
        with request.app.state.database.session_scope() as session:
            service = CapabilityActionService(session, request.app.state.settings)
            result = service.execute(
                action=payload.action,
                conversation_id=payload.conversation_id,
                project_id=payload.project_id,
                payload=payload.payload,
            )
        scheduled_task = result.get("scheduled_task")
        if isinstance(scheduled_task, dict):
            scheduler_runtime = getattr(request.app.state, "scheduler_runtime", None)
            if scheduler_runtime is not None:
                scheduler_runtime.refresh_scheduled_task(int(scheduled_task["scheduled_task_id"]))
        return result
    except (CapabilityActionError, ScheduledTaskError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/agent/artifacts/download")
def download_agent_artifact(
    request: Request,
    storage_path: str = Query(min_length=1),
) -> FileResponse:
    from atnos.services.output_artifact_service import ManagedArtifactPathError, OutputArtifactService

    try:
        resolved = OutputArtifactService(request.app.state.settings).resolve_download_path(storage_path)
    except ManagedArtifactPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return FileResponse(str(resolved), filename=resolved.name)


@router.post("/agent/action-previews")
def create_agent_action_preview(
    request: Request,
    payload: AgentActionPreviewRequest,
) -> dict[str, object]:
    from atnos.services.agent_action_preview_service import AgentActionPreviewError, AgentActionPreviewService

    try:
        with request.app.state.database.session_scope() as session:
            preview = AgentActionPreviewService(session, request.app.state.settings).create_preview(
                project_id=payload.project_id,
                kind=payload.kind,  # type: ignore[arg-type]
                title=payload.title,
                summary=payload.summary,
                impact_summary=payload.impact_summary,
                preview_payload=payload.preview_payload,
                required_approval_level=payload.required_approval_level,  # type: ignore[arg-type]
                connector_key=payload.connector_key,
                platform_key=payload.platform_key,
                risk_level=payload.risk_level,
            )
        return {"preview": preview.to_dict()}
    except AgentActionPreviewError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/agent/action-previews/pending")
def get_pending_agent_action_preview(request: Request) -> dict[str, object]:
    from atnos.services.agent_action_preview_service import AgentActionPreviewService

    with request.app.state.database.session_scope() as session:
        preview = AgentActionPreviewService(session, request.app.state.settings).get_pending_preview()
    return {"preview": preview.to_dict() if preview is not None else None}


@router.post("/agent/action-previews/{run_id}/approve")
def approve_agent_action_preview(
    run_id: int,
    request: Request,
    _: int = Depends(require_local_capability),
) -> dict[str, object]:
    from atnos.services.agent_action_preview_service import AgentActionPreviewError
    from atnos.services.agent_execution_service import AgentExecutionError, AgentExecutionService

    try:
        with request.app.state.database.session_scope() as session:
            run = AgentExecutionService(session, request.app.state.settings).execute_approved_preview(run_id)
        return {"run": run}
    except (AgentActionPreviewError, AgentExecutionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/agent/action-previews/{run_id}/reject")
def reject_agent_action_preview(
    run_id: int,
    request: Request,
    payload: AgentActionPreviewRejectRequest,
) -> dict[str, object]:
    from atnos.services.agent_action_preview_service import AgentActionPreviewError, AgentActionPreviewService

    try:
        with request.app.state.database.session_scope() as session:
            preview = AgentActionPreviewService(session, request.app.state.settings).mark_rejected(
                run_id,
                reason=payload.reason,
            )
        return {"preview": preview.to_dict()}
    except AgentActionPreviewError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/agent/browser/run")
def run_agent_browser_recipe(
    request: Request,
    payload: AgentBrowserRecipeRunRequest,
) -> dict[str, object]:
    from atnos.services.agent_execution_service import AgentExecutionError, AgentExecutionService

    try:
        with request.app.state.database.session_scope() as session:
            result = AgentExecutionService(session, request.app.state.settings).start_browser_recipe(
                project_id=payload.project_id,
                recipe=payload.recipe,
                connector_key=payload.connector_key,
                platform_key=payload.platform_key,
                risk_level=payload.risk_level,
            )
        return result
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/agent/executions/latest")
def get_latest_agent_execution(request: Request) -> dict[str, object]:
    from atnos.services.agent_execution_service import AgentExecutionService

    with request.app.state.database.session_scope() as session:
        run = AgentExecutionService(session, request.app.state.settings).get_latest_agent_run()
    return {"run": run}


@router.get("/agent/executions/{run_id}")
def get_agent_execution(run_id: int, request: Request) -> dict[str, object]:
    from atnos.services.agent_execution_service import AgentExecutionError, AgentExecutionService

    try:
        with request.app.state.database.session_scope() as session:
            run = AgentExecutionService(session, request.app.state.settings).serialize_run(run_id)
        return {"run": run}
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/agent/executions/{run_id}/pause")
def pause_agent_execution(run_id: int, request: Request) -> dict[str, object]:
    from atnos.services.agent_execution_service import AgentExecutionError, AgentExecutionService

    try:
        with request.app.state.database.session_scope() as session:
            run = AgentExecutionService(session, request.app.state.settings).pause(run_id)
        return {"run": run}
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/agent/executions/{run_id}/interrupt")
def interrupt_agent_execution(run_id: int, request: Request) -> dict[str, object]:
    from atnos.services.agent_execution_service import AgentExecutionError, AgentExecutionService

    try:
        with request.app.state.database.session_scope() as session:
            run = AgentExecutionService(session, request.app.state.settings).interrupt(run_id)
        return {"run": run}
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/agent/executions/{run_id}/resume")
def resume_agent_execution(run_id: int, request: Request) -> dict[str, object]:
    from atnos.services.agent_execution_service import AgentExecutionError, AgentExecutionService

    try:
        with request.app.state.database.session_scope() as session:
            run = AgentExecutionService(session, request.app.state.settings).resume(run_id)
        return {"run": run}
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/agent/scheduled-tasks")
def list_scheduled_tasks(
    request: Request,
    project_id: int | None = Query(default=None, gt=0),
) -> dict[str, object]:
    from atnos.services.scheduled_task_service import ScheduledTaskService

    with request.app.state.database.session_scope() as session:
        tasks = ScheduledTaskService(session, request.app.state.settings).list_tasks(project_id=project_id)
    return {"scheduled_tasks": tasks}


@router.patch("/agent/scheduled-tasks/{task_id}")
def update_scheduled_task(
    task_id: int,
    request: Request,
    payload: ScheduledTaskToggleRequest,
) -> dict[str, object]:
    from atnos.services.scheduled_task_service import ScheduledTaskError, ScheduledTaskService

    try:
        with request.app.state.database.session_scope() as session:
            service = ScheduledTaskService(session, request.app.state.settings)
            task = service.set_active(task_id, is_active=payload.is_active)
            serialized = service.serialize_task(task)
        scheduler_runtime = getattr(request.app.state, "scheduler_runtime", None)
        if scheduler_runtime is not None:
            scheduler_runtime.refresh_scheduled_task(task_id)
        return {"scheduled_task": serialized}
    except ScheduledTaskError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/agent/scheduled-tasks/{task_id}")
def delete_scheduled_task(task_id: int, request: Request) -> dict[str, object]:
    from atnos.services.scheduled_task_service import ScheduledTaskError, ScheduledTaskService

    try:
        with request.app.state.database.session_scope() as session:
            service = ScheduledTaskService(session, request.app.state.settings)
            service.delete_task(task_id)
        scheduler_runtime = getattr(request.app.state, "scheduler_runtime", None)
        if scheduler_runtime is not None:
            scheduler_runtime.remove_scheduled_task(task_id)
        return {"deleted": True, "scheduled_task_id": task_id}
    except ScheduledTaskError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _load_screen_capture_dependencies() -> tuple[object, type[Exception]]:
    from atnos.services.screen_capture_service import ScreenCaptureError
    from atnos.services.screen_capture_service import ScreenCaptureService as RealScreenCaptureService

    return ScreenCaptureService or RealScreenCaptureService, ScreenCaptureError


def _load_visual_qa_dependencies() -> tuple[object, type[Exception]]:
    from atnos.services.visual_qa_service import VisualQAError
    from atnos.services.visual_qa_service import VisualQAService as RealVisualQAService

    return VisualQAService or RealVisualQAService, VisualQAError
