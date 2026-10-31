from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atnos.production.asset_brain.catalog import default_library_root


class GenerationCache:
    """Maps request fingerprints → registered asset ids (dedupe across runs)."""

    def __init__(self, path: Path | None = None) -> None:
        root = default_library_root()
        self.path = path or (root / "generation_cache.json")

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        catalog = self._load()
        entry = catalog.get(fingerprint)
        return entry if isinstance(entry, dict) else None

    def put(
        self,
        *,
        fingerprint: str,
        asset_id: str,
        file_path: str,
        workflow_id: str,
        seed: int | None,
    ) -> None:
        catalog = self._load()
        catalog[fingerprint] = {
            "asset_id": asset_id,
            "file_path": file_path,
            "workflow_id": workflow_id,
            "seed": seed,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
