from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["action-broker"])


def _load_action_broker_dependencies():
    from atnos.services.account_action_broker_service import (
        AccountActionBrokerService,
        AccountActionDraftCapabilityError,
        AccountActionDraftNotFoundError,
        AccountActionDraftTransitionError,
    )

    return (
        AccountActionBrokerService,
        AccountActionDraftCapabilityError,
        AccountActionDraftNotFoundError,
        AccountActionDraftTransitionError,
    )


def _load_action_execution_dependencies():
    from atnos.services.action_draft_execution_service import (
        ActionDraftExecutionError,
        ActionDraftExecutionService,
    )

    return ActionDraftExecutionError, ActionDraftExecutionService


class ActionBrokerBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionDraftAccountSummaryResponse(BaseModel):
    id: int
    label: str
    provider_key: str
    account_identifier: str
    status: str
    risk_level: str
    masked_secret_state: str
    allowed_capabilities: list[str]


class ActionDraftResponse(BaseModel):
    id: int
    account_id: int
    integration_key: str
    project_id: int | None
    project_name: str | None
    action_type: str
    action_title: str
    action_summary: str
    risk_level: str
    status: str
    requires_approval: bool
    preview_payload: dict[str, object]
    approval_notes: str | None
    rejection_reason: str | None
    audit_log: list[dict[str, object]]
    created_by: str
    approved_at: datetime | None
    rejected_at: datetime | None
    executed_at: datetime | None
    required_capabilities: list[str]
    account_summary: ActionDraftAccountSummaryResponse
    created_at: datetime
    updated_at: datetime


class CreateActionDraftRequest(ActionBrokerBaseModel):
    account_id: int = Field(gt=0)
    integration_key: str = Field(min_length=1, max_length=120)
    project_id: int | None = Field(default=None, gt=0)
    action_type: Literal[
        "read_metadata_preview",
        "content_upload_preview",
        "publish_preview",
        "message_preview",
        "calendar_write_preview",
        "github_write_preview",
        "code_apply_preview",
        "account_onboarding_preview",
        "export_preview",
        "analytics_read_preview",
        "market_data_read_preview",
        "broker_read_preview",
        "trade_preview_later",
    ]
    action_title: str = Field(min_length=1, max_length=200)
    action_summary: str = Field(min_length=1, max_length=4000)
    preview_payload: dict[str, object] = Field(default_factory=dict)
    approval_notes: str | None = Field(default=None, max_length=4000)


class RejectActionDraftRequest(ActionBrokerBaseModel):
    rejection_reason: str = Field(min_length=1, max_length=4000)


class ExecuteActionDraftRequest(ActionBrokerBaseModel):
    confirm: bool = False


class ExecuteActionDraftResponse(ActionBrokerBaseModel):
    action_draft: ActionDraftResponse
    execution_result: dict[str, object]


@router.get("/action-drafts", response_model=list[ActionDraftResponse])
def list_action_drafts(request: Request) -> list[ActionDraftResponse]:
    AccountActionBrokerService, _, _, _ = _load_action_broker_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        drafts = AccountActionBrokerService(session, settings).list_action_drafts()
    return [_build_action_draft_response(item) for item in drafts]


@router.post("/action-drafts", response_model=ActionDraftResponse, status_code=status.HTTP_201_CREATED)
def create_action_draft(
    request: Request,
    payload: CreateActionDraftRequest,
) -> ActionDraftResponse:
    (
        AccountActionBrokerService,
        AccountActionDraftCapabilityError,
        AccountActionDraftNotFoundError,
        _,
    ) = _load_action_broker_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            item = AccountActionBrokerService(session, settings).create_action_draft(
                account_id=payload.account_id,
                integration_key=payload.integration_key,
                project_id=payload.project_id,
                action_type=payload.action_type,
                action_title=payload.action_title,
                action_summary=payload.action_summary,
                preview_payload=payload.preview_payload,
                approval_notes=payload.approval_notes,
            )
    except AccountActionDraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AccountActionDraftCapabilityError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _build_action_draft_response(item)


@router.get("/action-drafts/{draft_id}", response_model=ActionDraftResponse)
def get_action_draft(draft_id: int, request: Request) -> ActionDraftResponse:
    AccountActionBrokerService, _, _, _ = _load_action_broker_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        item = AccountActionBrokerService(session, settings).get_action_draft(draft_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action draft {draft_id} was not found.",
        )
    return _build_action_draft_response(item)


@router.post("/action-drafts/{draft_id}/submit-for-approval", response_model=ActionDraftResponse)
def submit_action_draft_for_approval(draft_id: int, request: Request) -> ActionDraftResponse:
    return _mutate_action_draft_status(request, draft_id, "submit")


@router.post("/action-drafts/{draft_id}/approve", response_model=ActionDraftResponse)
def approve_action_draft(draft_id: int, request: Request) -> ActionDraftResponse:
    return _mutate_action_draft_status(request, draft_id, "approve")


@router.post("/action-drafts/{draft_id}/reject", response_model=ActionDraftResponse)
def reject_action_draft(
    draft_id: int,
    request: Request,
    payload: RejectActionDraftRequest,
) -> ActionDraftResponse:
    (
        AccountActionBrokerService,
        _,
        AccountActionDraftNotFoundError,
        AccountActionDraftTransitionError,
    ) = _load_action_broker_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            item = AccountActionBrokerService(session, settings).reject(
                draft_id,
                rejection_reason=payload.rejection_reason,
            )
    except AccountActionDraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AccountActionDraftTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _build_action_draft_response(item)


@router.post("/action-drafts/{draft_id}/cancel", response_model=ActionDraftResponse)
def cancel_action_draft(draft_id: int, request: Request) -> ActionDraftResponse:
    return _mutate_action_draft_status(request, draft_id, "cancel")


@router.post("/action-drafts/{draft_id}/execute", response_model=ExecuteActionDraftResponse)
def execute_action_draft(
    draft_id: int,
    request: Request,
    payload: ExecuteActionDraftRequest,
) -> ExecuteActionDraftResponse:
    (
        AccountActionBrokerService,
        _,
        AccountActionDraftNotFoundError,
        AccountActionDraftTransitionError,
    ) = _load_action_broker_dependencies()
    ActionDraftExecutionError, ActionDraftExecutionService = _load_action_execution_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    providers = request.app.state.providers
    try:
        with database.session_scope() as session:
            execution_result = ActionDraftExecutionService(
                session,
                settings,
                provider_registry=providers,
            ).execute(draft_id=draft_id, confirm=payload.confirm)
            item = AccountActionBrokerService(session, settings).get_action_draft(draft_id)
            if item is None:
                raise AccountActionDraftNotFoundError(f"Action draft {draft_id} was not found.")
    except AccountActionDraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AccountActionDraftTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ActionDraftExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ExecuteActionDraftResponse(
        action_draft=_build_action_draft_response(item),
        execution_result=execution_result,
    )


def _mutate_action_draft_status(
    request: Request,
    draft_id: int,
    operation: Literal["submit", "approve", "cancel"],
) -> ActionDraftResponse:
    (
        AccountActionBrokerService,
        _,
        AccountActionDraftNotFoundError,
        AccountActionDraftTransitionError,
    ) = _load_action_broker_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            service = AccountActionBrokerService(session, settings)
            if operation == "submit":
                item = service.submit_for_approval(draft_id)
            elif operation == "approve":
                item = service.approve(draft_id)
            else:
                item = service.cancel(draft_id)
    except AccountActionDraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AccountActionDraftTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _build_action_draft_response(item)


def _build_action_draft_response(item) -> ActionDraftResponse:
    return ActionDraftResponse(
        id=item.id,
        account_id=item.account_id,
        integration_key=item.integration_key,
        project_id=item.project_id,
        project_name=item.project_name,
        action_type=item.action_type,
        action_title=item.action_title,
        action_summary=item.action_summary,
        risk_level=item.risk_level,
        status=item.status,
        requires_approval=item.requires_approval,
        preview_payload=dict(item.preview_payload),
        approval_notes=item.approval_notes,
        rejection_reason=item.rejection_reason,
        audit_log=list(item.audit_log),
        created_by=item.created_by,
        approved_at=item.approved_at,
        rejected_at=item.rejected_at,
        executed_at=item.executed_at,
        required_capabilities=list(item.required_capabilities),
        account_summary=ActionDraftAccountSummaryResponse(
            id=item.account_summary.id,
            label=item.account_summary.label,
            provider_key=item.account_summary.provider_key,
            account_identifier=item.account_summary.account_identifier,
            status=item.account_summary.status,
            risk_level=item.account_summary.risk_level,
            masked_secret_state=item.account_summary.masked_secret_state,
            allowed_capabilities=list(item.account_summary.allowed_capabilities),
        ),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
