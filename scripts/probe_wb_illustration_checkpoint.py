"""Probe an illustration checkpoint through Atnos' ComfyUI service on E:."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE
from atnos.production.steps.video_generator import _comfy_scene_image_prompt
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService


ROOT = Path("E:/Atnos/App/reports/llm-system-20260927/visual-plan-live-20260928")
MODEL = "DreamShaperXL_Turbo_V2-SFW.safetensors"
OUTPUT = ROOT / "atnos-wb-expense-dreamshaper-candidate.png"


def main() -> int:
    if ROOT.anchor.upper() != "E:\\":
        raise RuntimeError("Atnos probes must stay on E:")
    plan = json.loads((ROOT / "visual_plan.json").read_text(encoding="utf-8"))
    scene = next(item for item in plan["scenes"] if item.get("script_lines") == [1])
    prompt = _comfy_scene_image_prompt(
        scene, segment=2, total=len(plan["scenes"]), channel_profile=WEALTH_BLUEPRINT_PROFILE
    )
    service = ComfyUiImageGenerationService(get_settings())
    service.release_memory()
    result = service.generate_background(
        prompt=prompt,
        output_path=OUTPUT,
        width=512,
        height=896,
        seed=20260929,
        negative_prompt="photograph, photorealistic, 3D render, text, labels, logos, malformed hand, extra fingers",
        quality_mode=True,
        preferred_checkpoint=MODEL,
        steps_override=6,
        cfg_scale_override=2.0,
    )
    report = {
        "status": "candidate_generated",
        "checkpoint": MODEL,
        "steps": 6,
        "cfg_scale": 2.0,
        "output": str(OUTPUT),
        "bytes": OUTPUT.stat().st_size,
        "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "prompt": prompt,
        "backend": result.backend,
        "evidence_path": str(result.evidence_path),
        "approval": "pending_visual_review",
    }
    (ROOT / "atnos-wb-expense-dreamshaper-candidate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "prompt"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
