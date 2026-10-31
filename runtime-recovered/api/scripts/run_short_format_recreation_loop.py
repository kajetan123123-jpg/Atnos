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
from content.format_recreation_loop import FormatRecreationLoopService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run review -> repair-if-needed -> follow-up review against a public Shorts format observation."
    )
    parser.add_argument("--source-short-dir", required=True, type=Path)
    parser.add_argument("--reference-observation-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--candidate-video", type=Path, default=None)
    parser.add_argument("--sample-times", default="0.5,2,5,10,20,35")
    args = parser.parse_args()

    sample_times = tuple(float(item.strip()) for item in str(args.sample_times).split(",") if item.strip())
    result = FormatRecreationLoopService(Settings()).run(
        source_short_dir=args.source_short_dir,
        reference_observation_json=args.reference_observation_json,
        output_dir=args.output_dir,
        candidate_video_path=args.candidate_video,
        sample_times_sec=sample_times,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status in {"approved_without_repair", "repaired_approved"}:
        return 0
    if result.status in {"repaired_still_needs_review", "repaired_needs_human_review"}:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
