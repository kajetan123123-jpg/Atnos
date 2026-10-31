"""Canonical Wealth Blueprint visual profile — single active identity for Comfy/Remotion/QC."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PROFILE_ID = "wealth_blueprint_visual_v1"


class VisualProfileError(ValueError):
    pass


def visual_profile_path(settings: Any | None = None) -> Path:
    if settings is not None:
        root = getattr(settings, "app_root", None) or getattr(settings, "api_root", None)
        if root:
            for candidate in (
                Path(root) / "config" / "visual_profiles" / f"{PROFILE_ID}.json",
                Path(root) / "runtime-recovered" / "api" / "config" / "visual_profiles" / f"{PROFILE_ID}.json",
            ):
                if candidate.is_file():
                    return candidate
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "visual_profiles" / f"{PROFILE_ID}.json"
        if candidate.is_file():
            return candidate
    raise VisualProfileError(f"{PROFILE_ID}.json not found")


@lru_cache(maxsize=4)
def _load_raw(path_str: str) -> dict[str, Any]:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load_wealth_blueprint_visual_v1(settings: Any | None = None) -> dict[str, Any]:
    path = visual_profile_path(settings)
    payload = _load_raw(str(path.resolve()))
    if payload.get("profile_id") != PROFILE_ID:
        raise VisualProfileError(f"profile_id must be {PROFILE_ID}")
    if payload.get("status") != "active":
        raise VisualProfileError(f"{PROFILE_ID} is not active")
    return payload


def brand_roles_from_visual_profile(settings: Any | None = None) -> dict[str, str]:
    profile = load_wealth_blueprint_visual_v1(settings)
    roles = profile.get("palette", {}).get("roles") if isinstance(profile.get("palette"), dict) else {}
    if not isinstance(roles, dict):
        raise VisualProfileError("visual profile missing palette.roles")
    required = ("background", "panel", "accent", "accentSoft", "text", "muted", "wordmark")
    missing = [key for key in required if not str(roles.get(key) or "").strip()]
    if missing:
        raise VisualProfileError(f"visual profile palette.roles missing: {missing}")
    out = {key: str(roles[key]).strip() for key in required}
    out["captionStyle"] = str(roles.get("captionStyle") or "plain_phrase").strip() or "plain_phrase"
    return out


def comfy_style_from_visual_profile(settings: Any | None = None) -> str:
    profile = load_wealth_blueprint_visual_v1(settings)
    comfy = profile.get("comfy") if isinstance(profile.get("comfy"), dict) else {}
    style = str(comfy.get("style_prompt") or "").strip()
    if not style:
        raise VisualProfileError("visual profile missing comfy.style_prompt")
    return style


def animation_default_for_layout(layout_id: str, settings: Any | None = None) -> list[str]:
    profile = load_wealth_blueprint_visual_v1(settings)
    defaults = (profile.get("animation") or {}).get("defaults") if isinstance(profile.get("animation"), dict) else {}
    if not isinstance(defaults, dict):
        return ["TEXT_FADE"]
    key = (layout_id or "").strip()
    if key == "character_left_text_right":
        return [str(defaults.get("character_left") or "CHARACTER_ENTER_LEFT"), str(defaults.get("text") or "TEXT_FADE")]
    if key in {"character_right_text_left", "split_screen"}:
        return [str(defaults.get("character_right") or "CHARACTER_ENTER_RIGHT"), str(defaults.get("text") or "TEXT_FADE")]
    if key == "icon_explainer":
        return [str(defaults.get("icon") or "ICON_POP"), str(defaults.get("text") or "TEXT_FADE")]
    if key in {"chart_focus", "comparison"}:
        return [str(defaults.get("chart") or "CHART_GROW"), str(defaults.get("text") or "TEXT_FADE")]
    if key == "big_number":
        return [str(defaults.get("number") or "NUMBER_COUNT"), str(defaults.get("text") or "TEXT_FADE")]
    return [str(defaults.get("text") or "TEXT_FADE")]
