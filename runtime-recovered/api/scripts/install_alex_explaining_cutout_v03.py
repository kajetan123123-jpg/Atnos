#!/usr/bin/env python3
"""Install waist-up Alex explaining/pointing cutouts from the identity sheet.

Crops EXPLAINING / MAKING A POINT panels from channel_references image-1,
removes background with local U2-Net, and updates the WB asset catalog.
Head-only alex_front_cutout_v02 is demoted to pose=front (not explaining).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.production.asset_brain.catalog import default_library_root, load_catalog, save_catalog
from atnos.production.asset_brain.models import AssetRecord
from atnos.production.cutout_quality import run_alpha_qc
from atnos.services.local_cutout_service import remove_background

SHEET = Path(r"E:\Atnos\App\config\channel_references\20260916\image-1.png")
CREAM = (240, 229, 210)


def _prep_cell(band: Image.Image, col: int, cols: int = 8) -> Image.Image:
    bw, bh = band.size
    cw = bw // cols
    pad = 8
    cell = band.crop((col * cw + pad, 0, (col + 1) * cw - pad, bh)).convert("RGB")
    draw = ImageDraw.Draw(cell)
    draw.rectangle((0, 0, cell.width, 42), fill=CREAM)
    draw.rectangle((0, cell.height - 36, cell.width, cell.height), fill=CREAM)
    return cell.resize((cell.width * 4, cell.height * 4), Image.Resampling.LANCZOS)


def _tight_rgba(path: Path) -> None:
    with Image.open(path) as im:
        alpha = im.getchannel("A")
        bbox = alpha.point(lambda x: 255 if x > 8 else 0).getbbox()
        if not bbox:
            return
        m = 6
        cropped = im.crop(
            (
                max(0, bbox[0] - m),
                max(0, bbox[1] - m),
                min(im.width, bbox[2] + m),
                min(im.height, bbox[3] + m),
            )
        )
        cropped.save(path)


def _upsert(assets: list[AssetRecord], record: AssetRecord) -> list[AssetRecord]:
    return [a for a in assets if a.id != record.id] + [record]


def main() -> int:
    if not SHEET.is_file():
        raise FileNotFoundError(SHEET)
    root = default_library_root()
    illust = root / "illustrative"
    illust.mkdir(parents=True, exist_ok=True)
    work = Path(r"E:\Atnos\App\data\generated\_alex_cutout_v03")
    work.mkdir(parents=True, exist_ok=True)

    sheet = Image.open(SHEET).convert("RGB")
    band = sheet.crop((35, 375, 1510, 735))

    specs = [
        ("explaining", 0, "alex_explaining_cutout_v03.png", "explaining", "confident", ["explain", "torso", "hands", "gesture"]),
        ("pointing", 1, "alex_pointing_cutout_v03.png", "pointing", "confident", ["point", "torso", "hands", "gesture"]),
    ]
    built: dict[str, dict] = {}
    for name, col, filename, pose, emotion, extra_tags in specs:
        raw = work / f"{name}_install.png"
        _prep_cell(band, col).save(raw)
        dest = illust / filename
        meta = remove_background(raw, dest, run_qc=True)
        _tight_rgba(dest)
        qc = run_alpha_qc(dest, require_alpha=True)
        with Image.open(dest) as im:
            width, height = im.size
        built[name] = {
            "dest": dest,
            "pose": pose,
            "emotion": emotion,
            "extra_tags": extra_tags,
            "meta": meta,
            "qc": qc,
            "width": width,
            "height": height,
            "id": filename.replace(".png", ""),
        }

    now = datetime.now(UTC).isoformat()
    catalog = load_catalog()
    assets = list(catalog.assets)

    front = next(a for a in assets if a.id == "alex_front_cutout_v02")
    assets = _upsert(
        assets,
        front.model_copy(
            update={
                "pose": "front",
                "tags": ["finance", "host", "portrait", "cutout", "hand_drawn", "head_only"],
                "description": (
                    "Head/neck profile cutout — not sufficient for explaining gestures; "
                    "use alex_explaining_cutout_v03 for host explaining"
                ),
                "reviewed_at": now,
                "reviewed_by": "wb_quality_standard_cutout_fix",
            }
        ),
    )

    for name, payload in built.items():
        qc = payload["qc"]
        if name == "explaining" and not qc.passed:
            raise RuntimeError(f"explaining cutout failed alpha QC: {qc.verdict}")
        # Explaining must PASS; pointing may stay unapproved if alpha QC fails.
        assets = _upsert(
            assets,
            AssetRecord(
                id=str(payload["id"]),
                type="character",
                file_path=f"illustrative/{payload['dest'].name}",
                channel="wealth_blueprint",
                character="alex",
                pose=str(payload["pose"]),
                emotion=str(payload["emotion"]),
                direction="front",
                tags=["finance", "host", "cutout", "hand_drawn", *payload["extra_tags"]],
                transparent_background=True,
                width=int(payload["width"]),
                height=int(payload["height"]),
                version="v03",
                approved=bool(qc.passed),
                quality_status="APPROVED" if qc.passed else "NEEDS_REPROCESS",
                production_role="character_cutout",
                source="channel_reference_cutout",
                license_status="owned",
                description=(
                    "Waist-up Alex explaining cutout with visible torso and open-hand gesture"
                    if name == "explaining"
                    else "Chest-up Alex pointing cutout with raised index finger"
                ),
                reviewed_at=now,
                reviewed_by="wb_quality_standard_cutout_fix",
                reject_reason=None if qc.passed else "OTHER",
                reject_notes="" if qc.passed else "alpha_qc_failed",
                pre_gate={"alpha_qc": qc.as_dict()},
            ),
        )

    save_catalog(catalog.model_copy(update={"assets": assets}))
    print(
        {
            "library_root": str(root),
            "explaining": {
                "path": str(built["explaining"]["dest"]),
                "qc": built["explaining"]["qc"].verdict,
                "size": [built["explaining"]["width"], built["explaining"]["height"]],
            },
            "pointing": {
                "path": str(built["pointing"]["dest"]),
                "qc": built["pointing"]["qc"].verdict,
                "size": [built["pointing"]["width"], built["pointing"]["height"]],
            },
            "demoted": "alex_front_cutout_v02 -> pose=front",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
