"""Wealth Blueprint style pack loader — tokens + layout catalog (no Remotion code gen)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_IDS, LayoutId

STYLE_ID = "wealth_blueprint_style_v1"


class DesignSystemError(ValueError):
    pass


class LayoutSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=40)
    region_pct: list[float] = Field(min_length=4, max_length=4)

    @field_validator("region_pct")
    @classmethod
    def region_bounds(cls, value: list[float]) -> list[float]:
        if len(value) != 4:
            raise ValueError("region_pct must be [x0,y0,x1,y1]")
        x0, y0, x1, y1 = value
        for n in value:
            if n < 0 or n > 100:
                raise ValueError("region_pct values must be 0..100")
        if x1 <= x0 or y1 <= y0:
            raise ValueError("region_pct requires x1>x0 and y1>y0")
        return value


class LayoutSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    alex: bool = False
    max_elements: int = Field(ge=1, le=12)
    focus: str = Field(min_length=1, max_length=40)
    scene_type_hint: str | None = None
    slots: list[LayoutSlot] = Field(min_length=1)
    animation_presets: list[str] = Field(default_factory=list)
    transition_in: str = "cut"
    transition_out: str = "cut"


class WealthBlueprintStylePack(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    style_id: str
    channel: str
    canvas: dict[str, Any]
    colors: dict[str, Any]
    typography: dict[str, Any]
    spacing: dict[str, Any]
    safe_zones: dict[str, Any]
    text_hierarchy: list[str]
    layouts: dict[str, LayoutSpec]
    animation_presets: dict[str, Any] = Field(default_factory=dict)
    motion_defaults: dict[str, Any] = Field(default_factory=dict)
    transitions: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @field_validator("style_id")
    @classmethod
    def style_id_ok(cls, value: str) -> str:
        if value != STYLE_ID:
            raise ValueError(f"style_id must be {STYLE_ID}")
        return value

    @field_validator("layouts")
    @classmethod
    def all_layout_ids(cls, value: dict[str, LayoutSpec]) -> dict[str, LayoutSpec]:
        missing = [lid for lid in LAYOUT_IDS if lid not in value]
        if missing:
            raise ValueError(f"layouts missing: {missing}")
        unknown = [key for key in value if key not in LAYOUT_IDS]
        if unknown:
            raise ValueError(f"unknown layout ids: {unknown}")
        return value


def style_pack_path(settings: Any | None = None) -> Path:
    if settings is not None:
        root = getattr(settings, "app_root", None) or getattr(settings, "api_root", None)
        if root:
            candidate = Path(root) / "config" / "design_systems" / f"{STYLE_ID}.json"
            if candidate.is_file():
                return candidate
            # runtime-recovered/api/config
            api_cfg = Path(root) / "runtime-recovered" / "api" / "config" / "design_systems" / f"{STYLE_ID}.json"
            if api_cfg.is_file():
                return api_cfg
    # Prefer package-adjacent config (api/config)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "design_systems" / f"{STYLE_ID}.json"
        if candidate.is_file():
            return candidate
    raise DesignSystemError(f"{STYLE_ID}.json not found")


@lru_cache(maxsize=4)
def _load_raw(path_str: str) -> dict[str, Any]:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load_wealth_blueprint_style_v1(settings: Any | None = None) -> WealthBlueprintStylePack:
    path = style_pack_path(settings)
    payload = _load_raw(str(path.resolve()))
    try:
        return WealthBlueprintStylePack.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise DesignSystemError(f"Invalid {STYLE_ID}: {exc}") from exc


def require_layout(layout_id: str, settings: Any | None = None) -> LayoutSpec:
    pack = load_wealth_blueprint_style_v1(settings)
    key = str(layout_id).strip()
    if key not in pack.layouts:
        raise DesignSystemError(f"Unknown layout_id: {layout_id}")
    return pack.layouts[key]  # type: ignore[index]


def brand_tokens_for_manifest(settings: Any | None = None) -> dict[str, str]:
    """Active visual profile colors for Remotion brand injection (navy/cream/gold)."""
    from atnos.production.channels.wealth_blueprint.visual_profile import brand_roles_from_visual_profile

    try:
        return brand_roles_from_visual_profile(settings)
    except Exception:
        pack = load_wealth_blueprint_style_v1(settings)
        colors = pack.colors
        extended = colors.get("extended") if isinstance(colors.get("extended"), dict) else {}
        return {
            "background": str(colors.get("background") or "#142633"),
            "panel": str(extended.get("panel") or colors.get("primary") or "#1C3344"),
            "accent": str(colors.get("accent") or "#D5AD6C"),
            "accentSoft": str(colors.get("secondary") or extended.get("blueprint_blue") or "#426579"),
            "text": str(colors.get("text_on_dark") or "#F0E5D2"),
            "muted": str(extended.get("muted") or extended.get("muted_teal") or "#4B7977"),
            "wordmark": "WEALTH BLUEPRINT",
            "captionStyle": "plain_phrase",
        }


def list_layout_ids(settings: Any | None = None) -> tuple[LayoutId, ...]:
    pack = load_wealth_blueprint_style_v1(settings)
    return tuple(pack.layouts.keys())  # type: ignore[return-value]
