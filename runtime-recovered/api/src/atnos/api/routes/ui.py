from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from atnos.services.access_reality_service import AccessRealityService
from atnos.services.ui_adapter_service import UiAdapterService

router = APIRouter(tags=["ui"])


def _ui_adapter_service(
    session,
    *,
    settings,
    provider_registry,
):
    """Load the broad UI adapter only when an endpoint needs the full UI payload."""
    return UiAdapterService(
        session,
        settings=settings,
        provider_registry=provider_registry,
    )


def _access_reality_service(
    session,
    *,
    settings,
    provider_registry,
):
    """Load access reality probing only for live agent frames."""
    return AccessRealityService(
        session,
        settings=settings,
        provider_registry=provider_registry,
    )


def _proactive_briefing_service(
    session,
    *,
    settings,
    provider_registry,
):
    """Load briefing dependencies only during full bootstrap."""
    from atnos.services.proactive_briefing_service import ProactiveBriefingService

    return ProactiveBriefingService(
        session,
        settings=settings,
        provider_registry=provider_registry,
    )


def _runtime_settings_service(session, settings):
    """Load runtime settings lazily so the lightweight shell import stays cheap."""
    from atnos.services.runtime_settings_service import RuntimeSettingsService

    return RuntimeSettingsService(session, settings)


def _settings_control_center_service(
    session,
    *,
    settings,
    provider_registry,
):
    """Load the heavy Settings control-center service only when that panel is requested."""
    from atnos.services.settings_control_center_service import SettingsControlCenterService

    return SettingsControlCenterService(
        session,
        settings=settings,
        provider_registry=provider_registry,
    )


def _serialize_shell_project(project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "summary": project.summary,
        "status": project.status.value,
        "is_pinned": project.is_pinned,
        "provider_posture": project.provider_posture.value
        if project.provider_posture is not None
        else None,
        "persona_profile": project.persona_profile.value
        if project.persona_profile is not None
        else None,
    }


def _list_shell_conversations(session, *, limit: int = 24) -> list[dict[str, object]]:
    from sqlalchemy import select

    from atnos.db.models.conversation import Conversation
    from atnos.db.models.enums import ConversationStatus

    statement = (
        select(Conversation)
        .where(Conversation.status == ConversationStatus.ACTIVE)
        .order_by(
            Conversation.last_message_at.desc().nullslast(),
            Conversation.updated_at.desc(),
            Conversation.id.desc(),
        )
        .limit(limit)
    )
    return [
        {
            "id": item.id,
            "title": item.title,
            "project_id": item.project_id,
            "summary": item.summary,
            "last_message_at": item.last_message_at.isoformat()
            if item.last_message_at is not None
            else None,
            "updated_at": item.updated_at.isoformat(),
        }
        for item in session.scalars(statement)
    ]


class UiBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderCardUpdateRequest(UiBaseModel):
    api_key: SecretStr | None = None
    base_url: str | None = Field(default=None, max_length=240)
    enabled: bool | None = None


class ProviderCardTestRequest(UiBaseModel):
    api_key: SecretStr | None = None
    base_url: str | None = Field(default=None, max_length=240)


class ControlCenterProfileUpdateRequest(UiBaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    handle: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=255)
    bio: str | None = Field(default=None, max_length=2000)
    locale: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)


class ControlCenterStyleUpdateRequest(UiBaseModel):
    response_length: str | None = Field(default=None, pattern="^(short|medium|long)$")
    tone: str | None = Field(default=None, pattern="^(professional|natural|direct|supportive)$")
    structure: str | None = Field(default=None, pattern="^(paragraphs|bullets|mixed)$")
    criticality_level: int | None = Field(default=None, ge=1, le=5)
    supportiveness_level: int | None = Field(default=None, ge=1, le=5)
    professional_default: bool | None = None


class ControlCenterPolicyUpdateRequest(UiBaseModel):
    automation_level: str | None = Field(default=None, pattern="^(manual|assisted|auto)$")
    verification_mode: str | None = Field(default=None, pattern="^(off|smart|strict)$")
    allow_local_file_read: bool | None = None
    allow_local_file_write: bool | None = None
    allow_web_read: bool | None = None
    allow_external_fallback: bool | None = None
    allow_external_attachment_metadata: bool | None = None
    allow_external_attachment_content: bool | None = None
    prefer_local_llm: bool | None = None
    default_provider_posture: str | None = Field(
        default=None,
        pattern="^(prefer-local|improve-when-needed|ask-before-external|strict-local)$",
    )
    default_persona_profile: str | None = Field(
        default=None,
        pattern="^(strategist|critic|executor|mentor|jarvis-mode)$",
    )
    jarvis_address_as: str | None = Field(
        default=None,
        pattern="^(none|first_name|sir_madam)$",
    )
    max_external_attachment_text_chars: int | None = Field(default=None, ge=0, le=200000)
    max_daily_cost: float | None = Field(default=None, ge=0)
    max_monthly_cost: float | None = Field(default=None, ge=0)


class ControlCenterRuntimeUpdateRequest(UiBaseModel):
    browser_control_enabled: bool | None = None
    stealth_headless_enabled: bool | None = None
    browser_allow_any_url: bool | None = None
    external_actions_enabled: bool | None = None
    scheduler_enabled: bool | None = None
    oauth_enabled: bool | None = None
    public_upload_enabled: bool | None = None
    voice_stt_engine: str | None = Field(
        default=None,
        pattern="^(browser-speech|whisper-local|faster-whisper-local)$",
    )
    voice_stt_model_path: str | None = Field(default=None, max_length=1024)
    voice_stt_device: str | None = Field(default=None, max_length=40)
    ambient_listening_enabled: bool | None = None
    ambient_wake_word: str | None = Field(default=None, max_length=120)
    ambient_mic_device: str | None = Field(default=None, max_length=255)
    ambient_muted: bool | None = None
    ambient_context_enabled: bool | None = None
    trend_watch_enabled: bool | None = None
    trend_watch_query: str | None = Field(default=None, max_length=255)
    trend_watch_region: str | None = Field(default=None, max_length=16)
    trend_watch_interval_minutes: int | None = Field(default=None, ge=30, le=1440)
    voice_replies_enabled: bool | None = None
    voice_reply_volume: float | None = Field(default=None, ge=0, le=1)
    voice_reply_voice_id: int | None = Field(default=None, gt=0)


class ControlCenterObsidianUpdateRequest(UiBaseModel):
    vault_path: str | None = Field(default=None, max_length=1024)
    export_enabled: bool


class ControlCenterVoiceUpsertRequest(UiBaseModel):
    provider_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=120)
    voice_key: str = Field(min_length=1, max_length=120)
    provider_mode: str = Field(pattern="^(local|remote)$")
    style: str | None = Field(default=None, max_length=80)
    language: str | None = Field(default="pl-PL", max_length=24)
    speaking_rate: str | None = Field(default=None, max_length=24)
    supports_realtime: bool = False
    auto_read_responses: bool = False
    is_default: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class ControlCenterRoleUpsertRequest(UiBaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str | None = Field(default=None, max_length=12000)
    tone: str | None = Field(default=None, max_length=60)
    response_style: str | None = Field(default=None, max_length=60)
    criticality_level: int = Field(default=3, ge=1, le=5)
    empathy_level: int = Field(default=3, ge=1, le=5)
    firmness_level: int = Field(default=3, ge=1, le=5)
    scope: str = Field(default="global", pattern="^(global|project)$")
    is_default: bool = False
    is_archived: bool = False


class ControlCenterPluginUpdateRequest(UiBaseModel):
    permission_policy: str | None = Field(default=None, pattern="^(auto|ask|disabled)$")
    requires_confirmation: bool | None = None
    is_enabled: bool | None = None
    config_json: dict[str, object] | None = None


class PipelineApprovalRequest(UiBaseModel):
    action: str = Field(
        default="approve_review",
        pattern="^(approve_review|request_changes|block|defer|mark_done)$",
    )
    review_notes: str | None = Field(default=None, max_length=400)


@router.get("/ui/shell")
def get_ui_shell(request: Request) -> dict[str, object]:
    """Minimal chat shell payload for fast first paint before full bootstrap finishes."""
    from atnos.repositories.project_repository import ProjectRepository

    with request.app.state.database.session_scope() as session:
        runtime_settings = _runtime_settings_service(session, request.app.state.settings)
        runtime_settings.mark_user_interaction()
        return {
            "ui_version": "axiom-prod-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "projects": [
                _serialize_shell_project(project)
                for project in ProjectRepository(session).list_all()
            ],
            "recent_conversations": _list_shell_conversations(session),
        }


@router.get("/ui/bootstrap")
def get_ui_bootstrap(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        runtime_settings = _runtime_settings_service(session, request.app.state.settings)
        briefing_service = _proactive_briefing_service(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
        )
        if briefing_service.should_emit_on_bootstrap():
            briefing_service.create_briefing_message(trigger="bootstrap")
        runtime_settings.mark_user_interaction()
        payload = _ui_adapter_service(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
        ).get_bootstrap()
        payload["ambient_state"] = _build_ambient_state_payload(request)
        return payload


@router.get("/settings/control-center")
def get_settings_control_center(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        return _settings_control_center_service(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
        ).get_payload()


@router.patch("/settings/control-center/profile")
def update_settings_control_center_profile(
    request: Request,
    payload: ControlCenterProfileUpdateRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_profile(**payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/settings/control-center/style")
def update_settings_control_center_style(
    request: Request,
    payload: ControlCenterStyleUpdateRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_style(**payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/settings/control-center/policy")
def update_settings_control_center_policy(
    request: Request,
    payload: ControlCenterPolicyUpdateRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_policy(**payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/settings/control-center/runtime")
def update_settings_control_center_runtime(
    request: Request,
    payload: ControlCenterRuntimeUpdateRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            result = _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_runtime(**payload.model_dump(exclude_unset=True))
        _sync_runtime_presence_services(request, runtime_payload=result.get("runtime"))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/control-center/trend-watch/run")
def run_settings_control_center_trend_watch(request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).run_trend_watch()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/control-center/omega-protocol/activate")
def activate_settings_control_center_omega_protocol(request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).activate_omega_protocol()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/control-center/runtime/full-access")
def enable_settings_control_center_full_access(request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            result = _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).enable_full_access_profile()
        _sync_runtime_presence_services(request, runtime_payload=result.get("runtime"))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/control-center/creative-assets/retest")
def retest_settings_control_center_creative_assets(request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).retest_creative_asset_lanes()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/control-center/creative-assets/clear-blockers")
def clear_settings_control_center_creative_asset_blockers(request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).clear_creative_asset_lane_blockers()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/settings/control-center/obsidian")
def update_settings_control_center_obsidian(
    request: Request,
    payload: ControlCenterObsidianUpdateRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_obsidian(
                vault_path=payload.vault_path,
                export_enabled=payload.export_enabled,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/control-center/voices")
def create_settings_control_center_voice(
    request: Request,
    payload: ControlCenterVoiceUpsertRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).create_voice(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/settings/control-center/voices/{voice_id}")
def update_settings_control_center_voice(
    voice_id: int,
    request: Request,
    payload: ControlCenterVoiceUpsertRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_voice(voice_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/control-center/roles")
def create_settings_control_center_role(
    request: Request,
    payload: ControlCenterRoleUpsertRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).create_role(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/settings/control-center/roles/{role_id}")
def update_settings_control_center_role(
    role_id: int,
    request: Request,
    payload: ControlCenterRoleUpsertRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_role(role_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/settings/control-center/plugins/{plugin_key}")
def update_settings_control_center_plugin(
    plugin_key: str,
    request: Request,
    payload: ControlCenterPluginUpdateRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _settings_control_center_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_plugin(plugin_key, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/ui/chat/conversations/{conversation_id}")
def get_ui_conversation_detail(
    conversation_id: int,
    request: Request,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _ui_adapter_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).get_conversation_detail(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/settings/provider-cards")
def list_provider_cards(request: Request) -> list[dict[str, object]]:
    with request.app.state.database.session_scope() as session:
        return _ui_adapter_service(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
        ).list_provider_cards()


@router.put("/settings/provider-cards/{provider_key}")
def update_provider_card(
    provider_key: str,
    request: Request,
    payload: ProviderCardUpdateRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _ui_adapter_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_provider_card(
                provider_key,
                api_key=payload.api_key.get_secret_value()
                if payload.api_key is not None
                else None,
                base_url=payload.base_url,
                enabled=payload.enabled,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/settings/provider-cards/{provider_key}/test")
def test_provider_card(
    provider_key: str,
    request: Request,
    payload: ProviderCardTestRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _ui_adapter_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).test_provider_card(
                provider_key,
                api_key=payload.api_key.get_secret_value() if payload.api_key is not None else None,
                base_url=payload.base_url,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/ui/pipeline/current")
def get_current_pipeline(request: Request) -> dict[str, object]:
    with request.app.state.database.session_scope() as session:
        return _ui_adapter_service(
            session,
            settings=request.app.state.settings,
            provider_registry=request.app.state.providers,
        ).get_current_pipeline()


@router.post("/ui/pipeline/approval/{work_item_id}")
def update_pipeline_approval(
    work_item_id: int,
    request: Request,
    payload: PipelineApprovalRequest,
) -> dict[str, object]:
    try:
        with request.app.state.database.session_scope() as session:
            return _ui_adapter_service(
                session,
                settings=request.app.state.settings,
                provider_registry=request.app.state.providers,
            ).update_pipeline_approval(
                work_item_id,
                action=payload.action,
                review_notes=payload.review_notes,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.websocket("/agent")
async def agent_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    previous_access_snapshot = None
    try:
        while True:
            with websocket.app.state.database.session_scope() as session:
                access_snapshot = None
                access_changes = ()
                try:
                    access_service = _access_reality_service(
                        session,
                        settings=websocket.app.state.settings,
                        provider_registry=websocket.app.state.providers,
                    )
                    access_snapshot = access_service.build_snapshot(
                        include_live_health=False
                    )
                    access_changes = access_service.diff(previous_access_snapshot, access_snapshot)
                    previous_access_snapshot = access_snapshot
                except Exception:
                    access_snapshot = None
                    access_changes = ()
                frames = _ui_adapter_service(
                    session,
                    settings=websocket.app.state.settings,
                    provider_registry=websocket.app.state.providers,
                ).build_agent_frames(
                    include_live_health=False,
                    fast_mode=True,
                )
            if access_snapshot is not None:
                frames.append({"type": "access.reality", "payload": access_snapshot.to_dict()})
            if access_snapshot is not None and access_changes:
                frames.append(
                    {
                        "type": "access.reality.change",
                        "payload": {
                            "snapshot_key": access_snapshot.snapshot_key,
                            "changes": [item.to_dict() for item in access_changes],
                        },
                    }
                )
            topic_events = getattr(websocket.app.state, "topic_ws_events", None)
            if isinstance(topic_events, list) and topic_events:
                frames.extend(dict(event) for event in topic_events)
                topic_events.clear()
            production_events = getattr(websocket.app.state, "production_ws_events", None)
            if isinstance(production_events, list) and production_events:
                frames.extend(dict(event) for event in production_events)
                production_events.clear()
            frames.append({"type": "ambient.state", "payload": _build_ambient_state_payload(websocket)})
            for frame in frames:
                await websocket.send_json(frame)

            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

            message_type = message.get("type")
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            if message_type == "pipeline.approve_preview":
                work_item_id = payload.get("work_item_id")
                if isinstance(work_item_id, int):
                    with websocket.app.state.database.session_scope() as session:
                        pipeline = _ui_adapter_service(
                            session,
                            settings=websocket.app.state.settings,
                            provider_registry=websocket.app.state.providers,
                        ).update_pipeline_approval(
                            work_item_id,
                            action="approve_review",
                            review_notes=payload.get("review_notes"),
                        )
                    await websocket.send_json(
                        {
                            "type": "toast",
                            "payload": {
                                "type": "success",
                                "title": "Preview approved",
                                "message": "Publish preview package marked as approved.",
                            },
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "pipeline.snapshot",
                            "payload": pipeline,
                        }
                    )
            elif message_type == "pipeline.reject_preview":
                work_item_id = payload.get("work_item_id")
                if isinstance(work_item_id, int):
                    with websocket.app.state.database.session_scope() as session:
                        pipeline = _ui_adapter_service(
                            session,
                            settings=websocket.app.state.settings,
                            provider_registry=websocket.app.state.providers,
                        ).update_pipeline_approval(
                            work_item_id,
                            action="request_changes",
                            review_notes=payload.get("review_notes"),
                        )
                    await websocket.send_json(
                        {
                            "type": "toast",
                            "payload": {
                                "type": "warning",
                                "title": "Preview sent back",
                                "message": "Publish preview package now requires changes.",
                            },
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "pipeline.snapshot",
                            "payload": pipeline,
                        }
                    )
            elif message_type == "user.command":
                await websocket.send_json(
                    {
                        "type": "toast",
                        "payload": {
                            "type": "warning",
                            "title": "Command received",
                            "message": "UI command channel is connected, but command execution is still preview-only in this runtime.",
                        },
                    }
                )
            elif message_type == "user.message":
                await websocket.send_json(
                    {
                        "type": "toast",
                        "payload": {
                            "type": "warning",
                            "title": "REST chat required",
                            "message": "This runtime still routes live chat turns through POST /chat, not through the websocket bridge.",
                        },
                    }
                )
    except WebSocketDisconnect:
        return


def _sync_runtime_presence_services(request: Request, *, runtime_payload: object) -> None:
    if not isinstance(runtime_payload, dict):
        return
    ambient_context_service = getattr(request.app.state, "ambient_context_service", None)
    voice_ambient_listener_service = getattr(request.app.state, "voice_ambient_listener_service", None)
    if ambient_context_service is not None:
        if bool(runtime_payload.get("ambient_context_enabled")):
            ambient_context_service.start()
        else:
            ambient_context_service.stop()
    if voice_ambient_listener_service is not None:
        voice_ambient_listener_service.set_muted(bool(runtime_payload.get("ambient_muted")))
        if bool(runtime_payload.get("ambient_listening_enabled")):
            voice_ambient_listener_service.start_if_enabled()
        else:
            voice_ambient_listener_service.stop()


def _build_ambient_state_payload(request_like: Request | WebSocket) -> dict[str, object]:
    ambient_context_service = getattr(request_like.app.state, "ambient_context_service", None)
    voice_ambient_listener_service = getattr(request_like.app.state, "voice_ambient_listener_service", None)
    database = request_like.app.state.database
    settings = request_like.app.state.settings
    with database.session_scope() as session:
        runtime = _runtime_settings_service(session, settings).snapshot()
    readiness = (
        voice_ambient_listener_service.readiness()
        if voice_ambient_listener_service is not None
        else None
    )
    snapshot = (
        ambient_context_service.get_snapshot()
        if ambient_context_service is not None
        else None
    )
    return {
        "ambient_listening_enabled": runtime.ambient_listening_enabled,
        "ambient_wake_word": runtime.ambient_wake_word,
        "ambient_muted": runtime.ambient_muted,
        "ambient_context_enabled": runtime.ambient_context_enabled,
        "voice_replies_enabled": runtime.voice_replies_enabled,
        "voice_reply_voice_id": runtime.voice_reply_voice_id,
        "listening_available": readiness.available if readiness is not None else False,
        "listening_reason": readiness.reason if readiness is not None else "Ambient listener service unavailable.",
        "context_snapshot": snapshot.to_dict() if snapshot is not None else None,
    }
