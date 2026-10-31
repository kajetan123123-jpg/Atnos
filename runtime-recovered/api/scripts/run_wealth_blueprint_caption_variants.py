#!/usr/bin/env python3
"""Render three WB caption style variants on the same narration fragment.

Evidence: E:\\Atnos\\App\\data\\production\\wealth_blueprint\\caption_variants_20260920\\
Uses Remotion stills — does not overwrite historical orders.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")
API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.production.channels.wealth_blueprint.design_system import brand_tokens_for_manifest
from atnos.production.project_manifest import (
    AssetProvenance,
    BrandSpec,
    CaptionWord,
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

EVIDENCE = Path(r"E:\Atnos\App\data\production\wealth_blueprint\caption_variants_20260920")
REMOTION = Path(r"E:\Atnos\App\runtime-recovered\remotion-renderer")
STILL_SRC = Path(
    r"E:\Atnos\App\runtime-recovered\api\config\asset_libraries\wealth_blueprint\illustrative\illust_weekly_auto_transfer_v01.png"
)

NARRATION = (
    "Turn on automatic weekly transfers so money leaves checking on Friday "
    "and lands in savings without willpower."
)
WORDS = [
    "Turn", "on", "automatic", "weekly", "transfers", "so", "money", "leaves",
    "checking", "on", "Friday", "and", "lands", "in", "savings", "without", "willpower.",
]


def render_still(manifest: Path, out: Path, frame: int = 45) -> None:
    node = Path(os.environ.get("ATNOS_NODE") or r"E:\Atnos\Tools\nodejs\node.exe")
    tsx = REMOTION / "node_modules" / "tsx" / "dist" / "cli.mjs"
    if not tsx.is_file():
        tsx = REMOTION / "node_modules" / "tsx" / "dist" / "cli.js"
    helper = REMOTION / "scripts" / "render-still-frame.ts"
    env = os.environ.copy()
    env["ATNOS_FONTS_DIR"] = env.get("ATNOS_FONTS_DIR") or r"C:\Windows\Fonts"
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    cmd = [
        str(node), str(tsx), str(helper),
        "--manifest", str(manifest), "--output", str(out), "--frame", str(frame),
    ]
    proc = subprocess.run(cmd, cwd=str(REMOTION), env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(proc.stderr[-3000:] or proc.stdout[-1500:])


def build_manifest(style: str, still: Path) -> ProjectManifest:
    step = 0.22
    caption_words = [
        CaptionWord(text=w, start_sec=round(i * step, 3), end_sec=round((i + 0.9) * step, 3))
        for i, w in enumerate(WORDS)
    ]
    end = caption_words[-1].end_sec + 0.2
    asset = SceneAsset(
        asset_id="illust_weekly_auto_transfer_v01",
        role="selected_image",
        path=str(still),
        exists=True,
        provenance=AssetProvenance(
            provider="caption_variants",
            model="none",
            workflow="illustrative",
            prompt=NARRATION,
            seed=0,
            generation_parameters={"caption_style": style},
            input_hash=deterministic_hash({"style": style}),
            artifact_hash=file_sha256(still) or deterministic_hash({"still": str(still)}),
        ),
        qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
    )
    scene = SceneManifest(
        scene_id="cap_demo",
        scene_type=SceneType.IMAGE,
        timing=TimeRange(start_sec=0.0, end_sec=end),
        narration=NARRATION,
        caption=NARRATION,
        headline="",
        overlay_text="",
        caption_words=caption_words,
        layout_id="icon_explainer",
        semantic_overlay_kind="weekly_transfer",
        visual_prompt=f"caption_style={style}",
        animation_presets=["ICON_POP", "TEXT_FADE"],
        input_hash=deterministic_hash({"style": style, "n": NARRATION}),
        assets=[asset],
    )
    brand = BrandSpec(**{**brand_tokens_for_manifest(), "captionStyle": style})
    now = datetime.now(UTC).isoformat()
    return ProjectManifest(
        schema_version="1.0",
        manifest_id=f"wb-caption-{style}",
        job_id=f"caption-variant-{style}",
        project_id="wealth_blueprint_captions",
        account_id="wealth-blueprint",
        topic_id="caption-variants",
        topic_title="Caption style variants",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="wealth_blueprint_visual_v1",
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="caption_variants_20260920",
        voice_version="none",
        brand=brand,
        status="caption_variant",
        current_step="remotion_still",
        scenes=[scene],
        created_at=now,
        updated_at=now,
    )


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    still = EVIDENCE / "base_still.png"
    shutil.copy2(STILL_SRC, still)
    # Patch Remotion still helper to accept caption style via env if supported;
    # otherwise write style into a sidecar consumed by regenerating design default.
    # Working default is paper_karaoke in design.tsx; variants rendered by temporarily
    # rewriting DEFAULT via env ATNOS_CAPTION_STYLE read in design (injected below).
    styles = ["paper_karaoke", "soft_pill", "outline_bold"]
    frames = []
    for style in styles:
        m = build_manifest(style, still)
        mp = EVIDENCE / f"manifest_{style}.json"
        save_project_manifest(mp, m)
        out = EVIDENCE / f"variant_{style}.png"
        # Mid phrase around "weekly transfers"
        env = os.environ.copy()
        env["ATNOS_CAPTION_STYLE"] = style
        node = Path(os.environ.get("ATNOS_NODE") or r"E:\Atnos\Tools\nodejs\node.exe")
        tsx = REMOTION / "node_modules" / "tsx" / "dist" / "cli.mjs"
        if not tsx.is_file():
            tsx = REMOTION / "node_modules" / "tsx" / "dist" / "cli.js"
        helper = REMOTION / "scripts" / "render-still-frame.ts"
        env["ATNOS_FONTS_DIR"] = env.get("ATNOS_FONTS_DIR") or r"C:\Windows\Fonts"
        env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
        cmd = [
            str(node), str(tsx), str(helper),
            "--manifest", str(mp), "--output", str(out), "--frame", "45",
        ]
        proc = subprocess.run(cmd, cwd=str(REMOTION), env=env, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(proc.stderr[-3000:] or proc.stdout[-1500:])
        frames.append({"style": style, "path": str(out)})

    choice = {
        "working_choice": "paper_karaoke",
        "why": (
            "Matches WB Visual Bible (navy ink on cream, gold accent on active word, "
            "no chrome pill competing with paper diagrams). soft_pill is more feed-native "
            "but fights paper scenes; outline_bold is loud TikTok-yellow-outline adjacent."
        ),
        "user_approval": "NOT_CLAIMED",
    }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_root": str(EVIDENCE),
        "fragment": NARRATION,
        "variants": frames,
        "choice": choice,
        "claim": "Still comparison only — motion/VO reception requires full Remotion MP4 order.",
    }
    (EVIDENCE / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "REPORT.md").write_text(
        "# Caption variants\n\n"
        f"- Working choice (agent): **paper_karaoke** — not user-approved.\n"
        f"- Files: {', '.join(f['path'] for f in frames)}\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
