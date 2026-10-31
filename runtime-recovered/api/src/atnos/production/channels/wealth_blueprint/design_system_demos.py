"""Build offline ProjectManifest demos — one scene per wealth_blueprint_style_v1 layout."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from atnos.production.channels.wealth_blueprint.design_system import (
    brand_tokens_for_manifest,
    load_wealth_blueprint_style_v1,
)
from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type
from atnos.production.project_manifest import (
    AssetProvenance,
    BrandSpec,
    ProjectManifest,
    QAAction,
    QAResult,
    SceneAsset,
    SceneManifest,
    SceneType,
    TimeRange,
    deterministic_hash,
    file_sha256,
    save_project_manifest,
)
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION

DEMO_COPY: dict[str, dict[str, Any]] = {
    "character_left_text_right": {"headline": "Keep more of your raise"},
    "character_right_text_left": {"headline": "Save first. Spend second."},
    "fullscreen_statement": {"headline": "Wealth is what you keep."},
    "big_number": {"headline": "Monthly automated transfer", "number": "500"},
    "chart_focus": {"headline": "Compound over time", "chart": [10.0, 18.0, 27.0, 40.0]},
    "comparison": {
        "headline": "Spend vs save",
        "chart": [40.0, 70.0],
        "labels": ["spend", "save"],
    },
    "three_points": {"headline": "Three habits that stick"},
    "icon_explainer": {"headline": "Emergency fund equals runway"},
    "split_screen": {"headline": "Lifestyle vs assets"},
    "quote_or_fact": {"headline": "Pay yourself first."},
}


def _placeholder_still(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        Image.new("RGB", (1080, 1920), (20, 38, 51)).save(path)
    return path


def build_layout_demo_manifest(
    layout_id: str,
    *,
    still: Path,
    duration_sec: float = 3.0,
) -> ProjectManifest:
    pack = load_wealth_blueprint_style_v1()
    if layout_id not in pack.layouts:
        raise ValueError(f"unknown layout {layout_id}")
    copy = DEMO_COPY[layout_id]
    scene_type = layout_to_scene_type(
        layout_id,
        visual_focus=str(pack.layouts[layout_id].focus),
        index=0,
        total=1,
        has_number=bool(copy.get("number")),
        has_chart=bool(copy.get("chart")),
    )
    image_hash = file_sha256(still) or deterministic_hash({"still": str(still)})
    asset = SceneAsset(
        asset_id=f"demo-{layout_id}",
        role="selected_image",
        path=str(still),
        exists=True,
        provenance=AssetProvenance(
            provider="design_system_demo",
            model="none",
            workflow="placeholder",
            prompt=f"demo {layout_id}",
            seed=0,
            generation_parameters={"width": 1080, "height": 1920},
            input_hash=deterministic_hash({"layout": layout_id}),
            artifact_hash=image_hash,
        ),
        qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
    )
    chart_values = [float(v) for v in (copy.get("chart") or [])]
    chart_labels = list(copy.get("labels") or [])
    if chart_values and not chart_labels:
        chart_labels = [f"Y{i + 1}" for i in range(len(chart_values))]
    headline = str(copy["headline"])[:80]
    material = {
        "layout_id": layout_id,
        "headline": headline,
        "scene_type": scene_type.value,
        "duration": duration_sec,
    }
    scene_assets = [asset] if scene_type == SceneType.IMAGE else []
    scene = SceneManifest(
        scene_id=f"demo_{layout_id}",
        scene_type=scene_type,
        timing=TimeRange(start_sec=0.0, end_sec=duration_sec),
        narration=headline,
        caption=headline,
        headline=headline,
        overlay_text=headline,
        layout_id=layout_id,
        animation_presets=list(pack.layouts[layout_id].animation_presets or []),
        number_value=copy.get("number"),
        chart_values=chart_values,
        chart_labels=chart_labels,
        comparison_left=chart_labels[0] if len(chart_labels) >= 2 else "",
        comparison_right=chart_labels[1] if len(chart_labels) >= 2 else "",
        input_hash=deterministic_hash(material),
        assets=scene_assets,
    )
    tokens = brand_tokens_for_manifest()
    brand = BrandSpec(**tokens)
    now = datetime.now(UTC).isoformat()
    return ProjectManifest(
        schema_version="1.0",
        manifest_id=f"wb-style-demo-{layout_id}",
        job_id=f"design-demo-{layout_id}",
        project_id="wealth_blueprint_visual_v1_demo",
        account_id="wealth-blueprint",
        topic_id="design-system-demo",
        topic_title=f"Layout demo: {layout_id}",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="wealth_blueprint_visual_v1",
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="design_system_demo_v1",
        voice_version="none",
        brand=brand,
        status="demo",
        current_step="design_system_demo",
        scenes=[scene],
        created_at=now,
        updated_at=now,
    )


def write_all_layout_demos(out_root: Path) -> list[Path]:
    """Write one ProjectManifest JSON per layout under demos/design_system/wealth_blueprint_style_v1/."""
    out_root.mkdir(parents=True, exist_ok=True)
    still = _placeholder_still(out_root / "_placeholder.png")
    written: list[Path] = []
    index: list[dict[str, str]] = []
    for layout_id in load_wealth_blueprint_style_v1().layouts:
        manifest = build_layout_demo_manifest(layout_id, still=still)
        path = out_root / f"{layout_id}.json"
        save_project_manifest(path, manifest)
        written.append(path)
        index.append(
            {
                "layout_id": layout_id,
                "manifest": path.name,
                "scene_type": manifest.scenes[0].scene_type.value,
            }
        )
    (out_root / "index.json").write_text(
        json.dumps(
            {
                "style_id": "wealth_blueprint_style_v1",
                "count": len(index),
                "layouts": index,
                "note": "Offline ProjectManifest demos — validate without Remotion render",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return written
