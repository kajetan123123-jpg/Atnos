#!/usr/bin/env python
"""Evidence smoke: per-scene production assets (Alex + non-Alex) via Remotion path.

Writes a NEW evidence directory under E:\\Atnos\\Evidence\\... — does not overwrite Phase 12 E2E.
No YouTube publish. Not a claim of full production readiness.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.scene_production_assets import (
    manifest_uses_reference_sheet,
    resolve_scene_production_assets,
)
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.project_manifest import load_project_manifest, save_project_manifest
from atnos.production.qa_gate import probe_media
from atnos.production.remotion_renderer import render_project_manifest
from atnos.production.steps.assembler import _mux_remotion_video


def _make_voice(path: Path, duration_sec: float = 9.0) -> None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg missing")
    # Simple sine bed as stand-in voice for mux (mechanism smoke, not content QA).
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:duration={duration_sec}",
        "-ar",
        "48000",
        "-ac",
        "1",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(r"E:\Atnos\Evidence\wealth_blueprint_asset_role_fix") / f"smoke-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    settings = get_settings()

    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Keep more of your raise",
                "narration_excerpt": "Your paycheck feels bigger this year.",
                "use_alex": False,
                "asset_needs": [],
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 6.0,
                "layout_id": "character_left_text_right",
                "visual_focus": "character",
                "on_screen_text": "Save first automatically",
                "narration_excerpt": "Alex explains why you should save first automatically.",
                "use_alex": True,
                "alex_pose": "explaining",
                "asset_needs": [
                    {
                        "type": "character",
                        "character": "alex",
                        "pose": "explaining",
                        "tags": ["finance", "host"],
                        "text": "Alex explaining save-first habit",
                        "required": True,
                    }
                ],
            },
            {
                "scene_id": "s03",
                "start_sec": 6.0,
                "end_sec": 9.0,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Piggy habit",
                "narration_excerpt": "A simple savings icon shows the habit without Alex.",
                "use_alex": False,
                "asset_needs": [
                    {"type": "icon", "tags": ["savings"], "text": "piggy bank habit", "required": True}
                ],
            },
        ]
    }

    asset_plan = resolve_scene_production_assets(
        plan=plan,
        settings=settings,
        artifact_dir=out,
        allow_generate=False,
    )
    (out / "scene_production_assets.json").write_text(
        json.dumps(asset_plan.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    if asset_plan.status != "ok":
        (out / "FAILED.json").write_text(json.dumps(asset_plan.as_dict(), indent=2), encoding="utf-8")
        print(json.dumps({"ok": False, "out": str(out), "waiting": asset_plan.waiting}))
        return 2

    scene_images = {sid: row.path for sid, row in asset_plan.assignments.items()}
    scene_asset_meta = {
        sid: {
            "asset_id": row.asset_id,
            "production_role": row.production_role,
            "channel": row.channel,
            "version": row.version,
            "preserve_alpha": row.preserve_alpha,
            "fit": row.fit,
            "function": row.function,
        }
        for sid, row in asset_plan.assignments.items()
    }

    script = (
        "Your paycheck feels bigger this year. "
        "Alex explains why you should save first automatically. "
        "A simple savings icon shows the habit without Alex."
    )
    manifest = scene_plan_to_project_manifest(
        job_id=f"asset-role-smoke-{stamp}",
        topic="Keep more of your raise",
        script=script,
        plan=plan,
        scene_images=scene_images,
        scene_asset_meta=scene_asset_meta,
        duration_hint=9.0,
    )
    manifest_path = out / "project_manifest.json"
    save_project_manifest(manifest_path, manifest)
    load_project_manifest(manifest_path, verify_hash=True)
    ref_hits = manifest_uses_reference_sheet(json.loads(manifest_path.read_text(encoding="utf-8")))
    if ref_hits:
        print(json.dumps({"ok": False, "reference_sheet_leak": ref_hits, "out": str(out)}))
        return 3

    silent = out / "renderer_silent.mp4"
    remotion = render_project_manifest(manifest_path, silent)
    (out / "remotion_result.json").write_text(json.dumps(remotion, indent=2, default=str) + "\n", encoding="utf-8")
    if not remotion.get("ok"):
        print(json.dumps({"ok": False, "remotion": remotion, "out": str(out)}))
        return 4

    voice = out / "voice.wav"
    _make_voice(voice, 9.0)
    final = out / "final_short.mp4"
    mux = _mux_remotion_video(raw_video_path=silent, audio_path=voice, output_path=final, target_lufs=-14.0)
    (out / "remotion_mux.json").write_text(json.dumps(mux, indent=2, default=str) + "\n", encoding="utf-8")
    if not mux.get("ok"):
        print(json.dumps({"ok": False, "mux": mux, "out": str(out)}))
        return 5

    probe = probe_media(final)
    report = {
        "ok": True,
        "evidence_dir": str(out),
        "final_mp4": str(final),
        "probe": probe,
        "assignments": asset_plan.as_dict()["assignments"],
        "reference_sheet_leaks": ref_hits,
        "alex_scene": "s02",
        "non_alex_image_scene": "s03",
        "note": (
            "Mechanism evidence only: approved character_cutout + icon roles via "
            "ProjectManifest → Remotion. Not a claim of full production readiness."
        ),
        "created_at": datetime.now(UTC).isoformat(),
    }
    (out / "evidence_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if probe.get("width") == 1080 and probe.get("height") == 1920 else 6


if __name__ == "__main__":
    raise SystemExit(main())
