"""Audit the one WB coin candidate without approving or importing it."""

from __future__ import annotations

import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.visual_semantic_audit import audit_visual_contract_compliance, audit_visual_semantics
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService


REPORT = Path("E:/Atnos/App/reports/llm-system-20260927/wb-coin-layer-20260929.json")


def main() -> int:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    image_path = Path(payload["metadata"]["output_path"])
    settings = get_settings()
    ComfyUiImageGenerationService(settings).release_memory()
    literal = audit_visual_contract_compliance(
        settings,
        image_path,
        required_visual_contract=(
            "A single unmarked plain round brass coin with no bird, portrait, symbol, number, or lettering; "
            "the object must be hand-drawn in navy ink and muted gold on cream paper."
        ),
    )
    semantic = audit_visual_semantics(
        settings,
        image_path,
        profile_key="wealth_blueprint",
        topic_title=(
            "Wealth Blueprint reusable animated coin layer | Required visible evidence: one plain unmarked "
            "brass coin, no bird or decoration, hand-drawn ink and painted shading"
        ),
        artifact_kind="image_scene_candidate",
    )
    payload["literal_contract_audit"] = literal
    payload["semantic_audit"] = semantic
    payload["appearance_status"] = "rejected_for_pilot_a" if literal.get("status") == "fail" else "pending_visual_review"
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "literal_status": literal.get("status"),
        "semantic_status": semantic.get("status"),
        "visual_medium": semantic.get("visual_medium"),
        "literal_blockers": literal.get("blockers"),
        "semantic_blockers": semantic.get("blockers"),
    }, ensure_ascii=False))
    return 0 if literal.get("status") in {"pass", "fail"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
