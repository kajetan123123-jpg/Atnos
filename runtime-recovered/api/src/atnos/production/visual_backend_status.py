from __future__ import annotations

from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import json_loads
from atnos.production.cost_guard import CostGuard


def build_visual_backend_status(settings: Settings) -> dict[str, Any]:
    """Zwraca ostatni realnie sprawdzony stan backendu wideo bez uruchamiania renderu."""
    reports_dir = settings.data_dir / "reports"
    report_path = _latest_visual_backend_report(reports_dir)
    if report_path is not None:
        payload = json_loads(report_path.read_text(encoding="utf-8"), {})
        if isinstance(payload, dict):
            return _sanitize_visual_backend_report(payload, report_path=report_path)

    selected = str(settings.shorts_video_backend)
    cloud_budget = _cloud_visual_budget_status(settings)
    return {
        "checked": False,
        "public_visual_ready": False,
        "selected_video_backend": selected,
        "effective_video_backend": selected,
        "next_action": "run_visual_backend_doctor",
        "report_path": None,
        "providers": {
            "comfyui": {
                "configured": False,
                "live_checked": False,
                "live_ready": False,
                "status": None,
                "error": None,
                "endpoint": str(settings.comfyui_base_url),
            },
            "openai_image_motion": {
                "configured": bool(settings.openai_api_key),
                "live_checked": False,
                "live_ready": False,
                "status": None,
                "error": None,
                "cloud_visuals_allowed": bool(settings.shorts_allow_cloud_visuals),
                "budget_allowed": bool(cloud_budget.get("allowed")),
                "budget_status": cloud_budget.get("status"),
                "estimated_generation_cost_pln": cloud_budget.get("estimated_cost_pln"),
                "monthly_remaining_pln": cloud_budget.get("monthly_remaining_pln"),
                "daily_remaining_pln": cloud_budget.get("daily_remaining_pln"),
            },
            "motion_graphic": {
                "configured": selected == "motion_graphic",
                "live_checked": False,
                "live_ready": selected == "motion_graphic",
                "status": "fallback_only" if selected == "motion_graphic" else None,
                "error": "Motion graphics fallback is not accepted as public-quality visual backend."
                if selected == "motion_graphic"
                else None,
            },
        },
    }


def _latest_visual_backend_report(reports_dir: Path) -> Path | None:
    if not reports_dir.exists():
        return None
    reports = [path for path in reports_dir.glob("visual_backend_doctor_*.json") if path.is_file()]
    if not reports:
        return None
    return max(reports, key=lambda path: path.stat().st_mtime)


def _sanitize_visual_backend_report(payload: dict[str, Any], *, report_path: Path) -> dict[str, Any]:
    providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
    return {
        "checked": True,
        "generated_at": payload.get("generated_at"),
        "public_visual_ready": bool(payload.get("public_visual_ready")),
        "selected_video_backend": str(payload.get("selected_video_backend") or ""),
        "effective_video_backend": str(payload.get("effective_video_backend") or ""),
        "next_action": str(payload.get("next_action") or "run_visual_backend_doctor"),
        "report_path": str(report_path),
        "providers": {
            "comfyui": _sanitize_provider_status(
                providers.get("comfyui"),
                extra_keys=("endpoint", "install_exists", "auto_launch_supported", "backend_public_quality"),
            ),
            "openai_image_motion": _sanitize_provider_status(
                providers.get("openai_image_motion"),
                extra_keys=(
                    "cloud_visuals_allowed",
                    "budget_allowed",
                    "budget_status",
                    "estimated_generation_cost_pln",
                    "monthly_remaining_pln",
                    "daily_remaining_pln",
                    "backend_public_quality",
                    "last_success_at",
                    "last_error_at",
                ),
            ),
            "motion_graphic": _sanitize_provider_status(
                providers.get("motion_graphic"),
                extra_keys=("backend_public_quality",),
            ),
        },
        "evidence": payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
    }


def _sanitize_provider_status(value: Any, *, extra_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    sanitized: dict[str, Any] = {
        "configured": bool(source.get("configured")),
        "live_checked": bool(source.get("live_checked")),
        "live_ready": bool(source.get("live_ready")),
        "status": source.get("status"),
        "error": _safe_error_text(source.get("error")),
    }
    for key in extra_keys:
        item = source.get(key)
        if isinstance(item, (bool, int, float)):
            sanitized[key] = item
        elif item is not None:
            sanitized[key] = str(item)
    return sanitized


def _safe_error_text(value: Any, *, limit: int = 280) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:limit]


def _cloud_visual_budget_status(settings: Settings) -> dict[str, Any]:
    estimate = round(max(float(getattr(settings, "atnos_estimated_visual_cost_per_short_pln", 0.0) or 0.0), 0.0), 4)
    if not bool(getattr(settings, "shorts_allow_cloud_visuals", False)):
        return {
            "allowed": False,
            "status": "cloud_visuals_disabled",
            "estimated_cost_pln": estimate,
            "monthly_remaining_pln": None,
            "daily_remaining_pln": None,
        }
    decision = CostGuard(settings).check_can_spend(
        estimate,
        operation="openai_image_motion_visual_generation",
    )
    return {
        "allowed": bool(decision.get("allowed")),
        "status": "allowed" if bool(decision.get("allowed")) else "budget_blocked",
        "estimated_cost_pln": estimate,
        "monthly_remaining_pln": decision.get("monthly_remaining_pln"),
        "daily_remaining_pln": decision.get("daily_remaining_pln"),
    }
