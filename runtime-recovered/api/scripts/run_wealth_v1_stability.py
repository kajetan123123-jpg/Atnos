from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from atnos.core.config import get_settings, load_runtime_env_files
from atnos.db.session import DatabaseManager
from atnos.production.stability_runner import run_stability_series
from atnos.services.runtime_settings_service import RuntimeSettingsService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the signed Wealth Blueprint V1 10/10 stability gate.")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--topic-id", action="append", required=True)
    parser.add_argument(
        "--resume-report",
        type=Path,
        help="Resume a cryptographically verified interrupted stability_report.json.",
    )
    args = parser.parse_args()
    load_runtime_env_files()
    base_settings = get_settings()
    database = DatabaseManager(base_settings)
    database.initialize()
    with database.session_scope() as session:
        settings = RuntimeSettingsService(session, base_settings).build_effective_settings()
    report = asyncio.run(
        run_stability_series(
            settings,
            account_id=args.account_id,
            topic_ids=args.topic_id,
            resume_report_path=args.resume_report,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
