from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

router = APIRouter(tags=["operational-memory"])

DecisionLogKindValue = Literal["decision", "commitment", "open_question", "pending_action"]
DecisionLogStatusValue = Literal["active", "resolved", "expired"]
OperationalWorkItemStatusValue = Literal["active", "blocked", "waiting", "done"]
OperationalWorkItemPriorityValue = Literal["P0", "P1", "P2"]
OperationalWorkItemReviewStateValue = Literal["unreviewed", "in_review", "reviewed"]
SelfImprovementDecisionValue = Literal["accepted", "follow_up", "rejected", "undecided"]
SelfImprovementSeverityValue = Literal["P0", "P1", "P2"]
SelfImprovementStatusValue = Literal["open", "selected", "closed"]


def _load_operational_memory_dependencies():
    from atnos.services.operational_memory_service import (
        OperationalMemoryNotFoundError,
        OperationalMemoryService,
    )

    return OperationalMemoryNotFoundError, OperationalMemoryService


def _load_operational_memory_enums():
    from atnos.db.models.enums import (
        OperationalWorkItemPriority,
        OperationalWorkItemReviewState,
        OperationalWorkItemStatus,
    )

    return OperationalWorkItemPriority, OperationalWorkItemReviewState, OperationalWorkItemStatus


def _coerce_work_item_status(value: str | None) -> Any | None:
    if value is None:
        return None
    _, _, OperationalWorkItemStatus = _load_operational_memory_enums()
    return OperationalWorkItemStatus(value)


def _coerce_work_item_priority(value: str | None) -> Any | None:
    if value is None:
        return None
    OperationalWorkItemPriority, _, _ = _load_operational_memory_enums()
    return OperationalWorkItemPriority(value)


def _coerce_work_item_review_state(value: str | None) -> Any | None:
    if value is None:
        return None
    _, OperationalWorkItemReviewState, _ = _load_operational_memory_enums()
    return OperationalWorkItemReviewState(value)


def _coerce_create_payload(payload: "OperationalWorkItemCreateRequest") -> dict[str, Any]:
    fields = payload.model_dump()
    fields["priority"] = _coerce_work_item_priority(fields.get("priority"))
    return fields


def _coerce_patch_payload(payload: "OperationalWorkItemPatchRequest") -> dict[str, Any]:
    fields = payload.model_dump(exclude_unset=True)
    if "status" in fields:
        fields["status"] = _coerce_work_item_status(fields["status"])
    if "priority" in fields:
        fields["priority"] = _coerce_work_item_priority(fields["priority"])
    if "review_state" in fields:
        fields["review_state"] = _coerce_work_item_review_state(fields["review_state"])
    return fields


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else str(value)


class OperationalWorkItemCreateRequest(BaseModel):
    project_id: int | None = Field(default=None, gt=0)
    source_note_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    next_step: str | None = Field(default=None, max_length=4000)
    priority: OperationalWorkItemPriorityValue | None = None


class OperationalWorkItemPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, min_length=1, max_length=4000)
    next_step: str | None = Field(default=None, max_length=4000)
    status: OperationalWorkItemStatusValue | None = None
    priority: OperationalWorkItemPriorityValue | None = None
    review_state: OperationalWorkItemReviewStateValue | None = None
    blocked_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("status", "priority", "review_state", mode="before")
    @classmethod
    def _reject_null_enum_fields(cls, value):
        if value is None:
            raise ValueError("Explicit null is not allowed for enum fields.")
        return value


class OperationalWorkItemResponse(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime
    project_id: int | None
    source_note_id: int | None
    title: str
    summary: str
    next_step: str | None
    status: str
    priority: str
    review_state: str
    blocked_reason: str | None
    source_note: "OperationalSourceNoteResponse | None" = None
    decision_context: "OperationalDecisionContextResponse | None" = None


class OperationalSourceNoteResponse(BaseModel):
    id: int
    source: str
    view: str
    friction: str
    severity: str
    proposed_fix: str | None
    chosen_next_patch: str | None
    decision: str
    status: str


class OperationalDecisionContextResponse(BaseModel):
    active_count: int
    latest_summary: str | None
    latest_kind: str | None
    latest_due_at: datetime | None
    recent_entries: list["OperationalDecisionLogResponse"]


class OperationalDecisionLogResponse(BaseModel):
    id: int
    conversation_id: int
    project_id: int | None
    kind: str
    summary: str
    made_at: datetime
    status: str
    due_at: datetime | None
    resolved_at: datetime | None
    resolution_summary: str | None
    evidence_message_ids: list[int] | None


class OperationalMemorySnapshotResponse(BaseModel):
    project_id: int | None
    source_note: OperationalSourceNoteResponse | None
    work_items: list[OperationalWorkItemResponse]
    active_decisions: list[OperationalDecisionLogResponse]


class OperationalMemoryPromotionRequest(BaseModel):
    project_id: int = Field(gt=0)
    note_id: int | None = Field(default=None, gt=0)


class OperationalMemoryPromotionDraftResponse(BaseModel):
    source_note_id: int
    title: str
    summary: str
    next_step: str | None
    priority: str


class OperationalMemoryPromotionPreviewResponse(BaseModel):
    project_id: int
    note: OperationalSourceNoteResponse | None
    work_item_draft: OperationalMemoryPromotionDraftResponse | None
    eligible: bool
    reason: str | None
    warnings: list[str]


@router.get("/operational-memory/work-items", response_model=list[OperationalWorkItemResponse])
def list_operational_work_items(
    request: Request,
    project_id: int | None = Query(default=None, gt=0),
    source_note_id: int | None = Query(default=None, gt=0),
    status_filter: OperationalWorkItemStatusValue | None = Query(default=None, alias="status"),
) -> list[OperationalWorkItemResponse]:
    _, OperationalMemoryService = _load_operational_memory_dependencies()
    database = request.app.state.database
    try:
        with database.session_scope() as session:
            items = OperationalMemoryService(session).list_work_items(
                project_id=project_id,
                source_note_id=source_note_id,
                status=_coerce_work_item_status(status_filter),
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [_build_work_item_response(item) for item in items]


@router.post(
    "/operational-memory/work-items",
    response_model=OperationalWorkItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_operational_work_item(
    request: Request,
    payload: OperationalWorkItemCreateRequest,
) -> OperationalWorkItemResponse:
    _, OperationalMemoryService = _load_operational_memory_dependencies()
    database = request.app.state.database
    try:
        with database.session_scope() as session:
            item = OperationalMemoryService(session).create_work_item(**_coerce_create_payload(payload))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _build_work_item_response(item)


@router.patch("/operational-memory/work-items/{work_item_id}", response_model=OperationalWorkItemResponse)
def patch_operational_work_item(
    work_item_id: int,
    request: Request,
    payload: OperationalWorkItemPatchRequest,
) -> OperationalWorkItemResponse:
    OperationalMemoryNotFoundError, OperationalMemoryService = _load_operational_memory_dependencies()
    database = request.app.state.database
    try:
        with database.session_scope() as session:
            item = OperationalMemoryService(session).update_work_item(
                work_item_id,
                **_coerce_patch_payload(payload),
            )
    except OperationalMemoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _build_work_item_response(item)


@router.get("/operational-memory/snapshot", response_model=OperationalMemorySnapshotResponse)
def get_operational_memory_snapshot(
    request: Request,
    project_id: int | None = Query(default=None, gt=0),
    source_note_id: int | None = Query(default=None, gt=0),
) -> OperationalMemorySnapshotResponse:
    _, OperationalMemoryService = _load_operational_memory_dependencies()
    database = request.app.state.database
    try:
        with database.session_scope() as session:
            snapshot = OperationalMemoryService(session).build_snapshot(
                project_id=project_id,
                source_note_id=source_note_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OperationalMemorySnapshotResponse(
        project_id=snapshot.project_id,
        source_note=_build_source_note_response(snapshot.source_note),
        work_items=[_build_work_item_snapshot_response(item) for item in snapshot.work_items],
        active_decisions=[
            OperationalDecisionLogResponse(
                id=item.id,
                conversation_id=item.conversation_id,
                project_id=item.project_id,
                kind=_enum_value(item.kind),
                summary=item.summary,
                made_at=item.made_at,
                status=_enum_value(item.status),
                due_at=item.due_at,
                resolved_at=item.resolved_at,
                resolution_summary=item.resolution_summary,
                evidence_message_ids=item.evidence_message_ids,
            )
            for item in snapshot.active_decisions
        ],
    )


@router.post(
    "/operational-memory/promotions/preview",
    response_model=OperationalMemoryPromotionPreviewResponse,
)
def preview_operational_memory_promotion(
    request: Request,
    payload: OperationalMemoryPromotionRequest,
) -> OperationalMemoryPromotionPreviewResponse:
    _, OperationalMemoryService = _load_operational_memory_dependencies()
    database = request.app.state.database
    try:
        with database.session_scope() as session:
            preview = OperationalMemoryService(session).build_promotion_preview(
                project_id=payload.project_id,
                note_id=payload.note_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return OperationalMemoryPromotionPreviewResponse(
        project_id=preview.project_id,
        note=_build_source_note_response(preview.note),
        work_item_draft=(
            OperationalMemoryPromotionDraftResponse(
                source_note_id=preview.work_item_draft.source_note_id,
                title=preview.work_item_draft.title,
                summary=preview.work_item_draft.summary,
                next_step=preview.work_item_draft.next_step,
                priority=_enum_value(preview.work_item_draft.priority),
            )
            if preview.work_item_draft is not None
            else None
        ),
        eligible=preview.eligible,
        reason=preview.reason,
        warnings=list(preview.warnings),
    )


@router.post(
    "/operational-memory/promotions",
    response_model=OperationalWorkItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def promote_selected_note_to_work_item(
    request: Request,
    payload: OperationalMemoryPromotionRequest,
) -> OperationalWorkItemResponse:
    _, OperationalMemoryService = _load_operational_memory_dependencies()
    database = request.app.state.database
    try:
        with database.session_scope() as session:
            item = OperationalMemoryService(session).promote_note_to_work_item(
                project_id=payload.project_id,
                note_id=payload.note_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _build_work_item_response(item)


def _build_work_item_response(item) -> OperationalWorkItemResponse:
    return OperationalWorkItemResponse(
        id=item.id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        project_id=item.project_id,
        source_note_id=item.source_note_id,
        title=item.title,
        summary=item.summary,
        next_step=item.next_step,
        status=_enum_value(item.status),
        priority=_enum_value(item.priority),
        review_state=_enum_value(item.review_state),
        blocked_reason=item.blocked_reason,
        source_note=None,
        decision_context=None,
    )


def _build_work_item_snapshot_response(item_view: Any) -> OperationalWorkItemResponse:
    return OperationalWorkItemResponse(
        id=item_view.item.id,
        created_at=item_view.item.created_at,
        updated_at=item_view.item.updated_at,
        project_id=item_view.item.project_id,
        source_note_id=item_view.item.source_note_id,
        title=item_view.item.title,
        summary=item_view.item.summary,
        next_step=item_view.item.next_step,
        status=_enum_value(item_view.item.status),
        priority=_enum_value(item_view.item.priority),
        review_state=_enum_value(item_view.item.review_state),
        blocked_reason=item_view.item.blocked_reason,
        source_note=_build_source_note_response(item_view.source_note),
        decision_context=_build_decision_context_response(item_view.decision_context),
    )


def _build_source_note_response(note) -> OperationalSourceNoteResponse | None:
    if note is None:
        return None
    return OperationalSourceNoteResponse(
        id=note.id,
        source=note.source,
        view=note.view,
        friction=note.friction,
        severity=_enum_value(note.severity),
        proposed_fix=note.proposed_fix,
        chosen_next_patch=note.chosen_next_patch,
        decision=_enum_value(note.decision),
        status=_enum_value(note.status),
    )


def _build_decision_context_response(
    context: Any | None,
) -> OperationalDecisionContextResponse | None:
    if context is None:
        return None
    return OperationalDecisionContextResponse(
        active_count=context.active_count,
        latest_summary=context.latest_summary,
        latest_kind=_enum_value(context.latest_kind),
        latest_due_at=context.latest_due_at,
        recent_entries=[
            OperationalDecisionLogResponse(
                id=item.id,
                conversation_id=item.conversation_id,
                project_id=item.project_id,
                kind=_enum_value(item.kind),
                summary=item.summary,
                made_at=item.made_at,
                status=_enum_value(item.status),
                due_at=item.due_at,
                resolved_at=item.resolved_at,
                resolution_summary=item.resolution_summary,
                evidence_message_ids=item.evidence_message_ids,
            )
            for item in context.recent_entries
        ],
    )
