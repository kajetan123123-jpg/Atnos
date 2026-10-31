from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.production.cost_guard import CostGuard
from atnos.production.readiness import ShortProductionReadinessService


def voice_provider_report_paths(settings: Settings) -> tuple[Path, Path]:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report_dir = settings.data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    return (
        report_dir / f"voice_provider_doctor_{timestamp}.json",
        report_dir / f"voice_provider_doctor_{timestamp}.md",
    )


async def build_voice_provider_doctor_report(settings: Settings) -> dict[str, Any]:
    """Wykonuje maly live-probe TTS i zwraca bezpieczny raport providerow glosu."""
    settings.shorts_tts_live_generation_probe = True
    readiness = await ShortProductionReadinessService(settings).check(include_live_checks=True)
    by_key = {str(item["key"]): item for item in readiness["items"]}
    audio = dict(by_key["audio_stack"]["evidence"])
    openai_api = await probe_openai_api_reachability(settings)
    cloud_voice_budget = _cloud_voice_budget_status(settings)
    cloud_voice_budget_required = _cloud_voice_budget_required(settings, audio)
    public_voice_ready = bool(audio.get("public_publication_voice_ready")) and (
        not cloud_voice_budget_required or bool(cloud_voice_budget.get("allowed"))
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "public_voice_ready": public_voice_ready,
        "selected_tts_backend": settings.shorts_tts_backend,
        "cloud_voice_budget": cloud_voice_budget,
        "providers": {
            "elevenlabs": {
                "configured": bool(audio.get("elevenlabs_configured")),
                "live_checked": bool(audio.get("elevenlabs_tts_live_checked")),
                "live_ready": bool(audio.get("elevenlabs_tts_live_ready")),
                "status_code": audio.get("elevenlabs_tts_live_status_code"),
                "error": audio.get("elevenlabs_tts_live_error"),
                "profile_voices": audio.get("elevenlabs_profile_voice_configured"),
                "budget_required": cloud_voice_budget_required and settings.shorts_tts_backend == "elevenlabs",
                "budget_allowed": bool(cloud_voice_budget.get("allowed")),
                "estimated_generation_cost_pln": cloud_voice_budget.get("estimated_cost_pln"),
            },
            "openai_tts": {
                "configured": bool(audio.get("openai_tts_configured")),
                "live_checked": bool(audio.get("openai_tts_live_checked")),
                "live_ready": bool(audio.get("openai_tts_live_ready")),
                "status_code": audio.get("openai_tts_live_status_code"),
                "error": audio.get("openai_tts_live_error"),
                "model": audio.get("openai_tts_model"),
                "profile_voices": audio.get("openai_tts_profile_voices"),
                "api_reachable": openai_api["reachable"],
                "api_status_code": openai_api["status_code"],
                "api_error": openai_api["error"],
                "budget_required": cloud_voice_budget_required and settings.shorts_tts_backend == "openai_tts",
                "budget_allowed": bool(cloud_voice_budget.get("allowed")),
                "estimated_generation_cost_pln": cloud_voice_budget.get("estimated_cost_pln"),
            },
            "f5_tts": {
                "configured": bool(audio.get("f5_tts_configured")),
                "live_checked": bool(audio.get("f5_tts_live_checked")),
                "live_ready": bool(audio.get("f5_tts_live_ready")),
                "status_code": audio.get("f5_tts_live_status_code"),
                "error": audio.get("f5_tts_live_error"),
                "base_url": audio.get("f5_tts_base_url"),
            },
            "kokoro_local": {
                "configured": bool(audio.get("kokoro_local_assets_ready")),
                "live_checked": bool(audio.get("kokoro_local_live_checked")),
                "live_ready": bool(audio.get("kokoro_local_live_ready")),
                "status_code": audio.get("kokoro_local_live_status_code"),
                "error": audio.get("kokoro_local_live_error"),
                "model_path": audio.get("kokoro_local_model_path"),
                "voices_path": audio.get("kokoro_local_voices_path"),
                "voice_name": audio.get("kokoro_local_voice_name"),
            },
        },
        "next_action": voice_provider_next_action(audio, cloud_voice_budget=cloud_voice_budget),
        "remediation_actions": voice_provider_remediation_actions(audio, openai_api=openai_api),
    }


async def probe_openai_api_reachability(settings: Settings) -> dict[str, Any]:
    """Sprawdza czy klucz OpenAI dziala ogolnie, bez generowania platnych tresci."""
    if not str(settings.openai_api_key or "").strip():
        return {"reachable": False, "status_code": None, "error": "OPENAI_API_KEY is not configured."}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
    except Exception as exc:
        return {"reachable": False, "status_code": None, "error": str(exc)[:240]}
    if response.status_code < 400:
        return {"reachable": True, "status_code": response.status_code, "error": None}
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text[:240]}
    error = payload.get("error") if isinstance(payload, dict) else payload
    return {"reachable": False, "status_code": response.status_code, "error": error}


async def write_voice_provider_doctor_report(settings: Settings) -> dict[str, Any]:
    report = await build_voice_provider_doctor_report(settings)
    json_path, md_path = voice_provider_report_paths(settings)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(voice_provider_report_to_markdown(report), encoding="utf-8")
    return {**report, "report_path": str(json_path), "markdown_report_path": str(md_path)}


def voice_provider_next_action(audio: dict[str, Any], *, cloud_voice_budget: dict[str, Any] | None = None) -> str:
    if bool(audio.get("public_publication_voice_ready")) and cloud_voice_budget is not None and not bool(
        cloud_voice_budget.get("allowed")
    ):
        return "approve_cloud_voice_budget_or_use_local_f5_tts"
    if bool(audio.get("public_publication_voice_ready")):
        return "premium_voice_ready_generate_private_public_quality_short"
    if bool(audio.get("openai_api_key_configured")) and not bool(audio.get("openai_tts_allow_cloud_tts")):
        return "enable_openai_tts_cloud_backend"
    if bool(audio.get("openai_tts_configured")) and not bool(audio.get("openai_tts_live_checked")):
        return "run_voice_provider_doctor_with_openai_tts_live_probe"
    kokoro_error = str(audio.get("kokoro_local_live_error") or "")
    if not bool(audio.get("kokoro_local_assets_ready")) or not bool(audio.get("kokoro_local_live_ready")):
        if kokoro_error:
            return "install_or_fix_kokoro_local_tts"
    eleven_error = str(audio.get("elevenlabs_tts_live_error") or "")
    openai_error = str(audio.get("openai_tts_live_error") or "")
    if "insufficient_quota" in openai_error:
        return "add_openai_billing_or_raise_project_quota"
    if "detected_unusual_activity" in eleven_error:
        return "upgrade_or_unblock_elevenlabs_account"
    return "start_local_f5_tts_or_configure_paid_cloud_tts"


def _cloud_voice_budget_status(settings: Settings) -> dict[str, Any]:
    estimate = round(max(float(getattr(settings, "atnos_estimated_tts_cost_per_short_pln", 0.0) or 0.0), 0.0), 4)
    if not bool(getattr(settings, "shorts_allow_cloud_tts", False)):
        return {
            "required": False,
            "allowed": False,
            "status": "cloud_tts_disabled",
            "estimated_cost_pln": estimate,
            "blockers": ["cloud_tts_disabled"],
            "message": "Cloud TTS is disabled in settings.",
            "monthly_remaining_pln": None,
            "daily_remaining_pln": None,
        }
    decision = CostGuard(settings).check_can_spend(
        estimate,
        operation="cloud_voice_generation",
    )
    blockers = [str(item) for item in decision.get("blockers") or [] if str(item).strip()]
    allowed = bool(decision.get("allowed"))
    return {
        "required": estimate > 0,
        "allowed": allowed,
        "status": "allowed" if allowed else "budget_blocked",
        "estimated_cost_pln": estimate,
        "blockers": blockers,
        "message": str(decision.get("message") or ""),
        "monthly_remaining_pln": decision.get("monthly_remaining_pln"),
        "daily_remaining_pln": decision.get("daily_remaining_pln"),
        "monthly_spent_pln": decision.get("monthly_spent_pln"),
        "daily_spent_pln": decision.get("daily_spent_pln"),
    }


def _cloud_voice_budget_required(settings: Settings, audio: dict[str, Any]) -> bool:
    backend = str(getattr(settings, "shorts_tts_backend", "") or "").strip().lower()
    if backend not in {"openai_tts", "elevenlabs"}:
        return False
    return bool(getattr(settings, "shorts_allow_cloud_tts", False)) and (
        bool(audio.get("openai_tts_live_ready")) or bool(audio.get("elevenlabs_tts_live_ready"))
    )


def voice_provider_remediation_actions(
    audio: dict[str, Any],
    *,
    openai_api: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Zwróć bezpieczne, operatorskie ścieżki naprawy premium voice."""
    if bool(audio.get("public_publication_voice_ready")):
        return [
            {
                "id": "generate_private_public_quality_short",
                "label": "Generate a private public-quality sample",
                "kind": "safe_local_action",
                "priority": "normal",
                "reason": "At least one public-quality voice provider is live.",
            }
        ]

    actions: list[dict[str, Any]] = []
    openai_error = str(audio.get("openai_tts_live_error") or "")
    eleven_error = str(audio.get("elevenlabs_tts_live_error") or "")
    f5_error = str(audio.get("f5_tts_live_error") or "")
    kokoro_ready = bool(audio.get("kokoro_local_live_ready"))

    if bool(audio.get("openai_tts_configured")):
        if "insufficient_quota" in openai_error:
            api_reachable = bool((openai_api or {}).get("reachable"))
            actions.append(
                {
                    "id": "fix_openai_tts_quota_or_project_limit",
                    "label": "Fix OpenAI API billing/quota for TTS",
                    "kind": "owner_external_action",
                    "priority": "high",
                    "reason": (
                        "OpenAI API key is valid, but TTS returned insufficient_quota; fix billing, project budget or hard quota."
                        if api_reachable
                        else "OpenAI reached the API but returned insufficient_quota; this is not a retryable rate limit."
                        if openai_api is None
                        else "OpenAI TTS returned insufficient_quota; fix billing, project budget or hard quota."
                    ),
                }
            )
        elif not bool(audio.get("openai_tts_live_ready")):
            actions.append(
                {
                    "id": "rerun_openai_tts_probe_after_key_or_limit_fix",
                    "label": "Rerun OpenAI TTS probe after key/project fix",
                    "kind": "safe_local_action",
                    "priority": "normal",
                    "reason": openai_error[:220] or "OpenAI TTS is configured but not live-ready.",
                }
            )
    elif bool(audio.get("openai_api_key_configured")) and not bool(audio.get("openai_tts_allow_cloud_tts")):
        actions.append(
            {
                "id": "enable_openai_tts_cloud_backend",
                "label": "Enable OpenAI TTS cloud backend",
                "kind": "config_change",
                "priority": "high",
                "reason": "OPENAI_API_KEY exists but cloud TTS is disabled.",
            }
        )

    if bool(audio.get("f5_tts_configured")) and not bool(audio.get("f5_tts_live_ready")):
        actions.append(
            {
                "id": "start_local_f5_tts",
                "label": "Start local F5-TTS service",
                "kind": "owner_or_local_runtime_action",
                "priority": "high",
                "reason": f5_error[:220] or "F5-TTS endpoint is configured but unreachable.",
            }
        )

    if bool(audio.get("elevenlabs_configured")) and not bool(audio.get("elevenlabs_tts_live_ready")):
        actions.append(
            {
                "id": "upgrade_or_unblock_elevenlabs_account",
                "label": "Upgrade or unblock ElevenLabs account",
                "kind": "owner_external_action",
                "priority": "medium",
                "reason": eleven_error[:220] or "ElevenLabs is configured but not live-ready.",
            }
        )

    if kokoro_ready:
        actions.append(
            {
                "id": "use_kokoro_for_private_qa_only",
                "label": "Use Kokoro only for private QA samples",
                "kind": "safe_local_fallback",
                "priority": "low",
                "reason": "Kokoro local works, but premium voice is required before public publication.",
            }
        )

    return actions


def voice_provider_report_to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Atnos Voice Provider Doctor",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- public_voice_ready: `{report['public_voice_ready']}`",
        f"- selected_tts_backend: `{report['selected_tts_backend']}`",
        f"- next_action: `{report['next_action']}`",
        "",
        "## Providers",
        "| provider | configured | live_checked | live_ready | status | error |",
        "|---|---:|---:|---:|---:|---|",
    ]
    providers = report["providers"]
    for name, payload in providers.items():
        error = str(payload.get("error") or "").replace("\n", " ")[:260]
        lines.append(
            "| {name} | {configured} | {live_checked} | {live_ready} | {status} | {error} |".format(
                name=name,
                configured=payload.get("configured"),
                live_checked=payload.get("live_checked"),
                live_ready=payload.get("live_ready"),
                status=payload.get("status_code") or "-",
                error=error or "-",
            )
        )
    actions = report.get("remediation_actions") if isinstance(report.get("remediation_actions"), list) else []
    if actions:
        lines.extend(["", "## Remediation actions"])
        for action in actions:
            lines.append(
                "- `{id}` ({priority}, {kind}): {label} - {reason}".format(
                    id=action.get("id") or "unknown",
                    priority=action.get("priority") or "-",
                    kind=action.get("kind") or "-",
                    label=action.get("label") or "-",
                    reason=str(action.get("reason") or "-").replace("\n", " ")[:220],
                )
            )
    lines.append("")
    return "\n".join(lines)
