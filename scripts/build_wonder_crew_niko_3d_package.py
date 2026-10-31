"""Build Wonder Crew Niko structure fixture (no Blender; no production approval)."""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")

from atnos.production.wonder_crew_3d_handoff import build_niko_test_package


def main() -> int:
    # Disposable fixture only — never auto-approve / never auto-canonical.
    result = build_niko_test_package(
        version=1,
        set_as_canonical=False,
        packages_root_override=None,
        overwrite=False,
        grant_production_approval=False,
    )
    print(json.dumps(result, indent=2))
    # Success = structure fixture built without falsely claiming READY_FOR_3D
    ok = result["validation"]["structure_ok"] and not result["validation"]["ready_for_3d"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
