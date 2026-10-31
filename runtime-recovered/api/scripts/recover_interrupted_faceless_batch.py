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
from content.faceless_recreation_batch_recovery import FacelessRecreationBatchRecoveryService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover an interrupted faceless recreation batch.")
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--benchmark-json", type=Path, default=None)
    parser.add_argument("--visual-plan-json", type=Path, default=None)
    parser.add_argument("--reference-observation-json", type=Path, default=None)
    parser.add_argument("--no-memory-update", action="store_true")
    args = parser.parse_args()

    result = FacelessRecreationBatchRecoveryService(Settings()).recover(
        batch_dir=args.batch_dir,
        benchmark_json=args.benchmark_json,
        visual_plan_json=args.visual_plan_json,
        reference_observation_summary_json=args.reference_observation_json,
        update_memory=not args.no_memory_update,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))
    return 0 if result.recovered_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
