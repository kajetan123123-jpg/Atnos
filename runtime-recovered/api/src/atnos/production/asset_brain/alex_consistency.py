from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from atnos.core.config import Settings
from atnos.production.asset_brain.fingerprint import file_sha256
from atnos.production.channel_contract import manifest_path


class AlexAnchorError(RuntimeError):
    """Alex character generation cannot proceed without quality anchors."""


@dataclass(frozen=True, slots=True)
class AlexAnchors:
    character_id: str
    reference_files: tuple[Path, ...]
    reference_hashes: dict[str, str]
    silhouette: str
    design_lock: str
    style: str
    expressions: tuple[str, ...]
    production_poses: tuple[str, ...]
    brand_kit_dir: Path | None

    @property
    def ready(self) -> bool:
        return bool(self.reference_files) and bool(self.design_lock.strip())


def load_alex_anchors(settings: Settings | None = None) -> AlexAnchors:
    """Load Wealth Blueprint Alex quality anchors — never invent a new look."""
    references: list[Path] = []
    design_lock = ""
    style = ""
    silhouette = "navy overshirt + cream tee + dark trousers + sneakers + analog watch"
    expressions: tuple[str, ...] = ()
    production_poses: tuple[str, ...] = ()
    brand_kit_dir: Path | None = None
    character_id = "WB_ALEX_01"

    channel_refs: list[Path] = []
    if settings is not None and settings.wealth_blueprint_channel_references_dir is not None:
        refs_root = Path(settings.wealth_blueprint_channel_references_dir)
        if refs_root.is_dir():
            channel_refs.extend(sorted(refs_root.glob("**/*.png")))
            channel_refs.extend(sorted(refs_root.glob("**/*.jpg")))
    if settings is not None and settings.app_root is not None:
        channel_refs.extend(
            [
                Path(settings.app_root) / "config" / "channel_references" / "20260916" / "image-1.png",
                Path(settings.app_root) / "config" / "channel_references" / "20260916" / "image-2.png",
            ]
        )
    for path in channel_refs:
        if path.is_file() and path.stat().st_size > 0:
            references.append(path.resolve())

    if settings is not None:
        source = manifest_path(settings)
        if source is not None:
            try:
                manifest = json.loads(source.read_text(encoding="utf-8"))
                visual = ((manifest.get("projects") or {}).get("wealth_blueprint") or {}).get("visual") or {}
                style = str(visual.get("style") or style)
                bible = visual.get("character_bible") if isinstance(visual.get("character_bible"), dict) else {}
                if bible.get("design"):
                    design_lock = str(bible["design"])
                for raw in visual.get("reference_files") or []:
                    path = Path(str(raw))
                    if path.is_file() and path.stat().st_size > 0:
                        resolved = path.resolve()
                        if resolved not in references:
                            references.append(resolved)
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        kit = Path(settings.data_dir) / "brand-kits" / "wealth-blueprint-alex-01"
        config_kit = Path(__file__).resolve().parents[4] / "config" / "brand-kits" / "wealth-blueprint-alex-01"
        for candidate in (kit, config_kit):
            if candidate.is_dir():
                brand_kit_dir = candidate
                break

    if brand_kit_dir is None:
        config_kit = Path(__file__).resolve().parents[4] / "config" / "brand-kits" / "wealth-blueprint-alex-01"
        if config_kit.is_dir():
            brand_kit_dir = config_kit

    if brand_kit_dir is not None:
        brand_json = brand_kit_dir / "brand.json"
        if brand_json.is_file():
            try:
                brand = json.loads(brand_json.read_text(encoding="utf-8"))
                character_id = str(brand.get("character_id") or character_id)
                if brand.get("character"):
                    design_lock = str(brand["character"])
                if brand.get("style"):
                    style = str(brand["style"])
                ref_name = str(brand.get("reference_file") or "visual-reference.png")
                ref_path = (brand_kit_dir / ref_name).resolve()
                if ref_path.is_file() and ref_path.stat().st_size > 0 and ref_path not in references:
                    references.append(ref_path)
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        look_bank = brand_kit_dir / "training" / "look-bank-manifest.json"
        if look_bank.is_file():
            try:
                look = json.loads(look_bank.read_text(encoding="utf-8"))
                anchors = look.get("anchors") if isinstance(look.get("anchors"), dict) else {}
                character_id = str(anchors.get("character_id") or character_id)
                silhouette = str(anchors.get("silhouette") or silhouette)
                expressions = tuple(str(item) for item in (anchors.get("expressions") or []) if str(item).strip())
                production_poses = tuple(
                    str(item) for item in (anchors.get("production_poses") or []) if str(item).strip()
                )
            except (OSError, json.JSONDecodeError, TypeError):
                pass

    hashes = {str(path): file_sha256(path) for path in references}
    return AlexAnchors(
        character_id=character_id,
        reference_files=tuple(references),
        reference_hashes=hashes,
        silhouette=silhouette,
        design_lock=design_lock
        or (
            "adult man with dark swept hair, navy overshirt and light shirt, "
            "realistic hand-drawn editorial proportions — identity locked to WB_ALEX_01 references"
        ),
        style=style
        or "premium hand-drawn editorial blueprint, subtle 2.5D, paper texture, restrained gold",
        expressions=expressions,
        production_poses=production_poses,
        brand_kit_dir=brand_kit_dir,
    )


def require_alex_anchors(settings: Settings | None = None) -> AlexAnchors:
    anchors = load_alex_anchors(settings)
    if not anchors.reference_files:
        raise AlexAnchorError(
            "Alex asset generation requires quality reference files "
            "(channel_references / brand-kit visual-reference). Do not invent Alex from scratch."
        )
    return anchors
