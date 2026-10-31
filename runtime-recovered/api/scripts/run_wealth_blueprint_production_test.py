#!/usr/bin/env python
"""Full Wealth Blueprint Video Engine production test (health + job, no upload).

Exit codes:
  0 — DONE
  2 — BLOCKED_DEPENDENCY
  1 — FAILED / other error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.health import run_wealth_blueprint_video_engine_health
from atnos.production.channels.wealth_blueprint.video_job import (
    BlockedDependencyError,
    DEFAULT_SCRIPT,
    DEFAULT_TOPIC,
    WealthBlueprintVideoJob,
    WealthBlueprintVideoJobError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wealth Blueprint Video Engine production test")
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--script", default=DEFAULT_SCRIPT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument(
        "--require-comfy",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override WEALTH_BLUEPRINT_REQUIRE_COMFY_FOR_MISSING_ASSETS for health matrix",
    )
    parser.add_argument(
        "--require-whisper",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override WEALTH_BLUEPRINT_REQUIRE_WHISPER_ALIGNMENT for health matrix",
    )
    args = parser.parse_args()

    settings = get_settings()
    health = run_wealth_blueprint_video_engine_health(
        settings,
        require_comfy=args.require_comfy,
        require_whisper=args.require_whisper,
    )
    print(json.dumps({"phase": "health", **health}, ensure_ascii=False, indent=2))
    if args.health_only:
        return 0 if health["status"] != "BLOCKED_DEPENDENCY" else 2
    if health["status"] == "BLOCKED_DEPENDENCY":
        print(
            json.dumps(
                {
                    "phase": "job",
                    "status": "BLOCKED_DEPENDENCY",
                    "blocked_dependencies": health.get("blocked_dependencies"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    artifact_root = Path(args.artifact_root) if args.artifact_root else None
    job = WealthBlueprintVideoJob(settings, job_id=args.job_id, artifact_root=artifact_root)
    try:
        state = job.run(topic=args.topic, script=args.script, force=args.force)
    except BlockedDependencyError as exc:
        print(
            json.dumps(
                {
                    "phase": "job",
                    "status": "BLOCKED_DEPENDENCY",
                    "dependency": exc.dependency,
                    "detail": exc.detail,
                    "artifact_dir": str(job.artifact_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except WealthBlueprintVideoJobError as exc:
        print(json.dumps({"phase": "job", "status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    summary = {
        "phase": "job",
        "job_id": state.job_id,
        "status": state.status,
        "final_mp4": state.final_mp4,
        "report_path": state.report_path,
        "artifact_dir": state.artifact_dir,
        "blocked_dependencies": state.blocked_dependencies,
        "upload": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if state.status == "DONE":
        return 0
    if state.status == "BLOCKED_DEPENDENCY":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
