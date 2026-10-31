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
from content.faceless_recreation_batch_session import FacelessRecreationBatchSessionService  # noqa: E402
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run batch faceless Shorts learning/recreation for the highest-view formats."
    )
    parser.add_argument("--benchmark-json", type=Path, default=None)
    parser.add_argument("--reference-observation-json", type=Path, default=None)
    parser.add_argument("--visual-plan-json", type=Path, default=None)
    parser.add_argument("--source-short-dir", type=Path, default=None, help="Only reused when --max-items=1.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--query", action="append", default=None)
    parser.add_argument("--max-items", type=int, default=3)
    parser.add_argument("--per-query-limit", type=int, default=8)
    parser.add_argument("--top-limit", type=int, default=12)
    parser.add_argument("--brief-limit", type=int, default=3)
    parser.add_argument("--sample-times", default="0.5,2,5,10,20,35")
    parser.add_argument("--keep-public-frames", action="store_true")
    parser.add_argument("--allow-duration-anomalies", action="store_true")
    parser.add_argument("--max-scenes", type=int, default=5)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=1344)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--prompt-only", action="store_true")
    parser.add_argument("--no-render-video", action="store_true")
    args = parser.parse_args()

    sample_times = tuple(float(item.strip()) for item in args.sample_times.split(",") if item.strip())
    result = FacelessRecreationBatchSessionService(Settings()).run(
        benchmark_json=args.benchmark_json,
        reference_observation_json=args.reference_observation_json,
        visual_plan_json=args.visual_plan_json,
        source_short_dir=args.source_short_dir,
        output_dir=args.output_dir,
        queries=tuple(args.query) if args.query else DEFAULT_FACELESS_AI_QUERIES,
        max_items=args.max_items,
        per_query_limit=args.per_query_limit,
        top_limit=args.top_limit,
        brief_limit=args.brief_limit,
        sample_times_sec=sample_times,
        keep_public_frames=args.keep_public_frames,
        allow_duration_anomalies=args.allow_duration_anomalies,
        max_scenes=args.max_scenes,
        width=args.width,
        height=args.height,
        steps=args.steps,
        generate_images=not args.prompt_only,
        render_video=not args.no_render_video,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ready_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
