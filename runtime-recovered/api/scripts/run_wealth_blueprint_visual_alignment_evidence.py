#!/usr/bin/env python3
"""Render representative WB layouts + comparison contact sheet (real Remotion frames).

Evidence only under a NEW directory — does not overwrite Phase 12 E2E.
Does NOT auto-create aesthetic baselines from arbitrary current pixels.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.production.channels.wealth_blueprint.design_system import brand_tokens_for_manifest
from atnos.production.channels.wealth_blueprint.visual_profile import PROFILE_ID, load_wealth_blueprint_visual_v1
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

EVIDENCE_ROOT = Path(r"E:\Atnos\App\data\production\wealth_blueprint\visual_alignment_20260919")
REMOTION_ROOT = Path(r"E:\Atnos\App\runtime-recovered\remotion-renderer")
ALEX_STILL = Path(r"E:\Atnos\App\config\channel_references\20260916\crops\alex_front_v1.png")
ICON_STILL = Path(r"E:\Atnos\App\config\channel_references\20260916\crops\alex_portrait_v1.png")

REPRESENTATIVE = (
    ("character_left_text_right", SceneType.IMAGE, "Keep more of your raise"),
    ("character_right_text_left", SceneType.IMAGE, "Save first. Spend second."),
    ("icon_explainer", SceneType.IMAGE, "Emergency fund equals runway"),
    ("comparison", SceneType.CHART, "Spend versus save"),
)


def perceptual_delta(a: Path, b: Path) -> float:
    left = Image.open(a).convert("RGB").resize((270, 480), Image.Resampling.BILINEAR)
    right = Image.open(b).convert("RGB").resize((270, 480), Image.Resampling.BILINEAR)
    diff = ImageChops.difference(left, right)
    return float(sum(ImageStat.Stat(diff).mean) / 3.0)


def prepare_still(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.open(src).convert("RGBA").resize((1080, 1920), Image.Resampling.LANCZOS).convert("RGB").save(dest)
    return dest


def build_manifest(layout_id: str, scene_type: SceneType, headline: str, still: Path) -> ProjectManifest:
    image_hash = file_sha256(still) or deterministic_hash({"still": str(still)})
    asset = SceneAsset(
        asset_id=f"align-{layout_id}",
        role="selected_image",
        path=str(still),
        exists=True,
        provenance=AssetProvenance(
            provider="visual_alignment",
            model="none",
            workflow="channel_reference",
            prompt=f"alignment {layout_id}",
            seed=0,
            generation_parameters={"width": 1080, "height": 1920},
            input_hash=deterministic_hash({"layout": layout_id}),
            artifact_hash=image_hash,
        ),
        qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
    )
    chart_values = [40.0, 70.0] if scene_type == SceneType.CHART else []
    chart_labels = ["spend", "save"] if scene_type == SceneType.CHART else []
    presets = {
        "character_left_text_right": ["CHARACTER_ENTER_LEFT", "TEXT_FADE"],
        "character_right_text_left": ["CHARACTER_ENTER_RIGHT", "TEXT_FADE"],
        "icon_explainer": ["ICON_POP", "TEXT_FADE"],
        "comparison": ["CHART_GROW", "TEXT_FADE"],
    }.get(layout_id, ["TEXT_FADE"])
    scene = SceneManifest(
        scene_id=f"align_{layout_id}",
        scene_type=scene_type,
        timing=TimeRange(start_sec=0.0, end_sec=2.0),
        narration=f"{headline}. spend 40 save 70." if scene_type == SceneType.CHART else headline,
        caption=headline,
        headline=headline,
        overlay_text=headline,
        layout_id=layout_id,
        animation_presets=presets,
        chart_values=chart_values,
        chart_labels=chart_labels,
        claim_ids=["align-claim-spend-save"] if scene_type == SceneType.CHART else [],
        comparison_left=chart_labels[0] if chart_labels else "",
        comparison_right=chart_labels[1] if chart_labels else "",
        input_hash=deterministic_hash({"layout": layout_id, "headline": headline}),
        assets=[asset] if scene_type == SceneType.IMAGE else [],
    )
    now = datetime.now(UTC).isoformat()
    return ProjectManifest(
        schema_version="1.0",
        manifest_id=f"wb-visual-align-{layout_id}",
        job_id=f"visual-align-{layout_id}",
        project_id="wealth_blueprint_visual_alignment",
        account_id="wealth-blueprint",
        topic_id="visual-alignment",
        topic_title=f"Visual alignment: {layout_id}",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version=PROFILE_ID,
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="visual_alignment_20260919",
        voice_version="none",
        brand=BrandSpec(**brand_tokens_for_manifest()),
        status="visual_alignment",
        current_step="remotion_still",
        scenes=[scene],
        created_at=now,
        updated_at=now,
    )


def render_mid_frame(manifest_path: Path, frame_out: Path) -> None:
    """Use Remotion renderStill via Node for one mid-frame PNG."""
    frame_out.parent.mkdir(parents=True, exist_ok=True)
    helper = REMOTION_ROOT / "scripts" / "render-still-frame.ts"
    node = Path(os.environ.get("ATNOS_NODE") or r"E:\Atnos\Tools\nodejs\node.exe")
    tsx_cli = REMOTION_ROOT / "node_modules" / "tsx" / "dist" / "cli.mjs"
    if not tsx_cli.is_file():
        tsx_cli = REMOTION_ROOT / "node_modules" / "tsx" / "dist" / "cli.js"
    cmd = [
        str(node),
        str(tsx_cli),
        str(helper),
        "--manifest",
        str(manifest_path),
        "--output",
        str(frame_out),
        "--frame",
        "30",
    ]
    env = os.environ.copy()
    env["ATNOS_FONTS_DIR"] = env.get("ATNOS_FONTS_DIR") or r"C:\Windows\Fonts"
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        cmd,
        cwd=str(REMOTION_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not frame_out.is_file():
        raise RuntimeError(
            f"Remotion still failed for {manifest_path.name}:\n"
            f"stdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-4000:]}"
        )


def contact_sheet(frames: list[tuple[str, Path]], out: Path) -> None:
    tiles = []
    for label, path in frames:
        img = Image.open(path).convert("RGB").resize((270, 480), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (270, 520), (20, 38, 51))
        canvas.paste(img, (0, 40))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 270, 36), fill=(28, 51, 68))
        draw.text((8, 10), label[:28], fill=(240, 229, 210))
        tiles.append(canvas)
    sheet = Image.new("RGB", (270 * len(tiles) + 12 * (len(tiles) + 1), 544), (14, 26, 35))
    x = 12
    for tile in tiles:
        sheet.paste(tile, (x, 12))
        x += 270 + 12
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=92)


def main() -> int:
    profile = load_wealth_blueprint_visual_v1()
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    scenes_dir = EVIDENCE_ROOT / "scenes"
    manifests_dir = EVIDENCE_ROOT / "manifests"
    assets_dir = EVIDENCE_ROOT / "assets"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)

    alex = prepare_still(ALEX_STILL, assets_dir / "alex_host.png")
    icon = prepare_still(ICON_STILL, assets_dir / "icon_subject.png")

    rendered: list[tuple[str, Path]] = []
    for layout_id, scene_type, headline in REPRESENTATIVE:
        still = icon if layout_id == "icon_explainer" else alex
        manifest = build_manifest(layout_id, scene_type, headline, still)
        # Chart scenes need source-backed narration order; already embedded above.
        stamped = save_project_manifest(manifests_dir / f"{layout_id}.json", manifest)
        # Remotion validateManifest requires content_hash present — save_project_manifest sets it.
        frame_path = scenes_dir / f"{layout_id}.png"
        render_mid_frame(manifests_dir / f"{layout_id}.json", frame_path)
        rendered.append((layout_id, frame_path))

    sheet_path = EVIDENCE_ROOT / "comparison_contact_sheet.jpg"
    contact_sheet(rendered, sheet_path)

    # Technical differentiation evidence (not aesthetic pass)
    deltas = {
        "left_vs_right": perceptual_delta(scenes_dir / "character_left_text_right.png", scenes_dir / "character_right_text_left.png"),
        "left_vs_icon": perceptual_delta(scenes_dir / "character_left_text_right.png", scenes_dir / "icon_explainer.png"),
        "left_vs_comparison": perceptual_delta(scenes_dir / "character_left_text_right.png", scenes_dir / "comparison.png"),
    }
    tech_ok = all(value > 4.0 for value in deltas.values())

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "profile_id": profile["profile_id"],
        "palette": profile["palette"]["roles"],
        "authority": profile["authority"],
        "evidence_root": str(EVIDENCE_ROOT),
        "scenes": [str(path) for _, path in rendered],
        "contact_sheet": str(sheet_path),
        "perceptual_deltas": deltas,
        "technical_layout_differentiation": "PASS" if tech_ok else "FAIL",
        "aesthetic_judgment": "AWAITING_USER_REVIEW",
        "note": (
            "Technical PASS means Remotion rendered distinct compositions with visual_v1 brand tokens. "
            "It is NOT an aesthetic approval and NOT a claim that full production is ready."
        ),
    }
    (EVIDENCE_ROOT / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE_ROOT / "REPORT.md").write_text(
        "\n".join(
            [
                "# Wealth Blueprint visual alignment — 2026-09-19",
                "",
                f"- Profile: `{PROFILE_ID}`",
                f"- Technical layout differentiation: **{report['technical_layout_differentiation']}**",
                f"- Aesthetic judgment: **{report['aesthetic_judgment']}**",
                f"- Contact sheet: `{sheet_path}`",
                "",
                "## Perceptual deltas (layout vs layout)",
                *[f"- {key}: {value:.2f}" for key, value in deltas.items()],
                "",
                "## Scope limit",
                "This evidence covers representative Remotion layouts + brand wiring only.",
                "It does **not** prove end-to-end production readiness or YouTube publishability.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0 if tech_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
