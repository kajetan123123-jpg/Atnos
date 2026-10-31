"""Comfy channel profile API — validated generation only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from atnos.production.comfy_channels import (
    ChannelComfyGenerationError,
    ChannelComfyGenerationService,
    check_channel_comfy,
    list_profiles,
    persist_builtin_profiles,
    run_all_channel_comfy_health,
    validate_generation_request,
)
from atnos.production.comfy_channels.models import ChannelId

router = APIRouter(prefix="/api/production/comfy-channels", tags=["comfy-channels"])


class GenerateBody(BaseModel):
    channel: str
    workflow: str = Field(min_length=3, max_length=80)
    subject: str = ""
    emotion: str = "neutral"
    direction: str = "front"
    asset_type: str = ""
    composition: str = ""
    lighting: str = ""
    camera: str = ""
    references: list[str] = Field(default_factory=list, max_length=8)
    aspect_ratio: str = "1:1"
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    force_regenerate: bool = False


@router.get("/profiles")
def get_profiles(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    profiles = list_profiles(settings)
    return {
        "channels": {
            cid: {
                "display_name": p.display_name,
                "role": p.role,
                "workflows": [w.model_dump(mode="json") for w in p.workflows],
                "output_subdir": p.output_subdir,
                "comfy_project_dir": p.comfy_project_dir,
                "primary_characters": p.primary_characters,
                "shared_models": True,
            }
            for cid, p in profiles.items()
        }
    }


@router.get("/health")
def get_all_health(request: Request) -> dict[str, Any]:
    return run_all_channel_comfy_health(request.app.state.settings)


@router.get("/health/{channel}")
def get_channel_health(channel: str, request: Request) -> dict[str, Any]:
    from atnos.production.comfy_channels.profiles import resolve_channel_id

    try:
        channel_id: ChannelId = resolve_channel_id(channel)
        return check_channel_comfy(request.app.state.settings, channel_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/generate")
def generate_channel_asset(body: GenerateBody, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        validated = validate_generation_request(settings, body.model_dump())
        meta = ChannelComfyGenerationService(settings).generate(validated)
    except (ChannelComfyGenerationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return meta.model_dump(mode="json")


@router.post("/persist-profiles")
def persist_profiles(request: Request) -> dict[str, Any]:
    paths = persist_builtin_profiles(request.app.state.settings)
    return {"written": [str(p) for p in paths]}
