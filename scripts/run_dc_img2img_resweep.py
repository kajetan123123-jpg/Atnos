"""Re-run Dark Chronicles env img2img denoise sweep after fixing reference approval."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")
os.environ.setdefault("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
os.environ.setdefault("COMFYUI_MODELS_DIR", r"E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Shared\models")
os.environ.setdefault("COMFYUI_INSTALL_DIR", r"E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Installs\Atnos")
os.environ.setdefault("COMFYUI_ALLOW_MANAGED_LAUNCH", "true")
os.environ.setdefault("DATA_DIR", r"E:\Atnos\App\data")
os.environ.setdefault("COMFYUI_LOWVRAM", "true")

from atnos.core.config import get_settings
from atnos.production.comfy_channels import ChannelComfyGenerationService, GenerationRequest
from atnos.production.comfy_channels.consistency_metrics import compare_to_anchor, mean_scores
from atnos.production.comfy_channels.profiles import persist_builtin_profiles
from run_img2img_tuning_sweep import (
    CROPS,
    DC_GRID,
    DC_SCENES,
    OUT,
    PRESETS_PATH,
    PROFILES_PATH,
    _artifact,
    _pick,
    _score_dc,
    _vram,
    write_presets,
)

# Import pick helper for WB/WC from existing summary
SWEEP_ROOT = OUT / "sweep_20260918T072244Z"


def ensure_anchor(svc: ChannelComfyGenerationService, run_dir: Path) -> Path:
    dest = CROPS / "dc_env_mood_anchor_v1.png"
    if not (dest.is_file() and dest.stat().st_size > 10_000):
        print("Creating DC mood anchor...")
        meta = svc.generate(
            GenerationRequest(
                channel="dark_chronicles",
                workflow="DC_LOCATION",
                subject="foggy coastal cliff at night",
                composition="wide establishing shot, strong leading lines",
                lighting="low-key moonlight, deep shadows",
                camera="35mm cinematic",
                width=768,
                height=512,
                seed=830000,
                force_regenerate=True,
                references=[],
            )
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(meta.output_path, dest)
    shutil.copy2(dest, run_dir / "dc_env_mood_anchor_v1.png")
    # Refresh profile.reference_files so assert_reference_belongs passes
    get_settings.cache_clear()
    settings = get_settings()
    written = persist_builtin_profiles(settings)
    print("Persisted profiles:", [str(p) for p in written])
    return dest


def main() -> None:
    run_dir = SWEEP_ROOT / "dc_env_rerun"
    run_dir.mkdir(parents=True, exist_ok=True)
    get_settings.cache_clear()
    settings = get_settings()
    svc = ChannelComfyGenerationService(settings)
    anchor = ensure_anchor(svc, run_dir)
    # Reload service settings/profiles after persist
    get_settings.cache_clear()
    settings = get_settings()
    svc = ChannelComfyGenerationService(settings)

    results: list[dict[str, Any]] = []
    for denoise in DC_GRID:
        cell_dir = run_dir / f"denoise_{denoise:.2f}".replace(".", "p")
        cell_dir.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        times: list[float] = []
        artifacts = 0
        print(f"\n=== dc_env denoise={denoise:.2f} ===")
        for scene_key, seed, prompt_extra in DC_SCENES:
            t0 = time.perf_counter()
            try:
                meta = svc.generate(
                    GenerationRequest(
                        channel="dark_chronicles",
                        workflow="DC_LOCATION",
                        subject=prompt_extra,
                        composition="cinematic establishing composition, strong mood",
                        lighting="low-key horror lighting",
                        camera="wide cinematic",
                        width=768,
                        height=512,
                        seed=seed,
                        references=[str(anchor)],
                        force_regenerate=True,
                        denoise_override=denoise,
                    )
                )
                elapsed = time.perf_counter() - t0
                times.append(elapsed)
                dest = cell_dir / f"{scene_key}_{seed}.png"
                dest.write_bytes(Path(meta.output_path).read_bytes())
                metrics = compare_to_anchor(dest, anchor)
                art = _artifact(meta, metrics)
                if art:
                    artifacts += 1
                rows.append(
                    {
                        "scene": scene_key,
                        "seed": seed,
                        "ok": True,
                        "generation_time_s": round(elapsed, 3),
                        "metrics": metrics,
                        "artifact": art,
                        "output": str(dest),
                        "vram": _vram(),
                    }
                )
                print(f"  OK {scene_key} {elapsed:.1f}s palette={metrics.get('color_palette')}")
            except Exception as exc:  # noqa: BLE001
                elapsed = time.perf_counter() - t0
                times.append(elapsed)
                artifacts += 1
                rows.append({"scene": scene_key, "seed": seed, "ok": False, "error": str(exc)[:500], "artifact": True})
                print(f"  FAIL {scene_key}: {exc}")

        metric_rows = [r["metrics"] for r in rows if r.get("ok") and r.get("metrics")]
        mean_m = mean_scores(metric_rows)
        mean_t = sum(times) / len(times) if times else 0.0
        scores = _score_dc(mean_m, mean_t, artifacts / max(len(DC_SCENES), 1))
        cell = {"denoise": denoise, "mean_metrics": mean_m, "scores": scores, "vram": _vram(), "variants": rows}
        results.append(cell)
        (cell_dir / "cell.json").write_text(json.dumps(cell, indent=2) + "\n", encoding="utf-8")
        print(f"  SCORE composite={scores['composite']} mood={scores.get('mood_composition')} t={scores['generation_time']}s")

    dc = {"label": "dc_env", "channel": "dark_chronicles", "grid": DC_GRID, "cells": results}
    dc_sel = _pick(dc, baseline_denoise=0.70, min_identity=None)

    # Keep WB/WC selections from prior sweep; update DC only
    prior = json.loads((SWEEP_ROOT / "sweep_summary.json").read_text(encoding="utf-8"))
    selections = {
        "wealth_blueprint": prior["selections"]["wealth_blueprint"],
        "wonder_crew": prior["selections"]["wonder_crew"],
        "dark_chronicles": dc_sel,
    }
    write_presets(selections)
    out = {
        "updated_at": datetime.now(UTC).isoformat(),
        "dc_selection": dc_sel,
        "dc_cells": [{"denoise": c["denoise"], "scores": c["scores"]} for c in results],
    }
    (run_dir / "dc_rerun_summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print("DC selected denoise=", dc_sel["selected_denoise"], dc_sel["selected_scores"])


if __name__ == "__main__":
    main()
