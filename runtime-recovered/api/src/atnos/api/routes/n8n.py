from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/n8n", tags=["n8n"])


def _load_n8n_dependencies():
    from atnos.services.n8n_youtube_automation_service import (
        N8nYouTubeAutomationError,
        N8nYouTubeAutomationService,
    )

    return N8nYouTubeAutomationError, N8nYouTubeAutomationService


class CreateYouTubeApprovalRequest(BaseModel):
    video_path: str | None = None
    title: str | None = Field(default=None, max_length=100)
    description: str | None = None
    tags: list[str] | None = None


class UploadAnalyticsRequest(BaseModel):
    video_id: str = Field(..., min_length=3)
    start_date: str | None = None
    end_date: str | None = None


class DailyOpsRequest(BaseModel):
    create_approval: bool = False
    limit: int = Field(default=20, ge=1, le=100)


@router.get("/status")
def get_n8n_status(request: Request) -> dict[str, object]:
    return _service(request).status()


@router.get("/youtube/queue")
def get_youtube_queue(request: Request, limit: int = 20) -> dict[str, object]:
    items = _service(request).scan_ready_shorts(limit=max(1, min(limit, 100)))
    return {"items": [item.to_dict() for item in items]}


@router.post("/youtube/approvals")
def create_youtube_approval(
    payload: CreateYouTubeApprovalRequest,
    request: Request,
    x_atnos_n8n_token: str | None = Header(default=None),
) -> dict[str, object]:
    _check_token(request, x_atnos_n8n_token)
    N8nYouTubeAutomationError, _ = _load_n8n_dependencies()
    try:
        return _service(request).create_upload_approval(
            video_path=Path(payload.video_path) if payload.video_path else None,
            title=payload.title,
            description=payload.description,
            tags=payload.tags,
        )
    except N8nYouTubeAutomationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/youtube/daily-ops")
def run_youtube_daily_ops(
    payload: DailyOpsRequest,
    request: Request,
    x_atnos_n8n_token: str | None = Header(default=None),
) -> dict[str, object]:
    _check_token(request, x_atnos_n8n_token)
    N8nYouTubeAutomationError, _ = _load_n8n_dependencies()
    try:
        return _service(request).run_daily_ops(create_approval=payload.create_approval, limit=payload.limit)
    except N8nYouTubeAutomationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/youtube/approvals/{approval_id}/approve")
def approve_youtube_upload(
    approval_id: str,
    request: Request,
    x_atnos_n8n_token: str | None = Header(default=None),
) -> dict[str, object]:
    _check_token(request, x_atnos_n8n_token)
    N8nYouTubeAutomationError, _ = _load_n8n_dependencies()
    try:
        return _service(request).approve_upload(approval_id)
    except N8nYouTubeAutomationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/youtube/approvals/{approval_id}/upload-private")
def upload_approved_youtube_short(
    approval_id: str,
    request: Request,
    x_atnos_n8n_token: str | None = Header(default=None),
) -> dict[str, object]:
    _check_token(request, x_atnos_n8n_token)
    N8nYouTubeAutomationError, _ = _load_n8n_dependencies()
    try:
        return _service(request).upload_approved(approval_id)
    except N8nYouTubeAutomationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/youtube/analytics")
def sync_youtube_analytics(
    payload: UploadAnalyticsRequest,
    request: Request,
    x_atnos_n8n_token: str | None = Header(default=None),
) -> dict[str, object]:
    _check_token(request, x_atnos_n8n_token)
    try:
        return _service(request).sync_analytics(
            video_id=payload.video_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _service(request: Request):
    _, N8nYouTubeAutomationService = _load_n8n_dependencies()
    return N8nYouTubeAutomationService(request.app.state.settings)


def _check_token(request: Request, token: str | None) -> None:
    expected = request.app.state.settings.n8n_atnos_webhook_token
    if expected and token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid n8n token.")
