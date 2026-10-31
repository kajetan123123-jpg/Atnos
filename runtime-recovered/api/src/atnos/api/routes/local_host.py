from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Any, Literal

from atnos.core.local_capability_auth import (
    LOCAL_CAPABILITY_HEADER,
    LocalCapabilityDeniedError,
    LocalCapabilityUnavailableError,
    bind_local_capability_session,
    validate_local_capability,
)

router = APIRouter(tags=["local-host"])


class LocalHostControlError(ValueError):
    pass


class BrowserRuntimeError(RuntimeError):
    pass


ScreenCaptureService = None


class LocalHostControlService:
    """Lazy proxy kept patchable for route-level API tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from atnos.services.local_host_control_service import LocalHostControlService as RealService

        self._delegate = RealService(*args, **kwargs)

    def get_capability_snapshot(self):
        return self._delegate.get_capability_snapshot()

    def list_desktop_apps(self):
        return self._delegate.list_desktop_apps()

    def open_desktop_app(self, *args: Any, **kwargs: Any):
        return self._delegate.open_desktop_app(*args, **kwargs)

    def focus_desktop_app(self, *args: Any, **kwargs: Any):
        return self._delegate.focus_desktop_app(*args, **kwargs)

    def run_powershell(self, *args: Any, **kwargs: Any):
        return self._delegate.run_powershell(*args, **kwargs)

    def run_public_powershell(self, *args: Any, **kwargs: Any):
        return self._delegate.run_public_powershell(*args, **kwargs)

    def open_browser_url(self, *args: Any, **kwargs: Any):
        return self._delegate.open_browser_url(*args, **kwargs)

    def run_browser_recipe(self, *args: Any, **kwargs: Any):
        return self._delegate.run_browser_recipe(*args, **kwargs)

    def move_mouse(self, *args: Any, **kwargs: Any):
        return self._delegate.move_mouse(*args, **kwargs)

    def click_mouse(self, *args: Any, **kwargs: Any):
        return self._delegate.click_mouse(*args, **kwargs)

    def scroll_mouse(self, *args: Any, **kwargs: Any):
        return self._delegate.scroll_mouse(*args, **kwargs)

    def type_text(self, *args: Any, **kwargs: Any):
        return self._delegate.type_text(*args, **kwargs)

    def press_keys(self, *args: Any, **kwargs: Any):
        return self._delegate.press_keys(*args, **kwargs)


class DesktopBrowserObserverService:
    """Lazy proxy kept patchable for browser-learning endpoint tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from atnos.services.desktop_browser_observer_service import (
            DesktopBrowserObserverService as RealService,
        )

        self._delegate = RealService(*args, **kwargs)

    def observe_instagram_reels(self, *args: Any, **kwargs: Any):
        return self._delegate.observe_instagram_reels(*args, **kwargs)

    def learn_reproducible_instagram_formats(self, *args: Any, **kwargs: Any):
        return self._delegate.learn_reproducible_instagram_formats(*args, **kwargs)

    def learn_from_webpage(self, *args: Any, **kwargs: Any):
        return self._delegate.learn_from_webpage(*args, **kwargs)

    def learn_targeted_instagram_strategy(self, *args: Any, **kwargs: Any):
        return self._delegate.learn_targeted_instagram_strategy(*args, **kwargs)


class BrowserAccessDiagnosticsService:
    """Lazy proxy kept patchable for browser diagnostics endpoint tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from atnos.services.browser_access_diagnostics_service import (
            BrowserAccessDiagnosticsService as RealService,
        )

        self._delegate = RealService(*args, **kwargs)

    def diagnose_chrome(self, *args: Any, **kwargs: Any):
        return self._delegate.diagnose_chrome(*args, **kwargs)


class BrowserRuntimeService:
    """Lazy proxy kept patchable for Chrome bridge endpoint tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from atnos.services.browser_runtime_service import BrowserRuntimeService as RealService

        self._delegate = RealService(*args, **kwargs)

    def ensure_chrome_devtools_bridge(self, *args: Any, **kwargs: Any):
        return self._delegate.ensure_chrome_devtools_bridge(*args, **kwargs)


def _load_screen_capture_dependencies():
    from atnos.services.screen_capture_service import ScreenCaptureError, ScreenCaptureService as RealScreenCaptureService

    return ScreenCaptureService or RealScreenCaptureService, ScreenCaptureError


def _is_local_host_control_error(exc: Exception) -> bool:
    if isinstance(exc, LocalHostControlError):
        return True
    try:
        from atnos.services.local_host_control_service import LocalHostControlError as RealError
    except Exception:
        return False
    return isinstance(exc, RealError)


def _is_browser_runtime_error(exc: Exception) -> bool:
    if isinstance(exc, BrowserRuntimeError):
        return True
    try:
        from atnos.services.browser_runtime_service import BrowserRuntimeError as RealError
    except Exception:
        return False
    return isinstance(exc, RealError)


def _raise_local_host_http_error(exc: Exception, *, allow_runtime: bool = False) -> None:
    if _is_local_host_control_error(exc) or (allow_runtime and isinstance(exc, RuntimeError)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise exc


def _raise_browser_runtime_http_error(exc: Exception) -> None:
    if _is_browser_runtime_error(exc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise exc


def require_local_capability(
    request: Request,
    token: Annotated[str | None, Header(alias=LOCAL_CAPABILITY_HEADER)] = None,
) -> int:
    try:
        session = validate_local_capability(request.app.state.settings, token)
    except LocalCapabilityUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except LocalCapabilityDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "AtnosLocalCapability"},
        ) from exc
    return session.expires_at


class LocalHostBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalHostCapabilityResponse(LocalHostBaseModel):
    host_label: str
    platform: str
    current_user: str
    current_workdir: str
    powershell_available: bool
    browser_open_available: bool
    playwright_adapter_available: bool
    desktop_input_available: bool
    browser_recipe_available: bool
    elevated: bool
    execution_enabled: bool
    trusted_auto_enabled: bool
    command_log_path: str
    last_checked_at: str
    desktop_apps: list["LocalHostDesktopAppResponse"] = Field(default_factory=list)


class LocalHostRunCommandRequest(LocalHostBaseModel):
    command: str = Field(min_length=1, max_length=20000)
    timeout_s: int = Field(default=120, ge=1, le=1800)
    workdir: str | None = Field(default=None, max_length=1024)


class LocalHostOpenUrlRequest(LocalHostBaseModel):
    url: str = Field(min_length=1, max_length=2048)


class LocalHostBrowserRecipeRequest(LocalHostBaseModel):
    steps: list[dict[str, Any]] = Field(default_factory=list)
    headless: bool | None = None
    action_timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    total_timeout_ms: int | None = Field(default=None, ge=5000, le=1800000)
    retry_limit: int | None = Field(default=None, ge=0, le=4)
    allowed_domains: list[str] = Field(default_factory=list)
    recipe_name: str | None = Field(default=None, max_length=120)
    recipe_payload: dict[str, Any] = Field(default_factory=dict)
    conversation_id: int | None = Field(default=None, gt=0)


class LocalHostBrowserObserveRequest(LocalHostBaseModel):
    count: int = Field(default=5, ge=1, le=10)
    target_url: str = Field(default="https://www.instagram.com/reels/", min_length=1, max_length=2048)
    browser_process_name: str | None = Field(default=None, max_length=128)
    title_hint: str | None = Field(default="instagram", max_length=256)
    wait_ms: int = Field(default=2500, ge=500, le=10000)


class LocalHostBrowserLearnRequest(LocalHostBaseModel):
    count: int = Field(default=5, ge=1, le=10)
    target_url: str = Field(default="https://www.instagram.com/reels/", min_length=1, max_length=2048)
    browser_process_name: str | None = Field(default=None, max_length=128)
    title_hint: str | None = Field(default="instagram", max_length=256)
    wait_ms: int = Field(default=5000, ge=1000, le=15000)


class LocalHostBrowserPageLearnRequest(LocalHostBaseModel):
    target_url: str | None = Field(default=None, max_length=2048)
    selector: str = Field(default="body", min_length=1, max_length=1024)
    steps_before_extract: list[dict[str, Any]] = Field(default_factory=list)
    research_focus: str | None = Field(default=None, max_length=4000)
    note_title: str | None = Field(default=None, max_length=240)
    allowed_domains: list[str] = Field(default_factory=list)
    headless: bool | None = None
    action_timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    total_timeout_ms: int | None = Field(default=None, ge=5000, le=1800000)
    retry_limit: int | None = Field(default=None, ge=0, le=4)


class LocalHostBrowserResearchRequest(LocalHostBaseModel):
    count_per_profile: int = Field(default=3, ge=1, le=8)
    profile_keys: list[str] = Field(default_factory=list)
    browser_process_name: str | None = Field(default=None, max_length=128)
    title_hint: str | None = Field(default="instagram", max_length=256)
    wait_ms: int = Field(default=5000, ge=1000, le=15000)


class LocalHostDesktopInputRequest(LocalHostBaseModel):
    action: Literal["move_mouse", "click_mouse", "scroll_mouse", "type_text", "press_keys"]
    x: int | None = None
    y: int | None = None
    button: Literal["left", "right", "middle"] = "left"
    clicks: int = Field(default=1, ge=1, le=8)
    delta: int = Field(default=120, ge=-10000, le=10000)
    text: str | None = Field(default=None, max_length=20000)
    keys: list[str] | None = Field(default=None, max_length=8)


class LocalHostScreenCaptureRequest(LocalHostBaseModel):
    label: str = Field(default="local-host-screen", min_length=1, max_length=120)


class LocalHostScreenRecordRequest(LocalHostBaseModel):
    label: str = Field(default="local-host-screen-recording", min_length=1, max_length=120)
    duration_seconds: int = Field(default=10, ge=1, le=120)


class LocalHostCommandResponse(LocalHostBaseModel):
    action_kind: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    workdir: str
    elevated: bool
    started_at: str
    finished_at: str


class LocalHostCapabilitySessionResponse(LocalHostBaseModel):
    status: Literal["authorized"]
    expires_at: int


class LocalHostCapabilityBindRequest(LocalHostBaseModel):
    token: str = Field(min_length=8, max_length=512)
    expires_at: int = Field(gt=0)


class LocalHostBrowserRecipeResponse(LocalHostBaseModel):
    action_kind: str
    ok: bool
    final_url: str
    results: list[dict[str, Any]]
    artifacts: dict[str, str]
    runtime_fallback: str | None = None
    started_at: str
    finished_at: str


class LocalHostObservedReelResponse(LocalHostBaseModel):
    index: int
    screenshot_path: str
    window_title: str
    provider_key: str | None = None
    model_key: str | None = None
    summary: str | None = None
    error: str | None = None


class LocalHostBrowserObservationResponse(LocalHostBaseModel):
    action_kind: str
    ok: bool
    target_url: str
    browser_process_name: str | None = None
    title_hint: str | None = None
    started_at: str
    finished_at: str
    items: list[LocalHostObservedReelResponse]


class LocalHostLearnedInstagramItemResponse(LocalHostBaseModel):
    index: int
    source_excerpt: str
    analysis: str | None = None
    provider_key: str | None = None
    model_key: str | None = None
    error: str | None = None


class LocalHostBrowserLearningResponse(LocalHostBaseModel):
    action_kind: str
    ok: bool
    target_url: str
    browser_process_name: str | None = None
    title_hint: str | None = None
    note_id: str | None = None
    curated_path: str | None = None
    knowledge_path: str | None = None
    vault_note_path: str | None = None
    summary: str | None = None
    started_at: str
    finished_at: str
    items: list[LocalHostLearnedInstagramItemResponse]


class LocalHostInstagramResearchProfileResponse(LocalHostBaseModel):
    profile_key: str
    label: str
    lane: str
    search_query: str
    target_url: str
    note_id: str | None = None
    curated_path: str | None = None
    knowledge_path: str | None = None
    vault_note_path: str | None = None
    summary: str | None = None
    items: list[LocalHostLearnedInstagramItemResponse]
    error: str | None = None


class LocalHostBrowserResearchResponse(LocalHostBaseModel):
    action_kind: str
    ok: bool
    profile_count: int
    note_id: str | None = None
    curated_path: str | None = None
    knowledge_path: str | None = None
    vault_note_path: str | None = None
    summary: str | None = None
    started_at: str
    finished_at: str
    profiles: list[LocalHostInstagramResearchProfileResponse]


class LocalHostBrowserPageLearningResponse(LocalHostBaseModel):
    action_kind: str
    ok: bool
    target_url: str
    final_url: str
    selector: str
    note_id: str | None = None
    curated_path: str | None = None
    knowledge_path: str | None = None
    vault_note_path: str | None = None
    extracted_text_excerpt: str
    analysis: str | None = None
    provider_key: str | None = None
    model_key: str | None = None
    runtime_fallback: str | None = None
    started_at: str
    finished_at: str
    browser_artifacts: dict[str, str] = Field(default_factory=dict)


class LocalHostChromeDiagnosticsResponse(LocalHostBaseModel):
    browser_running: bool
    process_count: int
    mode: Literal["devtools_live", "session_fallback", "window_titles_only", "unavailable"]
    summary: str
    reasons: list[str]
    remote_debugging_port: int | None = None
    visible_windows: list[dict[str, Any]]
    tabs: list[dict[str, Any]]
    detected_apps: list[str]
    session_source_file: str | None = None
    checked_at: str


class LocalHostChromeBridgeStartRequest(LocalHostBaseModel):
    port: int = Field(default=9222, ge=1024, le=65535)
    restart_existing: bool = True
    timeout_s: int = Field(default=20, ge=5, le=60)


class LocalHostChromeBridgeResponse(LocalHostBaseModel):
    ok: bool
    status: Literal["already_live", "launched", "restarted_existing"]
    port: int
    browser_executable: str
    using_default_profile: bool
    restarted_existing: bool
    command: list[str]
    notes: list[str]
    started_at: str
    diagnostics: LocalHostChromeDiagnosticsResponse


class LocalHostScreenArtifactResponse(LocalHostBaseModel):
    kind: str
    path: str
    metadata_path: str
    label: str
    created_at: str
    duration_seconds: int | None = None


class LocalHostDesktopAppResponse(LocalHostBaseModel):
    app_key: str
    label: str
    category: str
    installed: bool
    running: bool
    window_visible: bool
    launch_supported: bool
    focus_supported: bool
    preferred_entry_path: str | None = None
    preferred_entry_kind: str | None = None
    running_processes: list[str]
    visible_window_titles: list[str]
    notes: list[str]


@router.get("/local-host/capabilities", response_model=LocalHostCapabilityResponse)
def get_local_host_capabilities(request: Request) -> LocalHostCapabilityResponse:
    with request.app.state.database.session_scope() as session:
        service = LocalHostControlService(session, settings=request.app.state.settings)
        snapshot = service.get_capability_snapshot()
        apps = service.list_desktop_apps()
    return _build_capability_response(snapshot, apps)


@router.get("/local-host/apps", response_model=list[LocalHostDesktopAppResponse])
def list_local_host_desktop_apps(request: Request) -> list[LocalHostDesktopAppResponse]:
    with request.app.state.database.session_scope() as session:
        apps = LocalHostControlService(session, settings=request.app.state.settings).list_desktop_apps()
    return [_build_desktop_app_response(item) for item in apps]


@router.post("/local-host/apps/{app_key}/open", response_model=LocalHostCommandResponse)
def open_local_host_desktop_app(
    request: Request,
    app_key: str,
    _: int = Depends(require_local_capability),
) -> LocalHostCommandResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = LocalHostControlService(
                session,
                settings=request.app.state.settings,
            ).open_desktop_app(app_key)
    except Exception as exc:
        _raise_local_host_http_error(exc)
    return _build_command_response(result)


@router.post("/local-host/apps/{app_key}/focus", response_model=LocalHostCommandResponse)
def focus_local_host_desktop_app(
    request: Request,
    app_key: str,
    _: int = Depends(require_local_capability),
) -> LocalHostCommandResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = LocalHostControlService(
                session,
                settings=request.app.state.settings,
            ).focus_desktop_app(app_key)
    except Exception as exc:
        _raise_local_host_http_error(exc)
    return _build_command_response(result)


@router.post("/local-host/run", response_model=LocalHostCommandResponse)
def run_local_host_command(
    request: Request,
    payload: LocalHostRunCommandRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostCommandResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = LocalHostControlService(
                session,
                settings=request.app.state.settings,
            ).run_public_powershell(
                payload.command,
                timeout_s=payload.timeout_s,
                workdir=payload.workdir,
            )
    except Exception as exc:
        _raise_local_host_http_error(exc)
    return _build_command_response(result)


@router.get(
    "/local-host/capability-session",
    response_model=LocalHostCapabilitySessionResponse,
)
def get_local_host_capability_session(
    expires_at: int = Depends(require_local_capability),
) -> LocalHostCapabilitySessionResponse:
    return LocalHostCapabilitySessionResponse(status="authorized", expires_at=expires_at)


@router.post(
    "/local-host/capability-session/bind",
    response_model=LocalHostCapabilitySessionResponse,
)
def bind_local_host_capability_session(
    request: Request,
    payload: LocalHostCapabilityBindRequest,
) -> LocalHostCapabilitySessionResponse:
    """Hot-bind launcher capability onto a healthy API without process restart."""
    client_host = getattr(getattr(request, "client", None), "host", None)
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Capability bind is only allowed from localhost.",
        )
    try:
        session = bind_local_capability_session(
            token=payload.token,
            expires_at=payload.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return LocalHostCapabilitySessionResponse(status="authorized", expires_at=session.expires_at)


@router.post("/local-host/browser/open", response_model=LocalHostCommandResponse)
def open_local_host_browser_url(
    request: Request,
    payload: LocalHostOpenUrlRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostCommandResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = LocalHostControlService(
                session,
                settings=request.app.state.settings,
            ).open_browser_url(payload.url)
    except Exception as exc:
        _raise_local_host_http_error(exc)
    return _build_command_response(result)


@router.post("/local-host/browser/run", response_model=LocalHostBrowserRecipeResponse)
def run_local_host_browser_recipe(
    request: Request,
    payload: LocalHostBrowserRecipeRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostBrowserRecipeResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = LocalHostControlService(
                session,
                settings=request.app.state.settings,
            ).run_browser_recipe(
                steps=payload.steps,
                headless=payload.headless,
                action_timeout_ms=payload.action_timeout_ms,
                total_timeout_ms=payload.total_timeout_ms,
                retry_limit=payload.retry_limit,
                allowed_domains=payload.allowed_domains,
                recipe_name=payload.recipe_name,
                recipe_payload=payload.recipe_payload,
                conversation_id=payload.conversation_id,
            )
    except Exception as exc:
        _raise_local_host_http_error(exc)
    return _build_browser_recipe_response(result)


@router.post("/local-host/browser/observe-instagram", response_model=LocalHostBrowserObservationResponse)
def observe_local_host_instagram_reels(
    request: Request,
    payload: LocalHostBrowserObserveRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostBrowserObservationResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = DesktopBrowserObserverService(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).observe_instagram_reels(
                count=payload.count,
                target_url=payload.target_url,
                browser_process_name=payload.browser_process_name,
                title_hint=payload.title_hint,
                wait_ms=payload.wait_ms,
            )
    except Exception as exc:
        _raise_local_host_http_error(exc, allow_runtime=True)
    return _build_browser_observation_response(result)


@router.post("/local-host/browser/learn-instagram", response_model=LocalHostBrowserLearningResponse)
def learn_from_local_host_instagram_feed(
    request: Request,
    payload: LocalHostBrowserLearnRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostBrowserLearningResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = DesktopBrowserObserverService(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).learn_reproducible_instagram_formats(
                count=payload.count,
                target_url=payload.target_url,
                browser_process_name=payload.browser_process_name,
                title_hint=payload.title_hint,
                wait_ms=payload.wait_ms,
            )
    except Exception as exc:
        _raise_local_host_http_error(exc, allow_runtime=True)
    return _build_browser_learning_response(result)


@router.post("/local-host/browser/learn-page", response_model=LocalHostBrowserPageLearningResponse)
def learn_from_local_host_browser_page(
    request: Request,
    payload: LocalHostBrowserPageLearnRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostBrowserPageLearningResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = DesktopBrowserObserverService(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).learn_from_webpage(
                target_url=payload.target_url,
                selector=payload.selector,
                steps_before_extract=payload.steps_before_extract,
                research_focus=payload.research_focus,
                note_title=payload.note_title,
                allowed_domains=payload.allowed_domains,
                headless=payload.headless,
                action_timeout_ms=payload.action_timeout_ms,
                total_timeout_ms=payload.total_timeout_ms,
                retry_limit=payload.retry_limit,
            )
    except Exception as exc:
        _raise_local_host_http_error(exc, allow_runtime=True)
    return _build_browser_page_learning_response(result)


@router.post("/local-host/browser/research-instagram", response_model=LocalHostBrowserResearchResponse)
def research_local_host_instagram_strategy(
    request: Request,
    payload: LocalHostBrowserResearchRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostBrowserResearchResponse:
    try:
        with request.app.state.database.session_scope() as session:
            result = DesktopBrowserObserverService(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).learn_targeted_instagram_strategy(
                count_per_profile=payload.count_per_profile,
                profile_keys=payload.profile_keys,
                browser_process_name=payload.browser_process_name,
                title_hint=payload.title_hint,
                wait_ms=payload.wait_ms,
            )
    except Exception as exc:
        _raise_local_host_http_error(exc, allow_runtime=True)
    return _build_browser_research_response(result)


@router.get("/local-host/browser/chrome-diagnostics", response_model=LocalHostChromeDiagnosticsResponse)
def get_local_host_chrome_diagnostics(
    request: Request,
    tab_limit: int = 20,
) -> LocalHostChromeDiagnosticsResponse:
    with request.app.state.database.session_scope() as session:
        diagnostics = BrowserAccessDiagnosticsService(
            session,
            settings=request.app.state.settings,
        ).diagnose_chrome(tab_limit=max(1, min(tab_limit, 50)))
    return LocalHostChromeDiagnosticsResponse(**diagnostics.to_dict())


@router.post("/local-host/browser/chrome-bridge/start", response_model=LocalHostChromeBridgeResponse)
def start_local_host_chrome_bridge(
    request: Request,
    payload: LocalHostChromeBridgeStartRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostChromeBridgeResponse:
    try:
        launch = BrowserRuntimeService(request.app.state.settings).ensure_chrome_devtools_bridge(
            port=payload.port,
            restart_existing=payload.restart_existing,
            timeout_s=payload.timeout_s,
        )
        with request.app.state.database.session_scope() as session:
            diagnostics = BrowserAccessDiagnosticsService(
                session,
                settings=request.app.state.settings,
            ).diagnose_chrome(tab_limit=20)
    except Exception as exc:
        _raise_browser_runtime_http_error(exc)
    return LocalHostChromeBridgeResponse(
        **launch.to_dict(),
        diagnostics=LocalHostChromeDiagnosticsResponse(**diagnostics.to_dict()),
    )


@router.post("/local-host/screen/screenshot", response_model=LocalHostScreenArtifactResponse)
def capture_local_host_screen(
    request: Request,
    payload: LocalHostScreenCaptureRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostScreenArtifactResponse:
    service_cls, ScreenCaptureError = _load_screen_capture_dependencies()
    try:
        artifact = service_cls(request.app.state.settings).capture_screenshot(label=payload.label)
    except ScreenCaptureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return LocalHostScreenArtifactResponse(**artifact.to_dict())


@router.post("/local-host/screen/record", response_model=LocalHostScreenArtifactResponse)
def record_local_host_screen(
    request: Request,
    payload: LocalHostScreenRecordRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostScreenArtifactResponse:
    service_cls, ScreenCaptureError = _load_screen_capture_dependencies()
    try:
        artifact = service_cls(request.app.state.settings).record_screen(
            label=payload.label,
            duration_seconds=payload.duration_seconds,
        )
    except ScreenCaptureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return LocalHostScreenArtifactResponse(**artifact.to_dict())


@router.post("/local-host/input", response_model=LocalHostCommandResponse)
def perform_local_host_input_action(
    request: Request,
    payload: LocalHostDesktopInputRequest,
    _: int = Depends(require_local_capability),
) -> LocalHostCommandResponse:
    try:
        with request.app.state.database.session_scope() as session:
            service = LocalHostControlService(
                session,
                settings=request.app.state.settings,
            )
            if payload.action == "move_mouse":
                if payload.x is None or payload.y is None:
                    raise LocalHostControlError("move_mouse requires x and y.")
                result = service.move_mouse(x=payload.x, y=payload.y)
            elif payload.action == "click_mouse":
                result = service.click_mouse(
                    button=payload.button,
                    clicks=payload.clicks,
                    x=payload.x,
                    y=payload.y,
                )
            elif payload.action == "scroll_mouse":
                result = service.scroll_mouse(delta=payload.delta)
            elif payload.action == "type_text":
                if payload.text is None:
                    raise LocalHostControlError("type_text requires text.")
                result = service.type_text(text=payload.text)
            elif payload.action == "press_keys":
                if not payload.keys:
                    raise LocalHostControlError("press_keys requires keys.")
                result = service.press_keys(keys=payload.keys)
            else:
                raise LocalHostControlError(f"Unsupported input action '{payload.action}'.")
    except Exception as exc:
        _raise_local_host_http_error(exc)
    return _build_command_response(result)


def _build_capability_response(
    item: Any,
    desktop_apps: list[Any],
) -> LocalHostCapabilityResponse:
    payload = item.to_dict()
    payload["desktop_apps"] = [_build_desktop_app_response(app).model_dump() for app in desktop_apps]
    return LocalHostCapabilityResponse(**payload)


def _build_command_response(item: Any) -> LocalHostCommandResponse:
    return LocalHostCommandResponse(**item.to_dict())


def _build_browser_recipe_response(
    item: Any,
) -> LocalHostBrowserRecipeResponse:
    return LocalHostBrowserRecipeResponse(**item.to_dict())


def _build_browser_observation_response(
    item: Any,
) -> LocalHostBrowserObservationResponse:
    return LocalHostBrowserObservationResponse(**item.to_dict())


def _build_browser_learning_response(
    item: Any,
) -> LocalHostBrowserLearningResponse:
    return LocalHostBrowserLearningResponse(**item.to_dict())


def _build_browser_research_response(
    item: Any,
) -> LocalHostBrowserResearchResponse:
    return LocalHostBrowserResearchResponse(**item.to_dict())


def _build_browser_page_learning_response(
    item: Any,
) -> LocalHostBrowserPageLearningResponse:
    return LocalHostBrowserPageLearningResponse(**item.to_dict())


def _build_desktop_app_response(item: Any) -> LocalHostDesktopAppResponse:
    return LocalHostDesktopAppResponse(**item.to_dict())
