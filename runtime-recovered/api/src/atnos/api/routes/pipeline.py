from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field

from atnos.api.routes.production import (
    ProductionStartRequest,
    get_production_job,
    list_production_jobs,
    start_production,
)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class PipelineStartRequest(BaseModel):
    """Kompatybilny payload dla starszego API pipeline oraz nowego E2E topic payload."""

    topic_id: str | None = Field(default=None, min_length=1)
    topic: dict[str, Any] | None = None
    account_id: str = Field(default="yt-finanse-pl", min_length=1)


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
async def start_pipeline(
    request: Request,
    payload: PipelineStartRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Uruchom produkcje przez DB topic_id albo przez bezposredni E2E topic payload."""
    if payload.topic is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Direct legacy rendering is disabled because it bypasses the current English style guide, premium voice, semantic visual audit and production QA.",
                "next_action": "Add the topic to the Topic Queue, approve it, then call POST /api/production/start.",
                "required_route": "POST /api/production/start",
            },
        )

    if not payload.topic_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either topic_id or topic.",
        )
    return await start_production(
        request,
        ProductionStartRequest(account_id=payload.account_id, topic_id=payload.topic_id),
        background_tasks,
    )


@router.get("/jobs")
async def list_pipeline_jobs(
    request: Request,
    account_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Alias dla listy jobow produkcyjnych."""
    return await list_production_jobs(request, account_id=account_id, limit=limit)


@router.get("/jobs/{job_id}")
async def get_pipeline_job(job_id: str, request: Request) -> dict[str, Any]:
    """Alias dla szczegolow joba produkcyjnego."""
    return await get_production_job(job_id, request)


@router.get("/jobs/{job_id}/qa")
async def get_pipeline_job_qa(job_id: str, request: Request) -> dict[str, Any]:
    """Zwroc raport QA dla joba, jesli jest dostepny."""
    job = await get_production_job(job_id, request)
    qa_report = job.get("qa_report")
    if not isinstance(qa_report, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"QA report for job {job_id} was not found.",
        )
    return qa_report


def _push_pipeline_event(request: Request, event: dict[str, Any]) -> None:
    manager = getattr(request.app.state, "ws_manager", None)
    if manager is None:
        return
    try:
        result = manager.broadcast(event)
        if hasattr(result, "close"):
            result.close()
    except Exception:
        return
