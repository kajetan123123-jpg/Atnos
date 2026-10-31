from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["creator-ops"])


def _load_creator_ops_service():
    from atnos.services.creator_ops_service import CreatorOpsService

    return CreatorOpsService


def _load_synthetic_creator_workflow_service():
    from atnos.services.synthetic_creator_workflow_service import SyntheticCreatorWorkflowService

    return SyntheticCreatorWorkflowService


class CreatorOpsBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatorProjectRequest(CreatorOpsBaseModel):
    name: str = Field(min_length=1, max_length=160)
    project_type: str = Field(
        pattern="^(youtube_channel|shorts_channel|instagram_brand|synthetic_influencer|synthetic_model|creator_monetization|market_lab|growth_experiment|self_improvement)$"
    )
    niche: str | None = Field(default=None, max_length=240)
    target_audience: str | None = Field(default=None, max_length=240)
    monetization_goal: str | None = Field(default=None, max_length=240)
    content_boundaries: str | None = Field(default=None, max_length=1000)
    platforms: list[str] = Field(default_factory=list)
    persona_type: str = Field(default="none", pattern="^(none|human_user|brand|synthetic_original_persona)$")
    disclosure_policy: str = Field(default="not_required", pattern="^(not_required|synthetic_disclosed|brand_account|platform_dependent)$")
    operating_mode: str = Field(default="planning_only", pattern="^(planning_only|draft_only|supervised_actions|autonomous_later)$")
    linked_account_ids: list[int] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=400)


class CreatorProjectUpdateRequest(CreatorOpsBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    niche: str | None = Field(default=None, max_length=240)
    target_audience: str | None = Field(default=None, max_length=240)
    monetization_goal: str | None = Field(default=None, max_length=240)
    content_boundaries: str | None = Field(default=None, max_length=1000)
    platforms: list[str] | None = None
    persona_type: str | None = Field(default=None, pattern="^(none|human_user|brand|synthetic_original_persona)$")
    disclosure_policy: str | None = Field(default=None, pattern="^(not_required|synthetic_disclosed|brand_account|platform_dependent)$")
    operating_mode: str | None = Field(default=None, pattern="^(planning_only|draft_only|supervised_actions|autonomous_later)$")
    linked_account_ids: list[int] | None = None
    notes: str | None = Field(default=None, max_length=400)
    status: str | None = Field(default=None, pattern="^(planned|active|paused|archived)$")
    approval_policy: str | None = Field(default=None, pattern="^(always_require_approval|allow_low_risk_drafts|future_policy)$")


class GenerateCreatorWorkItemsRequest(CreatorOpsBaseModel):
    preview_only: bool = True
    persist: bool = False


class GenerateSyntheticCreatorPreviewRequest(CreatorOpsBaseModel):
    conversation_id: int | None = Field(default=None, ge=1)
    goal: str | None = Field(default=None, max_length=400)
    style_key: str | None = Field(default=None, max_length=80)
    reel_type: str | None = Field(default=None, max_length=80)
    quality_profile: str = Field(default="standard", max_length=80)


class GenerateSyntheticCreatorPreviewBatchRequest(CreatorOpsBaseModel):
    conversation_id: int | None = Field(default=None, ge=1)
    goal: str | None = Field(default=None, max_length=400)
    style_keys: list[str] | None = None
    reel_types: list[str] | None = None
    quality_profile: str = Field(default="standard", max_length=80)
    max_items: int = Field(default=4, ge=1, le=12)


class CreatorWorkItemUpdateRequest(CreatorOpsBaseModel):
    status: str | None = Field(default=None, pattern="^(backlog|drafted|needs_review|approved|blocked|done)$")
    review_notes: str | None = Field(default=None, max_length=2000)
    next_action: str | None = Field(default=None, max_length=400)
    action: str | None = Field(default=None, pattern="^(approve_review|request_changes|block|defer|mark_done)$")


class ContentCalendarItemUpdateRequest(CreatorOpsBaseModel):
    planned_date: date | None = None
    status: str | None = Field(default=None, pattern="^(idea|planned|drafted|needs_review|approved|scheduled_later|done|blocked)$")
    notes: str | None = Field(default=None, max_length=1000)


@router.get("/creator-projects")
def list_creator_projects(request: Request) -> list[dict[str, Any]]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        items = CreatorOpsService(session, request.app.state.settings).list_projects()
    return [_to_jsonable(item) for item in items]


@router.post("/creator-projects", status_code=status.HTTP_201_CREATED)
def create_creator_project(request: Request, payload: CreatorProjectRequest) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        item = CreatorOpsService(session, request.app.state.settings).create_project(**payload.model_dump())
    return _to_jsonable(item)


@router.get("/creator-projects/{project_id}")
def get_creator_project(project_id: int, request: Request) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        item = CreatorOpsService(session, request.app.state.settings).get_project(project_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator project not found.")
    return _to_jsonable(item)


@router.patch("/creator-projects/{project_id}")
def update_creator_project(project_id: int, request: Request, payload: CreatorProjectUpdateRequest) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        item = CreatorOpsService(session, request.app.state.settings).update_project(
            project_id,
            **payload.model_dump(exclude_unset=True),
        )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator project not found.")
    return _to_jsonable(item)


@router.post("/creator-projects/{project_id}/generate-operating-plan")
def generate_operating_plan(project_id: int, request: Request) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        plan = CreatorOpsService(session, request.app.state.settings).generate_operating_plan(project_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator project not found.")
    return plan


@router.post("/creator-projects/{project_id}/generate-work-items")
def generate_work_items(project_id: int, request: Request, payload: GenerateCreatorWorkItemsRequest) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        result = CreatorOpsService(session, request.app.state.settings).generate_work_items(
            project_id,
            persist=payload.persist and not payload.preview_only,
        )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator project not found.")
    return _to_jsonable(result)


@router.post("/creator-projects/{project_id}/generate-synthetic-preview")
def generate_synthetic_preview(
    project_id: int,
    request: Request,
    payload: GenerateSyntheticCreatorPreviewRequest,
) -> dict[str, Any]:
    SyntheticCreatorWorkflowService = _load_synthetic_creator_workflow_service()
    with request.app.state.database.session_scope() as session:
        result = SyntheticCreatorWorkflowService(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
            plugin_registry=request.app.state.plugins,
        ).generate_preview(
            creator_project_id=project_id,
            conversation_id=payload.conversation_id,
            goal=payload.goal,
            style_key=payload.style_key,
            reel_type=payload.reel_type,
            quality_profile=payload.quality_profile,
        )
    return _to_jsonable(result)


@router.post("/creator-projects/{project_id}/generate-synthetic-preview-batch")
def generate_synthetic_preview_batch(
    project_id: int,
    request: Request,
    payload: GenerateSyntheticCreatorPreviewBatchRequest,
) -> dict[str, Any]:
    SyntheticCreatorWorkflowService = _load_synthetic_creator_workflow_service()
    with request.app.state.database.session_scope() as session:
        result = SyntheticCreatorWorkflowService(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
            plugin_registry=request.app.state.plugins,
        ).generate_preview_batch(
            creator_project_id=project_id,
            conversation_id=payload.conversation_id,
            goal=payload.goal,
            style_keys=payload.style_keys,
            reel_types=payload.reel_types,
            quality_profile=payload.quality_profile,
            max_items=payload.max_items,
        )
    return _to_jsonable(result)


@router.get("/creator-projects/{project_id}/work-items")
def list_work_items(
    project_id: int,
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    sort: str = Query(default="queue_default"),
) -> list[dict[str, Any]]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        items = CreatorOpsService(session, request.app.state.settings).list_work_items(
            project_id,
            status=status_filter,
            sort=sort,
        )
    if items is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator project not found.")
    return [_to_jsonable(item) for item in items]


@router.patch("/creator-work-items/{work_item_id}")
def update_work_item(work_item_id: int, request: Request, payload: CreatorWorkItemUpdateRequest) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        item = CreatorOpsService(session, request.app.state.settings).update_work_item(
            work_item_id,
            **payload.model_dump(exclude_unset=True),
        )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator work item not found.")
    return _to_jsonable(item)


@router.get("/creator-work-items/{work_item_id}/approval-package")
def get_work_item_approval_package(work_item_id: int, request: Request) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        item = CreatorOpsService(session, request.app.state.settings).get_work_item_approval_package(work_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator work item not found.")
    return _to_jsonable(item)


@router.get("/creator-projects/{project_id}/content-calendar")
def list_content_calendar(project_id: int, request: Request) -> list[dict[str, Any]]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        items = CreatorOpsService(session, request.app.state.settings).list_content_calendar_items(project_id)
    if items is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creator project not found.")
    return [_to_jsonable(item) for item in items]


@router.patch("/content-calendar-items/{item_id}")
def update_content_calendar_item(item_id: int, request: Request, payload: ContentCalendarItemUpdateRequest) -> dict[str, Any]:
    CreatorOpsService = _load_creator_ops_service()
    with request.app.state.database.session_scope() as session:
        item = CreatorOpsService(session, request.app.state.settings).update_content_calendar_item(
            item_id,
            **payload.model_dump(exclude_unset=True),
        )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content calendar item not found.")
    return _to_jsonable(item)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value
