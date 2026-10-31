"""Versioned per-order WB visual variants. A frozen copy travels with the order."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from atnos.production.project_manifest import BrandSpec

STYLE_IDS = frozenset({"A", "B", "C", "D"})
STYLE_VERSION = "wb_visual_styles_v2"
_SOURCE = Path(__file__).resolve().parents[5] / "config" / "visual_styles" / "wealth_blueprint_variants_v1.json"


def freeze_visual_style(style_id: str) -> dict[str, Any]:
    """Resolve only a closed ID and snapshot its complete visual contract."""
    if style_id not in STYLE_IDS:
        raise ValueError(f"Unsupported Wealth Blueprint visual_style_id: {style_id!r}")
    payload = json.loads(_SOURCE.read_text(encoding="utf-8"))
    if payload.get("version") != STYLE_VERSION:
        raise ValueError("Wealth Blueprint visual style version mismatch")
    styles = payload.get("styles")
    if not isinstance(styles, dict) or set(styles) != STYLE_IDS:
        raise ValueError("Wealth Blueprint visual style catalog is incomplete")
    entry = styles[style_id]
    if not isinstance(entry, dict) or not isinstance(entry.get("prompt"), str) or not entry["prompt"].strip():
        raise ValueError("Wealth Blueprint visual style prompt is missing")
    brand = BrandSpec.model_validate(entry.get("brand"))
    core = {"id": style_id, "version": STYLE_VERSION, "name": str(entry["name"]),
            "prompt": entry["prompt"].strip(), "brand": brand.model_dump(mode="json")}
    digest = hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {**core, "definition_hash": digest, "asset_tag": f"wb-style-{style_id.lower()}-v2-{digest[:8]}"}


def style_asset_tags(style: dict[str, Any], job_id: str | None = None, scene_id: str | None = None) -> list[str]:
    """Both variant and order tags prevent a generic approved still from leaking in."""
    tags = [str(style["asset_tag"])]
    if job_id:
        tags.append(f"wb-order-{job_id.casefold()}")
    if scene_id:
        tags.append(f"wb-scene-{scene_id.casefold()}")
    return tags
