"""Permanent character look banks for channel visual identity."""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.production.project_profile import content_hash


KIT_KEYS = (
    "wealth-blueprint-alex-01",
    "archipelag-snow-v1",
)


def config_kits_root() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "brand-kits"


def data_kits_root(settings: Settings) -> Path:
    return (settings.data_dir / "brand-kits").resolve()


def install_kit(settings: Settings, kit_key: str) -> dict[str, Any]:
    if kit_key not in KIT_KEYS:
        raise ValueError(f"Unsupported look-bank kit: {kit_key}")
    source = (config_kits_root() / kit_key).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Missing kit source: {source}")
    target = data_kits_root(settings) / kit_key
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        copied.append(str(rel).replace("\\", "/"))
    manifest = _read_manifest(target)
    look_root = target / "look_bank"
    look_root.mkdir(parents=True, exist_ok=True)
    slots = list(manifest.get("approved_asset_slots") or [])
    for slot in slots:
        slot_path = target / slot
        slot_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "kit_key": kit_key,
        "channel": manifest.get("channel"),
        "installed_at": datetime.now(UTC).isoformat(),
        "source_hash": _tree_hash(source),
        "manifest_hash": content_hash(manifest),
        "copied_files": len(copied),
        "slots_total": len(slots),
        "slots_approved": _count_approved(target, slots),
        "training_films": [film["id"] for film in manifest.get("training_films") or []],
        "status": "ready_for_training",
        "rule": "Only approved look_bank assets may enter public-facing shorts.",
    }
    state_path = target / "look_bank" / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def install_all(settings: Settings) -> list[dict[str, Any]]:
    return [install_kit(settings, key) for key in KIT_KEYS]


def kit_status(settings: Settings, kit_key: str) -> dict[str, Any]:
    target = data_kits_root(settings) / kit_key
    state_path = target / "look_bank" / "state.json"
    if not state_path.is_file():
        return {"kit_key": kit_key, "status": "not_installed"}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    manifest = _read_manifest(target)
    slots = list(manifest.get("approved_asset_slots") or [])
    state["slots_approved"] = _count_approved(target, slots)
    state["slots_total"] = len(slots)
    state["training_films"] = [film["id"] for film in manifest.get("training_films") or []]
    missing = [slot for slot in slots if not (target / slot).is_file() or (target / slot).stat().st_size < 32]
    state["missing_slots"] = missing
    state["ready_for_episode_production"] = len(missing) == 0
    return state


def all_status(settings: Settings) -> dict[str, Any]:
    return {"kits": [kit_status(settings, key) for key in KIT_KEYS]}


def _read_manifest(kit_dir: Path) -> dict[str, Any]:
    path = kit_dir / "training" / "look-bank-manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing look-bank manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _count_approved(kit_dir: Path, slots: list[str]) -> int:
    return sum(1 for slot in slots if (kit_dir / slot).is_file() and (kit_dir / slot).stat().st_size >= 32)


def _tree_hash(root: Path) -> str:
    payload = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root)).replace("\\", "/")
            payload.append({"path": rel, "size": path.stat().st_size})
    return content_hash(payload)
