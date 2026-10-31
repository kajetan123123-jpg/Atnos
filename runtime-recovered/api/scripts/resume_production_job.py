from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from atnos.core.config import get_settings
from atnos.db.session import DatabaseManager
from atnos.production.job_resume import resume_video_job
from atnos.services.runtime_settings_service import RuntimeSettingsService


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume an interrupted production job from its video checkpoint.")
    parser.add_argument("job_id")
    return parser.parse_args()


async def _run(job_id: str) -> dict[str, Any]:
    base_settings = get_settings()
    database = DatabaseManager(base_settings)
    database.initialize()
    with database.session_scope() as session:
        settings = RuntimeSettingsService(session, base_settings).build_effective_settings()

    async def emit(event: dict[str, Any]) -> None:
        print(json.dumps(event, ensure_ascii=False), flush=True)

    return await resume_video_job(settings, job_id, event_sink=emit)


def main() -> int:
    result = asyncio.run(_run(_args().job_id))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if str(result.get("status") or "") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
