"""Run one local Wealth Blueprint visual-plan smoke through Atnos itself."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.llm.role_registry import ModelRole, OllamaRoleRegistry
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import visual_planner


OUTPUT_DIR = Path("E:/Atnos/App/reports/llm-system-20260927/visual-plan-live-20260928")


def main() -> int:
    if OUTPUT_DIR.anchor.upper() != "E:\\":
        raise RuntimeError("Visual plan smoke must stay on E:")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    lines = [
        "A raise increases income, but it does not automatically build wealth.",
        "A new recurring expense can absorb the extra cash each month.",
        "Choose a specific amount for a savings goal before adding a new bill.",
        "What remains is available for everyday choices.",
    ]
    context = ProductionContext(
        job_id="wb-llm-role-smoke-20260928",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wb-raise-allocation-smoke",
            account_id="yt-wealth-blueprint",
            title="Where a Pay Raise Goes",
            description="A hypothetical non-numeric example of recurring costs and saving.",
            total_score=80,
        ),
        artifact_dir=OUTPUT_DIR,
        artifacts={
            "script": {
                "body": lines,
                "segments": [
                    {"id": f"s{index:02d}", "type": "body", "text": line}
                    for index, line in enumerate(lines, start=1)
                ],
            }
        },
    )
    result = asyncio.run(visual_planner.run(settings, context))
    payload = result.payload
    summary = {
        "requested_model": OllamaRoleRegistry(settings).resolve(ModelRole.VIDEO_EDIT_PLANNER).model,
        "llm_unavailable": payload.get("llm_unavailable"),
        "visual_style": payload.get("visual_style"),
        "scene_count": len(payload.get("scenes") or []),
        "scene_profiles": [scene.get("channel_profile_key") for scene in payload.get("scenes") or []],
        "scene_types": [
            scene.get("renderer_scene_type") or scene.get("segment_type")
            for scene in payload.get("scenes") or []
        ],
        "artifact_path": str(result.artifact_path),
    }
    (OUTPUT_DIR / "smoke-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if (
        payload.get("llm_unavailable") is False
        and summary["scene_count"]
        and "hand-drawn" in str(summary["visual_style"]).casefold()
        and all(key == "wealth_blueprint" for key in summary["scene_profiles"])
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
