"""Pipeline-generated hook / transfer / raise scenes. Not a hand-edited film."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan
from atnos.production.channels.wealth_blueprint.visual_decision import (
    composition_flags,
    storyboard_blocks_render,
)
from atnos.production.project_manifest import save_project_manifest
from atnos.production.remotion_renderer import render_project_manifest

OUT = Path(r"E:\Atnos\App\data\production\wealth_blueprint\visual_intent_20260924")


def main() -> None:
    segments = [
        NarrationSegment(id="s01", text="Your paycheck can feel bigger.", start=0, end=4),
        NarrationSegment(
            id="s02",
            text="Turn on an automatic weekly transfer so money leaves checking on Friday and lands in savings.",
            start=4,
            end=11,
        ),
        NarrationSegment(
            id="s03",
            text="Keep part of every raise. Spend the rest.",
            start=11,
            end=16,
        ),
    ]
    plan = plan_scenes_deterministic(
        ScenePlannerInput(script=" ".join(segment.text for segment in segments), segments=segments)
    )
    verdict = validate_scene_plan(plan)
    payload = plan.model_dump(mode="json")
    blocked = storyboard_blocks_render(payload)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "scene_plan.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (OUT / "composition_report.json").write_text(
        json.dumps({"ok": verdict.ok, "blocked": blocked, "flags": composition_flags(payload), "issues": [i.model_dump() for i in verdict.issues]}, indent=2),
        encoding="utf-8",
    )
    if blocked or not verdict.ok:
        raise SystemExit(blocked or "scene plan failed validation")
    still = OUT / "placeholder.png"
    Image.new("RGBA", (64, 64), (20, 38, 51, 255)).save(still)
    manifest = scene_plan_to_project_manifest(
        job_id="visual-intent-20260924",
        topic="Keep a slice of the raise and automate Friday savings",
        script=plan.script,
        plan=payload,
        selected_image=still,
        scene_images={scene.scene_id: still for scene in plan.scenes},
        duration_hint=plan.duration_sec,
    )
    path = OUT / "project_manifest.json"
    save_project_manifest(path, manifest)
    result = render_project_manifest(path, OUT / "wealth_blueprint_visual_intent_scenes.mp4", timeout_s=900)
    print(json.dumps({"output": str(OUT / "wealth_blueprint_visual_intent_scenes.mp4"), "render": result, "blocked": blocked}, default=str))


if __name__ == "__main__":
    main()
