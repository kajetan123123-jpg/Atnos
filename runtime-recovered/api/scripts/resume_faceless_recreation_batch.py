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


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume an interrupted faceless recreation batch.")
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--max-items", type=int, required=True)
    parser.add_argument("--benchmark-json", type=Path, default=None)
    parser.add_argument("--visual-plan-json", type=Path, default=None)
    parser.add_argument("--reference-observation-json", type=Path, default=None)
    parser.add_argument("--prompt-only", action="store_true")
    parser.add_argument("--no-render-video", action="store_true")
    args = parser.parse_args()

    batch_dir = args.batch_dir.resolve()
    benchmark_json = args.benchmark_json.resolve() if args.benchmark_json else _infer_benchmark_path(batch_dir)
    visual_plan_json = args.visual_plan_json.resolve() if args.visual_plan_json else _infer_visual_plan_path(benchmark_json)
    reference_json = (
        args.reference_observation_json.resolve()
        if args.reference_observation_json
        else _infer_reference_summary_path(batch_dir)
    )

    result = FacelessRecreationBatchSessionService(Settings()).run(
        benchmark_json=benchmark_json,
        visual_plan_json=visual_plan_json,
        reference_observation_json=reference_json,
        output_dir=batch_dir,
        max_items=args.max_items,
        resume_existing=True,
        generate_images=False if args.prompt_only else True,
        render_video=False if args.no_render_video else True,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))
    return 0 if result.ready_count else 1


def _infer_benchmark_path(batch_dir: Path) -> Path:
    run_dir = batch_dir.parent
    if run_dir.name.endswith("_faceless_watch_session"):
        benchmark_dir = run_dir.parent / run_dir.name.replace(
            "_faceless_watch_session",
            "_faceless_ai_shorts_benchmark",
        )
        benchmark = benchmark_dir / "benchmark_run.json"
        if benchmark.exists():
            return benchmark.resolve()
    raise FileNotFoundError(f"Cannot infer benchmark JSON from batch dir: {batch_dir}")


def _infer_visual_plan_path(benchmark_json: Path) -> Path:
    payload = json.loads(benchmark_json.read_text(encoding="utf-8-sig"))
    explicit = str(payload.get("visual_recreation_plans_path") or "").strip()
    if explicit and Path(explicit).exists():
        return Path(explicit).resolve()
    fallback = benchmark_json.parent / "visual_recreation_plans.json"
    if fallback.exists():
        return fallback.resolve()
    raise FileNotFoundError(f"Cannot infer visual plan JSON from benchmark: {benchmark_json}")


def _infer_reference_summary_path(batch_dir: Path) -> Path:
    reference = batch_dir / "public_player_observations" / "public_player_observation_summary.json"
    if reference.exists():
        return reference.resolve()
    raise FileNotFoundError(f"Cannot infer reference observation JSON from batch dir: {batch_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
