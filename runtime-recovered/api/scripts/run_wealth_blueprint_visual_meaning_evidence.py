#!/usr/bin/env python3
"""Evidence: before-class geometric problem vs improved meaning-bound WB frames.

NEW dir only: E:\\Atnos\\App\\data\\production\\wealth_blueprint\\visual_meaning_fix_20260920_paper_v2\\
Uses canonical Remotion still path — does not overwrite historical E2E or prior visual_meaning evidence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.design_system import brand_tokens_for_manifest
from atnos.production.channels.wealth_blueprint.geometric_assets import GEOMETRIC_PLACEHOLDER_ASSET_IDS
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.scene_production_assets import resolve_scene_production_assets
from atnos.production.channels.wealth_blueprint.visual_profile import PROFILE_ID
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
    infer_semantic_overlay_kind,
    save_project_manifest,
)
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION

EVIDENCE_ROOT = Path(r"E:\Atnos\App\data\production\wealth_blueprint\visual_meaning_fix_20260920_paper_v2")
REMOTION_ROOT = Path(r"E:\Atnos\App\runtime-recovered\remotion-renderer")
PLACEHOLDER_DIR = Path(
    r"E:\Atnos\App\runtime-recovered\api\config\asset_libraries\wealth_blueprint\placeholders"
)


def render_mid_frame(manifest_path: Path, frame_out: Path) -> None:
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
    proc = subprocess.run(cmd, cwd=str(REMOTION_ROOT), env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not frame_out.is_file():
        raise RuntimeError(
            f"Remotion still failed:\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-4000:]}"
        )


def build_image_manifest(
    *,
    scene_id: str,
    headline: str,
    narration: str,
    still: Path,
    layout_id: str,
    job_tag: str,
) -> ProjectManifest:
    image_hash = file_sha256(still) or deterministic_hash({"still": str(still)})
    overlay = infer_semantic_overlay_kind(narration, headline)
    asset = SceneAsset(
        asset_id=f"meaning-{scene_id}",
        role="selected_image",
        path=str(still),
        exists=True,
        provenance=AssetProvenance(
            provider="visual_meaning_fix",
            model="none",
            workflow="illustrative_or_placeholder",
            prompt=narration,
            seed=0,
            generation_parameters={"width": 1080, "height": 1920, "fit": "contain"},
            input_hash=deterministic_hash({"scene": scene_id}),
            artifact_hash=image_hash,
        ),
        qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
    )
    scene = SceneManifest(
        scene_id=scene_id,
        scene_type=SceneType.IMAGE,
        timing=TimeRange(start_sec=0.0, end_sec=2.0),
        narration=narration,
        caption=headline,
        headline=headline,
        overlay_text=headline,
        layout_id=layout_id,
        animation_presets=["ICON_POP", "TEXT_FADE"] if layout_id == "icon_explainer" else ["CHARACTER_ENTER_LEFT", "TEXT_FADE"],
        semantic_overlay_kind=overlay,
        input_hash=deterministic_hash({"scene": scene_id, "headline": headline}),
        assets=[asset],
    )
    now = datetime.now(UTC).isoformat()
    return ProjectManifest(
        schema_version="1.0",
        manifest_id=f"wb-meaning-{job_tag}-{scene_id}",
        job_id=f"visual-meaning-{job_tag}-{scene_id}",
        project_id="wealth_blueprint_visual_meaning",
        account_id="wealth-blueprint",
        topic_id="visual-meaning",
        topic_title=f"Visual meaning: {headline}",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version=PROFILE_ID,
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="visual_meaning_fix_20260920_paper_v2",
        voice_version="none",
        brand=BrandSpec(**brand_tokens_for_manifest()),
        status="visual_meaning_fix",
        current_step="remotion_still",
        scenes=[scene],
        created_at=now,
        updated_at=now,
    )


def contact_sheet(frames: list[tuple[str, Path]], out: Path) -> None:
    tiles = []
    for label, path in frames:
        img = Image.open(path).convert("RGB").resize((270, 480), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (270, 520), (20, 38, 51))
        canvas.paste(img, (0, 40))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 270, 36), fill=(28, 51, 68))
        draw.text((8, 10), label[:30], fill=(240, 229, 210))
        tiles.append(canvas)
    sheet = Image.new("RGB", (270 * len(tiles) + 12 * (len(tiles) + 1), 544), (14, 26, 35))
    x = 12
    for tile in tiles:
        sheet.paste(tile, (x, 12))
        x += 270 + 12
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=92)


def main() -> int:
    if EVIDENCE_ROOT.exists():
        # Keep historical — write into a subfolder if re-run
        pass
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    before_dir = EVIDENCE_ROOT / "before_problem_class"
    after_dir = EVIDENCE_ROOT / "after_meaning_bound"
    manifests = EVIDENCE_ROOT / "manifests"
    for d in (before_dir, after_dir, manifests):
        d.mkdir(parents=True, exist_ok=True)

    # --- Before: geometric placeholder stills composited as Remotion would ---
    before_cases = [
        (
            "stick_alex_savings",
            "CHOOSE A SEPARATE SAVINGS ACCOUNT.",
            "Choose a separate savings account.",
            "character_left_text_right",
            PLACEHOLDER_DIR / "alex_pointing_right_cutout_v01.png",
        ),
        (
            "circle_bar_transfers",
            "TURN ON AUTOMATIC WEEKLY TRANSFERS.",
            "Turn on automatic weekly transfers.",
            "icon_explainer",
            PLACEHOLDER_DIR / "icon_piggy_bank_v01.png",
        ),
    ]
    before_frames: list[tuple[str, Path]] = []
    for scene_id, headline, narration, layout, still in before_cases:
        # Copy problem-class asset into evidence for audit (do not mutate catalog placeholders)
        staged = before_dir / f"{scene_id}_asset.png"
        shutil.copy2(still, staged)
        # For before frames we intentionally omit semantic overlays to show problem class
        image_hash = file_sha256(staged) or deterministic_hash({"still": str(staged)})
        asset = SceneAsset(
            asset_id=f"before-{scene_id}",
            role="selected_image",
            path=str(staged),
            exists=True,
            provenance=AssetProvenance(
                provider="visual_meaning_fix",
                model="none",
                workflow="geometric_placeholder_repro",
                prompt=narration,
                seed=0,
                generation_parameters={"width": 1080, "height": 1920, "fit": "contain"},
                input_hash=deterministic_hash({"before": scene_id}),
                artifact_hash=image_hash,
            ),
            qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
        )
        scene = SceneManifest(
            scene_id=scene_id,
            scene_type=SceneType.IMAGE,
            timing=TimeRange(start_sec=0.0, end_sec=2.0),
            narration=narration,
            caption=headline,
            headline=headline,
            overlay_text=headline,
            layout_id=layout,
            animation_presets=["TEXT_FADE"],
            semantic_overlay_kind=None,
            input_hash=deterministic_hash({"before": scene_id}),
            assets=[asset],
        )
        now = datetime.now(UTC).isoformat()
        manifest = ProjectManifest(
            schema_version="1.0",
            manifest_id=f"wb-before-{scene_id}",
            job_id=f"visual-meaning-before-{scene_id}",
            project_id="wealth_blueprint_visual_meaning",
            account_id="wealth-blueprint",
            topic_id="visual-meaning-before",
            topic_title=headline,
            channel="wealth_blueprint",
            language_code="en",
            brand_profile_version=PROFILE_ID,
            renderer_version=REMOTION_RENDERER_VERSION,
            workflow_version="visual_meaning_fix_20260920_paper_v2",
            voice_version="none",
            brand=BrandSpec(**brand_tokens_for_manifest()),
            status="visual_meaning_before",
            current_step="remotion_still",
            scenes=[scene],
            created_at=now,
            updated_at=now,
        )
        mpath = manifests / f"before_{scene_id}.json"
        save_project_manifest(mpath, manifest)
        fout = before_dir / f"{scene_id}.png"
        render_mid_frame(mpath, fout)
        before_frames.append((f"BEFORE {scene_id}", fout))

    # --- After: production asset resolver + meaning overlays ---
    settings = get_settings()
    plan = {
        "scenes": [
            {
                "scene_id": "s_savings",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Choose a separate savings account",
                "narration_excerpt": "Choose a separate savings account.",
                "icon_tags": ["savings", "account", "choose", "finance"],
                "use_alex": False,
                "asset_needs": [
                    {"type": "icon", "tags": ["savings", "account", "choose", "finance"], "required": True}
                ],
            },
            {
                "scene_id": "s_transfer",
                "start_sec": 3.0,
                "end_sec": 6.0,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Turn on automatic weekly transfers",
                "narration_excerpt": "Turn on automatic weekly transfers so saving happens without willpower.",
                "icon_tags": ["transfer", "weekly", "automatic", "finance"],
                "use_alex": False,
                "asset_needs": [
                    {"type": "icon", "tags": ["transfer", "weekly", "automatic", "savings"], "required": True}
                ],
            },
            {
                "scene_id": "s_habit",
                "start_sec": 6.0,
                "end_sec": 9.0,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Make the habit stick",
                "narration_excerpt": "A simple savings habit keeps the plan moving.",
                "icon_tags": ["habit", "savings", "finance"],
                "use_alex": False,
                "asset_needs": [
                    {"type": "icon", "tags": ["habit", "savings", "finance"], "required": True}
                ],
            },
        ]
    }
    asset_plan = resolve_scene_production_assets(
        plan=plan, settings=settings, artifact_dir=after_dir / "assets", allow_generate=False
    )
    (EVIDENCE_ROOT / "scene_production_assets.json").write_text(
        json.dumps(asset_plan.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    if asset_plan.status != "ok":
        raise RuntimeError(f"asset plan waiting: {asset_plan.waiting}")

    after_frames: list[tuple[str, Path]] = []
    for scene in plan["scenes"]:
        sid = scene["scene_id"]
        if sid == "s_habit":
            continue  # adapter padding only; primary evidence is savings + transfer
        row = asset_plan.assignments[sid]
        assert row.asset_id not in GEOMETRIC_PLACEHOLDER_ASSET_IDS
        still = row.path
        headline = scene["on_screen_text"]
        narration = scene["narration_excerpt"]
        manifest = build_image_manifest(
            scene_id=sid,
            headline=headline.upper() + ".",
            narration=narration,
            still=still,
            layout_id="icon_explainer",
            job_tag="after",
        )
        mpath = manifests / f"after_{sid}.json"
        save_project_manifest(mpath, manifest)
        fout = after_dir / f"{sid}.png"
        render_mid_frame(mpath, fout)
        after_frames.append((f"AFTER {sid}", fout))

    # Full ProjectManifest via adapter (needs ≥3 scenes)
    try:
        full = scene_plan_to_project_manifest(
            job_id="visual-meaning-adapter",
            topic="Savings setup meaning",
            script=(
                "Choose a separate savings account. "
                "Turn on automatic weekly transfers. "
                "A simple savings habit keeps the plan moving."
            ),
            plan=plan,
            scene_images={sid: row.path for sid, row in asset_plan.assignments.items()},
            scene_asset_meta={
                sid: {
                    "asset_id": row.asset_id,
                    "production_role": row.production_role,
                    "channel": row.channel,
                    "version": row.version,
                    "preserve_alpha": row.preserve_alpha,
                    "fit": row.fit,
                }
                for sid, row in asset_plan.assignments.items()
            },
            duration_hint=9.0,
        )
        save_project_manifest(manifests / "adapter_project_manifest.json", full)
        adapter_ok = True
        adapter_err = None
    except Exception as exc:  # noqa: BLE001
        adapter_ok = False
        adapter_err = str(exc)
        (EVIDENCE_ROOT / "adapter_error.txt").write_text(str(exc), encoding="utf-8")

    sheet = EVIDENCE_ROOT / "comparison_contact_sheet.jpg"
    contact_sheet([*before_frames, *after_frames], sheet)

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_root": str(EVIDENCE_ROOT),
        "profile_id": PROFILE_ID,
        "blocked_geometric_ids": sorted(GEOMETRIC_PLACEHOLDER_ASSET_IDS),
        "before_frames": [str(p) for _, p in before_frames],
        "after_frames": [str(p) for _, p in after_frames],
        "after_asset_ids": {sid: row.asset_id for sid, row in asset_plan.assignments.items()},
        "contact_sheet": str(sheet),
        "adapter_manifest_ok": adapter_ok,
        "adapter_error": adapter_err,
        "claim": (
            "After frames use illustrative assets + semantic overlays bound to narration. "
            "Not a claim that full production / mass publish is ready."
        ),
        "unit_vs_integration_vs_production": {
            "unit": "tests/test_wealth_blueprint_visual_meaning.py",
            "integration": "this evidence Remotion still + asset resolver",
            "real_production": "NOT RUN — no YouTube publish, no mass order E2E in this gate",
        },
    }
    (EVIDENCE_ROOT / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE_ROOT / "REPORT.md").write_text(
        "\n".join(
            [
                "# Wealth Blueprint visual meaning fix — 2026-09-19",
                "",
                "## Root cause",
                "APPROVED `library_placeholder` assets were geometric primitives (stick-figure Alex, orange-circle icons).",
                "",
                "## Fix",
                "- Reject geometric placeholders from production selection",
                "- Illustrative meaning diagrams + Remotion overlays (`savings_account`, `weekly_transfer`)",
                "- Planner binds visual concept / icon tags to narration",
                "",
                f"- Contact sheet: `{sheet}`",
                f"- After asset ids: `{report['after_asset_ids']}`",
                "",
                "## Limits",
                "Not full production ready. Alex host still limited; Comfy generation not required for this gate.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
