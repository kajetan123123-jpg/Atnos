from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/preflight", tags=["preflight"])


@router.get("/shorts-real-actions")
def shorts_real_actions_preflight(request: Request) -> dict[str, object]:
    from atnos.services.external_action_preflight_service import ExternalActionPreflightService

    with request.app.state.database.session_scope() as session:
        return ExternalActionPreflightService(
            session,
            request.app.state.settings,
        ).check_shorts_real_actions().to_dict()
