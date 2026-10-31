from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.faceless_shorts_next_session_planner import FacelessShortsNextSessionPlanner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan the next faceless Shorts watch session from format memory.")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--output-config", type=Path, default=None)
    args = parser.parse_args()

    plan = FacelessShortsNextSessionPlanner(Settings()).write(
        max_items=args.max_items,
        config_path=args.output_config,
    )
    print(
        json.dumps(
            {
                "status": plan.status,
                "selected_pattern": plan.selected_pattern,
                "selected_channel": plan.selected_channel,
                "queries": list(plan.queries),
                "max_items": plan.max_items,
                "plan_path": plan.plan_path,
                "report_path": plan.report_path,
                "config_path": plan.config_path,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
