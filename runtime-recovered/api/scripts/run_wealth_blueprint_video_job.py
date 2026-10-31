#!/usr/bin/env python
"""Run a resumable Wealth Blueprint video pipeline job (~30s test film, no upload)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.video_job import (
    BlockedDependencyError,
    DEFAULT_SCRIPT,
    DEFAULT_TOPIC,
    WealthBlueprintVideoJob,
    WealthBlueprintVideoJobError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wealth Blueprint video pipeline job")
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--script", default=DEFAULT_SCRIPT)
    parser.add_argument("--force", action="store_true", help="Ignore checkpoints and rerun all stages")
    parser.add_argument("--artifact-root", default=None, help="Override job artifact directory on E:")
    args = parser.parse_args()

    settings = get_settings()
    artifact_root = Path(args.artifact_root) if args.artifact_root else None
    job = WealthBlueprintVideoJob(settings, job_id=args.job_id, artifact_root=artifact_root)
    try:
        state = job.run(topic=args.topic, script=args.script, force=args.force)
    except BlockedDependencyError as exc:
        print(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "status": "BLOCKED_DEPENDENCY",
                    "dependency": exc.dependency,
                    "detail": exc.detail,
                    "artifact_dir": str(job.artifact_dir),
                    "upload": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except WealthBlueprintVideoJobError as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    summary = {
        "job_id": state.job_id,
        "status": state.status,
        "final_mp4": state.final_mp4,
        "report_path": state.report_path,
        "artifact_dir": state.artifact_dir,
        "auto_corrections": state.auto_corrections,
        "blocked_dependencies": state.blocked_dependencies,
        "upload": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    md = Path(state.artifact_dir) / "pipeline_report.md"
    if md.is_file():
        print("\n" + md.read_text(encoding="utf-8"))
    if state.status == "DONE":
        return 0
    if state.status == "BLOCKED_DEPENDENCY":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
