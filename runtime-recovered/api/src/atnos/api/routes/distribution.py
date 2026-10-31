from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from atnos.production.cross_platform_cockpit import CrossPlatformDistributionCockpitService

router = APIRouter(prefix="/api/distribution", tags=["distribution"])


class DistributionPackageLatestRequest(BaseModel):
    account_id: str | None = None
    platforms: list[str] | None = None


@router.get("/cockpit")
async def distribution_cockpit(
    request: Request,
    account_id: str | None = None,
    limit: int = Query(default=5, ge=1, le=25),
) -> dict[str, Any]:
    """Return supervised cross-platform distribution status."""
    with request.app.state.database.session_scope() as session:
        return CrossPlatformDistributionCockpitService(
            request.app.state.settings,
            session=session,
        ).status(account_id=account_id, limit=limit)


@router.post("/package-latest")
async def create_latest_distribution_package(
    request: Request,
    payload: DistributionPackageLatestRequest,
) -> dict[str, Any]:
    """Create release packages for the latest export-ready production job."""
    with request.app.state.database.session_scope() as session:
        return CrossPlatformDistributionCockpitService(
            request.app.state.settings,
            session=session,
        ).create_latest_release_package(
            account_id=payload.account_id,
            platforms=payload.platforms,
        )
