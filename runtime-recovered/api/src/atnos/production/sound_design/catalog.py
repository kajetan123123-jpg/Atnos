from __future__ import annotations

import json
from pathlib import Path

from atnos.production.sound_design.models import CHANNEL, SoundCatalog, SoundAssetRecord


def default_sounds_root() -> Path:
    # sound_design/ → production → atnos → src → api
    return Path(__file__).resolve().parents[4] / "config" / "asset_libraries" / CHANNEL / "sounds"


def catalog_path(root: Path | None = None) -> Path:
    return (root or default_sounds_root()) / "catalog.json"


def load_catalog(path: Path | None = None) -> SoundCatalog:
    target = path or catalog_path()
    payload = json.loads(target.read_text(encoding="utf-8"))
    return SoundCatalog.model_validate(payload)


def save_catalog(catalog: SoundCatalog, path: Path | None = None) -> Path:
    target = path or catalog_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def index_by_event(catalog: SoundCatalog) -> dict[str, list[SoundAssetRecord]]:
    buckets: dict[str, list[SoundAssetRecord]] = {}
    for asset in catalog.assets:
        buckets.setdefault(asset.event, []).append(asset)
    return buckets
