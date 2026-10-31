#!/usr/bin/env python3
"""Render a full Wealth Blueprint short with burned paper_karaoke captions via Remotion.

Path: deterministic scene plan → production assets → ProjectManifest (caption_words) →
render_project_manifest (Atnos Remotion). Local only — no YouTube publish.

Evidence: E:\\Atnos\\App\\data\\production\\wealth_blueprint\\viewing_quality_20260920\\
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
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION, render_project_manifest

EVIDENCE = Path(r"E:\Atnos\App\data\production\wealth_blueprint\viewing_quality_20260920")
BEFORE_ORDER = Path(
    r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint\prod-eecacaf1ec11"
)
REMOTION_ROOT = Path(r"E:\Atnos\App\runtime-recovered\remotion-renderer")

# Verified-safe numbers only (avoid script_repair loops on unverifiable claims).
SEGMENTS: list[tuple[str, float, float]] = [
    (
        "Your paycheck can feel bigger this year while your wealth stays flat.",
        0.0,
        5.2,
    ),
    (
        "Turn on automatic weekly transfers so money leaves checking on Friday and lands in savings without willpower.",
        5.2,
        12.4,
    ),
    (
        "Keep part of every raise. Repeat every payday. That habit compounds.",
        12.4,
        18.6,
    ),
]


def _words(text: str, start: float, end: float) -> list[NarrationWord]:
    tokens = [t for t in text.replace(",", " ").split() if t]
    if not tokens:
        return []
    step = max(0.08, (end - start) / max(1, len(tokens)))
    out: list[NarrationWord] = []
    t = start
    for token in tokens:
        out.append(NarrationWord(word=token, start=round(t, 3), end=round(t + step * 0.85, 3)))
        t += step
    if out:
        out[-1] = NarrationWord(word=out[-1].word, start=out[-1].start, end=end)
    return out


def render_still(manifest_path: Path, frame_out: Path, *, frame: int) -> None:
    frame_out.parent.mkdir(parents=True, exist_ok=True)
    helper = REMOTION_ROOT / "scripts" / "render-still-frame.ts"
    node = Path(os.environ.get("ATNOS_NODE") or r"E:\Atnos\Tools\nodejs\node.exe")
    tsx = REMOTION_ROOT / "node_modules" / "tsx" / "dist" / "cli.mjs"
    if not tsx.is_file():
        tsx = REMOTION_ROOT / "node_modules" / "tsx" / "dist" / "cli.js"
    cmd = [
        str(node),
        str(tsx),
        str(helper),
        "--manifest",
        str(manifest_path),
        "--output",
        str(frame_out),
        "--frame",
        str(frame),
    ]
    env = os.environ.copy()
    env["ATNOS_FONTS_DIR"] = env.get("ATNOS_FONTS_DIR") or r"C:\Windows\Fonts"
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(cmd, cwd=str(REMOTION_ROOT), env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not frame_out.is_file():
        raise RuntimeError(proc.stderr[-2500:] or proc.stdout[-1500:])


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    segs = [
        NarrationSegment(id=f"seg{i+1}", text=text, start=a, end=b)
        for i, (text, a, b) in enumerate(SEGMENTS)
    ]
    words: list[NarrationWord] = []
    for seg in segs:
        words.extend(_words(seg.text, seg.start, seg.end))
    planner_in = ScenePlannerInput(
        script=" ".join(s.text for s in segs),
        segments=segs,
        words=words,
        profile={
            "channel": "wealth_blueprint",
            "topic": "Keep more of your raise",
            "direction": "Automatic transfers and keeping part of every raise",
            "optional_notes": "viewing quality short — burned captions, local only",
        },
        design_system={"id": "wealth_blueprint_style_v1", "visual_profile_id": PROFILE_ID},
        available_asset_types=["character", "icon", "charts", "background"],
        mode="deterministic",
    )
    plan = plan_scenes_deterministic(planner_in)
    plan_dict = plan.model_dump(mode="json")
    # Rebuild timed words aligned to planned scene ids (same shape as quality_scenes_abc).
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
    voice = {"word_timestamps": voice_words, "duration_sec": float(plan.duration_sec)}
    manifest = scene_plan_to_project_manifest(
        job_id="wb-viewing-quality-20260920",
        topic="Keep more of your raise",
        script=" ".join(s.text for s in segs),
        plan=plan_dict,
        scene_images=scene_images,
        scene_asset_meta=scene_meta,
        captions={"groups": []},
        voice=voice,
        duration_hint=float(plan.duration_sec),
        require_caption_words=True,
    )
    tokens = brand_tokens_for_manifest()
    tokens["captionStyle"] = "paper_karaoke"
    manifest.brand = BrandSpec(**tokens)
    manifest.brand_profile_version = PROFILE_ID
    manifest.renderer_version = REMOTION_RENDERER_VERSION
    manifest.status = "viewing_quality_render"
    manifest.current_step = "remotion"
    mpath = EVIDENCE / "project_manifest.json"
    save_project_manifest(mpath, manifest)

    mp4 = EVIDENCE / "wealth_blueprint_viewing_quality.mp4"
    marker = render_project_manifest(mpath, mp4, timeout_s=1200)

    # Sample frames with captions visible (mid scene 2 = weekly transfer + karaoke)
    after_dir = EVIDENCE / "after_frames"
    after_dir.mkdir(parents=True, exist_ok=True)
    for label, frame in (("hook_1s", 30), ("weekly_transfer_mid", 240), ("raise_habit_mid", 450)):
        render_still(mpath, after_dir / f"{label}.png", frame=frame)

    before_dir = EVIDENCE / "before_prod_eecacaf1ec11"
    before_dir.mkdir(parents=True, exist_ok=True)
    spa = BEFORE_ORDER / "scene_production_assets.json"
    before_note = "missing"
    if spa.is_file():
        before_note = "copied_asset_refs"
        (before_dir / "scene_production_assets.json").write_text(spa.read_text(encoding="utf-8"), encoding="utf-8")

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_root": str(EVIDENCE),
        "mp4": str(mp4),
        "mp4_exists": mp4.is_file(),
        "mp4_bytes": mp4.stat().st_size if mp4.is_file() else 0,
        "remotion_marker": marker,
        "caption_style": "paper_karaoke",
        "caption_words_total": sum(len(s.caption_words) for s in manifest.scenes),
        "scenes": [
            {
                "scene_id": s.scene_id,
                "layout_id": s.layout_id,
                "semantic_overlay_kind": s.semantic_overlay_kind,
                "caption": s.caption,
                "caption_word_count": len(s.caption_words),
                "timing": s.timing.model_dump(),
            }
            for s in manifest.scenes
        ],
        "before": {"order": str(BEFORE_ORDER), "note": before_note},
        "reception": {
            "A_silent_picture_captions": "PENDING_AGENT_VIEW — open MP4 muted; verify graphics+captions carry sense",
            "B_audio_only": "NOT_PERFORMED — this render is silent Remotion (no VO mux in this evidence path)",
            "C_full_av": "PARTIAL — picture+burned captions timed from word_timestamps; real VO not muxed here",
        },
        "claim": (
            "Full Remotion MP4 with burned paper_karaoke captions from caption_words. "
            "Not a hand-edited final. Next Production Order should inherit the same Remotion caption path."
        ),
    }
    (EVIDENCE / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "REPORT.md").write_text(
        "# Viewing quality short\n\n"
        f"- MP4: `{mp4}`\n"
        f"- Caption style: paper_karaoke (working default, not user-approved)\n"
        f"- Caption words: {report['caption_words_total']}\n"
        f"- Reception B/C VO: not claimed (silent Remotion evidence path)\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0 if mp4.is_file() else 2


if __name__ == "__main__":
    raise SystemExit(main())
