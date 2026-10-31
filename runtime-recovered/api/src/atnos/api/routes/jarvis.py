from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["jarvis"])


class JarvisBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JarvisGoalRequest(JarvisBaseModel):
    goal: str = Field(min_length=1, max_length=4000)


class JarvisManualLoginRequest(JarvisBaseModel):
    service: str = Field(min_length=1, max_length=128)


class JarvisManualLoginUrlRequest(JarvisBaseModel):
    service: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    login_url: str = Field(min_length=1, max_length=2000)
    validation_url: str | None = Field(default=None, max_length=2000)


class JarvisStatusResponse(JarvisBaseModel):
    running: bool
    config: dict[str, Any]
    modules: dict[str, Any]


class JarvisBrowserSessionResponse(JarvisBaseModel):
    service: str
    display_name: str
    login_url: str
    validation_url: str
    storage_state_path: str
    ready: bool
    expired: bool
    pending_manual_login: bool
    has_storage_state: bool
    login_window_open: bool
    status_reason: str
    semaphore_free: int | None = None


class JarvisBrowserSessionsResponse(JarvisBaseModel):
    running: bool
    sessions: list[JarvisBrowserSessionResponse]


class JarvisActionResponse(JarvisBaseModel):
    ok: bool = True
    status: JarvisStatusResponse


class JarvisBrowserSessionActionResponse(JarvisBaseModel):
    ok: bool = True
    session: JarvisBrowserSessionResponse
    status: JarvisStatusResponse


def _controller(request: Request) -> Any:
    return request.app.state.jarvis_controller


def _status_payload(controller: Any) -> JarvisStatusResponse:
    return JarvisStatusResponse(**controller.status())


@router.get("/jarvis/status", response_model=JarvisStatusResponse)
async def get_jarvis_status(request: Request) -> JarvisStatusResponse:
    return _status_payload(_controller(request))


@router.post("/jarvis/start", response_model=JarvisActionResponse)
async def start_jarvis(request: Request) -> JarvisActionResponse:
    status = await _controller(request).start()
    return JarvisActionResponse(status=JarvisStatusResponse(**status))


@router.post("/jarvis/stop", response_model=JarvisActionResponse)
async def stop_jarvis(request: Request) -> JarvisActionResponse:
    status = await _controller(request).stop()
    return JarvisActionResponse(status=JarvisStatusResponse(**status))


@router.post("/jarvis/goals", response_model=JarvisActionResponse)
async def inject_jarvis_goal(request: Request, payload: JarvisGoalRequest) -> JarvisActionResponse:
    controller = _controller(request)
    await controller.inject_goal(payload.goal)
    return JarvisActionResponse(status=_status_payload(controller))


@router.get("/jarvis/browser-sessions", response_model=JarvisBrowserSessionsResponse)
async def list_jarvis_browser_sessions(request: Request) -> JarvisBrowserSessionsResponse:
    controller = _controller(request)
    sessions = await controller.list_browser_sessions()
    return JarvisBrowserSessionsResponse(
        running=bool(controller.status().get("running")),
        sessions=[JarvisBrowserSessionResponse(**item) for item in sessions],
    )


@router.post(
    "/jarvis/browser-sessions/{service}/manual-login/start",
    response_model=JarvisBrowserSessionActionResponse,
)
async def start_jarvis_manual_login(
    request: Request,
    service: str,
) -> JarvisBrowserSessionActionResponse:
    controller = _controller(request)
    try:
        ok, session = await controller.start_manual_login(service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JarvisBrowserSessionActionResponse(
        ok=ok,
        session=JarvisBrowserSessionResponse(**session),
        status=_status_payload(controller),
    )


@router.post(
    "/jarvis/browser-sessions/manual-login/start",
    response_model=JarvisBrowserSessionActionResponse,
)
async def start_jarvis_manual_login_for_url(
    request: Request,
    payload: JarvisManualLoginUrlRequest,
) -> JarvisBrowserSessionActionResponse:
    controller = _controller(request)
    try:
        ok, session = await controller.start_manual_login_for_url(
            service=payload.service,
            display_name=payload.display_name,
            login_url=payload.login_url,
            validation_url=payload.validation_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JarvisBrowserSessionActionResponse(
        ok=ok,
        session=JarvisBrowserSessionResponse(**session),
        status=_status_payload(controller),
    )


@router.post("/jarvis/manual-login-confirmed", response_model=JarvisActionResponse)
async def confirm_jarvis_manual_login(
    request: Request,
    payload: JarvisManualLoginRequest,
) -> JarvisActionResponse:
    controller = _controller(request)
    try:
        await controller.confirm_manual_login(payload.service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JarvisActionResponse(status=_status_payload(controller))


@router.post(
    "/jarvis/browser-sessions/{service}/manual-login/confirm",
    response_model=JarvisBrowserSessionActionResponse,
)
async def confirm_jarvis_browser_session_manual_login(
    request: Request,
    service: str,
) -> JarvisBrowserSessionActionResponse:
    controller = _controller(request)
    try:
        ok, session = await controller.confirm_manual_login(service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JarvisBrowserSessionActionResponse(
        ok=ok,
        session=JarvisBrowserSessionResponse(**session),
        status=_status_payload(controller),
    )
