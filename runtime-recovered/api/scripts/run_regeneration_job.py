from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from atnos.core.config import Settings  # noqa: E402
from atnos.production.regeneration import prepare_regeneration_job  # noqa: E402


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def _run(source_job_id: str, reason: str) -> dict[str, Any]:
    settings = Settings()
    events: list[dict[str, Any]] = []

    def event_sink(event: dict[str, Any]) -> None:
        events.append({"at": _stamp(), **event})

    plan = await prepare_regeneration_job(
        settings,
        source_job_id=source_job_id,
        reason=reason,
        event_sink=event_sink,
    )
    result = await plan.orchestrator.run_job(plan.context)
    payload = {
        "status": "completed",
        "source_job_id": source_job_id,
        "new_job_id": plan.context.job_id,
        "reason": reason,
        "result": result,
        "events": events[-200:],
    }
    reports = Path(settings.data_dir) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"regeneration_{source_job_id}_{plan.context.job_id}_{_stamp()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["report_path"] = str(path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Atnos production regeneration job.")
    parser.add_argument("source_job_id")
    parser.add_argument("--reason", default="manual_quality_recovery")
    args = parser.parse_args()
    payload = asyncio.run(_run(args.source_job_id, args.reason))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
