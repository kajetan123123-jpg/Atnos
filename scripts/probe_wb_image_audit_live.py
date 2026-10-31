"""Check an Atnos-generated WB candidate with Atnos' real vision veto."""

from __future__ import annotations

import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.steps.video_generator import _finance_semantic_contract_for_scene
from atnos.production.visual_semantic_audit import audit_visual_contract_compliance
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService


ROOT = Path("E:/Atnos/App/reports/llm-system-20260927/visual-plan-live-20260928")
IMAGE = ROOT / "atnos-wb-expense-dreamshaper-candidate.png"


def main() -> int:
    settings = get_settings()
    plan = json.loads((ROOT / "visual_plan.json").read_text(encoding="utf-8"))
    scene = next(item for item in plan["scenes"] if item.get("script_lines") == [1])
    contract = _finance_semantic_contract_for_scene(scene)
    ComfyUiImageGenerationService(settings).release_memory()
    audit = audit_visual_contract_compliance(settings, IMAGE, required_visual_contract=contract)
    result = {"image": str(IMAGE), "contract": contract, "audit": audit}
    (ROOT / "atnos-wb-expense-dreamshaper-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if audit.get("status") in {"pass", "fail"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
