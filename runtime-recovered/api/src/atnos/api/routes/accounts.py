from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr

from atnos.intelligence.storage import upsert_intelligence_account_config, utc_now_iso

router = APIRouter(tags=["accounts"])


def _load_account_credentials_dependencies():
    from atnos.services.account_credentials_service import AccountCredentialsService, UNSET
    from atnos.services.secret_vault_service import SecretVaultUnavailableError

    return AccountCredentialsService, UNSET, SecretVaultUnavailableError


class AccountsBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountCredentialCreateRequest(AccountsBaseModel):
    label: str = Field(min_length=1, max_length=160)
    provider_key: str = Field(min_length=1, max_length=120)
    account_identifier: str = Field(min_length=1, max_length=240)
    secret_type: Literal[
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "app_password",
        "oauth_token_later",
        "session_cookie_later",
        "oauth_token",
        "session_cookie",
        "password_bundle",
    ]
    secret_value: SecretStr | None = None
    risk_level: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    allowed_capabilities: list[
        Literal[
            "read_metadata",
            "read_content",
            "draft_write",
            "confirmed_write_later",
            "browser_login_later",
            "publish_later",
            "financial_read_later",
            "trading_later",
            "trusted_login",
            "two_factor",
            "browser_workflow",
            "connector_write",
            "local_render",
            "oauth_session",
        ]
    ] = Field(default_factory=list)
    linked_integration_key: str | None = Field(default=None, max_length=120)
    linked_project_id: int | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=400)
    intelligence_niches: list[str] | None = None
    intelligence_region: str = Field(default="PL", max_length=16)
    intelligence_timeframe: str = Field(default="now 7-d", max_length=40)
    intelligence_refresh_now: bool = True


class AccountCredentialUpdateRequest(AccountsBaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=160)
    provider_key: str | None = Field(default=None, min_length=1, max_length=120)
    account_identifier: str | None = Field(default=None, min_length=1, max_length=240)
    secret_type: Literal[
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "app_password",
        "oauth_token_later",
        "session_cookie_later",
        "oauth_token",
        "session_cookie",
        "password_bundle",
    ] | None = None
    risk_level: str | None = Field(default=None, pattern="^(low|medium|high|critical)$")
    allowed_capabilities: list[
        Literal[
            "read_metadata",
            "read_content",
            "draft_write",
            "confirmed_write_later",
            "browser_login_later",
            "publish_later",
            "financial_read_later",
            "trading_later",
            "trusted_login",
            "two_factor",
            "browser_workflow",
            "connector_write",
            "local_render",
            "oauth_session",
        ]
    ] | None = None
    linked_integration_key: str | None = Field(default=None, max_length=120)
    linked_project_id: int | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=400)
    status: str | None = Field(
        default=None,
        pattern="^(configured|disabled|needs_rotation|invalid|metadata_only)$",
    )
    intelligence_niches: list[str] | None = None
    intelligence_region: str | None = Field(default=None, max_length=16)
    intelligence_timeframe: str | None = Field(default=None, max_length=40)
    intelligence_refresh_now: bool = True


class RotateAccountSecretRequest(AccountsBaseModel):
    secret_value: SecretStr = Field(
        validation_alias=AliasChoices("secret_value", "new_secret_value"),
    )


class AccountCredentialResponse(BaseModel):
    id: int
    label: str
    provider_key: str
    account_identifier: str
    secret_type: str
    storage_backend: str
    status: str
    has_secret: bool
    risk_level: str
    allowed_capabilities: list[str]
    linked_integration_key: str | None
    linked_project_id: int | None
    notes: str | None
    masked_secret_state: str
    created_at: datetime
    updated_at: datetime
    last_rotated_at: datetime | None
    last_used_at: datetime | None


class AccountSecretStorageStatusResponse(BaseModel):
    storage_backend: str
    is_secret_storage_enabled: bool
    summary: str
    warnings: list[str]


@router.get("/accounts/storage-status", response_model=AccountSecretStorageStatusResponse)
def get_account_storage_status(request: Request) -> AccountSecretStorageStatusResponse:
    AccountCredentialsService, _, _ = _load_account_credentials_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        status_view = AccountCredentialsService(session, settings).get_storage_status()
    return AccountSecretStorageStatusResponse(
        storage_backend=status_view.storage_backend,
        is_secret_storage_enabled=status_view.is_secret_storage_enabled,
        summary=status_view.summary,
        warnings=list(status_view.warnings),
    )


@router.get("/accounts", response_model=list[AccountCredentialResponse])
def list_accounts(request: Request) -> list[AccountCredentialResponse]:
    AccountCredentialsService, _, _ = _load_account_credentials_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        items = AccountCredentialsService(session, settings).list_accounts()
    return [_build_account_response(item) for item in items]


@router.post("/accounts", response_model=AccountCredentialResponse, status_code=status.HTTP_201_CREATED)
def create_account(
    request: Request,
    payload: AccountCredentialCreateRequest,
) -> AccountCredentialResponse:
    AccountCredentialsService, _, SecretVaultUnavailableError = _load_account_credentials_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            item = AccountCredentialsService(session, settings).create_account(
                label=payload.label,
                provider_key=payload.provider_key,
                account_identifier=payload.account_identifier,
                secret_type=payload.secret_type,
                secret_value=payload.secret_value.get_secret_value() if payload.secret_value is not None else None,
                risk_level=payload.risk_level,
                allowed_capabilities=payload.allowed_capabilities,
                linked_integration_key=payload.linked_integration_key,
                linked_project_id=payload.linked_project_id,
                notes=payload.notes,
            )
        _sync_intelligence_config_if_requested(
            settings,
            account_identifier=item.account_identifier,
            niches=payload.intelligence_niches,
            region=payload.intelligence_region,
            timeframe=payload.intelligence_timeframe,
            refresh_now=payload.intelligence_refresh_now,
        )
    except SecretVaultUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _build_account_response(item)


@router.get("/accounts/{account_id}", response_model=AccountCredentialResponse)
def get_account(account_id: int, request: Request) -> AccountCredentialResponse:
    AccountCredentialsService, _, _ = _load_account_credentials_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        item = AccountCredentialsService(session, settings).get_account(account_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account {account_id} was not found.")
    return _build_account_response(item)


@router.patch("/accounts/{account_id}", response_model=AccountCredentialResponse)
def update_account(
    account_id: int,
    request: Request,
    payload: AccountCredentialUpdateRequest,
) -> AccountCredentialResponse:
    AccountCredentialsService, UNSET, _ = _load_account_credentials_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    fields = payload.model_dump(exclude_unset=True)
    try:
        with database.session_scope() as session:
            item = AccountCredentialsService(session, settings).update_account(
                account_id,
                label=fields.get("label", UNSET),
                provider_key=fields.get("provider_key", UNSET),
                account_identifier=fields.get("account_identifier", UNSET),
                secret_type=fields.get("secret_type", UNSET),
                risk_level=fields.get("risk_level", UNSET),
                allowed_capabilities=fields.get("allowed_capabilities", UNSET),
                linked_integration_key=fields.get("linked_integration_key", UNSET),
                linked_project_id=fields.get("linked_project_id", UNSET),
                notes=fields.get("notes", UNSET),
                status=fields.get("status", UNSET),
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account {account_id} was not found.")
    _sync_intelligence_config_if_requested(
        settings,
        account_identifier=item.account_identifier,
        niches=fields.get("intelligence_niches"),
        region=fields.get("intelligence_region") or "PL",
        timeframe=fields.get("intelligence_timeframe") or "now 7-d",
        refresh_now=bool(fields.get("intelligence_refresh_now", True)),
    )
    return _build_account_response(item)


@router.post("/accounts/{account_id}/rotate-secret", response_model=AccountCredentialResponse)
def rotate_account_secret(
    account_id: int,
    request: Request,
    payload: RotateAccountSecretRequest,
) -> AccountCredentialResponse:
    AccountCredentialsService, _, SecretVaultUnavailableError = _load_account_credentials_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            item = AccountCredentialsService(session, settings).rotate_secret(
                account_id,
                secret_value=payload.secret_value.get_secret_value(),
            )
    except SecretVaultUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account {account_id} was not found.")
    return _build_account_response(item)


@router.post("/accounts/{account_id}/disable", response_model=AccountCredentialResponse)
def disable_account(account_id: int, request: Request) -> AccountCredentialResponse:
    AccountCredentialsService, _, _ = _load_account_credentials_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        item = AccountCredentialsService(session, settings).disable_account(account_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account {account_id} was not found.")
    return _build_account_response(item)


def _build_account_response(item) -> AccountCredentialResponse:
    return AccountCredentialResponse(
        id=item.id,
        label=item.label,
        provider_key=item.provider_key,
        account_identifier=item.account_identifier,
        secret_type=item.secret_type,
        storage_backend=item.storage_backend,
        status=item.status,
        has_secret=item.has_secret,
        risk_level=item.risk_level,
        allowed_capabilities=list(item.allowed_capabilities),
        linked_integration_key=item.linked_integration_key,
        linked_project_id=item.linked_project_id,
        notes=item.notes,
        masked_secret_state=item.masked_secret_state,
        created_at=item.created_at,
        updated_at=item.updated_at,
        last_rotated_at=item.last_rotated_at,
        last_used_at=item.last_used_at,
    )


def _sync_intelligence_config_if_requested(
    settings,
    *,
    account_identifier: str,
    niches: list[str] | None,
    region: str,
    timeframe: str,
    refresh_now: bool,
) -> None:
    if not niches:
        return
    upsert_intelligence_account_config(
        settings,
        account_id=account_identifier,
        niches=niches,
        region=region,
        timeframe=timeframe,
        next_refresh_at=utc_now_iso() if refresh_now else _deferred_intelligence_refresh_at(),
        preserve_existing_next_refresh=False,
    )


def _deferred_intelligence_refresh_at() -> str:
    return (datetime.fromisoformat(utc_now_iso()) + timedelta(hours=6)).isoformat()
