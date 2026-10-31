"""Explicit, server-resolved local image setup for a new WB film order."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore, _event, _same_process
from atnos.production.project_profile import content_hash

IMAGE_PROVIDER = "comfyui-local"
IMAGE_REFERENCE_ID = "wb-original-board"
_REFERENCE_PATH = (
    Path(__file__).resolve().parents[3]
    / "config" / "brand-kits" / "wealth-blueprint-alex-01" / "visual-bible-board.jpg"
)


def _valid_reference(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        if resolved.drive.casefold() != "e:" or not resolved.is_file():
            return False
        with Image.open(resolved) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                return False
            image.verify()
        return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def image_options(settings: Any) -> dict[str, Any]:
    """Return safe picker metadata, never a filesystem path or provider secret."""
    from atnos.production.channels.wealth_blueprint.visual_styles import STYLE_IDS, freeze_visual_style

    styles = []
    for style_id in sorted(STYLE_IDS):
        style = freeze_visual_style(style_id)
        styles.append({"id": style_id, "name": style["name"]})
    install = getattr(settings, "comfyui_install_dir", None)
    models = getattr(settings, "comfyui_models_dir", None)
    provider_available = bool(install and Path(install).is_dir() and models and Path(models).is_dir())
    reference_available = _valid_reference(_REFERENCE_PATH)
    return {
        "providers": [{
            "id": IMAGE_PROVIDER,
            "label": "ComfyUI (lokalny, bez opłaty za API)",
            "available": provider_available,
            "requires_reference": True,
        }],
        "references": [{
            "id": IMAGE_REFERENCE_ID,
            "label": "Plansza referencyjna Wealth Blueprint",
            "available": reference_available,
            **({} if reference_available else {"reason": "missing" if not _REFERENCE_PATH.exists() else "invalid"}),
        }],
        "styles": styles,
    }


def validate_image_selection(settings: Any, order: Any) -> Path | None:
    """Resolve and validate the fixed E: reference before persisting an order."""
    if order.image_provider is None and order.image_reference_id is None:
        return None
    if order.image_provider != IMAGE_PROVIDER or order.image_reference_id != IMAGE_REFERENCE_ID:
        raise ValueError("Nieobsługiwany lokalny dostawca lub obraz referencyjny.")
    if order.project != "wealth_blueprint" or order.delivery_mode != "local_only" or order.production_scope != "film":
        raise ValueError("Lokalne obrazy wymagają lokalnego zlecenia filmowego Wealth Blueprint.")
    options = image_options(settings)
    if not options["providers"][0]["available"]:
        raise ValueError("Lokalny ComfyUI nie jest skonfigurowany.")
    path = _REFERENCE_PATH.resolve()
    if not _valid_reference(path):
        raise ValueError("Referencja Wealth Blueprint jest niedostępna lub nieprawidłowa.")
    return path


def register_order_image_lane(settings: Any, job_id: str, reference_path: Path) -> dict:
    """Register exactly the explicitly selected reference for the created order."""
    from atnos.production.wb_image_api import load_image_lane, register_local_image_lane

    state = ProductionOrderStore(settings).get(job_id)
    request = state["request"]
    if (request.get("image_provider") != IMAGE_PROVIDER
            or request.get("image_reference_id") != IMAGE_REFERENCE_ID):
        raise ProductionBlocked("Order does not contain an explicit local image selection")
    existing = load_image_lane(settings, job_id)
    if existing is not None:
        if existing.get("provider") != IMAGE_PROVIDER or Path(existing["reference_path"]).resolve() != reference_path.resolve():
            raise ProductionBlocked("Existing image authorization differs from this order selection")
        lane = existing
    else:
        lane = register_local_image_lane(
            settings, job_id, reference_path=reference_path, authorized=True,
        )

    # Only a newly selected, stopped local-only order with autonomy already
    # enabled may append the separate free-local cap. Never touch the original
    # lane receipt, paid API/provider budget, or a live worker on idempotent POST.
    state = ProductionOrderStore(settings).get(job_id)
    if not state["worker_live"] and state["status"] in {"queued", "blocked"}:
        from atnos.production.order_autonomy import policy

        autonomy = policy(settings, job_id)
        if autonomy and autonomy.get("enabled"):
            from atnos.production.local_image_allowance import (
                effective_local_image_allowance,
                register_local_image_allowance,
            )

            if effective_local_image_allowance(settings, job_id, lane) == 20:
                register_local_image_allowance(
                    settings, job_id, 40,
                    reason="User selected local ComfyUI for this normal local WB film production with the displayed bounded 40-image limit.",
                    authorized=True,
                )
    _release_setup_hold(settings, job_id)
    return lane


def _release_setup_hold(settings: Any, job_id: str) -> None:
    """Atomically make a successfully configured new order eligible to dispatch."""
    from atnos.production.wb_image_api import load_image_lane

    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    lane = load_image_lane(settings, job_id)
    if (not lane or lane.get("provider") != IMAGE_PROVIDER
            or lane.get("reference_path") != str(_REFERENCE_PATH.resolve())
            or lane.get("request_hash") != content_hash(state["request"])
            or lane.get("profile_hash") != content_hash(state["profile"])):
        raise ProductionBlocked("Valid local image lane binding is required before dispatch")
    # The image recovery entry point can run independently of normal intake,
    # which binds voice first. Keep the combined setup hold until both selected
    # lanes exist; preflight is not a substitute for dispatch eligibility.
    if (state.get('current_step') == 'image_setup'
            and state['request'].get('voice_candidate') in {'Ryan-B', 'Ryan-neutral'}):
        from atnos.production.local_voice_lane import load_local_voice_lane, LocalVoiceLaneError
        try:
            voice_lane = load_local_voice_lane(settings, job_id)
        except LocalVoiceLaneError as exc:
            raise ProductionBlocked('Valid local voice lane binding is required before dispatch') from exc
        if voice_lane is None:
            raise ProductionBlocked('Valid local voice lane binding is required before dispatch')
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT j.status,j.current_step,j.error,o.request_json,o.profile_json,o.lease_token,o.lease_pid,o.lease_process_started "
            "FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        if not row:
            raise ProductionBlocked("Local image setup order disappeared")
        if row["status"] != "blocked" or row["current_step"] != "image_setup":
            db.commit()
            return
        try:
            error = json.loads(row["error"] or "{}")
            request = json.loads(row["request_json"] or "{}")
        except (ValueError, TypeError):
            raise ProductionBlocked("Local image setup hold is not intact") from None
        if (not isinstance(error, dict) or error.get("category") != "local_image_setup_pending"
                or request.get("image_provider") != IMAGE_PROVIDER
                or request.get("image_reference_id") != IMAGE_REFERENCE_ID
                or lane.get("request_hash") != content_hash(request)
                or lane.get("profile_hash") != content_hash(json.loads(row["profile_json"] or "{}"))):
            raise ProductionBlocked("Local image setup hold does not match the order selection")
        if row["lease_token"] or _same_process(row["lease_pid"], row["lease_process_started"]):
            raise ProductionBlocked("A live worker cannot be released from the local image setup hold")
        now = utc_now_iso()
        db.execute(
            "UPDATE pipeline_jobs SET status='queued',current_step='queued',error=NULL,updated_at=? "
            "WHERE id=? AND status='blocked' AND current_step='image_setup'",
            (now, job_id),
        )
        _event(db, job_id, "image_setup", "completed", {
            "image_lane_registered": True,
            "local_allowance": "bounded_append_only_if_autonomy_enabled",
            "dispatch_unblocked_after_setup": True,
        })
        db.commit()
