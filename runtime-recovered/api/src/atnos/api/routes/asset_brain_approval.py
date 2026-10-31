"""Asset Brain quality approval API — generated != approved."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from atnos.production.asset_brain.approval import (
    AssetApprovalError,
    AssetApprovalService,
    build_review_contact_sheet,
)
from atnos.production.asset_brain.models import REJECT_REASONS, RejectReason
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain

router = APIRouter(prefix="/api/production/asset-brain", tags=["asset-brain-approval"])


class ApproveBody(BaseModel):
    reviewed_by: str = Field(default="operator", max_length=80)


class RejectBody(BaseModel):
    reason: RejectReason
    notes: str = Field(default="", max_length=500)
    reviewed_by: str = Field(default="operator", max_length=80)


class ContactSheetBody(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=32)
    character: str | None = None


def _brain(request: Request) -> WealthBlueprintAssetBrain:
    return WealthBlueprintAssetBrain(settings=request.app.state.settings)


@router.get("/review-queue")
def review_queue(request: Request) -> dict[str, Any]:
    svc = AssetApprovalService(_brain(request))
    items = svc.list_awaiting_review()
    return {
        "count": len(items),
        "assets": [
            {
                "id": a.id,
                "character": a.character,
                "emotion": a.emotion,
                "pose": a.pose,
                "direction": a.direction,
                "quality_status": a.quality_status,
                "pre_gate": a.pre_gate,
                "file_path": a.file_path,
            }
            for a in items
        ],
    }


@router.get("/metrics")
def approval_metrics(request: Request) -> dict[str, Any]:
    return AssetApprovalService(_brain(request)).metrics()


@router.get("/reject-reasons")
def reject_reasons() -> dict[str, Any]:
    return {"reasons": list(REJECT_REASONS)}


@router.post("/assets/{asset_id}/approve")
def approve_asset(asset_id: str, body: ApproveBody, request: Request) -> dict[str, Any]:
    try:
        asset = AssetApprovalService(_brain(request)).approve(asset_id, reviewed_by=body.reviewed_by)
    except AssetApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"asset": asset.model_dump(mode="json")}


@router.post("/assets/{asset_id}/reject")
def reject_asset(asset_id: str, body: RejectBody, request: Request) -> dict[str, Any]:
    try:
        asset = AssetApprovalService(_brain(request)).reject(
            asset_id,
            body.reason,
            notes=body.notes,
            reviewed_by=body.reviewed_by,
        )
    except AssetApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"asset": asset.model_dump(mode="json")}


@router.post("/contact-sheet")
def make_contact_sheet(body: ContactSheetBody, request: Request) -> dict[str, Any]:
    brain = _brain(request)
    assets = []
    for asset_id in body.asset_ids:
        asset = brain.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Unknown asset {asset_id}")
        assets.append(asset)
    settings = request.app.state.settings
    root = (settings.app_root or settings.data_dir) / "data" / "generated" / "_review_sheets"
    if settings.app_root:
        root = settings.app_root / "data" / "generated" / "_review_sheets"
    char = (body.character or assets[0].character or "characters").casefold()
    dest = root / char / f"sheet_{assets[0].id[:12]}.jpg"
    try:
        path = build_review_contact_sheet(assets, resolve_file=brain.resolve_file, dest=dest)
    except AssetApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    svc = AssetApprovalService(brain)
    for asset in assets:
        updated = asset.model_copy(update={"contact_sheet_path": str(path)})
        svc._replace(updated)  # noqa: SLF001 — intentional catalog update
    return {"contact_sheet": str(path), "asset_ids": [a.id for a in assets]}
