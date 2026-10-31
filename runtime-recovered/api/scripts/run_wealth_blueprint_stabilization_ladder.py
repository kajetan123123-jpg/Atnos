#!/usr/bin/env python
"""Wealth Blueprint Remotion ladder + sound-mix smoke (no upload, no YouTube).

Builds deterministic ProjectManifests for 5s / 15s / 30s, renders via remotion-renderer,
mixes VO+SFX+bed when a voice fixture is provided or generated, runs video_qc.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from array import array
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from PIL import Image

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.video_qc import run_wealth_blueprint_qc
from atnos.production.project_manifest import save_project_manifest
from atnos.production.qa_gate import probe_media
from atnos.production.remotion_renderer import render_project_manifest
from atnos.production.sound_design import (
    MusicBedRequest,
    SoundEventRequest,
    VoiceTrackSpec,
    WealthBlueprintSoundDesignEngine,
    render_mix_plan,
)
from atnos.production.steps.assembler import _mux_remotion_video


def _tone(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24_000
    n = max(1, int(seconds * rate))
    samples = array("h")
    for i in range(n):
        env = min(1.0, i / 400) * min(1.0, (n - i) / 800)
        samples.append(int(7000 * env * math.sin(2 * math.pi * 180 * (i / rate))))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def _plan(duration: float) -> dict:
    third = duration / 3.0
    return {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": third,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Keep more of your raise",
                "narration_excerpt": "Your paycheck feels bigger this year.",
                "sound_events": [{"event": "soft_whoosh", "at_sec": 0.15, "priority": 2}],
            },
            {
                "scene_id": "s02",
                "start_sec": third,
                "end_sec": third * 2,
                "layout_id": "big_number",
                "visual_focus": "number",
                "on_screen_text": "Save first",
                "narration_excerpt": "Save first, then spend what remains.",
                "number_value": "10%",
                "sound_events": [{"event": "pop", "at_sec": third + 0.2, "priority": 3}],
            },
            {
                "scene_id": "s03",
                "start_sec": third * 2,
                "end_sec": duration,
                "layout_id": "chart_focus",
                "visual_focus": "chart",
                "on_screen_text": "Compound growth",
                "narration_excerpt": "Invest consistently. Time beats perfection.",
                "chart": {"values": [1, 2, 4, 8], "labels": ["Y1", "Y2", "Y3", "Y4"]},
                "sound_events": [{"event": "rise", "at_sec": third * 2 + 0.3, "priority": 3}],
            },
        ]
    }


def run_ladder(out_root: Path, durations: list[float]) -> dict:
    settings = get_settings()
    still = out_root / "still.png"
    refs = settings.wealth_blueprint_channel_references_dir
    source = None
    if refs:
        for pattern in ("**/*.png", "**/*.jpg"):
            hits = sorted(Path(refs).glob(pattern))
            if hits:
                source = hits[0]
                break
    if source is None and settings.app_root:
        candidate = Path(settings.app_root) / "config" / "channel_references" / "20260916" / "image-1.png"
        if candidate.is_file():
            source = candidate
    if source is None:
        Image.new("RGB", (1080, 1920), (18, 24, 32)).save(still)
    else:
        Image.open(source).convert("RGB").resize((1080, 1920)).save(still)

    results = {}
    for duration in durations:
        bucket = out_root / f"ladder_{int(duration)}s"
        bucket.mkdir(parents=True, exist_ok=True)
        plan = _plan(duration)
        (bucket / "scene_plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        manifest = scene_plan_to_project_manifest(
            job_id=f"ladder-{int(duration)}s",
            topic="Keep more of your raise",
            script="Your paycheck feels bigger. Save first. Invest consistently.",
            plan=plan,
            selected_image=still,
            duration_hint=duration,
        )
        manifest_path = bucket / "project_manifest.json"
        save_project_manifest(manifest_path, manifest)
        silent = bucket / "silent.mp4"
        remotion = render_project_manifest(manifest_path, silent)
        (bucket / "remotion.json").write_text(json.dumps(remotion, indent=2, default=str) + "\n", encoding="utf-8")
        if not remotion.get("ok"):
            results[f"{int(duration)}s"] = {"ok": False, "stage": "remotion", "detail": remotion}
            continue
        voice = bucket / "voice.wav"
        _tone(voice, duration)
        engine = WealthBlueprintSoundDesignEngine()
        mix_plan = engine.plan(
            voice=VoiceTrackSpec(file_path=str(voice), duration_sec=duration),
            events=[
                SoundEventRequest(event="soft_whoosh", at_sec=0.15),
                SoundEventRequest(event="pop", at_sec=max(0.5, duration * 0.35)),
                SoundEventRequest(event="rise", at_sec=max(1.0, duration * 0.7)),
            ],
            music=MusicBedRequest(event="bed_editorial", gain_db=-18.0),
            seed=f"ladder-{duration}",
        )
        (bucket / "sound_mix_plan.json").write_text(mix_plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
        audio = voice
        sound_ok = False
        if mix_plan.clips:
            mixed = bucket / "sound_mix.aac"
            try:
                render_mix_plan(mix_plan, mixed)
                audio = mixed
                sound_ok = True
            except Exception as exc:  # noqa: BLE001
                (bucket / "sound_error.txt").write_text(str(exc), encoding="utf-8")
        final = bucket / "final.mp4"
        mux = _mux_remotion_video(raw_video_path=silent, audio_path=audio, output_path=final, target_lufs=-14.0)
        (bucket / "mux.json").write_text(json.dumps(mux, indent=2, default=str) + "\n", encoding="utf-8")
        if not mux.get("ok"):
            results[f"{int(duration)}s"] = {"ok": False, "stage": "mux", "detail": mux, "sound_ok": sound_ok}
            continue
        qc = run_wealth_blueprint_qc(
            artifact_dir=bucket,
            video_path=final,
            audio_path=voice,
            manifest_path=manifest_path,
            scene_plan_path=bucket / "scene_plan.json",
        )
        (bucket / "video_qc.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
        probe = probe_media(final)
        results[f"{int(duration)}s"] = {
            "ok": bool(qc.get("ok") and mux.get("ok") and remotion.get("ok")),
            "sound_ok": sound_ok,
            "qc_ok": bool(qc.get("ok")),
            "probe": probe,
            "final": str(final),
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        default=str(Path("E:/Atnos/App/data/production/wealth_blueprint/stabilization-ladder")),
    )
    parser.add_argument("--durations", default="5,15,30")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    durations = [float(x.strip()) for x in args.durations.split(",") if x.strip()]
    results = run_ladder(out, durations)
    (out / "ladder_results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if all(item.get("ok") for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
