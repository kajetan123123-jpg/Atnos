from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from atnos.channel_config import load_channel_config, save_channel_config

router = APIRouter(prefix="/api/channel", tags=["channel"])


class ChannelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


@router.get("/config")
async def get_channel_config(request: Request) -> dict[str, Any]:
    try:
        config, warnings, path = load_channel_config(request.app.state.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "config": config,
        "warnings": warnings,
        "path": str(path),
    }


@router.post("/config")
async def post_channel_config(request: Request, payload: ChannelConfigRequest) -> dict[str, Any]:
    try:
        config, warnings, path = save_channel_config(
            request.app.state.settings,
            payload.model_dump(mode="json"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "saved",
        "config": config,
        "warnings": warnings,
        "path": str(path),
    }
