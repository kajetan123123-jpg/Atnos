from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import get_settings  # noqa: E402
from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE  # noqa: E402
from atnos.production.steps.video_generator import _prepare_wealth_image_candidates  # noqa: E402
from atnos.services.hardware_guard_service import HardwareGuardService  # noqa: E402


DEFAULT_TOPIC = "The Coffee Habit That Quietly Drains Your Wallet"
DEFAULT_SCENE = {
    "segment_type": "body",
    "headline": "THE LATTE TRAP",
    "narration": (
        "Five takeaway coffees every week can quietly consume more than a thousand dollars a year."
    ),
    "visual_prompt": (
        "A realistic open leather wallet beside exactly five plain takeaway coffee cups arranged in one clear row, "
        "with exactly one gold coin beside each cup, photographed from an overhead three-quarter angle on a dark "
        "charcoal desk, mature premium personal-finance documentary photography, no logos, no labels, no text."
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the production Wealth Blueprint three-candidate image selection gate."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    return parser.parse_args()


async def _run(output_dir: Path, topic: str) -> dict[str, object]:
    settings = get_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    gpu_release = await asyncio.to_thread(
        HardwareGuardService(None, settings).release_gpu_pressure  # type: ignore[arg-type]
    )
    selections = await _prepare_wealth_image_candidates(
        settings,
        [dict(DEFAULT_SCENE)],
        video_dir=output_dir,
        topic_title=topic,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )
    selection = selections.get(1) or {}
    return {
        "status": str(selection.get("status") or "failed"),
        "generated_at": datetime.now(UTC).isoformat(),
        "topic": topic,
        "scene": DEFAULT_SCENE,
        "gpu_release": gpu_release,
        "selection": selection,
    }


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    result = asyncio.run(_run(output_dir, str(args.topic)))
    evidence_path = output_dir / "selection.json"
    evidence_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "evidence": str(evidence_path),
                "selected_image_path": (result.get("selection") or {}).get("selected_image_path", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "selected" else 2


if __name__ == "__main__":
    raise SystemExit(main())
