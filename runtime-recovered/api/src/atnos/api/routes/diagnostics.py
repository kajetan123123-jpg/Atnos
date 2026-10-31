from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def _load_trace_retention_dependencies():
    from atnos.repositories.execution_trace_repository import ExecutionTraceRepository
    from atnos.services.trace_retention_service import (
        TraceRetentionConfirmationRequiredError,
        TraceRetentionService,
    )

    return ExecutionTraceRepository, TraceRetentionConfirmationRequiredError, TraceRetentionService


class DiagnosticsRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TraceRetentionPreviewResponse(BaseModel):
    retention_days: int
    cutoff_at: datetime
    total_trace_count: int
    eligible_trace_count: int
    newest_eligible_created_at: datetime | None = None
    oldest_eligible_created_at: datetime | None = None
    enabled: bool
    will_delete: bool


class TraceRetentionCleanupRequest(DiagnosticsRequestModel):
    confirm: bool = Field(default=False)
    retention_days: int | None = Field(default=None, ge=1)


class TraceRetentionCleanupResponse(BaseModel):
    deleted_count: int
    retention_days: int
    cutoff_at: datetime
    remaining_trace_count: int
    enabled: bool
    dry_run: bool


def _build_trace_retention_service(request: Request, session):
    ExecutionTraceRepository, _, TraceRetentionService = _load_trace_retention_dependencies()
    settings = request.app.state.settings
    repository = ExecutionTraceRepository(session)
    return TraceRetentionService(repository, settings)


@router.get(
    "/traces/retention-preview",
    response_model=TraceRetentionPreviewResponse,
)
def trace_retention_preview(
    request: Request,
    retention_days: int | None = Query(default=None, ge=1),
) -> TraceRetentionPreviewResponse:
    database = request.app.state.database
    with database.session_scope() as session:
        service = _build_trace_retention_service(request, session)
        preview = service.preview_cleanup(retention_days=retention_days)
    return TraceRetentionPreviewResponse(
        retention_days=preview.retention_days,
        cutoff_at=preview.cutoff_at,
        total_trace_count=preview.total_trace_count,
        eligible_trace_count=preview.eligible_trace_count,
        newest_eligible_created_at=preview.newest_eligible_created_at,
        oldest_eligible_created_at=preview.oldest_eligible_created_at,
        enabled=preview.enabled,
        will_delete=preview.will_delete,
    )


@router.post(
    "/traces/retention-cleanup",
    response_model=TraceRetentionCleanupResponse,
)
def trace_retention_cleanup(
    request: Request,
    payload: TraceRetentionCleanupRequest,
) -> TraceRetentionCleanupResponse:
    _, TraceRetentionConfirmationRequiredError, _ = _load_trace_retention_dependencies()
    database = request.app.state.database
    try:
        with database.session_scope() as session:
            service = _build_trace_retention_service(request, session)
            result = service.cleanup_old_traces(
                retention_days=payload.retention_days,
                confirm=payload.confirm,
            )
    except TraceRetentionConfirmationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return TraceRetentionCleanupResponse(
        deleted_count=result.deleted_count,
        retention_days=result.retention_days,
        cutoff_at=result.cutoff_at,
        remaining_trace_count=result.remaining_trace_count,
        enabled=result.enabled,
        dry_run=result.dry_run,
    )
