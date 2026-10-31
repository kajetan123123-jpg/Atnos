"""Independent Blender-side consumer stub — reads only JSON manifests + files.

Does not import Atnos production internals beyond stdlib.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def consume_wonder_crew_character(character_id: str = "niko") -> dict:
    registry_path = Path(r"E:\Atnos\App\config\wonder_crew_3d_handoff\canonical_characters.json")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entry = (registry.get("characters") or {}).get(character_id.lower())
    if not entry or not entry.get("canonical"):
        raise SystemExit(f"No canonical package for {character_id}")
    manifest_path = Path(entry["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent

    resolved = {}
    missing = []
    views = manifest.get("canonical_views") or {}
    for name, asset in views.items():
        if not asset:
            missing.append(f"canonical_views.{name}")
            continue
        path = base / asset["relative_path"]
        if not path.is_file():
            missing.append(str(path))
        else:
            resolved[name] = str(path)

    for label, asset in (
        ("expressions", manifest.get("expressions")),
        ("color_reference", manifest.get("color_reference")),
        ("costume", manifest.get("costume")),
    ):
        if not asset:
            missing.append(label)
            continue
        path = base / asset["relative_path"]
        if not path.is_file():
            missing.append(str(path))
        else:
            resolved[label] = str(path)

    props = []
    for asset in manifest.get("associated_props") or []:
        path = base / asset["relative_path"]
        if not path.is_file():
            missing.append(str(path))
        else:
            props.append(str(path))
    resolved["associated_props"] = props

    return {
        "character_id": character_id,
        "canonical_package_id": entry["canonical_package_id"],
        "manifest_path": str(manifest_path),
        "resolved": resolved,
        "missing": missing,
        "ok": len(missing) == 0,
        "asset_count": len(manifest.get("assets_index") or []),
    }


if __name__ == "__main__":
    result = consume_wonder_crew_character(sys.argv[1] if len(sys.argv) > 1 else "niko")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
