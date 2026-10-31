"""Detect and block geometric library placeholders from Wealth Blueprint film production.

The catalog once APPROVED primitive PNGs (stick-figure Alex, orange-circle icons) as
`source=library_placeholder`. Those assets keep brand colors but do not illustrate narration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

# Explicit catalog ids known to be primitive geometry (not editorial illustration).
GEOMETRIC_PLACEHOLDER_ASSET_IDS: frozenset[str] = frozenset(
    {
        "alex_pointing_right_cutout_v01",
        "alex_explaining_right_cutout_v01",
        "icon_piggy_bank_v01",
        "icon_etf_chart_up_v01",
        "bg_charcoal_grid_v01",
        "money_stack_coins_v01",
        "banking_transfer_flow_v01",
        "investing_dca_steps_v01",
        "house_budget_v01",
        "tech_phone_budget_app_v01",
    }
)

_MAX_UNIQUE_OPAQUE_COLORS = 8
_MIN_OPAQUE_FRACTION = 0.02


def is_known_geometric_placeholder_id(asset_id: str | None) -> bool:
    return bool(asset_id) and asset_id in GEOMETRIC_PLACEHOLDER_ASSET_IDS


def is_library_placeholder_source(source: str | None) -> bool:
    return str(source or "").strip().casefold() == "library_placeholder"


def count_unique_opaque_colors(path: Path, *, sample: int = 64) -> int | None:
    """Return approximate unique opaque RGBA colors, or None if unreadable."""
    try:
        with Image.open(path) as raw:
            img = raw.convert("RGBA")
            small = img.resize((sample, sample), Image.Resampling.NEAREST)
            opaque = [px for px in small.getdata() if px[3] > 12]
            if not opaque:
                return 0
            return len(set(opaque))
    except OSError:
        return None


def looks_like_geometric_raster(path: Path | None) -> bool:
    """Heuristic: tiny unique-color count on opaque pixels ⇒ stick figure / circle-bar."""
    if path is None or not Path(path).is_file():
        return False
    unique = count_unique_opaque_colors(Path(path))
    if unique is None:
        return False
    return unique <= _MAX_UNIQUE_OPAQUE_COLORS


def is_geometric_placeholder_asset(
    asset: Any,
    *,
    resolve_path: Path | None = None,
) -> bool:
    """True when an AssetRecord (or dict-like) must not enter explain/icon film frames."""
    if asset is None:
        return False
    if isinstance(asset, dict):
        asset_id = str(asset.get("id") or asset.get("asset_id") or "")
        source = str(asset.get("source") or "")
        file_path = str(asset.get("file_path") or "")
    else:
        asset_id = str(getattr(asset, "id", "") or getattr(asset, "asset_id", "") or "")
        source = str(getattr(asset, "source", "") or "")
        file_path = str(getattr(asset, "file_path", "") or "")

    if is_known_geometric_placeholder_id(asset_id):
        return True
    if is_library_placeholder_source(source):
        return True
    norm = file_path.replace("\\", "/").casefold()
    if "/placeholders/" in f"/{norm}" and "illustrative" not in norm:
        return True
    if resolve_path is not None and looks_like_geometric_raster(resolve_path):
        return True
    return False


def geometric_reject_reason(asset: Any) -> str:
    asset_id = ""
    if isinstance(asset, dict):
        asset_id = str(asset.get("id") or asset.get("asset_id") or "")
    else:
        asset_id = str(getattr(asset, "id", "") or getattr(asset, "asset_id", "") or "")
    return f"geometric_placeholder_not_production:{asset_id or 'unknown'}"
