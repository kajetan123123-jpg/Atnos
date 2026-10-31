"""Verify that Atnos rejects a photo-like WB candidate after style correction."""

from __future__ import annotations

import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE
from atnos.production.steps.video_generator import _image_candidate_semantic_context
from atnos.production.visual_semantic_audit import audit_visual_semantics


ROOT = Path("E:/Atnos/App/reports/llm-system-20260927/visual-plan-live-20260928")
IMAGE = ROOT / "normal-candidate-gate/s01.candidate-01.png"


def main() -> int:
    plan = json.loads((ROOT / "visual_plan.json").read_text(encoding="utf-8"))
    scene = next(item for item in plan["scenes"] if item.get("script_lines") == [1])
    context = _image_candidate_semantic_context(
        "Where a Pay Raise Goes", scene, channel_profile=WEALTH_BLUEPRINT_PROFILE
    )
    audit = audit_visual_semantics(
        get_settings(),
        IMAGE,
        profile_key="wealth_blueprint",
        topic_title=context,
        artifact_kind="image_scene_candidate",
    )
    result = {"image": str(IMAGE), "scene_context": context, "audit": audit}
    (ROOT / "wb-semantic-medium-audit-20260929.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "status": audit.get("status"),
        "visual_medium": audit.get("visual_medium"),
        "blockers": audit.get("blockers"),
    }, ensure_ascii=False))
    return 0 if audit.get("status") in {"pass", "fail"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
