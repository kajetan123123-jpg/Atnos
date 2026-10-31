"""Correct false WC_NIKO_V001 READY_FOR_3D — keep history, mark limitations."""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")

from atnos.production.wonder_crew_3d_handoff import mark_existing_niko_v001_limitations


def main() -> int:
    summary = mark_existing_niko_v001_limitations()
    print(json.dumps(summary, indent=2))
    v = summary["validation"]
    ok = (
        not v["ready_for_3d"]
        and not summary["canonical"]
        and summary["status"] == "NOT_READY"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
