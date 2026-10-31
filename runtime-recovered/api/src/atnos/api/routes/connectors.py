from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from atnos.api.routes.action_broker import (
    ActionDraftAccountSummaryResponse,
    ActionDraftResponse,
)

router = APIRouter(tags=["connectors"])


class LocalRuntimeProbeService:
    """Lazy proxy kept patchable for tests that patch this route-level service."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from atnos.services.local_runtime_probe_service import LocalRuntimeProbeService as RealService

        self._delegate = RealService(*args, **kwargs)

    def probe_all(self):
        return self._delegate.probe_all()


def _load_github_probe_service():
    from atnos.services.github_connector_probe_service import GitHubConnectorProbeService

    return GitHubConnectorProbeService


def _load_github_connector_service():
    from atnos.services.github_connector_service import GitHubConnectorService

    return GitHubConnectorService


def _load_google_calendar_connector_service():
    from atnos.services.google_calendar_connector_service import GoogleCalendarConnectorService

    return GoogleCalendarConnectorService


def _load_gmail_connector_service():
    from atnos.services.gmail_connector_service import GmailConnectorService

    return GmailConnectorService


def _load_smtp_mail_connector_service():
    from atnos.services.smtp_mail_connector_service import SmtpMailConnectorService

    return SmtpMailConnectorService


def _load_youtube_connector_dependencies():
    from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService

    return YouTubeConnectorError, YouTubeConnectorService


class ConnectorsBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitHubAccountMetadataResponse(ConnectorsBaseModel):
    login: str | None
    name: str | None
    profile_url: str | None


class GitHubConnectorStatusResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    connection_id: int | None
    account_id: int | None
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: list[str]
    granted_scopes: list[str]
    warnings: list[str]
    connected_at: datetime | None
    disconnected_at: datetime | None
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool


class GitHubConnectorProbeResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    probe_status: str
    connection_id: int | None
    account_id: int | None
    provider_reachable: bool
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: list[str]
    granted_scopes: list[str]
    warnings: list[str]
    account_metadata: GitHubAccountMetadataResponse | None
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    message: str


class YouTubeConnectorStatusResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    configured: bool
    connected: bool
    ready: bool
    warnings: list[str]
    account_id: int | None
    channel_title: str | None
    granted_scopes: list[str]


class GoogleCalendarConnectorStatusResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    connection_id: int | None
    account_id: int | None
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: list[str]
    granted_scopes: list[str]
    warnings: list[str]
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    connected: bool


class GoogleCalendarConnectorProbeResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    probe_status: str
    connection_id: int | None
    account_id: int | None
    provider_reachable: bool
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: list[str]
    granted_scopes: list[str]
    warnings: list[str]
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    message: str


class GmailConnectorStatusResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    connection_id: int | None
    account_id: int | None
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: list[str]
    granted_scopes: list[str]
    warnings: list[str]
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    connected: bool


class GmailConnectorProbeResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    integration_key: str
    oauth_status: str
    probe_status: str
    connection_id: int | None
    account_id: int | None
    provider_reachable: bool
    provider_enabled: bool
    provider_configured: bool
    token_present: bool
    required_scopes: list[str]
    granted_scopes: list[str]
    warnings: list[str]
    checked_at: datetime
    live_read_enabled: bool
    live_write_enabled: bool
    message: str


class SmtpMailStatusResponse(ConnectorsBaseModel):
    connector_key: str
    display_name: str
    provider_key: str
    account_id: int | None
    account_identifier: str | None
    configured: bool
    has_secret: bool
    warnings: list[str]
    checked_at: datetime
    live_login_enabled: bool
    live_send_enabled: bool
    probe_status: str
    message: str


class SmtpMailSendTestRequest(ConnectorsBaseModel):
    to: str = Field(min_length=3, max_length=400)
    subject: str = Field(min_length=1, max_length=400)
    body: str = Field(min_length=1, max_length=20000)
    account_id: int | None = Field(default=None, gt=0)


class YouTubeVideoStatusResponse(ConnectorsBaseModel):
    video_id: str
    found: bool
    privacy_status: str | None = None
    upload_status: str | None = None
    processing_status: str | None = None
    title: str | None = None


class YouTubeAnalyticsResponse(ConnectorsBaseModel):
    video_id: str | None = None
    status: str
    message: str | None = None
    views: int | None = None
    average_view_duration: float | None = None
    average_view_percentage: float | None = None


class YouTubeCommentPreviewRequest(ConnectorsBaseModel):
    account_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    video_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=4000)


class GoogleCalendarEventPreviewRequest(ConnectorsBaseModel):
    account_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    payload: dict[str, object]


class GoogleCalendarDeleteEventPreviewRequest(ConnectorsBaseModel):
    account_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    event_id: str = Field(min_length=1, max_length=200)


class GoogleCalendarFreeSlotsRequest(ConnectorsBaseModel):
    duration_minutes: int = Field(gt=0, le=1440)
    candidates: list[dict[str, str]] = Field(default_factory=list)


class GmailDraftMessageRequest(ConnectorsBaseModel):
    to: str = Field(min_length=1, max_length=400)
    subject: str = Field(min_length=1, max_length=400)
    body: str = Field(min_length=1, max_length=20000)


class GmailSendPreviewRequest(ConnectorsBaseModel):
    account_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    draft_id: str = Field(min_length=1, max_length=200)
    to: str = Field(min_length=1, max_length=400)
    subject: str = Field(min_length=1, max_length=400)
    body: str = Field(min_length=1, max_length=20000)


class GitHubIssuePreviewRequest(ConnectorsBaseModel):
    account_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    owner: str = Field(min_length=1, max_length=200)
    repo: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=400)
    body: str = Field(default="", max_length=20000)


class GitHubPullRequestPreviewRequest(ConnectorsBaseModel):
    account_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    owner: str = Field(min_length=1, max_length=200)
    repo: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=400)
    body: str = Field(default="", max_length=20000)
    head: str = Field(min_length=1, max_length=200)
    base: str = Field(min_length=1, max_length=200)


class LocalRuntimeProbeResponse(ConnectorsBaseModel):
    key: str
    label: str
    status: str
    ready: bool
    probe_kind: str
    reason: str
    checked_at: datetime
    details: dict[str, object]
    suggestions: list[str]


@router.get("/connectors/github/status", response_model=GitHubConnectorStatusResponse)
def get_github_connector_status(request: Request) -> GitHubConnectorStatusResponse:
    GitHubConnectorProbeService = _load_github_probe_service()
    with request.app.state.database.session_scope() as session:
        item = GitHubConnectorProbeService(session, request.app.state.settings).get_status()
    return _build_status_response(item)


@router.post("/connectors/github/probe", response_model=GitHubConnectorProbeResponse)
def probe_github_connector(request: Request) -> GitHubConnectorProbeResponse:
    GitHubConnectorProbeService = _load_github_probe_service()
    with request.app.state.database.session_scope() as session:
        item = GitHubConnectorProbeService(session, request.app.state.settings).probe()
    return _build_probe_response(item)


@router.get("/connectors/google-calendar/status", response_model=GoogleCalendarConnectorStatusResponse)
def get_google_calendar_connector_status(request: Request) -> GoogleCalendarConnectorStatusResponse:
    GoogleCalendarConnectorService = _load_google_calendar_connector_service()
    with request.app.state.database.session_scope() as session:
        item = GoogleCalendarConnectorService(session, request.app.state.settings).get_status()
    return _build_google_calendar_status_response(item)


@router.post("/connectors/google-calendar/probe", response_model=GoogleCalendarConnectorProbeResponse)
def probe_google_calendar_connector(request: Request) -> GoogleCalendarConnectorProbeResponse:
    GoogleCalendarConnectorService = _load_google_calendar_connector_service()
    with request.app.state.database.session_scope() as session:
        item = GoogleCalendarConnectorService(session, request.app.state.settings).probe()
    return _build_google_calendar_probe_response(item)


@router.get("/connectors/gmail/status", response_model=GmailConnectorStatusResponse)
def get_gmail_connector_status(request: Request) -> GmailConnectorStatusResponse:
    GmailConnectorService = _load_gmail_connector_service()
    with request.app.state.database.session_scope() as session:
        item = GmailConnectorService(session, request.app.state.settings).get_status()
    return _build_gmail_status_response(item)


@router.post("/connectors/gmail/probe", response_model=GmailConnectorProbeResponse)
def probe_gmail_connector(request: Request) -> GmailConnectorProbeResponse:
    GmailConnectorService = _load_gmail_connector_service()
    with request.app.state.database.session_scope() as session:
        item = GmailConnectorService(session, request.app.state.settings).probe()
    return _build_gmail_probe_response(item)


@router.get("/connectors/smtp-mail/status", response_model=SmtpMailStatusResponse)
def get_smtp_mail_connector_status(
    request: Request,
    account_id: int | None = Query(default=None, gt=0),
) -> SmtpMailStatusResponse:
    SmtpMailConnectorService = _load_smtp_mail_connector_service()
    with request.app.state.database.session_scope() as session:
        item = SmtpMailConnectorService(session, request.app.state.settings).get_status(account_id=account_id)
    return _build_smtp_mail_status_response(item)


@router.post("/connectors/smtp-mail/probe", response_model=SmtpMailStatusResponse)
def probe_smtp_mail_connector(
    request: Request,
    account_id: int | None = Query(default=None, gt=0),
) -> SmtpMailStatusResponse:
    SmtpMailConnectorService = _load_smtp_mail_connector_service()
    with request.app.state.database.session_scope() as session:
        item = SmtpMailConnectorService(session, request.app.state.settings).probe(account_id=account_id)
    return _build_smtp_mail_status_response(item)


@router.post("/connectors/smtp-mail/send-test")
def send_smtp_mail_test(request: Request, payload: SmtpMailSendTestRequest) -> dict[str, object]:
    SmtpMailConnectorService = _load_smtp_mail_connector_service()
    with request.app.state.database.session_scope() as session:
        return SmtpMailConnectorService(session, request.app.state.settings).send_test_message(
            account_id=payload.account_id,
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
        )


@router.get("/connectors/youtube/status", response_model=YouTubeConnectorStatusResponse)
def get_youtube_connector_status(
    request: Request,
    account_id: int | None = Query(default=None, gt=0),
) -> YouTubeConnectorStatusResponse:
    _, YouTubeConnectorService = _load_youtube_connector_dependencies()
    with request.app.state.database.session_scope() as session:
        item = YouTubeConnectorService(session, request.app.state.settings, account_id=account_id).get_status()
    return YouTubeConnectorStatusResponse(**item.to_dict())


@router.post("/connectors/youtube/probe", response_model=YouTubeConnectorStatusResponse)
def probe_youtube_connector(
    request: Request,
    account_id: int | None = Query(default=None, gt=0),
) -> YouTubeConnectorStatusResponse:
    YouTubeConnectorError, YouTubeConnectorService = _load_youtube_connector_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            item = YouTubeConnectorService(session, request.app.state.settings, account_id=account_id).probe()
        return YouTubeConnectorStatusResponse(**item.to_dict())
    except YouTubeConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/connectors/local-runtimes/probe", response_model=list[LocalRuntimeProbeResponse])
def probe_local_runtimes(request: Request) -> list[LocalRuntimeProbeResponse]:
    items = LocalRuntimeProbeService(request.app.state.settings).probe_all()
    return [_build_local_runtime_probe_response(item) for item in items]


@router.get("/connectors/youtube/videos/{video_id}", response_model=YouTubeVideoStatusResponse)
def get_youtube_video_status(video_id: str, request: Request) -> YouTubeVideoStatusResponse:
    YouTubeConnectorError, YouTubeConnectorService = _load_youtube_connector_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            payload = YouTubeConnectorService(session, request.app.state.settings).fetch_video_status(video_id)
        return YouTubeVideoStatusResponse(**payload)
    except YouTubeConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/connectors/youtube/analytics/{video_id}", response_model=YouTubeAnalyticsResponse)
def get_youtube_video_analytics(video_id: str, request: Request) -> YouTubeAnalyticsResponse:
    YouTubeConnectorError, YouTubeConnectorService = _load_youtube_connector_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            payload = YouTubeConnectorService(session, request.app.state.settings).fetch_analytics(video_id)
        return YouTubeAnalyticsResponse(**payload)
    except YouTubeConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/connectors/youtube/subscribers")
def get_youtube_subscribers_count(
    request: Request,
    channel_id: str | None = Query(default=None),
) -> dict[str, object]:
    YouTubeConnectorError, YouTubeConnectorService = _load_youtube_connector_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return YouTubeConnectorService(session, request.app.state.settings).get_subscribers_count(channel_id=channel_id)
    except YouTubeConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/connectors/youtube/comments/preview", response_model=ActionDraftResponse)
def create_youtube_comment_preview(
    request: Request,
    payload: YouTubeCommentPreviewRequest,
) -> ActionDraftResponse:
    _, YouTubeConnectorService = _load_youtube_connector_dependencies()
    with request.app.state.database.session_scope() as session:
        draft = YouTubeConnectorService(session, request.app.state.settings).create_comment_preview(
            account_id=payload.account_id,
            project_id=payload.project_id,
            video_id=payload.video_id,
            text=payload.text,
        )
    return _build_action_draft_response(draft)


@router.get("/connectors/google-calendar/events")
def list_google_calendar_events(
    request: Request,
    time_min: str,
    time_max: str,
    max_results: int = Query(default=50, ge=1, le=50),
) -> dict[str, object]:
    GoogleCalendarConnectorService = _load_google_calendar_connector_service()
    with request.app.state.database.session_scope() as session:
        return GoogleCalendarConnectorService(session, request.app.state.settings).list_events(
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )


@router.post("/connectors/google-calendar/free-slots")
def find_google_calendar_free_slots(
    request: Request,
    payload: GoogleCalendarFreeSlotsRequest,
) -> dict[str, object]:
    GoogleCalendarConnectorService = _load_google_calendar_connector_service()
    with request.app.state.database.session_scope() as session:
        return GoogleCalendarConnectorService(session, request.app.state.settings).find_free_slots(
            duration_minutes=payload.duration_minutes,
            candidates=payload.candidates,
        )


@router.post("/connectors/google-calendar/events/preview", response_model=ActionDraftResponse)
def create_google_calendar_event_preview(
    request: Request,
    payload: GoogleCalendarEventPreviewRequest,
) -> ActionDraftResponse:
    GoogleCalendarConnectorService = _load_google_calendar_connector_service()
    with request.app.state.database.session_scope() as session:
        draft = GoogleCalendarConnectorService(session, request.app.state.settings).create_event_preview(
            account_id=payload.account_id,
            project_id=payload.project_id,
            payload=payload.payload,
        )
    return _build_action_draft_response(draft)


@router.post("/connectors/google-calendar/events/delete-preview", response_model=ActionDraftResponse)
def create_google_calendar_delete_preview(
    request: Request,
    payload: GoogleCalendarDeleteEventPreviewRequest,
) -> ActionDraftResponse:
    GoogleCalendarConnectorService = _load_google_calendar_connector_service()
    with request.app.state.database.session_scope() as session:
        draft = GoogleCalendarConnectorService(session, request.app.state.settings).delete_event_preview(
            account_id=payload.account_id,
            project_id=payload.project_id,
            event_id=payload.event_id,
        )
    return _build_action_draft_response(draft)


@router.get("/connectors/gmail/messages/search")
def search_gmail_messages(
    request: Request,
    query: str,
    max_results: int = Query(default=20, ge=1, le=50),
) -> dict[str, object]:
    GmailConnectorService = _load_gmail_connector_service()
    with request.app.state.database.session_scope() as session:
        return GmailConnectorService(session, request.app.state.settings).search_messages(
            query=query,
            max_results=max_results,
        )


@router.get("/connectors/gmail/messages/{message_id}")
def read_gmail_message(message_id: str, request: Request) -> dict[str, object]:
    GmailConnectorService = _load_gmail_connector_service()
    with request.app.state.database.session_scope() as session:
        return GmailConnectorService(session, request.app.state.settings).read_message(message_id=message_id)


@router.post("/connectors/gmail/drafts")
def create_gmail_draft(request: Request, payload: GmailDraftMessageRequest) -> dict[str, object]:
    GmailConnectorService = _load_gmail_connector_service()
    with request.app.state.database.session_scope() as session:
        return GmailConnectorService(session, request.app.state.settings).draft_message(
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
        )


@router.post("/connectors/gmail/send-preview", response_model=ActionDraftResponse)
def create_gmail_send_preview(
    request: Request,
    payload: GmailSendPreviewRequest,
) -> ActionDraftResponse:
    GmailConnectorService = _load_gmail_connector_service()
    with request.app.state.database.session_scope() as session:
        draft = GmailConnectorService(session, request.app.state.settings).create_send_preview(
            account_id=payload.account_id,
            project_id=payload.project_id,
            draft_id=payload.draft_id,
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
        )
    return _build_action_draft_response(draft)


@router.get("/connectors/github/repos")
def list_github_repos(request: Request) -> dict[str, object]:
    GitHubConnectorService = _load_github_connector_service()
    with request.app.state.database.session_scope() as session:
        return GitHubConnectorService(session, request.app.state.settings).list_user_repos()


@router.get("/connectors/github/issues")
def list_github_repo_issues(
    request: Request,
    owner: str,
    repo: str,
    state: str = Query(default="open"),
) -> dict[str, object]:
    GitHubConnectorService = _load_github_connector_service()
    with request.app.state.database.session_scope() as session:
        return GitHubConnectorService(session, request.app.state.settings).list_repo_issues(
            owner=owner,
            repo=repo,
            state=state,
        )


@router.get("/connectors/github/file")
def read_github_repo_file(
    request: Request,
    owner: str,
    repo: str,
    path: str,
    ref: str = Query(default="main"),
) -> dict[str, object]:
    GitHubConnectorService = _load_github_connector_service()
    with request.app.state.database.session_scope() as session:
        return GitHubConnectorService(session, request.app.state.settings).read_file_content(
            owner=owner,
            repo=repo,
            path=path,
            ref=ref,
        )


@router.post("/connectors/github/issues/preview", response_model=ActionDraftResponse)
def create_github_issue_preview(
    request: Request,
    payload: GitHubIssuePreviewRequest,
) -> ActionDraftResponse:
    GitHubConnectorService = _load_github_connector_service()
    with request.app.state.database.session_scope() as session:
        draft = GitHubConnectorService(session, request.app.state.settings).create_issue_preview(
            account_id=payload.account_id,
            project_id=payload.project_id,
            owner=payload.owner,
            repo=payload.repo,
            title=payload.title,
            body=payload.body,
        )
    return _build_action_draft_response(draft)


@router.post("/connectors/github/pull-requests/preview", response_model=ActionDraftResponse)
def create_github_pull_request_preview(
    request: Request,
    payload: GitHubPullRequestPreviewRequest,
) -> ActionDraftResponse:
    GitHubConnectorService = _load_github_connector_service()
    with request.app.state.database.session_scope() as session:
        draft = GitHubConnectorService(session, request.app.state.settings).create_pull_request_preview(
            account_id=payload.account_id,
            project_id=payload.project_id,
            owner=payload.owner,
            repo=payload.repo,
            title=payload.title,
            body=payload.body,
            head=payload.head,
            base=payload.base,
        )
    return _build_action_draft_response(draft)


def _build_status_response(item: Any) -> GitHubConnectorStatusResponse:
    return GitHubConnectorStatusResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        integration_key=item.integration_key,
        oauth_status=item.oauth_status,
        connection_id=item.connection_id,
        account_id=item.account_id,
        provider_enabled=item.provider_enabled,
        provider_configured=item.provider_configured,
        token_present=item.token_present,
        required_scopes=list(item.required_scopes),
        granted_scopes=list(item.granted_scopes),
        warnings=list(item.warnings),
        connected_at=item.connected_at,
        disconnected_at=item.disconnected_at,
        checked_at=item.checked_at,
        live_read_enabled=item.live_read_enabled,
        live_write_enabled=item.live_write_enabled,
    )


def _build_probe_response(item: Any) -> GitHubConnectorProbeResponse:
    return GitHubConnectorProbeResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        integration_key=item.integration_key,
        oauth_status=item.oauth_status,
        probe_status=item.probe_status,
        connection_id=item.connection_id,
        account_id=item.account_id,
        provider_reachable=item.provider_reachable,
        provider_enabled=item.provider_enabled,
        provider_configured=item.provider_configured,
        token_present=item.token_present,
        required_scopes=list(item.required_scopes),
        granted_scopes=list(item.granted_scopes),
        warnings=list(item.warnings),
        account_metadata=_build_account_metadata_response(item.account_metadata),
        checked_at=item.checked_at,
        live_read_enabled=item.live_read_enabled,
        live_write_enabled=item.live_write_enabled,
        message=item.message,
    )


def _build_account_metadata_response(
    item: Any | None,
) -> GitHubAccountMetadataResponse | None:
    if item is None:
        return None
    return GitHubAccountMetadataResponse(
        login=item.login,
        name=item.name,
        profile_url=item.profile_url,
    )


def _build_google_calendar_status_response(
    item: Any,
) -> GoogleCalendarConnectorStatusResponse:
    return GoogleCalendarConnectorStatusResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        integration_key=item.integration_key,
        oauth_status=item.oauth_status,
        connection_id=item.connection_id,
        account_id=item.account_id,
        provider_enabled=item.provider_enabled,
        provider_configured=item.provider_configured,
        token_present=item.token_present,
        required_scopes=list(item.required_scopes),
        granted_scopes=list(item.granted_scopes),
        warnings=list(item.warnings),
        checked_at=item.checked_at,
        live_read_enabled=item.live_read_enabled,
        live_write_enabled=item.live_write_enabled,
        connected=item.connected,
    )


def _build_google_calendar_probe_response(
    item: Any,
) -> GoogleCalendarConnectorProbeResponse:
    return GoogleCalendarConnectorProbeResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        integration_key=item.integration_key,
        oauth_status=item.oauth_status,
        probe_status=item.probe_status,
        connection_id=item.connection_id,
        account_id=item.account_id,
        provider_reachable=item.provider_reachable,
        provider_enabled=item.provider_enabled,
        provider_configured=item.provider_configured,
        token_present=item.token_present,
        required_scopes=list(item.required_scopes),
        granted_scopes=list(item.granted_scopes),
        warnings=list(item.warnings),
        checked_at=item.checked_at,
        live_read_enabled=item.live_read_enabled,
        live_write_enabled=item.live_write_enabled,
        message=item.message,
    )


def _build_gmail_status_response(item: Any) -> GmailConnectorStatusResponse:
    return GmailConnectorStatusResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        integration_key=item.integration_key,
        oauth_status=item.oauth_status,
        connection_id=item.connection_id,
        account_id=item.account_id,
        provider_enabled=item.provider_enabled,
        provider_configured=item.provider_configured,
        token_present=item.token_present,
        required_scopes=list(item.required_scopes),
        granted_scopes=list(item.granted_scopes),
        warnings=list(item.warnings),
        checked_at=item.checked_at,
        live_read_enabled=item.live_read_enabled,
        live_write_enabled=item.live_write_enabled,
        connected=item.connected,
    )


def _build_gmail_probe_response(item: Any) -> GmailConnectorProbeResponse:
    return GmailConnectorProbeResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        integration_key=item.integration_key,
        oauth_status=item.oauth_status,
        probe_status=item.probe_status,
        connection_id=item.connection_id,
        account_id=item.account_id,
        provider_reachable=item.provider_reachable,
        provider_enabled=item.provider_enabled,
        provider_configured=item.provider_configured,
        token_present=item.token_present,
        required_scopes=list(item.required_scopes),
        granted_scopes=list(item.granted_scopes),
        warnings=list(item.warnings),
        checked_at=item.checked_at,
        live_read_enabled=item.live_read_enabled,
        live_write_enabled=item.live_write_enabled,
        message=item.message,
    )


def _build_smtp_mail_status_response(item: Any) -> SmtpMailStatusResponse:
    return SmtpMailStatusResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        provider_key=item.provider_key,
        account_id=item.account_id,
        account_identifier=item.account_identifier,
        configured=item.configured,
        has_secret=item.has_secret,
        warnings=list(item.warnings),
        checked_at=item.checked_at,
        live_login_enabled=item.live_login_enabled,
        live_send_enabled=item.live_send_enabled,
        probe_status=item.probe_status,
        message=item.message,
    )


def _build_action_draft_response(item: Any) -> ActionDraftResponse:
    return ActionDraftResponse(
        id=item.id,
        account_id=item.account_id,
        integration_key=item.integration_key,
        project_id=item.project_id,
        project_name=item.project_name,
        action_type=item.action_type,
        action_title=item.action_title,
        action_summary=item.action_summary,
        risk_level=item.risk_level,
        status=item.status,
        requires_approval=item.requires_approval,
        preview_payload=dict(item.preview_payload),
        approval_notes=item.approval_notes,
        rejection_reason=item.rejection_reason,
        audit_log=list(item.audit_log),
        created_by=item.created_by,
        approved_at=item.approved_at,
        rejected_at=item.rejected_at,
        executed_at=item.executed_at,
        required_capabilities=list(item.required_capabilities),
        account_summary=ActionDraftAccountSummaryResponse(
            id=item.account_summary.id,
            label=item.account_summary.label,
            provider_key=item.account_summary.provider_key,
            account_identifier=item.account_summary.account_identifier,
            status=item.account_summary.status,
            risk_level=item.account_summary.risk_level,
            masked_secret_state=item.account_summary.masked_secret_state,
            allowed_capabilities=list(item.account_summary.allowed_capabilities),
        ),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _build_local_runtime_probe_response(item: Any) -> LocalRuntimeProbeResponse:
    return LocalRuntimeProbeResponse(
        key=item.key,
        label=item.label,
        status=item.status,
        ready=item.ready,
        probe_kind=item.probe_kind,
        reason=item.reason,
        checked_at=item.checked_at,
        details=item.details,
        suggestions=list(item.suggestions),
    )
