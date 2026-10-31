from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import json_loads

VOICE_PROVIDER_REPORT_MAX_AGE_HOURS = 6.0


def build_voice_provider_status(settings: Settings) -> dict[str, Any]:
    """Zwraca ostatni realnie sprawdzony stan providerow glosu bez sekretow."""
    reports_dir = settings.data_dir / "reports"
    report_path = _latest_voice_provider_report(reports_dir)
    if report_path is not None:
        payload = json_loads(report_path.read_text(encoding="utf-8"), {})
        if isinstance(payload, dict):
            return _sanitize_voice_provider_report(payload, report_path=report_path, settings=settings)

    return {
        "checked": False,
        "public_voice_ready": not bool(settings.shorts_require_premium_voice_before_publication),
        "selected_tts_backend": str(settings.shorts_tts_backend),
        "next_action": "run_voice_provider_doctor",
        "report_path": None,
        "providers": {
            "elevenlabs": {
                "configured": bool(settings.elevenlabs_api_key),
                "live_checked": False,
                "live_ready": False,
                "status_code": None,
                "error": None,
            },
            "openai_tts": {
                "configured": bool(settings.openai_api_key),
                "live_checked": False,
                "live_ready": False,
                "status_code": None,
                "error": None,
                "model": str(settings.openai_tts_model),
            },
            "f5_tts": {
                "configured": bool(settings.f5_tts_base_url),
                "live_checked": False,
                "live_ready": False,
                "status_code": None,
                "error": None,
                "base_url": str(settings.f5_tts_base_url),
            },
            "kokoro_local": {
                "configured": bool(settings.kokoro_model_path and settings.kokoro_voices_path),
                "live_checked": False,
                "live_ready": False,
                "status_code": None,
                "error": None,
                "model_path": str(settings.kokoro_model_path),
                "voices_path": str(settings.kokoro_voices_path),
                "voice_name": str(settings.kokoro_voice_name),
            },
        },
    }


def _latest_voice_provider_report(reports_dir: Path) -> Path | None:
    if not reports_dir.exists():
        return None
    reports = [path for path in reports_dir.glob("voice_provider_doctor_*.json") if path.is_file()]
    if not reports:
        return None
    return max(reports, key=lambda path: path.stat().st_mtime)


def _sanitize_voice_provider_report(payload: dict[str, Any], *, report_path: Path, settings: Settings) -> dict[str, Any]:
    providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
    generated_at = payload.get("generated_at")
    age_hours = _report_age_hours(generated_at, report_path=report_path)
    stale = age_hours is None or age_hours > VOICE_PROVIDER_REPORT_MAX_AGE_HOURS
    raw_ready = bool(payload.get("public_voice_ready"))
    public_voice_ready = raw_ready and not stale
    next_action = str(payload.get("next_action") or "run_voice_provider_doctor")
    if stale and bool(settings.shorts_require_premium_voice_before_publication):
        next_action = "run_voice_provider_doctor"
    return {
        "checked": True,
        "generated_at": generated_at,
        "report_stale": stale,
        "report_age_hours": None if age_hours is None else round(age_hours, 3),
        "report_max_age_hours": VOICE_PROVIDER_REPORT_MAX_AGE_HOURS,
        "public_voice_ready": public_voice_ready,
        "selected_tts_backend": str(payload.get("selected_tts_backend") or ""),
        "next_action": next_action,
        "report_path": str(report_path),
        "cloud_voice_budget": _sanitize_cloud_budget(payload.get("cloud_voice_budget")),
        "remediation_actions": _sanitize_remediation_actions(payload.get("remediation_actions")),
        "providers": {
            "elevenlabs": _sanitize_provider_status(
                providers.get("elevenlabs"),
                extra_keys=("profile_voices", "budget_required", "budget_allowed", "estimated_generation_cost_pln"),
            ),
            "openai_tts": _sanitize_provider_status(
                providers.get("openai_tts"),
                extra_keys=("model", "profile_voices", "budget_required", "budget_allowed", "estimated_generation_cost_pln"),
            ),
            "f5_tts": _sanitize_provider_status(providers.get("f5_tts"), extra_keys=("base_url",)),
            "kokoro_local": _sanitize_provider_status(
                providers.get("kokoro_local"),
                extra_keys=("model_path", "voices_path", "voice_name"),
            ),
        },
    }


def _sanitize_remediation_actions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        actions.append(
            {
                "id": _safe_text(item.get("id"), limit=80),
                "label": _safe_text(item.get("label"), limit=120),
                "kind": _safe_text(item.get("kind"), limit=80),
                "priority": _safe_text(item.get("priority"), limit=40),
                "reason": _safe_text(item.get("reason"), limit=260),
            }
        )
    return actions


def _sanitize_cloud_budget(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "required": bool(source.get("required")),
        "allowed": bool(source.get("allowed")),
        "status": _safe_text(source.get("status"), limit=80),
        "estimated_cost_pln": source.get("estimated_cost_pln"),
        "monthly_remaining_pln": source.get("monthly_remaining_pln"),
        "daily_remaining_pln": source.get("daily_remaining_pln"),
        "message": _safe_text(source.get("message"), limit=220),
        "blockers": [str(item)[:120] for item in (source.get("blockers") or []) if str(item).strip()],
    }


def _safe_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _sanitize_provider_status(value: Any, *, extra_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    sanitized: dict[str, Any] = {
        "configured": bool(source.get("configured")),
        "live_checked": bool(source.get("live_checked")),
        "live_ready": bool(source.get("live_ready")),
        "status_code": source.get("status_code"),
        "error": _safe_error_text(source.get("error")),
    }
    for key in extra_keys:
        item = source.get(key)
        if isinstance(item, dict):
            sanitized[key] = {str(name): value for name, value in item.items()}
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


def _report_age_hours(value: Any, *, report_path: Path) -> float | None:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.fromtimestamp(report_path.stat().st_mtime, tz=UTC)
    else:
        parsed = datetime.fromtimestamp(report_path.stat().st_mtime, tz=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600, 0.0)
