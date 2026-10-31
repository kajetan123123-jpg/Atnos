"""Exercise Atnos' normal WB image candidate selection for one scene."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE
from atnos.production.steps.video_generator import _prepare_wealth_image_candidates


ROOT = Path("E:/Atnos/App/reports/llm-system-20260927/visual-plan-live-20260928")
OUTPUT = ROOT / "normal-candidate-gate"


def main() -> int:
    if OUTPUT.anchor.upper() != "E:\\":
        raise RuntimeError("Candidate evidence must stay on E:")
    plan = json.loads((ROOT / "visual_plan.json").read_text(encoding="utf-8"))
    scene = next(item for item in plan["scenes"] if item.get("script_lines") == [1])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(
        _prepare_wealth_image_candidates(
            get_settings(),
            [scene],
            video_dir=OUTPUT,
            topic_title="Where a Pay Raise Goes",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
            generation_attempt=1,
        )
    )
    (OUTPUT / "selection.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    selection = result.get(1, {})
    print(json.dumps({
        "status": selection.get("status"),
        "selected_image_path": selection.get("selected_image_path"),
        "candidate_count": selection.get("candidate_count"),
        "reasons": selection.get("reasons"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
