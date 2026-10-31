from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from atnos.services.runtime_health_service import RuntimeHealthService

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/health")
def runtime_health(request: Request) -> dict[str, Any]:
    database = getattr(request.app.state, "database", None)
    if database is None:
        return RuntimeHealthService(request.app.state.settings).snapshot()
    with database.session_scope() as session:
        return RuntimeHealthService(request.app.state.settings).snapshot(session=session)
