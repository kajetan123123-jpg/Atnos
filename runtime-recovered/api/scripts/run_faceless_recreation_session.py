from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES, FacelessShortsBenchmarkService  # noqa: E402
from content.format_recreation_loop import FormatRecreationLoopService  # noqa: E402
from content.public_short_frame_observer import PublicShortFrameObservationRunner  # noqa: E402
from content.short_agent import ShortAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a full faceless Shorts recreation session: public benchmark, "
            "public-player observation, local render and format review/repair loop."
        )
    )
    parser.add_argument("--benchmark-json", type=Path, default=None, help="Reuse an existing benchmark_run.json.")
    parser.add_argument("--reference-observation-json", type=Path, default=None, help="Reuse an existing public observation summary.")
    parser.add_argument("--source-short-dir", type=Path, default=None, help="Reuse an existing generated short directory.")
    parser.add_argument("--query", action="append", default=None, help="Benchmark query. Can be passed multiple times.")
    parser.add_argument("--per-query-limit", type=int, default=6)
    parser.add_argument("--top-limit", type=int, default=10)
    parser.add_argument("--brief-limit", type=int, default=1)
    parser.add_argument(
        "--allow-duration-anomalies",
        action="store_true",
        help="Allow long videos with Shorts hashtags as weak references. Off by default.",
    )
    parser.add_argument("--keep-public-frames", action="store_true")
    parser.add_argument("--sample-times", default="0.5,2,5,10,20,35")
    args = parser.parse_args()

    settings = Settings()
    created_at = datetime.now(UTC)
    session_dir = settings.data_dir / "learning" / "faceless-ai-shorts" / (
        f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_faceless_recreation_session"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    sample_times = tuple(float(item.strip()) for item in str(args.sample_times).split(",") if item.strip())

    result: dict[str, Any] = {
        "status": "started",
        "created_at": created_at.isoformat(),
        "session_dir": str(session_dir),
        "steps": [],
        "safety": [
            "Learn and recreate format mechanics only.",
            "Do not copy source footage, characters, voice, watermark, branding or script.",
            "Do not upload publicly from this session.",
        ],
    }

    try:
        benchmark_json = args.benchmark_json.resolve() if args.benchmark_json else _run_benchmark(settings, args, result)
        benchmark_payload = json.loads(benchmark_json.read_text(encoding="utf-8"))
        result["benchmark_json"] = str(benchmark_json)

        reference_json = (
            args.reference_observation_json.resolve()
            if args.reference_observation_json
            else _observe_top_public_short(settings, benchmark_json, session_dir, args, result)
        )
        result["reference_observation_json"] = str(reference_json)

        source_short_dir = (
            args.source_short_dir.resolve()
            if args.source_short_dir
            else _render_replication_sample(settings, benchmark_payload, result)
        )
        result["source_short_dir"] = str(source_short_dir)

        loop_result = FormatRecreationLoopService(settings).run(
            source_short_dir=source_short_dir,
            reference_observation_json=reference_json,
            output_dir=session_dir / "format_recreation_loop",
            sample_times_sec=sample_times,
        )
        result["steps"].append(
            {
                "step": "format_recreation_loop",
                "status": loop_result.status,
                "final_decision": loop_result.final_decision,
                "final_score": loop_result.final_score,
                "final_video_path": loop_result.final_video_path,
                "result_path": str(session_dir / "format_recreation_loop" / "format_recreation_loop_result.json"),
            }
        )
        result["format_recreation_loop"] = loop_result.to_dict()
        result["status"] = (
            "ready_for_owner_review"
            if loop_result.status in {"approved_without_repair", "repaired_approved"}
            else "needs_human_review"
        )
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"

    result_path = session_dir / "faceless_recreation_session_result.json"
    report_path = session_dir / "faceless_recreation_session_report.md"
    result["result_path"] = str(result_path)
    result["report_path"] = str(report_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report_path.write_text(_render_report(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "ready_for_owner_review" else 1


def _run_benchmark(settings: Settings, args: argparse.Namespace, result: dict[str, Any]) -> Path:
    queries = tuple(args.query) if args.query else DEFAULT_FACELESS_AI_QUERIES
    benchmark = FacelessShortsBenchmarkService(settings).run(
        queries=queries,
        per_query_limit=max(1, int(args.per_query_limit)),
        top_limit=max(1, int(args.top_limit)),
        brief_limit=max(1, int(args.brief_limit)),
        allow_duration_anomalies=bool(args.allow_duration_anomalies),
    )
    result["steps"].append(
        {
            "step": "benchmark",
            "status": "completed",
            "candidate_count": benchmark.candidate_count,
            "top_candidates": [candidate.to_dict() for candidate in benchmark.top_candidates[:3]],
            "json_path": benchmark.json_path,
            "markdown_path": benchmark.markdown_path,
        }
    )
    return Path(benchmark.json_path)


def _observe_top_public_short(
    settings: Settings,
    benchmark_json: Path,
    session_dir: Path,
    args: argparse.Namespace,
    result: dict[str, Any],
) -> Path:
    observation_dir = session_dir / "public_player_observation"
    observations = PublicShortFrameObservationRunner(settings).observe_benchmark(
        benchmark_json_path=benchmark_json,
        output_dir=observation_dir,
        top_limit=1,
        sample_times_sec=tuple(float(item.strip()) for item in str(args.sample_times).split(",") if item.strip()),
        keep_frames=bool(args.keep_public_frames),
    )
    summary = observation_dir / "public_player_observation_summary.json"
    result["steps"].append(
        {
            "step": "public_player_observation",
            "status": observations[0].status if observations else "no_observation",
            "source_video_id": observations[0].video_id if observations else None,
            "source_title": observations[0].title if observations else None,
            "extracted_frame_count": observations[0].extracted_frame_count if observations else 0,
            "summary_path": str(summary),
        }
    )
    return summary


def _render_replication_sample(settings: Settings, benchmark_payload: dict[str, Any], result: dict[str, Any]) -> Path:
    briefs = benchmark_payload.get("replication_briefs") if isinstance(benchmark_payload, dict) else None
    candidates = benchmark_payload.get("top_candidates") if isinstance(benchmark_payload, dict) else None
    topic = None
    if isinstance(briefs, list) and briefs and isinstance(briefs[0], dict):
        topic = str(briefs[0].get("legal_recreation_topic") or "").strip()
    if not topic and isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        title = str(candidates[0].get("title") or "top faceless AI short").strip()
        topic = f"Odtwórz bezpiecznie mechanikę formatu: {title}"
    if not topic:
        raise RuntimeError("Benchmark contains no replication brief or top candidate to render.")

    short = ShortAgent(settings).run(topic, force_repair=True)
    result["steps"].append(
        {
            "step": "local_render",
            "status": short.status,
            "topic": short.topic,
            "output_dir": str(short.output_dir),
            "final_mp4": str(short.final_mp4) if short.final_mp4 else None,
            "quality_report": short.quality_report,
        }
    )
    if short.status != "manual_upload_ready" or short.final_mp4 is None:
        raise RuntimeError(f"Short render did not produce a ready MP4. Status: {short.status}")
    return short.output_dir


def _render_report(result: dict[str, Any]) -> str:
    steps = "\n".join(
        f"- {item.get('step')}: {item.get('status')}" for item in result.get("steps", []) if isinstance(item, dict)
    )
    loop = result.get("format_recreation_loop") if isinstance(result.get("format_recreation_loop"), dict) else {}
    return "\n".join(
        [
            "# Faceless Recreation Session",
            "",
            f"- status: {result.get('status')}",
            f"- session_dir: {result.get('session_dir')}",
            f"- benchmark_json: {result.get('benchmark_json', 'n/a')}",
            f"- reference_observation_json: {result.get('reference_observation_json', 'n/a')}",
            f"- source_short_dir: {result.get('source_short_dir', 'n/a')}",
            f"- final_video: {loop.get('final_video_path', 'n/a')}",
            f"- final_decision: {loop.get('final_decision', 'n/a')}",
            f"- final_score: {loop.get('final_score', 'n/a')}",
            "",
            "## Steps",
            steps or "- none",
            "",
            "## Safety",
            "- Learn and recreate format mechanics only.",
            "- Do not copy source footage, characters, voice, watermark, branding or script.",
            "- Public upload is not part of this session.",
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
