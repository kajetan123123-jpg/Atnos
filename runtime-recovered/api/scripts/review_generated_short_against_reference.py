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
from content.format_recreation_review import FormatRecreationReviewService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a generated short against public/player frame observations."
    )
    parser.add_argument("--reference-observation-json", required=True, type=Path)
    parser.add_argument("--candidate-video", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sample-times", default="0.5,2,5,10,20,35")
    args = parser.parse_args()

    sample_times = tuple(
        float(item.strip()) for item in str(args.sample_times).split(",") if item.strip()
    )
    review = FormatRecreationReviewService(Settings()).review_from_paths(
        reference_observation_json=args.reference_observation_json,
        candidate_video_path=args.candidate_video,
        output_dir=args.output_dir,
        sample_times_sec=sample_times,
    )
    print(json.dumps(review.to_dict(), ensure_ascii=True, indent=2))
    return 0 if review.decision in {"approve_mechanics", "repair"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
