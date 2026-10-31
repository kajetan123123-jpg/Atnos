from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from atnos.services.google_oauth_client_secret_importer import (
    diagnose_google_oauth_client_secret_file,
    extract_google_oauth_client,
    looks_like_google_oauth_placeholder,
    persist_oauth_runtime_env,
)

router = APIRouter(tags=["oauth"])


def _load_oauth_service():
    from atnos.services.oauth_service import OAuthService

    return OAuthService


class OAuthBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuthConnectorResponse(OAuthBaseModel):
    connector_key: str
    display_name: str
    status: str
    scopes: list[str]
    supports_pkce: bool
    enabled: bool
    configured: bool
    setup_warnings: list[str]
    connection_id: int | None
    connected_at: datetime | None
    disconnected_at: datetime | None
    last_error: str | None


class OAuthConnectionResponse(OAuthBaseModel):
    id: int
    connector_key: str
    display_name: str
    status: str
    scopes: list[str]
    account_id: int | None
    connected_at: datetime | None
    disconnected_at: datetime | None
    last_error: str | None
    metadata: dict[str, object]
    audit_log: list[dict[str, object]]
    created_at: datetime
    updated_at: datetime


class OAuthStartResponse(OAuthBaseModel):
    authorization_url: str
    connector_key: str
    status: str
    scopes: list[str]
    connection_id: int
    content_account_id: str | None = None


class OAuthStartRequest(OAuthBaseModel):
    """Opcjonalny, jawny cel ponownego połączenia OAuth."""

    content_account_id: str | None = Field(default=None, min_length=1, max_length=255)


class OAuthCallbackResponse(OAuthBaseModel):
    connector_key: str
    status: str
    account_id: int | None
    connection: OAuthConnectionResponse


class YouTubeOAuthRuntimeConfigRequest(OAuthBaseModel):
    client_id: str = Field(min_length=12, max_length=255)
    client_secret: SecretStr | None = None
    redirect_base_url: str = Field(default="http://127.0.0.1:8000", max_length=255)
    enable_oauth: bool = True


class YouTubeOAuthRuntimeConfigResponse(OAuthBaseModel):
    connector: OAuthConnectorResponse
    env_path: str
    client_secret_ref: str | None
    secret_stored: bool


class YouTubeOAuthClientSecretImportRequest(OAuthBaseModel):
    client_secret_path: str | None = Field(default=None, max_length=1000)
    redirect_base_url: str = Field(default="http://127.0.0.1:8000", max_length=255)
    enable_oauth: bool = True


class YouTubeOAuthClientSecretJsonImportRequest(OAuthBaseModel):
    client_secret_json: dict[str, Any]
    save_path: str | None = Field(default=None, max_length=1000)
    redirect_base_url: str = Field(default="http://127.0.0.1:8000", max_length=255)
    enable_oauth: bool = True


class YouTubeOAuthClientSecretImportResponse(OAuthBaseModel):
    connector: OAuthConnectorResponse
    env_path: str
    client_secret_ref: str
    client_id: str
    client_secret_path: str
    redirect_uri: str
    authorization_start_endpoint: str
    warnings: list[str]


class YouTubeOAuthClientSecretStatusResponse(OAuthBaseModel):
    path: str
    exists: bool
    valid_json: bool
    client_type: str | None
    has_client_id: bool
    has_client_secret: bool
    redirect_uri: str
    redirect_uri_registered: bool | None
    likely_placeholder: bool
    import_ready: bool
    warnings: list[str]
    errors: list[str]
    import_endpoint: str
    authorization_start_endpoint: str
    connect_endpoint: str
    google_cloud_credentials_url: str
    youtube_data_api_url: str
    youtube_analytics_api_url: str
    required_scopes: list[str]
    next_steps: list[str]


class YouTubeOAuthConnectRequest(OAuthBaseModel):
    client_secret_path: str | None = Field(default=None, max_length=1000)
    redirect_base_url: str = Field(default="http://127.0.0.1:8000", max_length=255)
    enable_oauth: bool = True


class YouTubeOAuthConnectResponse(OAuthBaseModel):
    status: str
    imported: bool
    oauth_configured: bool
    authorization_url: str | None
    authorization_start_endpoint: str
    scopes: list[str]
    connector: OAuthConnectorResponse | None
    connection_id: int | None
    client_secret_status: YouTubeOAuthClientSecretStatusResponse


class YouTubeOAuthReadinessResponse(OAuthBaseModel):
    ready: bool
    configured: bool
    connected: bool
    token_ready: bool
    private_upload_ready: bool
    captions_api_ready: bool
    channel_management_ready: bool
    client_secret_ready: bool
    client_secret_status: YouTubeOAuthClientSecretStatusResponse
    connector_status: dict[str, object]
    token_store: dict[str, object]
    upload_contract: dict[str, object]
    required_scopes: list[str]
    private_upload_scopes: list[str]
    optional_scopes: list[str]
    granted_scopes: list[str]
    missing_scopes: list[str]
    optional_missing_scopes: list[str]
    reconnect_recommended: bool
    reconnect_reason: str | None
    reconnect_endpoint: str
    warnings: list[str]
    next_steps: list[str]


class YouTubeOAuthSetupGuideResponse(OAuthBaseModel):
    status: str
    ready: bool
    can_start_oauth: bool
    can_collect_public_trends: bool
    missing: list[str]
    endpoints: dict[str, str]
    google_cloud: dict[str, str]
    client_secret: dict[str, object]
    youtube_data_api_key: dict[str, object]
    oauth: dict[str, object]
    ordered_steps: list[str]


@router.get("/oauth/connectors", response_model=list[OAuthConnectorResponse])
def list_oauth_connectors(request: Request) -> list[OAuthConnectorResponse]:
    OAuthService = _load_oauth_service()
    with request.app.state.database.session_scope() as session:
        items = OAuthService(session, request.app.state.settings).list_connectors()
    return [_build_connector_response(item) for item in items]


@router.get("/oauth/connections", response_model=list[OAuthConnectionResponse])
def list_oauth_connections(request: Request) -> list[OAuthConnectionResponse]:
    OAuthService = _load_oauth_service()
    with request.app.state.database.session_scope() as session:
        items = OAuthService(session, request.app.state.settings).list_connections()
    return [_build_connection_response(item) for item in items]


@router.post("/oauth/{connector_key}/start", response_model=OAuthStartResponse)
def start_oauth_connection(
    connector_key: str,
    request: Request,
    payload: OAuthStartRequest | None = None,
) -> OAuthStartResponse:
    OAuthService = _load_oauth_service()
    try:
        with request.app.state.database.session_scope() as session:
            target_content_account_id = payload.content_account_id if payload is not None else None
            if target_content_account_id:
                if connector_key != "youtube-studio":
                    raise ValueError("Selected content channel is not available for YouTube OAuth reconnect.")
                from atnos.intelligence.storage import ensure_youtube_content_account

                ensure_youtube_content_account(
                    request.app.state.settings,
                    account_id=target_content_account_id,
                )
            result = OAuthService(session, request.app.state.settings).start_connection(
                connector_key,
                content_account_id=target_content_account_id,
            )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "Unknown connector" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return _build_start_response(result)


@router.get("/oauth/{connector_key}/callback", response_model=OAuthCallbackResponse)
def oauth_callback(
    connector_key: str,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> OAuthCallbackResponse:
    OAuthService = _load_oauth_service()
    try:
        with request.app.state.database.session_scope() as session:
            result = OAuthService(session, request.app.state.settings).complete_callback(
                connector_key,
                code=code,
                state=state,
                error=error,
            )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "Unknown connector" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return _build_callback_response(result)


@router.post("/oauth/{connection_id}/disconnect", response_model=OAuthConnectionResponse)
def disconnect_oauth_connection(connection_id: int, request: Request) -> OAuthConnectionResponse:
    OAuthService = _load_oauth_service()
    with request.app.state.database.session_scope() as session:
        item = OAuthService(session, request.app.state.settings).disconnect(connection_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"OAuth connection {connection_id} was not found.")
    return _build_connection_response(item)


@router.post("/oauth/youtube-studio/runtime-config", response_model=YouTubeOAuthRuntimeConfigResponse)
def configure_youtube_oauth_runtime(
    payload: YouTubeOAuthRuntimeConfigRequest,
    request: Request,
) -> YouTubeOAuthRuntimeConfigResponse:
    from atnos.services.runtime_settings_service import RuntimeSettingsService
    from atnos.services.secret_vault_service import SecretVaultService, SecretVaultUnavailableError

    settings = request.app.state.settings
    client_id = payload.client_id.strip()
    redirect_base_url = payload.redirect_base_url.strip().rstrip("/") or "http://127.0.0.1:8000"
    existing_secret_ref = settings.oauth_google_client_secret_ref
    secret_stored = False
    raw_client_secret = payload.client_secret.get_secret_value().strip() if payload.client_secret is not None else None

    if raw_client_secret is not None and looks_like_google_oauth_placeholder(
        {
            "client_id": client_id,
            "client_secret": raw_client_secret,
        }
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Google OAuth client_id/client_secret do not look like a real Google Cloud OAuth client. "
                "Do not enter a Google/YouTube password. Download OAuth Client ID credentials from Google Cloud."
            ),
        )

    if raw_client_secret is not None:
        try:
            _backend, existing_secret_ref = SecretVaultService(settings).rotate_secret(
                secret_value=raw_client_secret,
                existing_secret_ref=existing_secret_ref,
            )
            secret_stored = True
        except (SecretVaultUnavailableError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not existing_secret_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth client secret is required the first time you configure YouTube OAuth.",
        )

    settings.oauth_enabled = payload.enable_oauth
    settings.oauth_redirect_base_url = redirect_base_url
    settings.oauth_google_client_id = client_id
    settings.oauth_google_client_secret_ref = existing_secret_ref

    with request.app.state.database.session_scope() as session:
        RuntimeSettingsService(session, settings).update_owner_controls(oauth_enabled=payload.enable_oauth)

    env_path = _persist_oauth_runtime_env(
        settings.runtime_env_file,
        {
            "OAUTH_ENABLED": "true" if payload.enable_oauth else "false",
            "OAUTH_REDIRECT_BASE_URL": redirect_base_url,
            "OAUTH_GOOGLE_CLIENT_ID": client_id,
            "OAUTH_GOOGLE_CLIENT_SECRET_REF": existing_secret_ref,
        },
    )

    OAuthService = _load_oauth_service()
    with request.app.state.database.session_scope() as session:
        connector = OAuthService(session, settings).list_connectors()
    youtube_connector = next(item for item in connector if item.connector_key == "youtube-studio")
    return YouTubeOAuthRuntimeConfigResponse(
        connector=_build_connector_response(youtube_connector),
        env_path=str(env_path),
        client_secret_ref=existing_secret_ref,
        secret_stored=secret_stored,
    )


@router.post("/oauth/youtube-studio/import-client-secret", response_model=YouTubeOAuthClientSecretImportResponse)
def import_youtube_oauth_client_secret(
    payload: YouTubeOAuthClientSecretImportRequest,
    request: Request,
) -> YouTubeOAuthClientSecretImportResponse:
    """Import Google OAuth client_secret.json into Atnos without exposing the secret in .env."""
    from atnos.services.google_oauth_client_secret_importer import GoogleOAuthClientSecretImporter
    from atnos.services.secret_vault_service import SecretVaultUnavailableError

    settings = request.app.state.settings
    path = Path(payload.client_secret_path).expanduser().resolve() if payload.client_secret_path else settings.youtube_client_secret_file
    try:
        with request.app.state.database.session_scope() as session:
            result = GoogleOAuthClientSecretImporter(session, settings).import_file(
                client_secret_path=path,
                redirect_base_url=payload.redirect_base_url,
                enable_oauth=payload.enable_oauth,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google OAuth client secret file is not valid JSON.") from exc
    except (SecretVaultUnavailableError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result.connector is None or result.client_id is None or result.client_secret_ref is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google OAuth client secret import did not configure YouTube connector.")
    return YouTubeOAuthClientSecretImportResponse(
        connector=_build_connector_response(result.connector),
        env_path=str(result.env_path),
        client_secret_ref=result.client_secret_ref,
        client_id=result.client_id,
        client_secret_path=str(result.client_secret_path),
        redirect_uri=result.redirect_uri,
        authorization_start_endpoint="POST /oauth/youtube-studio/start",
        warnings=list(result.warnings),
    )


@router.post("/oauth/youtube-studio/import-client-secret-json", response_model=YouTubeOAuthClientSecretImportResponse)
def import_youtube_oauth_client_secret_json(
    payload: YouTubeOAuthClientSecretJsonImportRequest,
    request: Request,
) -> YouTubeOAuthClientSecretImportResponse:
    """Import a pasted/downloaded Google OAuth client_secret JSON without requiring manual file placement."""
    from atnos.services.google_oauth_client_secret_importer import GoogleOAuthClientSecretImporter
    from atnos.services.secret_vault_service import SecretVaultUnavailableError

    settings = request.app.state.settings
    try:
        client = extract_google_oauth_client(payload.client_secret_json)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if looks_like_google_oauth_placeholder(client):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth client_secret.json looks like documentation/sample data. Download a real OAuth client JSON from Google Cloud Console.",
        )

    path = Path(payload.save_path).expanduser().resolve() if payload.save_path else settings.youtube_client_secret_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload.client_secret_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        with request.app.state.database.session_scope() as session:
            result = GoogleOAuthClientSecretImporter(session, settings).import_file(
                client_secret_path=path,
                redirect_base_url=payload.redirect_base_url,
                enable_oauth=payload.enable_oauth,
            )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, SecretVaultUnavailableError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result.connector is None or result.client_id is None or result.client_secret_ref is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google OAuth client secret JSON import did not configure YouTube connector.")
    return YouTubeOAuthClientSecretImportResponse(
        connector=_build_connector_response(result.connector),
        env_path=str(result.env_path),
        client_secret_ref=result.client_secret_ref,
        client_id=result.client_id,
        client_secret_path=str(result.client_secret_path),
        redirect_uri=result.redirect_uri,
        authorization_start_endpoint="POST /oauth/youtube-studio/start",
        warnings=list(result.warnings),
    )


@router.get("/oauth/youtube-studio/client-secret-status", response_model=YouTubeOAuthClientSecretStatusResponse)
def get_youtube_oauth_client_secret_status(
    request: Request,
    client_secret_path: str | None = Query(default=None, max_length=1000),
    redirect_base_url: str | None = Query(default=None, max_length=255),
) -> YouTubeOAuthClientSecretStatusResponse:
    """Return safe YouTube OAuth client secret diagnostics without exposing secret values."""
    settings = request.app.state.settings
    path = Path(client_secret_path).expanduser().resolve() if client_secret_path else settings.youtube_client_secret_file
    base_url = (redirect_base_url or settings.oauth_redirect_base_url or "http://127.0.0.1:8000").strip().rstrip("/")
    return _build_youtube_oauth_client_secret_status_response(settings, path, base_url)


@router.get("/oauth/youtube-studio/status", response_model=YouTubeOAuthReadinessResponse)
def get_youtube_oauth_readiness_status(
    request: Request,
    client_secret_path: str | None = Query(default=None, max_length=1000),
    redirect_base_url: str | None = Query(default=None, max_length=255),
) -> YouTubeOAuthReadinessResponse:
    """Return safe end-to-end YouTube OAuth readiness: client JSON, provider config, token and scopes."""
    from atnos.services.oauth_provider_registry import OAuthProviderRegistry
    from atnos.services.secret_vault_service import AES_256_GCM_STORAGE_BACKEND, SecretVaultService
    from atnos.services.youtube_connector_service import YouTubeConnectorService

    settings = request.app.state.settings
    path = Path(client_secret_path).expanduser().resolve() if client_secret_path else settings.youtube_client_secret_file
    base_url = (redirect_base_url or settings.oauth_redirect_base_url or "http://127.0.0.1:8000").strip().rstrip("/")
    client_secret_status = _build_youtube_oauth_client_secret_status_response(settings, path, base_url)
    required_scopes = list(OAuthProviderRegistry(settings).get_connector("youtube-studio").scopes)
    with request.app.state.database.session_scope() as session:
        connector_status = YouTubeConnectorService(session, settings).get_status()
    token_store_status = SecretVaultService(settings).get_storage_status()
    granted_scopes = list(connector_status.granted_scopes)
    private_upload_scopes = _private_upload_oauth_scopes(required_scopes)
    optional_scopes = _optional_oauth_scopes(required_scopes)
    if connector_status.connected:
        missing_scopes = _missing_oauth_scopes(required_scopes, granted_scopes)
        optional_missing_scopes = _missing_optional_oauth_scopes(required_scopes, granted_scopes)
    else:
        missing_scopes = sorted(private_upload_scopes)
        optional_missing_scopes = sorted(optional_scopes)
    token_ready = bool(connector_status.ready and not missing_scopes)
    captions_api_ready = bool(token_ready and not optional_missing_scopes)
    channel_management_ready = captions_api_ready
    reconnect_warning = next(
        (
            warning
            for warning in connector_status.warnings
            if any(
                marker in warning.casefold()
                for marker in ("requires reconnect", "wymaga ponownego połączenia", "połącz konto ponownie")
            )
        ),
        None,
    )
    reconnect_for_expired_token = bool(connector_status.configured and not connector_status.connected and reconnect_warning)
    reconnect_for_optional_scopes = bool(connector_status.connected and token_ready and optional_missing_scopes)
    reconnect_recommended = bool(reconnect_for_expired_token or reconnect_for_optional_scopes)
    reconnect_reason = (
        reconnect_warning
        if reconnect_for_expired_token
        else (
            "YouTube OAuth is ready for private upload, but optional youtube.force-ssl is missing for captions API and fuller channel operations."
            if reconnect_for_optional_scopes
            else None
        )
    )
    provider_config_ready = bool(
        settings.oauth_google_client_id
        and settings.oauth_google_client_secret_ref
        and client_secret_status.exists
        and client_secret_status.import_ready
    )
    client_secret_ready = bool(client_secret_status.import_ready or provider_config_ready)
    next_steps = _build_youtube_oauth_readiness_next_steps(
        client_secret_status=client_secret_status,
        configured=connector_status.configured,
        connected=connector_status.connected,
        token_ready=token_ready,
        missing_scopes=missing_scopes,
        optional_missing_scopes=optional_missing_scopes,
        reconnect_required=reconnect_for_expired_token,
    )
    return YouTubeOAuthReadinessResponse(
        ready=bool(client_secret_ready and token_ready),
        configured=bool(connector_status.configured),
        connected=bool(connector_status.connected),
        token_ready=token_ready,
        private_upload_ready=bool(client_secret_ready and token_ready),
        captions_api_ready=captions_api_ready,
        channel_management_ready=channel_management_ready,
        client_secret_ready=client_secret_ready,
        client_secret_status=client_secret_status,
        connector_status=connector_status.to_dict(),
        token_store={
            "service": "atnos.services.oauth_token_store.OAuthTokenStore",
            "vault_service": "atnos.services.secret_vault_service.SecretVaultService",
            "storage_backend": token_store_status.storage_backend,
            "required_backend": AES_256_GCM_STORAGE_BACKEND,
            "encrypted": bool(token_store_status.is_secret_storage_enabled),
            "summary": token_store_status.summary,
            "warnings": list(token_store_status.warnings),
        },
        upload_contract={
            "service": "atnos.services.youtube_connector_service.YouTubeConnectorService.upload_short",
            "youtube_api": "YouTube Data API v3 videos.insert",
            "privacy_default": settings.youtube_upload_privacy,
            "resumable": True,
            "chunk_size_bytes": YouTubeConnectorService.VIDEO_UPLOAD_CHUNK_SIZE_BYTES,
            "max_attempts": 3,
            "thumbnail_supported": True,
            "captions_supported": True,
            "captions_require_optional_scope": "https://www.googleapis.com/auth/youtube.force-ssl",
        },
        required_scopes=required_scopes,
        private_upload_scopes=private_upload_scopes,
        optional_scopes=optional_scopes,
        granted_scopes=granted_scopes,
        missing_scopes=missing_scopes,
        optional_missing_scopes=optional_missing_scopes,
        reconnect_recommended=reconnect_recommended,
        reconnect_reason=reconnect_reason,
        reconnect_endpoint="POST /oauth/youtube-studio/connect",
        warnings=list(connector_status.warnings),
        next_steps=next_steps,
    )


@router.get("/oauth/youtube-studio/setup-guide", response_model=YouTubeOAuthSetupGuideResponse)
def get_youtube_oauth_setup_guide(
    request: Request,
    client_secret_path: str | None = Query(default=None, max_length=1000),
    redirect_base_url: str | None = Query(default=None, max_length=255),
) -> YouTubeOAuthSetupGuideResponse:
    """Return a single safe setup contract for YouTube trends, OAuth, private upload and analytics."""
    from atnos.services.oauth_provider_registry import OAuthProviderRegistry
    from atnos.services.youtube_connector_service import YouTubeConnectorService

    settings = request.app.state.settings
    path = Path(client_secret_path).expanduser().resolve() if client_secret_path else settings.youtube_client_secret_file
    base_url = (redirect_base_url or settings.oauth_redirect_base_url or "http://127.0.0.1:8000").strip().rstrip("/")
    client_secret_status = _build_youtube_oauth_client_secret_status_response(settings, path, base_url)
    with request.app.state.database.session_scope() as session:
        oauth_status = YouTubeConnectorService(session, settings).get_status()
    required_scopes = list(OAuthProviderRegistry(settings).get_connector("youtube-studio").scopes)
    configured = bool(
        settings.oauth_enabled
        and settings.oauth_google_client_id
        and settings.oauth_google_client_secret_ref
        and client_secret_status.import_ready
    )
    can_start_oauth = bool(configured or client_secret_status.import_ready)
    api_key_configured = bool(settings.youtube_data_api_key)
    missing = []
    if not api_key_configured:
        missing.append("youtube_data_api_key_for_public_trends")
    if not client_secret_status.import_ready and not configured:
        missing.append("google_oauth_client_secret_json")
    if not oauth_status.ready:
        missing.append("youtube_oauth_token")
    ordered_steps = _build_youtube_setup_ordered_steps(
        api_key_configured=api_key_configured,
        client_secret_status=client_secret_status,
        oauth_configured=configured,
        oauth_ready=bool(oauth_status.ready),
    )
    ready = bool(api_key_configured and oauth_status.ready)
    return YouTubeOAuthSetupGuideResponse(
        status="ready" if ready else "needs_setup",
        ready=ready,
        can_start_oauth=can_start_oauth,
        can_collect_public_trends=api_key_configured or bool(oauth_status.ready),
        missing=missing,
        endpoints={
            "configure_youtube_data_api_key": "POST /api/intelligence/youtube-data-api-key",
            "configure_oauth_client_id_secret": "POST /oauth/youtube-studio/runtime-config",
            "client_secret_status": "GET /oauth/youtube-studio/client-secret-status",
            "import_client_secret_file": "POST /oauth/youtube-studio/import-client-secret",
            "import_client_secret_json": "POST /oauth/youtube-studio/import-client-secret-json",
            "connect": "POST /oauth/youtube-studio/connect",
            "start_oauth": "POST /oauth/youtube-studio/start",
            "oauth_status": "GET /oauth/youtube-studio/status",
            "run_intelligence": "POST /api/intelligence/run",
            "production_readiness": "GET /api/production/readiness",
        },
        google_cloud={
            "credentials_url": "https://console.cloud.google.com/apis/credentials",
            "youtube_data_api_url": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
            "youtube_analytics_api_url": "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
            "required_oauth_client_type": "Desktop app albo Web app z redirect URI Atnosa",
            "redirect_uri": client_secret_status.redirect_uri,
        },
        client_secret={
            "path": client_secret_status.path,
            "exists": client_secret_status.exists,
            "import_ready": client_secret_status.import_ready,
            "likely_placeholder": client_secret_status.likely_placeholder,
            "warnings": client_secret_status.warnings,
            "errors": client_secret_status.errors,
        },
        youtube_data_api_key={
            "configured": api_key_configured,
            "purpose": "Publiczne trendy YouTube / yt_signals bez dostepu do prywatnego kanalu.",
            "required_for": ["youtube_trends_collector", "topic_scoring_real_signals"],
            "not_enough_for": ["private_upload", "own_channel_analytics"],
        },
        oauth={
            "configured": configured,
            "connected": bool(oauth_status.connected),
            "ready": bool(oauth_status.ready),
            "required_for": ["private_upload", "own_channel_analytics", "analytics_followups_24_48_72h"],
            "accepted_credentials": [
                "Google Cloud OAuth 2.0 Client ID + client_secret",
                "Google Cloud downloaded client_secret.json",
                "Google OAuth consent token generated after opening authorization_url",
            ],
            "not_accepted_credentials": [
                "Google/YouTube login password",
                "email account password",
                "documentation sample client_secret.json",
            ],
            "required_scopes": required_scopes,
            "warnings": list(oauth_status.warnings),
        },
        ordered_steps=ordered_steps,
    )


@router.post("/oauth/youtube-studio/connect", response_model=YouTubeOAuthConnectResponse)
def connect_youtube_oauth(
    payload: YouTubeOAuthConnectRequest,
    request: Request,
) -> YouTubeOAuthConnectResponse:
    """Import client_secret.json when possible, then start YouTube OAuth consent."""
    from atnos.services.google_oauth_client_secret_importer import GoogleOAuthClientSecretImporter
    from atnos.services.secret_vault_service import SecretVaultUnavailableError

    settings = request.app.state.settings
    path = Path(payload.client_secret_path).expanduser().resolve() if payload.client_secret_path else settings.youtube_client_secret_file
    redirect_base_url = payload.redirect_base_url.strip().rstrip("/") or "http://127.0.0.1:8000"
    client_secret_status = _build_youtube_oauth_client_secret_status_response(settings, path, redirect_base_url)
    imported = False
    connector_response: OAuthConnectorResponse | None = None

    if client_secret_status.import_ready:
        try:
            with request.app.state.database.session_scope() as session:
                import_result = GoogleOAuthClientSecretImporter(session, settings).import_file(
                    client_secret_path=path,
                    redirect_base_url=redirect_base_url,
                    enable_oauth=payload.enable_oauth,
                )
            imported = True
            connector_response = _build_connector_response(import_result.connector) if import_result.connector is not None else None
        except (FileNotFoundError, ValueError, json.JSONDecodeError, SecretVaultUnavailableError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        return YouTubeOAuthConnectResponse(
            status="needs_client_secret",
            imported=False,
            oauth_configured=False,
            authorization_url=None,
            authorization_start_endpoint="POST /oauth/youtube-studio/start",
            scopes=[],
            connector=connector_response,
            connection_id=None,
            client_secret_status=client_secret_status,
        )

    oauth_configured = bool(settings.oauth_enabled and settings.oauth_google_client_id and settings.oauth_google_client_secret_ref)
    if not oauth_configured:
        return YouTubeOAuthConnectResponse(
            status="needs_client_secret",
            imported=False,
            oauth_configured=False,
            authorization_url=None,
            authorization_start_endpoint="POST /oauth/youtube-studio/start",
            scopes=[],
            connector=connector_response,
            connection_id=None,
            client_secret_status=client_secret_status,
        )

    OAuthService = _load_oauth_service()
    try:
        with request.app.state.database.session_scope() as session:
            start_result = OAuthService(session, settings).start_connection("youtube-studio")
            if connector_response is None:
                connector = next(
                    item for item in OAuthService(session, settings).list_connectors() if item.connector_key == "youtube-studio"
                )
                connector_response = _build_connector_response(connector)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return YouTubeOAuthConnectResponse(
        status="authorization_started",
        imported=imported,
        oauth_configured=True,
        authorization_url=start_result.authorization_url,
        authorization_start_endpoint="POST /oauth/youtube-studio/start",
        scopes=list(start_result.scopes),
        connector=connector_response,
        connection_id=start_result.connection_id,
        client_secret_status=client_secret_status,
    )


def _build_connector_response(item) -> OAuthConnectorResponse:
    return OAuthConnectorResponse(
        connector_key=item.connector_key,
        display_name=item.display_name,
        status=item.status,
        scopes=list(item.scopes),
        supports_pkce=item.supports_pkce,
        enabled=item.enabled,
        configured=item.configured,
        setup_warnings=list(item.setup_warnings),
        connection_id=item.connection_id,
        connected_at=item.connected_at,
        disconnected_at=item.disconnected_at,
        last_error=item.last_error,
    )


def _build_connection_response(item) -> OAuthConnectionResponse:
    return OAuthConnectionResponse(
        id=item.id,
        connector_key=item.connector_key,
        display_name=item.display_name,
        status=item.status,
        scopes=list(item.scopes),
        account_id=item.account_id,
        connected_at=item.connected_at,
        disconnected_at=item.disconnected_at,
        last_error=item.last_error,
        metadata=dict(item.metadata),
        audit_log=[dict(entry) for entry in item.audit_log],
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _build_start_response(item) -> OAuthStartResponse:
    return OAuthStartResponse(
        authorization_url=item.authorization_url,
        connector_key=item.connector_key,
        status=item.status,
        scopes=list(item.scopes),
        connection_id=item.connection_id,
        content_account_id=item.content_account_id,
    )


def _build_callback_response(item) -> OAuthCallbackResponse:
    return OAuthCallbackResponse(
        connector_key=item.connector_key,
        status=item.status,
        account_id=item.account_id,
        connection=_build_connection_response(item.connection),
    )


def _build_youtube_oauth_next_steps(import_ready: bool, diagnostics) -> list[str]:
    steps: list[str] = []
    if not diagnostics.exists:
        steps.append(
            "Otworz Google Cloud Credentials, utworz OAuth Client ID i pobierz realny client_secret.json."
        )
        steps.append(f"Zapisz pobrany plik dokladnie tutaj: {diagnostics.path}.")
    elif not diagnostics.valid_json:
        steps.append("Popraw client_secret.json: plik musi byc poprawnym JSON-em z blokiem installed albo web.")
    elif diagnostics.likely_placeholder:
        steps.append("Wklejony plik wyglada jak przyklad z dokumentacji. Pobierz realny plik z Google Cloud Console.")
    elif not diagnostics.has_client_id or not diagnostics.has_client_secret:
        steps.append("Pobierz pelny client_secret.json z polami client_id i client_secret.")
    if diagnostics.redirect_uri_registered is False:
        steps.append(f"Dodaj redirect URI w Google Cloud: {diagnostics.redirect_uri}.")
    if import_ready:
        steps.append("Wywolaj POST /oauth/youtube-studio/connect, zeby zaimportowac plik i dostac authorization_url.")
        steps.append("Otworz authorization_url w przegladarce i zaakceptuj zgody YouTube.")
    return steps


def _build_youtube_oauth_client_secret_status_response(
    settings,
    path: Path,
    redirect_base_url: str,
) -> YouTubeOAuthClientSecretStatusResponse:
    diagnostics = diagnose_google_oauth_client_secret_file(path, redirect_base_url=redirect_base_url)
    from atnos.services.oauth_provider_registry import OAuthProviderRegistry

    required_scopes = [
        scope
        for scope in OAuthProviderRegistry(settings).get_connector("youtube-studio").scopes
        if scope.startswith("https://www.googleapis.com/auth/")
    ]
    return YouTubeOAuthClientSecretStatusResponse(
        path=str(diagnostics.path),
        exists=diagnostics.exists,
        valid_json=diagnostics.valid_json,
        client_type=diagnostics.client_type,
        has_client_id=diagnostics.has_client_id,
        has_client_secret=diagnostics.has_client_secret,
        redirect_uri=diagnostics.redirect_uri,
        redirect_uri_registered=diagnostics.redirect_uri_registered,
        likely_placeholder=diagnostics.likely_placeholder,
        import_ready=diagnostics.import_ready,
        warnings=list(diagnostics.warnings),
        errors=list(diagnostics.errors),
        import_endpoint="POST /oauth/youtube-studio/import-client-secret",
        authorization_start_endpoint="POST /oauth/youtube-studio/start",
        connect_endpoint="POST /oauth/youtube-studio/connect",
        google_cloud_credentials_url="https://console.cloud.google.com/apis/credentials",
        youtube_data_api_url="https://console.cloud.google.com/apis/library/youtube.googleapis.com",
        youtube_analytics_api_url="https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
        required_scopes=required_scopes,
        next_steps=_build_youtube_oauth_next_steps(diagnostics.import_ready, diagnostics),
    )


def _build_youtube_oauth_readiness_next_steps(
    *,
    client_secret_status: YouTubeOAuthClientSecretStatusResponse,
    configured: bool,
    connected: bool,
    token_ready: bool,
    missing_scopes: list[str],
    optional_missing_scopes: list[str] | None = None,
    reconnect_required: bool = False,
) -> list[str]:
    steps: list[str] = []
    steps.extend(client_secret_status.next_steps)
    if client_secret_status.import_ready and not configured:
        steps.append("Wywolaj POST /oauth/youtube-studio/import-client-secret, zeby zapisac client_id i secret ref.")
    if reconnect_required:
        steps.append("Ponownie polacz konto YouTube w aplikacji Atnos. Poprzedni token wygasl albo zostal uniewazniony.")
    elif configured and not connected:
        steps.append("Polacz konto YouTube w aplikacji Atnos i zaakceptuj zgody Google.")
    if connected and not token_ready:
        steps.append("Powtorz OAuth consent dla YouTube Studio, bo token nie ma wymaganych scope'ow.")
    if missing_scopes and not reconnect_required:
        steps.append(f"Brakujace scope'y: {', '.join(missing_scopes)}.")
    if connected and token_ready and optional_missing_scopes:
        steps.append(
            "Opcjonalnie powtorz OAuth przez POST /oauth/youtube-studio/connect, "
            f"zeby dodac scope'y do napisow i pelniejszego zarzadzania kanalem: {', '.join(optional_missing_scopes)}."
        )
    return list(dict.fromkeys(step for step in steps if step))


def _missing_oauth_scopes(required_scopes: list[str], granted_scopes: list[str]) -> list[str]:
    """Compare requested OAuth scopes while accepting Google's canonical OIDC aliases."""
    from atnos.services.oauth_provider_registry import YOUTUBE_OPTIONAL_CAPTION_SCOPE

    granted = {_normalize_oauth_scope(scope) for scope in granted_scopes}
    optional = {_normalize_oauth_scope(YOUTUBE_OPTIONAL_CAPTION_SCOPE)}
    return sorted(scope for scope in required_scopes if _normalize_oauth_scope(scope) not in granted and _normalize_oauth_scope(scope) not in optional)


def _missing_optional_oauth_scopes(required_scopes: list[str], granted_scopes: list[str]) -> list[str]:
    from atnos.services.oauth_provider_registry import YOUTUBE_OPTIONAL_CAPTION_SCOPE

    granted = {_normalize_oauth_scope(scope) for scope in granted_scopes}
    optional = {_normalize_oauth_scope(scope) for scope in required_scopes if _normalize_oauth_scope(scope) == _normalize_oauth_scope(YOUTUBE_OPTIONAL_CAPTION_SCOPE)}
    return sorted(scope for scope in optional if _normalize_oauth_scope(scope) not in granted)


def _optional_oauth_scopes(required_scopes: list[str]) -> list[str]:
    from atnos.services.oauth_provider_registry import YOUTUBE_OPTIONAL_CAPTION_SCOPE

    optional = _normalize_oauth_scope(YOUTUBE_OPTIONAL_CAPTION_SCOPE)
    return sorted(scope for scope in required_scopes if _normalize_oauth_scope(scope) == optional)


def _private_upload_oauth_scopes(required_scopes: list[str]) -> list[str]:
    optional = {_normalize_oauth_scope(scope) for scope in _optional_oauth_scopes(required_scopes)}
    return sorted(scope for scope in required_scopes if _normalize_oauth_scope(scope) not in optional)


def _normalize_oauth_scope(scope: str) -> str:
    normalized = scope.strip()
    aliases = {
        "https://www.googleapis.com/auth/userinfo.email": "email",
        "https://www.googleapis.com/auth/userinfo.profile": "profile",
    }
    return aliases.get(normalized, normalized)


def _build_youtube_setup_ordered_steps(
    *,
    api_key_configured: bool,
    client_secret_status: YouTubeOAuthClientSecretStatusResponse,
    oauth_configured: bool,
    oauth_ready: bool,
) -> list[str]:
    steps: list[str] = []
    if not api_key_configured:
        steps.append("1. Utworz w Google Cloud klucz API ograniczony do YouTube Data API v3 i zapisz go przez POST /api/intelligence/youtube-data-api-key.")
    if not client_secret_status.import_ready and not oauth_configured:
        steps.append(
            "2. Utworz OAuth Client ID w Google Cloud. Potem uzyj pobranego JSON przez "
            "POST /oauth/youtube-studio/import-client-secret-json albo podaj realne client_id/client_secret "
            "przez POST /oauth/youtube-studio/runtime-config."
        )
    elif client_secret_status.import_ready and not oauth_configured:
        steps.append("2. Zaimportuj wykryty client_secret.json przez POST /oauth/youtube-studio/import-client-secret albo /connect.")
    if oauth_configured and not oauth_ready:
        steps.append("3. Uruchom POST /oauth/youtube-studio/start i otworz authorization_url w przegladarce.")
    if api_key_configured:
        steps.append("4. Uruchom POST /api/intelligence/run dla nisz kanalu, zeby wypelnic yt_signals i gtrends_signals.")
    if oauth_ready:
        steps.append("5. Uruchom GET /api/production/readiness i potem pipeline produkcyjny z prywatnym uploadem.")
    return steps


def _persist_oauth_runtime_env(env_path: Path, updates: dict[str, str]) -> Path:
    return persist_oauth_runtime_env(env_path, updates)


def _quote_env_value(value: str) -> str:
    from atnos.services.google_oauth_client_secret_importer import quote_env_value

    return quote_env_value(value)
