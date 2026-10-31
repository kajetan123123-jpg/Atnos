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
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES, FacelessShortsBenchmarkService  # noqa: E402
from content.short_agent import ShortAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Atnos faceless AI Shorts public benchmark.")
    parser.add_argument("--per-query-limit", type=int, default=8)
    parser.add_argument("--top-limit", type=int, default=12)
    parser.add_argument("--brief-limit", type=int, default=3)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--render-top-sample", action="store_true")
    parser.add_argument(
        "--allow-duration-anomalies",
        action="store_true",
        help="Allow long videos with Shorts hashtags as weak references. Off by default.",
    )
    args = parser.parse_args()

    settings = Settings()
    queries = tuple(args.query) if args.query else DEFAULT_FACELESS_AI_QUERIES
    run = FacelessShortsBenchmarkService(settings).run(
        queries=queries,
        per_query_limit=max(1, int(args.per_query_limit)),
        top_limit=max(1, int(args.top_limit)),
        brief_limit=max(1, int(args.brief_limit)),
        allow_duration_anomalies=bool(args.allow_duration_anomalies),
    )
    payload = run.to_dict()

    if args.render_top_sample and run.replication_briefs:
        brief = run.replication_briefs[0]
        topic = f"{brief.recreation_prompt} Nie kopiuj zrodla, tylko mechanike formatu."
        result = ShortAgent(settings).run(topic, force_repair=True)
        payload["render_top_sample"] = {
            "status": result.status,
            "topic": topic,
            "output_dir": str(result.output_dir),
            "final_mp4": str(result.final_mp4) if result.final_mp4 else None,
            "quality_score": _quality_score(result.quality_report),
            "quality_run": result.quality_run,
        }

        run_json = Path(run.json_path)
        current = json.loads(run_json.read_text(encoding="utf-8"))
        current["render_top_sample"] = payload["render_top_sample"]
        run_json.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _quality_score(report: object) -> float | None:
    if isinstance(report, dict):
        value = report.get("overall_score")
    else:
        value = getattr(report, "overall_score", None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
