from __future__ import annotations

from typing import Any

from atnos.core.config import Settings
from atnos.memory.obsidian_style_guide import read_channel_style_guide
from atnos.production.visual_backend_status import build_visual_backend_status
from atnos.production.voice_provider_status import build_voice_provider_status


def build_public_quality_gate_status(settings: Settings, *, account_id: str | None = None) -> dict[str, Any]:
    """Zbuduj status gotowosci do produkcji materialu publicznej jakosci."""
    voice_provider = build_voice_provider_status(settings)
    visual_backend = build_visual_backend_status(settings)
    style_guide = _style_guide_status(settings, account_id=account_id)

    blockers: list[str] = []
    if bool(settings.shorts_require_premium_voice_before_publication) and not bool(
        voice_provider.get("public_voice_ready")
    ):
        blockers.append("premium_voice_provider_not_ready")
    if not bool(visual_backend.get("public_visual_ready")):
        blockers.append("visual_backend_not_ready_for_public")
    if account_id and not bool(style_guide.get("ready")):
        blockers.append("style_guide_missing_for_public")

    return {
        "ready": not blockers,
        "blockers": blockers,
        "voice_provider": voice_provider,
        "visual_backend": visual_backend,
        "style_guide": style_guide,
        "next_actions": _next_actions(blockers, voice_provider, visual_backend, style_guide),
    }


def _style_guide_status(settings: Settings, *, account_id: str | None) -> dict[str, Any]:
    if not account_id:
        return {
            "ready": False,
            "status": "account_id_missing",
            "source": None,
            "used_for_account": None,
            "next_action": "select_account_for_style_guide_check",
        }
    result = read_channel_style_guide(settings, account_id)
    return {
        "ready": bool(result.content),
        "status": result.status,
        "source": result.source,
        "used_for_account": account_id,
        "next_action": None
        if result.content
        else "create_channel_style_guide_or_run_learning_session_for_account",
    }


def _next_actions(
    blockers: list[str],
    voice_provider: dict[str, Any],
    visual_backend: dict[str, Any],
    style_guide: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if "premium_voice_provider_not_ready" in blockers:
        actions.append(str(voice_provider.get("next_action") or "run_voice_provider_doctor"))
    if "visual_backend_not_ready_for_public" in blockers:
        actions.append(str(visual_backend.get("next_action") or "run_visual_backend_doctor"))
    if "style_guide_missing_for_public" in blockers:
        actions.append(str(style_guide.get("next_action") or "create_channel_style_guide"))
    return list(dict.fromkeys(action for action in actions if action))
