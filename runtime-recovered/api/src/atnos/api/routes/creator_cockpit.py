from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from atnos.services.creator_cockpit_service import CreatorCockpitService

router = APIRouter(prefix="/api/creator-cockpit", tags=["creator-cockpit"])


@router.get("/status")
async def creator_cockpit_status(request: Request) -> dict[str, Any]:
    """Return Synthetic Model Lab cockpit state."""
    with request.app.state.database.session_scope() as session:
        return CreatorCockpitService(session, request.app.state.settings).status()


@router.post("/ensure-default", status_code=status.HTTP_201_CREATED)
async def ensure_default_creator_project(request: Request) -> dict[str, Any]:
    """Create the safe default Synthetic Model Lab if it does not exist."""
    with request.app.state.database.session_scope() as session:
        return CreatorCockpitService(session, request.app.state.settings).ensure_default_project()


@router.post("/seed-work-items")
async def seed_creator_work_items(request: Request) -> dict[str, Any]:
    """Persist private draft work items for the default synthetic creator project."""
    with request.app.state.database.session_scope() as session:
        return CreatorCockpitService(session, request.app.state.settings).seed_work_items()


@router.post("/external-publish")
async def blocked_creator_external_publish(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reject external synthetic creator publishing until review and account gates are ready."""
    with request.app.state.database.session_scope() as session:
        result = CreatorCockpitService(session, request.app.state.settings).reject_external_publish(payload)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result)
