#!/usr/bin/env python3
"""Install illustrative WB stills + reject geometric library placeholders in catalog.

Writes under api/config/asset_libraries/wealth_blueprint/ only (E:).
Does not overwrite historical E2E evidence.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.production.asset_brain.catalog import default_library_root, load_catalog, save_catalog
from atnos.production.asset_brain.models import AssetRecord
from atnos.production.channels.wealth_blueprint.geometric_assets import GEOMETRIC_PLACEHOLDER_ASSET_IDS
from atnos.production.channels.wealth_blueprint.illustrative_stills import (
    ILLUSTRATIVE_SPECS,
    ensure_illustrative_stills,
)


def main() -> int:
    root = default_library_root()
    written = ensure_illustrative_stills(root)
    catalog = load_catalog()
    now = datetime.now(UTC).isoformat()

    updated: list[AssetRecord] = []
    for asset in catalog.assets:
        if asset.id in GEOMETRIC_PLACEHOLDER_ASSET_IDS or asset.source == "library_placeholder":
            updated.append(
                asset.model_copy(
                    update={
                        "quality_status": "REJECTED",
                        "approved": False,
                        "reject_reason": "OTHER",
                        "reject_notes": "geometric_placeholder: primitive geometry cannot illustrate narration; superseded by illustrative/",
                        "reviewed_at": now,
                        "reviewed_by": "visual_meaning_fix",
                    }
                )
            )
        else:
            updated.append(asset)

    existing = {a.id for a in updated}
    for spec in ILLUSTRATIVE_SPECS:
        if spec["id"] in existing:
            continue
        updated.append(
            AssetRecord(
                id=str(spec["id"]),
                type=str(spec["type"]),  # type: ignore[arg-type]
                file_path=str(spec["file_path"]),
                channel="wealth_blueprint",
                tags=list(spec["tags"]),
                transparent_background=True,
                width=1080,
                height=1920,
                version="v01",
                approved=True,
                quality_status="APPROVED",
                production_role=str(spec["production_role"]),  # type: ignore[arg-type]
                source="illustrative_editorial",
                license_status="owned",
                description=str(spec["description"]),
                reviewed_at=now,
                reviewed_by="visual_meaning_fix",
            )
        )

    # Optional hand-drawn Alex cutout from crop (if cutout service available).
    alex_src = Path(r"E:\Atnos\App\config\channel_references\20260916\crops\alex_front_v1.png")
    alex_dest = root / "illustrative" / "alex_front_cutout_v02.png"
    alex_ok = False
    if alex_src.is_file():
        try:
            from atnos.services.local_cutout_service import remove_background

            meta = remove_background(alex_src, alex_dest, run_qc=True)
            alex_ok = alex_dest.is_file() and bool(meta.get("qc", {}).get("passed", True) or alex_dest.stat().st_size > 1000)
        except Exception as exc:  # noqa: BLE001
            print(f"alex_cutout_skip: {exc}")
            alex_ok = False
    if alex_ok and "alex_front_cutout_v02" not in {a.id for a in updated}:
        updated.append(
            AssetRecord(
                id="alex_front_cutout_v02",
                type="character",
                file_path="illustrative/alex_front_cutout_v02.png",
                channel="wealth_blueprint",
                character="alex",
                pose="explaining",
                emotion="confident",
                direction="front",
                tags=["finance", "host", "explain", "cutout", "hand_drawn"],
                transparent_background=True,
                version="v02",
                approved=True,
                quality_status="APPROVED",
                production_role="character_cutout",
                source="channel_reference_cutout",
                license_status="owned",
                description="Hand-drawn Alex front cutout for functional host scenes",
                reviewed_at=now,
                reviewed_by="visual_meaning_fix",
            )
        )

    catalog = catalog.model_copy(update={"assets": updated})
    save_catalog(catalog)
    print(
        {
            "library_root": str(root),
            "illustrative_written": [str(p) for p in written],
            "alex_cutout": str(alex_dest) if alex_ok else None,
            "rejected_geometric": sorted(GEOMETRIC_PLACEHOLDER_ASSET_IDS),
            "asset_count": len(catalog.assets),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
