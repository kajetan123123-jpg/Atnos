from __future__ import annotations

import hashlib
import random
from pathlib import Path

from atnos.production.sound_design.catalog import catalog_path, default_sounds_root, index_by_event, load_catalog
from atnos.production.sound_design.models import (
    SFX_EVENTS,
    SemanticSoundEvent,
    SoundAssetRecord,
    SoundCatalog,
)


class SoundRegistryError(RuntimeError):
    pass


class SoundAssetRegistry:
    """Semantic event → approved library variant (no hardcoded scene file paths)."""

    def __init__(
        self,
        *,
        catalog: SoundCatalog | None = None,
        catalog_file: Path | None = None,
        library_root: Path | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.library_root = library_root or default_sounds_root()
        self.catalog_file = catalog_file or catalog_path(self.library_root)
        self.catalog = catalog or load_catalog(self.catalog_file)
        self.rng = rng or random.Random()

    def reload(self) -> SoundCatalog:
        self.catalog = load_catalog(self.catalog_file)
        return self.catalog

    def list_for_event(self, event: SemanticSoundEvent, *, approved_only: bool = True) -> list[SoundAssetRecord]:
        rows = index_by_event(self.catalog).get(event, [])
        if approved_only:
            rows = [asset for asset in rows if asset.approved]
        return list(rows)

    def select(
        self,
        event: SemanticSoundEvent,
        *,
        prefer_variant: str | None = None,
        allow_random_variant: bool = True,
        require_approved: bool = True,
        seed: str | None = None,
    ) -> SoundAssetRecord:
        candidates = self.list_for_event(event, approved_only=require_approved)
        if not candidates:
            raise SoundRegistryError(f"No approved sound asset for event '{event}'")

        if prefer_variant:
            preferred = [asset for asset in candidates if asset.variant == prefer_variant]
            if preferred:
                candidates = preferred

        if len(candidates) == 1 or not allow_random_variant:
            return candidates[0]

        picker = random.Random(_seed_int(seed or f"{event}:{prefer_variant or ''}")) if seed else self.rng
        return picker.choice(candidates)

    def resolve_file(self, asset: SoundAssetRecord) -> Path:
        path = Path(asset.file_path)
        if path.is_absolute():
            return path
        return (self.library_root / path).resolve()

    def is_sfx(self, event: str) -> bool:
        return event in SFX_EVENTS


def _seed_int(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)
