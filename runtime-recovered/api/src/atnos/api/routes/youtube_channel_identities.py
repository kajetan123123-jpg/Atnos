from __future__ import annotations

"""Endpoints for explicit, reviewable YouTube channel configuration."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from atnos.services.youtube_channel_identity_service import (
    YouTubeChannelIdentityError,
    YouTubeChannelIdentityNotFoundError,
    YouTubeChannelIdentityService,
)


router = APIRouter(prefix="/api/youtube-channel-identities", tags=["youtube-channel-identities"])


class IdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_binding_key: str = Field(min_length=6, max_length=320)
    youtube_channel_id: str | None = Field(default=None, max_length=128)
    channel_handle: str | None = Field(default=None, max_length=160)
    channel_url: str | None = Field(default=None, max_length=1024)
    display_name: str | None = Field(default=None, max_length=240)
    owner_label: str | None = Field(default=None, max_length=240)
    language_code: str | None = Field(default=None, max_length=24)
    target_market: str | None = Field(default=None, max_length=80)
    niche: str | None = Field(default=None, max_length=4000)
    oauth_connection_id: int | None = Field(default=None, gt=0)


class IdentityConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_channel_id: str = Field(min_length=10, max_length=128)


class ManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]


class PublishingPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: dict[str, Any]


@router.get("/projects/{project_id}")
def get_channel_identity(project_id: int, request: Request) -> dict[str, Any]:
    return _run(request, lambda service: service.snapshot(project_id))


@router.put("/projects/{project_id}/identity")
def put_channel_identity(project_id: int, payload: IdentityRequest, request: Request) -> dict[str, Any]:
    return _run(request, lambda service: service.upsert_identity(project_id, payload.model_dump()))


@router.post("/projects/{project_id}/identity/confirm")
def confirm_channel_identity(project_id: int, payload: IdentityConfirmationRequest, request: Request) -> dict[str, Any]:
    return _run(request, lambda service: service.confirm_identity(project_id, confirmed_channel_id=payload.confirmed_channel_id))


@router.post("/projects/{project_id}/identity/live-verify")
def verify_channel_identity_live(project_id: int, request: Request) -> dict[str, Any]:
    """Read the OAuth-selected channel. This endpoint never writes to YouTube."""
    return _run(request, lambda service: service.verify_live_identity(project_id, settings=request.app.state.settings))


@router.put("/projects/{project_id}/manifest")
def put_channel_manifest(project_id: int, payload: ManifestRequest, request: Request) -> dict[str, Any]:
    return _run(request, lambda service: service.update_manifest(project_id, payload.manifest))


@router.post("/projects/{project_id}/manifest/approve")
def approve_channel_manifest(project_id: int, request: Request) -> dict[str, Any]:
    return _run(request, lambda service: service.approve_manifest(project_id))


@router.put("/projects/{project_id}/publishing-policy")
def put_publishing_policy(project_id: int, payload: PublishingPolicyRequest, request: Request) -> dict[str, Any]:
    return _run(request, lambda service: service.update_publishing_policy(project_id, payload.policy))


@router.post("/projects/{project_id}/publishing-policy/approve")
def approve_publishing_policy(project_id: int, request: Request) -> dict[str, Any]:
    return _run(request, lambda service: service.approve_publishing_policy(project_id))


@router.get("/projects/{project_id}/publish-eligibility")
def get_publish_eligibility(project_id: int, request: Request) -> dict[str, Any]:
    snapshot = _run(request, lambda service: service.snapshot(project_id))
    return {
        "project_id": snapshot["project_id"],
        "identity_status": snapshot["status"],
        "publish_eligibility": snapshot["publish_eligibility"],
    }


def _run(request: Request, operation):
    database = request.app.state.database
    with database.session_scope() as session:
        service = YouTubeChannelIdentityService(session)
        try:
            return operation(service)
        except YouTubeChannelIdentityNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except YouTubeChannelIdentityError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
