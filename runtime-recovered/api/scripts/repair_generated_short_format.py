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
from content.format_recreation_repair import FormatRecreationRepairService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a visual repair pass from format recreation review.")
    parser.add_argument("--source-short-dir", required=True, type=Path)
    parser.add_argument("--review-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--reference-observation-json", type=Path, default=None)
    args = parser.parse_args()

    result = FormatRecreationRepairService(Settings()).run(
        source_short_dir=args.source_short_dir,
        review_json_path=args.review_json,
        output_dir=args.output_dir,
        reference_observation_json=args.reference_observation_json,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status != "repaired":
        return 2
    if result.followup_decision == "reject":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
