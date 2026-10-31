from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from atnos.pipeline.production_orchestrator import run_production_pipeline  # noqa: E402


TEST_TOPIC = {
    "id": str(uuid4()),
    "title": "3 błędy finansowe które zjadają wypłatę",
    "hook": "Większość ludzi traci pieniądze nie przez zarobki, tylko przez te trzy decyzje.",
    "angle": "Krótka edukacja o prostych błędach w budżecie domowym bez obiecywania zysków.",
    "niche": "finanse osobiste",
    "tags": ["finanse", "budzet", "pieniadze", "edukacja finansowa", "shorts"],
    "estimated_virality": 75,
    "difficulty": "easy",
}


async def main() -> None:
    settings = Settings()
    channel_config = _load_channel_config(settings)
    job_id = f"e2e_test_{uuid4().hex[:8]}"
    print(f"Starting E2E test job: {job_id}")
    result = await run_production_pipeline(job_id, TEST_TOPIC, channel_config, settings=settings)
    output = {
        "job_id": job_id,
        "status": result.status,
        "video_url": result.video_url,
        "video_path": result.video_path,
        "error": result.error,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    logs_dir = API_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "first_real_run.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _load_channel_config(settings: Settings) -> dict[str, object]:
    candidates = [
        settings.data_dir / "intelligence" / "channel_config.json",
        API_ROOT / "backend" / "intelligence" / "channel_config.json",
        API_ROOT / "config" / "channel_config.json",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("account_id", "yt-finanse-pl")
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return {
        "account_id": "yt-finanse-pl",
        "language": "pl",
        "tts_voice": "pl-PL-MarekNeural",
        "visual_style": "cinematic dark, premium AI tools, dramatic lighting",
    }


if __name__ == "__main__":
    asyncio.run(main())
