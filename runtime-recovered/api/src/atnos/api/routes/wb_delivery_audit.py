"""Non-destructive WB audit endpoints, separate from production render workers."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.order_store import ProductionOrderStore
from atnos.production.wb_delivery_audit import audit_order, library_inventory, write_report

router = APIRouter(prefix="/api/production", tags=["wealth-blueprint-delivery-audit"])


class DeliveryAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    editorial_review: dict | None = None


def _library(settings) -> Path:
    return Path(settings.app_root) / "runtime-recovered/api/config/asset_libraries/wealth_blueprint"


@router.get("/wealth-blueprint/library-audit")
def get_library_audit(request: Request):
    settings = request.app.state.settings
    root = _library(settings)
    try:
        return {"visual": library_inventory(root / "catalog.json", Path(settings.app_root)),
                "sound": library_inventory(root / "sounds/catalog.json", Path(settings.app_root)),
                "automatic_approval": False}
    except (OSError, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/orders/{job_id}/delivery-audit")
def run_delivery_audit(job_id: str, body: DeliveryAuditRequest, request: Request):
    settings = request.app.state.settings
    store = ProductionOrderStore(settings)
    try:
        state = store.get(job_id)
        if state["request"]["project"] != "wealth_blueprint":
            raise HTTPException(400, detail="Odbiór dotyczy wyłącznie Wealth Blueprint.")
        if state.get("worker_live"):
            raise HTTPException(409, detail="Produkcja nadal pracuje. Odbiór wymaga zakończonego przebiegu.")
        context = store.context(job_id)
        root = _library(settings)
        ffmpeg = resolve_ffmpeg()
        ffprobe = resolve_ffprobe(ffmpeg) if ffmpeg else None
        report = audit_order(context.artifact_dir, app_root=Path(settings.app_root),
                             catalog_path=root / "catalog.json", sound_catalog_path=root / "sounds/catalog.json",
                             ffmpeg=Path(ffmpeg) if ffmpeg else None, ffprobe=Path(ffprobe) if ffprobe else None,
                             human_review=body.editorial_review)
        output = Path(settings.app_root) / "reports/wb-delivery-audits" / uuid4().hex
        write_report(report, output)
        return {**report, "report_path": str(output / "audit.json"), "review_path": str(output / "review.html"),
                "production_order_modified": False, "published": False}
    except KeyError as exc:
        raise HTTPException(404, detail="Nie znaleziono produkcji.") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc
