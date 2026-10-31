from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from atnos.core.config import get_settings
from atnos.db.session import DatabaseManager
from atnos.production.orchestrator import ProductionOrchestrator
from atnos.services.runtime_settings_service import RuntimeSettingsService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one strict Atnos production render.")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--result-path", type=Path, required=True)
    parser.add_argument(
        "--allow-local-sample-voice",
        action="store_true",
        help="Use Kokoro for a private review sample without weakening publication policy.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    base_settings = get_settings()
    database = DatabaseManager(base_settings)
    database.initialize()
    with database.session_scope() as session:
        settings = RuntimeSettingsService(session, base_settings).build_effective_settings()
    if args.allow_local_sample_voice:
        settings.shorts_tts_backend = "kokoro_local"
        settings.shorts_require_premium_voice_before_render = False
    events: list[dict[str, Any]] = []

    async def _event_sink(event: dict[str, Any]) -> None:
        events.append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    result = await ProductionOrchestrator(settings, event_sink=_event_sink).start(
        account_id=args.account_id,
        topic_id=args.topic_id,
    )
    return {"result": result, "events": events}


def main() -> int:
    args = _parse_args()
    args.result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = asyncio.run(_run(args))
    except Exception as exc:
        payload = {"error": f"{type(exc).__name__}: {exc}"}
        args.result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    args.result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["result"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
