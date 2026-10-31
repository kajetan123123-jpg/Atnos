"""Generate one Wealth Blueprint preview using Atnos' normal Comfy image path."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE
from atnos.production.steps.video_generator import _comfy_scene_image_prompt, _generate_comfy_scene_still
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService


OUTPUT_DIR = Path("E:/Atnos/App/reports/llm-system-20260927/visual-plan-live-20260928")


def main() -> int:
    if OUTPUT_DIR.anchor.upper() != "E:\\":
        raise RuntimeError("WB preview must stay on E:")
    plan = json.loads((OUTPUT_DIR / "visual_plan.json").read_text(encoding="utf-8"))
    scene = next(
        item for item in plan["scenes"]
        if item.get("script_lines") == [1] and item.get("channel_profile_key") == "wealth_blueprint"
    )
    prompt = _comfy_scene_image_prompt(
        scene, segment=2, total=len(plan["scenes"]), channel_profile=WEALTH_BLUEPRINT_PROFILE
    )
    image_path = OUTPUT_DIR / "atnos-wb-expense-preview-v2.png"
    errors = asyncio.run(
        _generate_comfy_scene_still(
            ComfyUiImageGenerationService(get_settings()),
            image_path,
            prompt=prompt,
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
            deterministic_seed=20260929,
            max_prompt_attempts=1,
        )
    )
    result = {
        "status": "generated" if image_path.is_file() and not errors else "failed",
        "scene_line": 1,
        "profile": "wealth_blueprint",
        "image_path": str(image_path) if image_path.is_file() else None,
        "image_bytes": image_path.stat().st_size if image_path.is_file() else 0,
        "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path.is_file() else None,
        "prompt": prompt,
        "errors": errors,
    }
    (OUTPUT_DIR / "atnos-wb-expense-preview-v2.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in result.items() if key != "prompt"}, ensure_ascii=False))
    return 0 if result["status"] == "generated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
