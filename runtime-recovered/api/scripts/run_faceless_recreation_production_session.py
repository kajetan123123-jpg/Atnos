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
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES  # noqa: E402
from content.faceless_recreation_production_session import FacelessRecreationProductionSessionService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run full faceless Shorts public-learning to local-production recreation session."
    )
    parser.add_argument("--benchmark-json", type=Path, default=None)
    parser.add_argument("--reference-observation-json", type=Path, default=None)
    parser.add_argument("--source-short-dir", type=Path, default=None)
    parser.add_argument("--visual-plan-json", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--query", action="append", default=None)
    parser.add_argument("--per-query-limit", type=int, default=8)
    parser.add_argument("--top-limit", type=int, default=12)
    parser.add_argument("--brief-limit", type=int, default=1)
    parser.add_argument("--sample-times", default="0.5,2,5,10,20,35")
    parser.add_argument("--keep-public-frames", action="store_true")
    parser.add_argument("--allow-duration-anomalies", action="store_true")
    parser.add_argument("--max-scenes", type=int, default=5)
    parser.add_argument("--plan-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=1344)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--prompt-only", action="store_true")
    parser.add_argument("--no-render-video", action="store_true")
    args = parser.parse_args()

    sample_times = tuple(float(item.strip()) for item in args.sample_times.split(",") if item.strip())
    result = FacelessRecreationProductionSessionService(Settings()).run(
        benchmark_json=args.benchmark_json,
        reference_observation_json=args.reference_observation_json,
        source_short_dir=args.source_short_dir,
        visual_plan_json=args.visual_plan_json,
        output_dir=args.output_dir,
        queries=tuple(args.query) if args.query else DEFAULT_FACELESS_AI_QUERIES,
        per_query_limit=args.per_query_limit,
        top_limit=args.top_limit,
        brief_limit=args.brief_limit,
        sample_times_sec=sample_times,
        keep_public_frames=args.keep_public_frames,
        allow_duration_anomalies=args.allow_duration_anomalies,
        max_scenes=args.max_scenes,
        plan_index=args.plan_index,
        width=args.width,
        height=args.height,
        steps=args.steps,
        generate_images=not args.prompt_only,
        render_video=not args.no_render_video,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "ready_for_owner_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
