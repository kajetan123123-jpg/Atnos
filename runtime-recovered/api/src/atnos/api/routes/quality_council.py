from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from atnos.services.quality_council_service import AuditKey, DEFAULT_AUDITS, QualityCouncilService


router = APIRouter(prefix="/api/quality-council", tags=["quality-council"])


class QualityCouncilRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audits: list[AuditKey] = Field(default_factory=lambda: list(DEFAULT_AUDITS))
    include_external_handoffs: bool = True
    attempt_gmail_delivery: bool = True


@router.get("/status")
def quality_council_status(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        return QualityCouncilService(session, request.app.state.settings).status()


@router.get("/reports")
def quality_council_reports(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        return {"runs": QualityCouncilService(session, request.app.state.settings).recent_runs(limit=limit)}


@router.post("/run")
async def run_quality_council(request: Request, payload: QualityCouncilRunRequest) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return await QualityCouncilService(session, request.app.state.settings).run(
                audits=tuple(payload.audits),
                include_external_handoffs=payload.include_external_handoffs,
                attempt_gmail_delivery=payload.attempt_gmail_delivery,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
