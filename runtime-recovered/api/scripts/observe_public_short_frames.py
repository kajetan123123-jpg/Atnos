from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.public_short_frame_observer import PublicShortFrameObservationRunner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe public Shorts frames for analysis-only learning.")
    parser.add_argument("--url", help="Public YouTube/Shorts URL to observe.")
    parser.add_argument("--benchmark-json", help="Benchmark JSON with top_candidates to observe.")
    parser.add_argument("--top", type=int, default=1, help="How many benchmark candidates to observe.")
    parser.add_argument("--sample-times", default="0.5,2,5,10,20", help="Comma-separated timestamps in seconds.")
    parser.add_argument("--output-dir", help="Output directory. Defaults under data/learning/public-player-observations.")
    parser.add_argument("--keep-frames", action="store_true", help="Keep raw analytical frames. Default deletes them.")
    args = parser.parse_args()

    if not args.url and not args.benchmark_json:
        parser.error("Pass --url or --benchmark-json.")

    settings = Settings()
    sample_times = tuple(float(item.strip()) for item in args.sample_times.split(",") if item.strip())
    runner = PublicShortFrameObservationRunner(settings)

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_dir = settings.data_dir / "learning" / "public-player-observations" / stamp

    if args.benchmark_json:
        results = runner.observe_benchmark(
            benchmark_json_path=args.benchmark_json,
            output_dir=output_dir,
            top_limit=args.top,
            sample_times_sec=sample_times,
            keep_frames=bool(args.keep_frames),
        )
        payload = {
            "mode": "benchmark",
            "benchmark_json": str(Path(args.benchmark_json).resolve()),
            "output_dir": str(output_dir.resolve()),
            "count": len(results),
            "results": [result.to_dict() for result in results],
        }
    else:
        result = runner.observe_url(
            url=str(args.url),
            output_dir=output_dir,
            sample_times_sec=sample_times,
            keep_frames=bool(args.keep_frames),
        )
        payload = {
            "mode": "url",
            "output_dir": str(output_dir.resolve()),
            "result": result.to_dict(),
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
