from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from atnos.core.config import get_settings, load_runtime_env_files
from atnos.production.stability_topics import verify_stability_topic_sources


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify primary sources for the Wealth Blueprint V1 stability suite.")
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "wealth_blueprint_v1_stability_topics.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_runtime_env_files()
    settings = get_settings()
    output = args.output or (
        Path(settings.data_dir)
        / "production"
        / "evidence"
        / f"wealth_v1_stability_sources_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report = asyncio.run(verify_stability_topic_sources(args.suite, output_path=output))
    if report.get("passed") is True:
        latest = Path(settings.data_dir) / "production" / "evidence" / "wealth_v1_stability_sources_latest.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{latest.name}.", suffix=".tmp", dir=latest.parent)
        os.close(descriptor)
        try:
            shutil.copy2(output, temporary)
            os.replace(temporary, latest)
        finally:
            Path(temporary).unlink(missing_ok=True)
        report["latest_report_path"] = str(latest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
