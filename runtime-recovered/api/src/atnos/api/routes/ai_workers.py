from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegatePlanInput, DelegateTaskInput

router = APIRouter(prefix="/api/ai-workers", tags=["ai-workers"])


class AiWorkersBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DelegateTaskRequest(AiWorkersBaseModel):
    task_type: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=20000)
    context: dict[str, Any] = Field(default_factory=dict)
    preferred_worker: str | None = Field(default=None, max_length=120)
    allowed_workers: list[str] = Field(default_factory=list)
    privacy_mode: Literal["strict_local", "allow_browser_ai", "allow_cloud_api"] = "strict_local"
    priority: Literal["low", "normal", "high"] = "normal"
    requested_by: str = Field(default="atnos", min_length=1, max_length=120)
    requires_approval_for_result: bool = False
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=12)
    project_id: int | None = Field(default=None, gt=0)


class DelegatePlanRequest(DelegateTaskRequest):
    max_steps: int = Field(default=20, ge=1, le=50)


class CodexReviewRequest(AiWorkersBaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2000)


class CodexRollbackRequest(AiWorkersBaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


@router.get("/status")
async def ai_workers_status(request: Request) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        return await AiDelegationRouter(session, request.app.state.settings).status()


@router.post("/delegate")
async def delegate_ai_worker_task(request: Request, payload: DelegateTaskRequest) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        try:
            return await AiDelegationRouter(session, request.app.state.settings).delegate(
                DelegateTaskInput(
                    task_type=payload.task_type,
                    prompt=payload.prompt,
                    context=payload.context,
                    preferred_worker=payload.preferred_worker,
                    allowed_workers=tuple(payload.allowed_workers),
                    privacy_mode=payload.privacy_mode,
                    priority=payload.priority,
                    requested_by=payload.requested_by,
                    requires_approval_for_result=payload.requires_approval_for_result,
                    acceptance_criteria=tuple(payload.acceptance_criteria),
                    project_id=payload.project_id,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/delegate-plan")
def delegate_ai_worker_plan(request: Request, payload: DelegatePlanRequest) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        try:
            return AiDelegationRouter(session, request.app.state.settings).delegate_plan(
                DelegatePlanInput(
                    task_type=payload.task_type,
                    prompt=payload.prompt,
                    context=payload.context,
                    preferred_worker=payload.preferred_worker,
                    allowed_workers=tuple(payload.allowed_workers),
                    privacy_mode=payload.privacy_mode,
                    priority=payload.priority,
                    requested_by=payload.requested_by,
                    requires_approval_for_result=payload.requires_approval_for_result,
                    acceptance_criteria=tuple(payload.acceptance_criteria),
                    project_id=payload.project_id,
                    max_steps=payload.max_steps,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/tasks")
def list_ai_worker_tasks(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    parent_task_id: int | None = Query(default=None, alias="parent_id"),
    roots_only: bool = Query(default=False),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    conversation_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        tasks = AiDelegationRouter(session, request.app.state.settings).list_tasks(
            status=status_filter,
            parent_task_id=parent_task_id,
            roots_only=roots_only,
            query=query,
            conversation_id=conversation_id,
            limit=limit,
        )
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
def get_ai_worker_task(task_id: int, request: Request) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        task = AiDelegationRouter(session, request.app.state.settings).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"AI worker task {task_id} was not found.")
    return task


@router.patch("/tasks/{task_id}/cancel")
def cancel_ai_worker_task(task_id: int, request: Request) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        task = AiDelegationRouter(session, request.app.state.settings).cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"AI worker task {task_id} was not found.")
    return task


@router.post("/tasks/{task_id}/run")
async def run_ai_worker_task(task_id: int, request: Request) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        try:
            task = await AiDelegationRouter(session, request.app.state.settings).run_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"AI worker task {task_id} was not found.")
    return task


@router.post("/tasks/{task_id}/run-safe-plan")
async def run_ai_worker_safe_plan(task_id: int, request: Request) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        try:
            task = await AiDelegationRouter(session, request.app.state.settings).run_safe_plan(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"AI worker task {task_id} was not found.")
    return task


@router.post("/tasks/{task_id}/review")
def review_codex_task(task_id: int, request: Request, payload: CodexReviewRequest) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        try:
            task = AiDelegationRouter(session, request.app.state.settings).review_codex_task(
                task_id,
                decision=payload.decision,
                reviewer=payload.reviewer,
                note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"AI worker task {task_id} was not found.")
    return task


@router.post("/tasks/{task_id}/rollback-proposal")
def rollback_codex_proposal(task_id: int, request: Request, payload: CodexRollbackRequest) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        try:
            task = AiDelegationRouter(session, request.app.state.settings).rollback_codex_proposal(
                task_id,
                reviewer=payload.reviewer,
                reason=payload.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"AI worker task {task_id} was not found.")
    return task


@router.post("/probe/{worker_key}")
async def probe_ai_worker(worker_key: str, request: Request) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        return await AiDelegationRouter(session, request.app.state.settings).probe(worker_key)
