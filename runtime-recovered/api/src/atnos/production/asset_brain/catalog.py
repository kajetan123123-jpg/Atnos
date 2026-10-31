from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from atnos.production.asset_brain.models import AssetCatalog, AssetRecord

DEFAULT_CHANNEL = "wealth_blueprint"


def default_library_root() -> Path:
    """Config-owned library root under api/config (E:)."""
    # catalog.py → asset_brain → production → atnos → src → api
    return Path(__file__).resolve().parents[4] / "config" / "asset_libraries" / DEFAULT_CHANNEL


def catalog_path(library_root: Path | None = None) -> Path:
    return (library_root or default_library_root()) / "catalog.json"


def load_catalog(path: Path | None = None) -> AssetCatalog:
    target = path or catalog_path()
    payload = json.loads(target.read_text(encoding="utf-8"))
    return AssetCatalog.model_validate(payload)


def save_catalog(catalog: AssetCatalog, path: Path | None = None) -> Path:
    target = path or catalog_path()
    # Assignment on a mutable asset does not perform full Pydantic validation.
    # Validate before touching the production catalog, then replace atomically.
    payload=AssetCatalog.model_validate(catalog.model_dump(mode='json')).model_dump(mode='json')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as handle:
            handle.write(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def index_by_id(catalog: AssetCatalog) -> dict[str, AssetRecord]:
    return {asset.id: asset for asset in catalog.assets}
