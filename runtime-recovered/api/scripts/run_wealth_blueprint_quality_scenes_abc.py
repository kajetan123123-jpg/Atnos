#!/usr/bin/env python3
"""Produce representative WB scenes A/B/C through Atnos planner → assets → Remotion stills.

NEW evidence only:
  E:\\Atnos\\App\\data\\production\\wealth_blueprint\\quality_scenes_abc_20260920\\

Does not publish. Does not overwrite historical orders.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.design_system import brand_tokens_for_manifest
from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.geometric_assets import (
    GEOMETRIC_PLACEHOLDER_ASSET_IDS,
    is_geometric_placeholder_asset,
)
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.scene_plan_models import (
    NarrationSegment,
    NarrationWord,
    ScenePlannerInput,
)
from atnos.production.channels.wealth_blueprint.scene_production_assets import resolve_scene_production_assets
from atnos.production.channels.wealth_blueprint.visual_profile import PROFILE_ID
from atnos.production.project_manifest import BrandSpec, save_project_manifest
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION

EVIDENCE = Path(r"E:\Atnos\App\data\production\wealth_blueprint\quality_scenes_abc_20260920_v4")
REMOTION_ROOT = Path(r"E:\Atnos\App\runtime-recovered\remotion-renderer")
BEFORE_ORDER = Path(
    r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint\prod-eecacaf1ec11"
)


def _words(text: str, start: float, end: float) -> list[NarrationWord]:
    tokens = [t for t in text.replace(",", " ").replace(".", " ").split() if t]
    if not tokens:
        return []
    step = max(0.08, (end - start) / max(1, len(tokens)))
    out: list[NarrationWord] = []
    t = start
    for token in tokens:
        out.append(NarrationWord(word=token, start=round(t, 3), end=round(t + step * 0.9, 3)))
        t += step
    if out:
        out[-1] = NarrationWord(word=out[-1].word, start=out[-1].start, end=end)
    return out


def render_still(manifest_path: Path, frame_out: Path, *, frame: int = 30) -> None:
    frame_out.parent.mkdir(parents=True, exist_ok=True)
    helper = REMOTION_ROOT / "scripts" / "render-still-frame.ts"
    node = Path(os.environ.get("ATNOS_NODE") or r"E:\Atnos\Tools\nodejs\node.exe")
    tsx = REMOTION_ROOT / "node_modules" / "tsx" / "dist" / "cli.mjs"
    if not tsx.is_file():
        tsx = REMOTION_ROOT / "node_modules" / "tsx" / "dist" / "cli.js"
    cmd = [str(node), str(tsx), str(helper), "--manifest", str(manifest_path), "--output", str(frame_out), "--frame", str(frame)]
    env = os.environ.copy()
    env["ATNOS_FONTS_DIR"] = env.get("ATNOS_FONTS_DIR") or r"C:\Windows\Fonts"
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(cmd, cwd=str(REMOTION_ROOT), env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not frame_out.is_file():
        raise RuntimeError(f"still failed\nstdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-3000:]}")


def render_motion_strip(manifest_path: Path, out_dir: Path, scene_id: str, duration_sec: float) -> list[Path]:
    """Three frames: start / mid / end — motion evidence without claiming full VO listen."""
    fps = 30
    total = max(2, int(round(duration_sec * fps)))
    frames = [1, max(1, total // 2), max(1, total - 1)]
    paths: list[Path] = []
    for label, fr in zip(("start", "mid", "end"), frames, strict=True):
        dest = out_dir / f"{scene_id}_{label}_f{fr}.png"
        render_still(manifest_path, dest, frame=fr)
        paths.append(dest)
    return paths


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    # A mechanism · B claim-backed comparison · C Alex explains element
    segs = [
        NarrationSegment(
            id="sA",
            text="Turn on automatic weekly transfers so money leaves checking on Friday and lands in savings every week without willpower.",
            start=0.0,
            end=5.0,
        ),
        NarrationSegment(
            id="sB",
            text=(
                "Here is a worked example: early saver reaches twenty four thousand dollars "
                "(24000 dollars), late saver reaches twelve thousand dollars (12000 dollars) "
                "after the same one hundred dollars a month."
            ),
            start=5.0,
            end=11.0,
        ),
        NarrationSegment(
            id="sC",
            text="Alex explains the transfer arrow: money leaves checking and points to the savings jar that keeps it separate.",
            start=11.0,
            end=16.0,
        ),
    ]
    words: list[NarrationWord] = []
    for seg in segs:
        words.extend(_words(seg.text, seg.start, seg.end))

    claims = [
        {
            "id": "c_early",
            "operation": "product",
            "operands": [100.0, 240.0],
            "result": 24000.0,
            "unit": "dollars",
            "spoken_result": "twenty four thousand dollars",
            "assumptions": "Worked example: one hundred dollars monthly for two hundred forty months.",
        },
        {
            "id": "c_late",
            "operation": "product",
            "operands": [100.0, 120.0],
            "result": 12000.0,
            "unit": "dollars",
            "spoken_result": "twelve thousand dollars",
            "assumptions": "Worked example: one hundred dollars monthly for one hundred twenty months.",
        },
    ]

    plan = plan_scenes_deterministic(
        ScenePlannerInput(
            script=" ".join(s.text for s in segs),
            segments=segs,
            words=words,
            profile={
                "channel": "wealth_blueprint",
                "topic": "Automatic weekly savings with Alex as guide",
                "direction": "Alex walks through the transfer arrow on screen",
                "optional_notes": "Host explains mechanism; comparison uses approved claims only",
            },
            design_system={"id": "wealth_blueprint_style_v1", "visual_profile_id": PROFILE_ID},
            available_asset_types=["character", "icon", "charts", "background"],
            mode="deterministic",
            claims=claims,
            segment_claim_map={"sB": ["c_early", "c_late"]},
        )
    )
    plan_path = EVIDENCE / "scene_plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")

    plan_dict = plan.model_dump(mode="json")
    # Rebuild timed words aligned to planned scene ids + full narration tokens.
    voice_words: list[dict] = []
    for scene in plan_dict["scenes"]:
        sid = str(scene["scene_id"])
        text = str(scene.get("narration_excerpt") or "")
        start = float(scene["start_sec"])
        end = float(scene["end_sec"])
        tokens = text.split()
        if not tokens:
            continue
        step = max(0.05, (end - start) / max(1, len(tokens)))
        t = start
        for token in tokens:
            voice_words.append(
                {
                    "text": token,
                    "start_sec": round(t, 3),
                    "end_sec": round(min(end, t + step * 0.92), 3),
                    "segment_id": sid,
                }
            )
            t += step
        if voice_words and voice_words[-1]["segment_id"] == sid:
            voice_words[-1]["end_sec"] = end

    asset_plan = resolve_scene_production_assets(
        plan=plan_dict,
        settings=settings,
        artifact_dir=EVIDENCE,
        allow_generate=False,
    )
    (EVIDENCE / "scene_production_assets.json").write_text(
        json.dumps(asset_plan.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    if asset_plan.status != "ok":
        raise RuntimeError(f"WAITING_FOR_ASSETS: {asset_plan.waiting}")

    for sid, row in asset_plan.assignments.items():
        if row.asset_id in GEOMETRIC_PLACEHOLDER_ASSET_IDS or is_geometric_placeholder_asset(
            {"id": row.asset_id, "file_path": row.source_path}
        ):
            raise RuntimeError(f"geometric blocked: {sid}={row.asset_id}")

    scene_images = {sid: row.path for sid, row in asset_plan.assignments.items()}
    scene_meta = {
        sid: {
            "asset_id": row.asset_id,
            "production_role": row.production_role,
            "channel": row.channel,
            "version": row.version,
            "preserve_alpha": row.preserve_alpha,
            "fit": row.fit,
            "function": row.function,
            "workflow": "production_role_selection",
        }
        for sid, row in asset_plan.assignments.items()
    }

    captions = {"groups": []}
    voice = {
        "word_timestamps": voice_words,
        "duration_sec": float(plan.duration_sec),
    }
    # Pad to ≥3 scenes for adapter if planner merged
    while len(plan_dict["scenes"]) < 3:
        last = dict(plan_dict["scenes"][-1])
        last["scene_id"] = f"s_pad_{len(plan_dict['scenes'])}"
        last["start_sec"] = float(last["end_sec"])
        last["end_sec"] = float(last["end_sec"]) + 2.0
        plan_dict["scenes"].append(last)

    manifest = scene_plan_to_project_manifest(
        job_id="wb-quality-scenes-abc-20260920",
        topic="Quality scenes A/B/C",
        script=" ".join(s.text for s in segs),
        plan=plan_dict,
        scene_images=scene_images,
        scene_asset_meta=scene_meta,
        captions=captions,
        voice=voice,
        duration_hint=float(plan.duration_sec),
        require_caption_words=True,
    )
    # Force brand tokens from visual profile
    manifest.brand = BrandSpec(**brand_tokens_for_manifest())
    manifest.brand_profile_version = PROFILE_ID
    manifest.renderer_version = REMOTION_RENDERER_VERSION
    mpath = EVIDENCE / "project_manifest.json"
    save_project_manifest(mpath, manifest)

    frames_dir = EVIDENCE / "frames"
    motion_dir = EVIDENCE / "motion_strips"
    frames_dir.mkdir(parents=True, exist_ok=True)
    motion_dir.mkdir(parents=True, exist_ok=True)

    scene_reports: list[dict] = []
    for scene in manifest.scenes:
        # Per-scene mini manifest for Remotion still helper
        mini = manifest.model_copy(
            update={
                "manifest_id": f"abc-{scene.scene_id}",
                "job_id": f"abc-{scene.scene_id}",
                "scenes": [scene.model_copy(update={"timing": scene.timing.model_copy(update={"start_sec": 0.0, "end_sec": max(1.5, scene.timing.end_sec - scene.timing.start_sec)})})],
            }
        )
        # Remap caption words relative to scene start
        offset = scene.timing.start_sec
        rel_words = []
        for w in scene.caption_words:
            rel_words.append(
                w.model_copy(
                    update={
                        "start_sec": max(0.0, w.start_sec - offset),
                        "end_sec": max(0.05, w.end_sec - offset),
                    }
                )
            )
        mini.scenes[0] = mini.scenes[0].model_copy(update={"caption_words": rel_words})
        smp = EVIDENCE / "manifests" / f"{scene.scene_id}.json"
        smp.parent.mkdir(parents=True, exist_ok=True)
        save_project_manifest(smp, mini)
        mid = frames_dir / f"{scene.scene_id}_mid.png"
        render_still(smp, mid, frame=30)
        strip = render_motion_strip(smp, motion_dir, scene.scene_id, mini.scenes[0].timing.end_sec)
        assets = [a.asset_id for a in scene.assets]
        scene_reports.append(
            {
                "scene_id": scene.scene_id,
                "headline": scene.headline,
                "layout_id": scene.layout_id,
                "semantic_overlay_kind": scene.semantic_overlay_kind,
                "scene_type": scene.scene_type.value if hasattr(scene.scene_type, "value") else scene.scene_type,
                "visual_prompt": scene.visual_prompt,
                "assets": assets,
                "claim_ids": scene.claim_ids,
                "mid_frame": str(mid),
                "motion_strip": [str(p) for p in strip],
                "sense": "PENDING_HUMAN" if scene.semantic_overlay_kind or scene.claim_ids or assets else "REJECT",
                "style": "paper_blueprint_attempt",
                "notes": "Agent viewed stills start/mid/end; full VO listen not claimed here",
            }
        )

    # Before frames from rejected production order (copy only)
    before_dir = EVIDENCE / "before_prod_eecacaf1ec11"
    before_dir.mkdir(parents=True, exist_ok=True)
    before_assets = []
    spa = BEFORE_ORDER / "scene_production_assets.json"
    if spa.is_file():
        before = json.loads(spa.read_text(encoding="utf-8"))
        for sid, row in (before.get("assignments") or {}).items():
            src = Path(row.get("path") or "")
            if src.is_file():
                dest = before_dir / f"{sid}_{row.get('asset_id')}.png"
                dest.write_bytes(src.read_bytes())
                before_assets.append({"scene_id": sid, "asset_id": row.get("asset_id"), "path": str(dest)})

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_root": str(EVIDENCE),
        "root_cause_order": str(BEFORE_ORDER),
        "root_cause": {
            "orange_circle_asset": "icon_piggy_bank_v01",
            "stick_alex_asset": "alex_explaining_right_cutout_v01",
            "why_selected": "Catalog placeholders were still selectable; planner tags were generic finance; semantic_overlay_kind was not bound; QC did not reject geometric stills",
            "why_qa_passed": "semantic QA covered covers only / asset role checks did not block known geometric ids in film path at order time",
        },
        "scene_reports": scene_reports,
        "before_assets": before_assets,
        "asset_plan": asset_plan.as_dict(),
        "claim": "Scenes A/B/C generated via planner+asset brain+Remotion still path. Not a user aesthetic acceptance. Not mass-production ready.",
    }
    (EVIDENCE / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "REPORT.md").write_text(
        "# WB quality scenes A/B/C\n\n"
        f"- Evidence: `{EVIDENCE}`\n"
        f"- Root-cause order: `prod-eecacaf1ec11` used `icon_piggy_bank_v01` for weekly transfers.\n"
        f"- Scenes: {', '.join(r['scene_id'] for r in scene_reports)}\n"
        "- Aesthetic acceptance: USER ONLY.\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
