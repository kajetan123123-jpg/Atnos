from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from atnos.core.config import Settings
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService


@dataclass(frozen=True, slots=True)
class AssetCatalogEntry:
    cache_key: str
    prompt_hash: str
    seed: int
    filename: str
    storage_path: str
    media_type: str
    file_extension: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_key": self.cache_key,
            "prompt_hash": self.prompt_hash,
            "seed": self.seed,
            "filename": self.filename,
            "storage_path": self.storage_path,
            "media_type": self.media_type,
            "file_extension": self.file_extension,
            "size_bytes": self.size_bytes,
        }


class AssetDedupeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)
        self.catalog_path = (self.settings.data_dir / "asset-catalog.json").resolve()
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)

    def resolve(self, *, prompt: str, seed: int = 0) -> ManagedArtifactDescriptor | None:
        catalog = self._load_catalog()
        cache_key = self._cache_key(prompt=prompt, seed=seed)
        payload = catalog.get(cache_key)
        if not isinstance(payload, dict):
            return None
        storage_path = payload.get("storage_path")
        filename = payload.get("filename")
        media_type = payload.get("media_type")
        if not isinstance(storage_path, str) or not isinstance(filename, str) or not isinstance(media_type, str):
            return None
        try:
            absolute_path = self.artifacts.resolve_download_path(storage_path)
        except Exception:
            return None
        return ManagedArtifactDescriptor(
            filename=filename,
            storage_path=storage_path,
            absolute_path=absolute_path,
            media_type=media_type,
            file_extension=str(payload.get("file_extension") or absolute_path.suffix.lower()),
            size_bytes=int(payload.get("size_bytes") or absolute_path.stat().st_size),
        )

    def remember(self, *, prompt: str, descriptor: ManagedArtifactDescriptor, seed: int = 0) -> AssetCatalogEntry:
        catalog = self._load_catalog()
        cache_key = self._cache_key(prompt=prompt, seed=seed)
        entry = AssetCatalogEntry(
            cache_key=cache_key,
            prompt_hash=self._prompt_hash(prompt),
            seed=seed,
            filename=descriptor.filename,
            storage_path=descriptor.storage_path,
            media_type=descriptor.media_type,
            file_extension=descriptor.file_extension,
            size_bytes=descriptor.size_bytes,
        )
        catalog[cache_key] = entry.to_dict()
        self.catalog_path.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return entry

    def _load_catalog(self) -> dict[str, Any]:
        if not self.catalog_path.exists():
            return {}
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return self._migrate_catalog_paths(payload)

    def _migrate_catalog_paths(self, catalog: dict[str, Any]) -> dict[str, Any]:
        """Rebase historical managed artifacts onto the active data directory."""
        changed = False
        for item in catalog.values():
            if not isinstance(item, dict):
                continue
            storage_path = item.get("storage_path")
            if not isinstance(storage_path, str):
                continue
            normalized = storage_path.replace("\\", "/")
            marker = "/data/"
            lower = normalized.casefold()
            if marker not in lower or not (
                lower.startswith("f:/atnossystem/")
                or lower.startswith("f:/apps/atnos/")
            ):
                continue
            relative = normalized[lower.index(marker) + len(marker) :]
            item["storage_path"] = (self.settings.data_dir / relative).resolve().as_posix()
            changed = True
        if changed:
            self.catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return catalog

    @classmethod
    def _cache_key(cls, *, prompt: str, seed: int) -> str:
        return f"{cls._prompt_hash(prompt)}:{seed}"

    @staticmethod
    def _prompt_hash(prompt: str) -> str:
        return sha256(prompt.strip().encode("utf-8")).hexdigest()
