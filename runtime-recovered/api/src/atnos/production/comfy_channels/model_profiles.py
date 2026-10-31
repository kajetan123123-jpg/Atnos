"""Load per-channel production model profiles + versioned img2img presets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.production.comfy_channels.models import ChannelId


def model_profiles_path(settings: Settings) -> Path:
    if settings.app_root:
        return Path(settings.app_root) / "config" / "comfy_channel_model_profiles.json"
    return Path(r"E:\Atnos\App\config\comfy_channel_model_profiles.json")


def img2img_presets_path(settings: Settings) -> Path:
    if settings.app_root:
        return Path(settings.app_root) / "config" / "comfy_img2img_presets.json"
    return Path(r"E:\Atnos\App\config\comfy_img2img_presets.json")


def load_img2img_presets(settings: Settings) -> dict[str, Any]:
    path = img2img_presets_path(settings)
    if not path.is_file():
        return {"version": 0, "presets": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def get_img2img_preset(settings: Settings, preset_id: str) -> dict[str, Any] | None:
    presets = load_img2img_presets(settings).get("presets") or {}
    entry = presets.get(preset_id)
    return dict(entry) if isinstance(entry, dict) else None


def load_model_profile(settings: Settings, channel: ChannelId) -> dict[str, Any]:
    path = model_profiles_path(settings)
    if not path.is_file():
        return _with_illustration_trial(settings, channel, {
            "primary_model": "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
            "fallback_model": "checkpoints/sd_xl_base_1.0_0.9vae.safetensors",
            "reference_mode": "core_img2img",
            "reference_denoise": 0.6,
        })
    payload = json.loads(path.read_text(encoding="utf-8"))
    channels = payload.get("channels") or {}
    entry = dict(channels.get(channel) or {})
    # Resolve versioned img2img preset (single source of denoise truth)
    preset_id = str(entry.get("img2img_preset") or "").strip()
    if preset_id:
        preset = get_img2img_preset(settings, preset_id)
        if preset:
            entry["img2img_preset"] = preset_id
            if preset.get("reference_mode") is not None:
                entry["reference_mode"] = preset["reference_mode"]
            if preset.get("reference_denoise") is not None:
                entry["reference_denoise"] = preset["reference_denoise"]
            if preset.get("preferred_reference"):
                entry["preferred_reference"] = preset["preferred_reference"]
            entry["img2img_preset_meta"] = {
                "preset_id": preset.get("preset_id") or preset_id,
                "status": preset.get("status"),
                "goal": preset.get("goal"),
            }
    return _with_illustration_trial(settings, channel, entry)


def _with_illustration_trial(settings: Settings, channel: ChannelId, entry: dict[str, Any]) -> dict[str, Any]:
    """An explicit worker opt-in selects a verified candidate, not a new default."""
    from atnos.production.comfy_channels.illustration_checkpoint import load_opt_in_candidate

    trial = load_opt_in_candidate(settings, channel)
    if trial is None:
        return entry
    return {**entry, "quality_model": trial["checkpoint"],
            "quality_fallback_model": "", "fallback_model": "",
            "illustration_trial": trial, "required_quality": True}


def custom_node_policy_path(settings: Settings) -> Path:
    if settings.app_root:
        return Path(settings.app_root) / "config" / "comfy_custom_node_policy.json"
    return Path(r"E:\Atnos\App\config\comfy_custom_node_policy.json")


def load_custom_node_policy(settings: Settings) -> dict[str, Any]:
    path = custom_node_policy_path(settings)
    if not path.is_file():
        return {"policy": "CORE_ONLY", "disable_all_custom_nodes": True, "allowlist": []}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_generation_tier(
    profile: dict[str, Any],
    *,
    channel: ChannelId,
    asset_type: str,
    workflow_title: str = "",
    visual_style_id: str | None = None,
) -> dict[str, Any]:
    """Pick FAST vs QUALITY checkpoint for the channel (no Flux/Z-Image).

    Returns preferred_checkpoint, quality_mode, tier label, and explicit fallback.
    Failures must surface — callers should record fallback_used when switching.
    """
    asset = (asset_type or "").strip().casefold()
    title = (workflow_title or "").strip().casefold()
    if channel == "wealth_blueprint" and profile.get("illustration_trial"):
        return {"tier": "quality", "preferred_checkpoint": profile["quality_model"],
                "quality_mode": True, "fallback_checkpoint": None,
                "fallback_quality_mode": False}
    fast = str(profile.get("fast_model") or profile.get("primary_model") or "").strip()
    quality = str(profile.get("quality_model") or "").strip()
    fallback = str(
        profile.get("quality_fallback_model")
        or profile.get("fallback_model")
        or profile.get("primary_model")
        or fast
    ).strip()

    if channel != "wealth_blueprint" or not quality:
        primary = str(profile.get("primary_model") or fast).strip()
        quality_mode = channel == "dark_chronicles" and "juggernaut" in primary.casefold()
        return {
            "tier": "primary",
            "preferred_checkpoint": primary,
            "quality_mode": quality_mode,
            "fallback_checkpoint": str(profile.get("fallback_model") or "").strip() or None,
            "fallback_quality_mode": False,
        }

    quality_types = {str(x).casefold() for x in (profile.get("quality_asset_types") or [])}
    fast_types = {str(x).casefold() for x in (profile.get("fast_asset_types") or [])}
    quality_titles = ("character", "editorial", "object", "reference_variation")
    if visual_style_id in {'A', 'B', 'C', 'D'}:
        # Style trials compare the real illustration medium, including icon/background
        # roles. The fast tier must not quietly determine their final art quality.
        use_quality = True
    elif asset in quality_types:
        use_quality = True
    elif asset in fast_types:
        use_quality = False
    elif any(token in title for token in quality_titles):
        use_quality = True
    elif asset in {"icon", "background"}:
        use_quality = False
    else:
        use_quality = True

    if use_quality:
        return {
            "tier": "quality",
            "preferred_checkpoint": quality,
            "quality_mode": True,
            "fallback_checkpoint": fallback or fast or None,
            "fallback_quality_mode": False,
        }
    return {
        "tier": "fast",
        "preferred_checkpoint": fast or str(profile.get("primary_model") or "").strip(),
        "quality_mode": False,
        "fallback_checkpoint": str(profile.get("fallback_model") or quality or "").strip() or None,
        "fallback_quality_mode": False,
    }
