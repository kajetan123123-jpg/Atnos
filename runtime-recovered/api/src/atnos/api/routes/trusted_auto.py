from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

router = APIRouter(tags=["trusted-auto"])


def _create_trusted_auto_service(session, settings):
    from atnos.services.trusted_auto_service import TrustedAutoService

    return TrustedAutoService(session, settings)


class TrustedAutoBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsAccountUpsertRequest(TrustedAutoBaseModel):
    account_id: int | None = Field(default=None, gt=0)
    label: str = Field(min_length=1, max_length=160)
    provider_key: str = Field(min_length=1, max_length=120)
    account_identifier: str = Field(min_length=1, max_length=240)
    secret_type: str = Field(min_length=1, max_length=40)
    secret_value: SecretStr | None = None
    linked_project_id: int | None = Field(default=None, gt=0)
    linked_integration_key: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    allowed_capabilities: list[str] = Field(default_factory=list)
    login_identifier: str | None = Field(default=None, max_length=255)
    password_value: SecretStr | None = None
    totp_value: SecretStr | None = None
    backup_codes_value: SecretStr | None = None
    recovery_email: str | None = Field(default=None, max_length=255)
    recovery_email_secret_value: SecretStr | None = None
    connector_mapping_json: dict[str, object] | None = None


class TrustGrantUpsertRequest(TrustedAutoBaseModel):
    trust_grant_id: int | None = Field(default=None, gt=0)
    project_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    connector_key: str = Field(min_length=1, max_length=120)
    mode: str = Field(pattern="^(blocked|supervised|trusted_auto)$")
    domain_allowlist: list[str] = Field(default_factory=list)
    action_families: list[str] = Field(default_factory=list)
    file_write_scope: list[str] = Field(default_factory=list)
    login_allowed: bool = False
    two_factor_allowed: bool = False
    publish_allowed: bool = False
    browser_automation_allowed: bool = False
    connector_write_allowed: bool = False
    local_render_allowed: bool = False
    max_retries: int = Field(default=2, ge=0, le=20)
    cooldown_minutes: int = Field(default=60, ge=0, le=10080)
    is_enabled: bool = True
    notes: str | None = Field(default=None, max_length=4000)


class SchedulerControlsUpdateRequest(TrustedAutoBaseModel):
    autonomy_mode: str | None = Field(default=None, pattern="^(blocked|supervised|trusted_auto)$")
    scheduler_enabled: bool | None = None
    emergency_stop_enabled: bool | None = None
    pause_all_jobs: bool | None = None
    login_automation_enabled: bool | None = None
    two_factor_automation_enabled: bool | None = None
    publish_automation_enabled: bool | None = None
    default_retry_budget: int | None = Field(default=None, ge=0, le=20)
    default_vram_floor_gb: int | None = Field(default=None, ge=1, le=32)


class SchedulerJobUpsertRequest(TrustedAutoBaseModel):
    job_id: int | None = Field(default=None, gt=0)
    project_id: int = Field(gt=0)
    job_type: str = Field(min_length=1, max_length=64)
    trigger_mode: str = Field(default="manual", min_length=1, max_length=24)
    connector_key: str | None = Field(default=None, max_length=120)
    platform_key: str | None = Field(default=None, max_length=120)
    scheduled_for: datetime | None = None
    cron_expression: str | None = Field(default=None, max_length=120)
    payload_json: dict[str, object] = Field(default_factory=dict)
    retry_budget: int | None = Field(default=None, ge=0, le=20)
    requires_trusted_auto: bool = True
    cooldown_key: str | None = Field(default=None, max_length=160)


class MediaPipelinePresetUpsertRequest(TrustedAutoBaseModel):
    preset_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=160)
    target_platform: str = Field(min_length=1, max_length=120)
    tts_engine: str = Field(min_length=1, max_length=120)
    image_provider: str = Field(min_length=1, max_length=120)
    video_renderer: str = Field(min_length=1, max_length=120)
    subtitle_engine: str = Field(min_length=1, max_length=120)
    prompt_style: str | None = Field(default=None, max_length=4000)
    target_duration_sec: int = Field(default=40, ge=1, le=600)
    vram_floor_gb: int = Field(default=4, ge=1, le=32)
    publish_mode: str = Field(default="trusted_auto", max_length=32)
    cooldown_hours: int = Field(default=24, ge=0, le=720)
    steps_json: dict[str, object] = Field(default_factory=dict)
    is_default: bool = False


class RunJobRequest(TrustedAutoBaseModel):
    project_id: int = Field(gt=0)
    job_type: str = Field(min_length=1, max_length=64)
    goal: str = Field(min_length=1, max_length=4000)
    connector_key: str | None = Field(default=None, max_length=120)
    platform_key: str | None = Field(default=None, max_length=120)
    account_id: int | None = Field(default=None, gt=0)
    trust_grant_id: int | None = Field(default=None, gt=0)
    payload_json: dict[str, object] = Field(default_factory=dict)
    requires_trusted_auto: bool = True


class AccountFeedbackProbeRequest(TrustedAutoBaseModel):
    timeout_seconds: float = Field(default=12.0, ge=1.0, le=30.0)


@router.get("/settings/accounts")
def list_settings_accounts(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        service = _create_trusted_auto_service(session, request.app.state.settings)
        return {"accounts": service.list_accounts()}


@router.put("/settings/accounts")
def upsert_settings_account(request: Request, payload: SettingsAccountUpsertRequest) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            account = service.upsert_account(
                account_id=payload.account_id,
                label=payload.label,
                provider_key=payload.provider_key,
                account_identifier=payload.account_identifier,
                secret_type=payload.secret_type,
                secret_value=payload.secret_value.get_secret_value() if payload.secret_value is not None else None,
                linked_project_id=payload.linked_project_id,
                linked_integration_key=payload.linked_integration_key,
                notes=payload.notes,
                allowed_capabilities=payload.allowed_capabilities,
                login_identifier=payload.login_identifier,
                password_value=payload.password_value.get_secret_value() if payload.password_value is not None else None,
                totp_value=payload.totp_value.get_secret_value() if payload.totp_value is not None else None,
                backup_codes_value=payload.backup_codes_value.get_secret_value() if payload.backup_codes_value is not None else None,
                recovery_email=payload.recovery_email,
                recovery_email_secret_value=payload.recovery_email_secret_value.get_secret_value()
                if payload.recovery_email_secret_value is not None
                else None,
                connector_mapping_json=payload.connector_mapping_json,
            )
            return {"account": account, "accounts": service.list_accounts()}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/accounts/feedback-probe")
def probe_settings_accounts_feedback(
    request: Request,
    payload: AccountFeedbackProbeRequest | None = None,
) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        service = _create_trusted_auto_service(session, request.app.state.settings)
        return {
            "results": service.probe_account_feedback(
                timeout_seconds=payload.timeout_seconds if payload is not None else 12.0
            )
        }


@router.get("/settings/trust-grants")
def list_settings_trust_grants(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        service = _create_trusted_auto_service(session, request.app.state.settings)
        return {"trust_grants": service.list_trust_grants()}


@router.put("/settings/trust-grants")
def upsert_settings_trust_grant(request: Request, payload: TrustGrantUpsertRequest) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            grant = service.upsert_trust_grant(**payload.model_dump())
            return {"trust_grant": grant, "trust_grants": service.list_trust_grants()}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/settings/scheduler")
def get_settings_scheduler(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        service = _create_trusted_auto_service(session, request.app.state.settings)
        dashboard = service.dashboard_payload()
        return {
            "runtime_controls": dashboard["runtime_controls"],
            "scheduler_jobs": dashboard["scheduler_jobs"],
            "execution_runs": dashboard["execution_runs"],
            "hardware_snapshot": dashboard["hardware_snapshot"],
        }


@router.put("/settings/scheduler")
def update_settings_scheduler(request: Request, payload: SchedulerControlsUpdateRequest) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            runtime_controls = service.update_scheduler_controls(**payload.model_dump(exclude_unset=True))
            dashboard = service.dashboard_payload()
            return {
                "runtime_controls": runtime_controls,
                "scheduler_jobs": dashboard["scheduler_jobs"],
                "execution_runs": dashboard["execution_runs"],
                "hardware_snapshot": dashboard["hardware_snapshot"],
            }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/settings/scheduler/jobs")
def upsert_settings_scheduler_job(request: Request, payload: SchedulerJobUpsertRequest) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            job = service.upsert_scheduler_job(**payload.model_dump())
            return {"job": job, "scheduler_jobs": service.list_scheduler_jobs()}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/settings/media-pipeline")
def get_settings_media_pipeline(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        service = _create_trusted_auto_service(session, request.app.state.settings)
        return {"presets": service.list_media_pipeline_presets()}


@router.put("/settings/media-pipeline")
def upsert_settings_media_pipeline(request: Request, payload: MediaPipelinePresetUpsertRequest) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            preset = service.upsert_media_pipeline_preset(**payload.model_dump())
            return {"preset": preset, "presets": service.list_media_pipeline_presets()}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/jobs/run")
def run_job(request: Request, payload: RunJobRequest) -> dict[str, object]:
    try:
        job_id: int | None = None
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            result = service.run_job_now(**payload.model_dump(), execute_now=False)
            job_id = int(result["job"]["job_id"])

        scheduler_runtime = getattr(request.app.state, "scheduler_runtime", None)
        if scheduler_runtime is not None:
            scheduler_runtime.kick_trusted_auto()

        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            refreshed_job = service.scheduler_jobs.get_by_id(job_id) if job_id is not None else None
            latest_run = next(
                (
                    item
                    for item in service.execution_runs.list_runs(limit=12)
                    if item.scheduler_job_id == job_id
                ),
                None,
            )
            return {
                "job": service._serialize_scheduler_job(refreshed_job) if refreshed_job is not None else result["job"],
                "run": service._serialize_execution_run(latest_run) if latest_run is not None else None,
            }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            return {"job": service.cancel_job(job_id)}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            return {"job": service.retry_job(job_id)}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/runs/{run_id}/approve-publish")
def approve_campaign_publish(run_id: int, request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            service = _create_trusted_auto_service(session, request.app.state.settings)
            return {"run": service.approve_campaign_publish(run_id)}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
