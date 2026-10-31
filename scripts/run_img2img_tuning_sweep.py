"""Controlled per-channel img2img denoise sweep → WB/WC/DC_IMG2IMG_V1.

Small grid, same anchors/seeds/prompts; only denoise changes.
Does not set approved=true.
"""

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
os.environ.setdefault("COMFYUI_DEFAULT_CHECKPOINT", "sd_xl_turbo_1.0_fp16.safetensors")

from atnos.core.config import get_settings
from atnos.production.comfy_channels import ChannelComfyGenerationService, GenerationRequest
from atnos.production.comfy_channels.consistency_metrics import compare_to_anchor, mean_scores
from atnos.production.comfy_channels.model_profiles import img2img_presets_path, model_profiles_path

OUT = Path(r"E:\Atnos\App\data\generated\_img2img_tuning")
PRESETS_PATH = Path(r"E:\Atnos\App\config\comfy_img2img_presets.json")
PROFILES_PATH = Path(r"E:\Atnos\App\config\comfy_channel_model_profiles.json")
REFS = Path(r"E:\Atnos\App\config\channel_references\20260916")
CROPS = REFS / "crops"

# Narrow grids around current 0.48 (WB/WC) and higher for DC mood
WB_GRID = [0.40, 0.44, 0.48, 0.52, 0.56]
WC_GRID = [0.36, 0.40, 0.44, 0.48, 0.52]
DC_GRID = [0.55, 0.62, 0.70, 0.78, 0.85]

# Representative poses (not full 8) — same seeds as consistency bench where possible
WB_POSES = [
    ("neutral", "front", 810001),
    ("explaining", "right", 810002),
    ("pointing", "left", 810005),
]
WC_POSES = [
    ("neutral", "front", 820001),
    ("happy", "front", 820002),
    ("walking", "side", 820006),
]
DC_SCENES = [
    ("corridor", 830001, "abandoned hospital corridor, flickering lights, wet floor reflection, horror atmosphere"),
    ("woods", 830002, "foggy night forest path, distant silhouette, oppressive mood, cinematic framing"),
]


def _vram() -> dict[str, Any]:
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:8188/system_stats", timeout=5.0)
        r.raise_for_status()
        devices = (r.json().get("devices") or [])
        if devices and isinstance(devices[0], dict):
            d0 = devices[0]
            used = None
            if d0.get("vram_total") and d0.get("vram_free") is not None:
                used = int(d0["vram_total"]) - int(d0["vram_free"])
            return {
                "name": d0.get("name"),
                "vram_total": d0.get("vram_total"),
                "vram_free": d0.get("vram_free"),
                "vram_used_est": used,
                "torch_vram_free": d0.get("torch_vram_free"),
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {}


def _artifact(meta: Any, metrics: dict[str, Any]) -> bool:
    verdict = str((meta.generation_settings or {}).get("validation_verdict") or "")
    if verdict.upper() in {"FAIL", "REJECT", "BLANK"}:
        return True
    # Near-solid / collapsed identity signal
    if metrics and float(metrics.get("perceptual_ssim_like") or 1) < 0.05:
        return True
    return False


def _score_wb(mean_m: dict[str, Any], mean_t: float, artifact_rate: float) -> dict[str, float]:
    identity = float(mean_m.get("identity_proxy") or 0)
    face = float(mean_m.get("face_region_ssim") or 0)
    style = float(mean_m.get("color_palette") or 0)
    outfit = float(mean_m.get("outfit_color") or 0)
    # Useful variation: pose/gesture change while face locked
    sil = float(mean_m.get("silhouette_iou") or 0)
    useful_var = max(0.0, 1.0 - sil)
    # Prefer mid variation with strong face
    composite = 0.40 * identity + 0.30 * face + 0.15 * outfit + 0.10 * style + 0.15 * useful_var - 0.25 * artifact_rate
    # slight penalty if almost copy (no pose room)
    if sil > 0.88:
        composite -= 0.04
    qps = identity / max(mean_t, 0.1)
    return {
        "identity_quality": round(identity, 4),
        "style_quality": round((style + outfit) / 2, 4),
        "face_lock": round(face, 4),
        "useful_variation": round(useful_var, 4),
        "artifact_rate": round(artifact_rate, 4),
        "generation_time": round(mean_t, 3),
        "quality_per_second": round(qps, 5),
        "composite": round(composite, 4),
    }


def _score_wc(mean_m: dict[str, Any], mean_t: float, artifact_rate: float) -> dict[str, float]:
    identity = float(mean_m.get("identity_proxy") or 0)
    face = float(mean_m.get("face_region_ssim") or 0)
    style = float(mean_m.get("color_palette") or 0)
    outfit = float(mean_m.get("outfit_color") or 0)
    hair = float(mean_m.get("hair_region_color") or 0)
    sil = float(mean_m.get("silhouette_iou") or 0)
    # Consistency first — reward lock, lightly penalize excess drift
    composite = (
        0.45 * identity
        + 0.20 * face
        + 0.15 * outfit
        + 0.10 * hair
        + 0.10 * style
        + 0.05 * sil
        - 0.30 * artifact_rate
    )
    qps = identity / max(mean_t, 0.1)
    return {
        "identity_quality": round(identity, 4),
        "style_quality": round((style + outfit + hair) / 3, 4),
        "face_lock": round(face, 4),
        "useful_variation": round(max(0.0, 1.0 - sil), 4),
        "artifact_rate": round(artifact_rate, 4),
        "generation_time": round(mean_t, 3),
        "quality_per_second": round(qps, 5),
        "composite": round(composite, 4),
    }


def _score_dc(mean_m: dict[str, Any], mean_t: float, artifact_rate: float) -> dict[str, float]:
    # Mood/composition over face identity
    palette = float(mean_m.get("color_palette") or 0)
    ssim = float(mean_m.get("perceptual_ssim_like") or 0)
    sil = float(mean_m.get("silhouette_iou") or 0)
    # Want some scene change but keep mood (palette) and composition structure
    useful_var = max(0.0, 1.0 - sil)
    mood = 0.45 * palette + 0.30 * ssim + 0.25 * min(useful_var, 0.55)
    composite = mood - 0.25 * artifact_rate
    # mild time penalty only
    if mean_t > 45:
        composite -= 0.02
    qps = mood / max(mean_t, 0.1)
    return {
        "identity_quality": round(float(mean_m.get("identity_proxy") or 0), 4),
        "style_quality": round(palette, 4),
        "mood_composition": round(mood, 4),
        "useful_variation": round(useful_var, 4),
        "artifact_rate": round(artifact_rate, 4),
        "generation_time": round(mean_t, 3),
        "quality_per_second": round(qps, 5),
        "composite": round(composite, 4),
    }


def _ensure_dc_anchor(svc: ChannelComfyGenerationService, run_dir: Path) -> Path:
    dest = CROPS / "dc_env_mood_anchor_v1.png"
    if dest.is_file() and dest.stat().st_size > 10_000:
        return dest
    print("Creating DC environment mood anchor (txt2img once)...")
    meta = svc.generate(
        GenerationRequest(
            channel="dark_chronicles",
            workflow="DC_LOCATION",
            subject="abandoned hospital corridor",
            composition="wide establishing shot, strong leading lines",
            lighting="low-key green emergency lights, deep shadows",
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
    shutil.copy2(meta.output_path, run_dir / "dc_env_mood_anchor_v1.png")
    from atnos.production.comfy_channels.profiles import persist_builtin_profiles

    persist_builtin_profiles(get_settings())
    print(f"  DC anchor -> {dest} (profiles refreshed)")
    return dest


def sweep_character(
    *,
    label: str,
    channel: str,
    workflow: str,
    subject: str,
    anchor_name: str,
    poses: list[tuple[str, str, int]],
    grid: list[float],
    score_fn,
    run_dir: Path,
) -> dict[str, Any]:
    get_settings.cache_clear()
    settings = get_settings()
    svc = ChannelComfyGenerationService(settings)
    results: list[dict[str, Any]] = []

    for denoise in grid:
        cell_dir = run_dir / label / f"denoise_{denoise:.2f}".replace(".", "p")
        cell_dir.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        times: list[float] = []
        artifacts = 0
        print(f"\n=== {label} denoise={denoise:.2f} ===")
        for emotion, direction, seed in poses:
            direc = "front" if direction == "front_ref" else direction
            composition = ""
            if emotion == "walking":
                composition = "full body walking pose reference, readable silhouette"
            t0 = time.perf_counter()
            try:
                meta = svc.generate(
                    GenerationRequest(
                        channel=channel,  # type: ignore[arg-type]
                        workflow=workflow,
                        subject=subject,
                        emotion=emotion,
                        direction=direc,
                        composition=composition,
                        width=512,
                        height=512,
                        seed=seed,
                        references=[anchor_name],
                        force_regenerate=True,
                        denoise_override=denoise,
                    )
                )
                elapsed = time.perf_counter() - t0
                times.append(elapsed)
                dest = cell_dir / f"{emotion}_{direc}_{seed}.png"
                dest.write_bytes(Path(meta.output_path).read_bytes())
                anchor = Path(meta.reference_paths[0]) if meta.reference_paths else Path()
                metrics = compare_to_anchor(dest, anchor) if anchor.is_file() else {}
                art = _artifact(meta, metrics)
                if art:
                    artifacts += 1
                rows.append(
                    {
                        "pose": f"{emotion}/{direc}",
                        "seed": seed,
                        "ok": True,
                        "generation_time_s": round(elapsed, 3),
                        "metrics": metrics,
                        "artifact": art,
                        "output": str(dest),
                        "vram": _vram(),
                    }
                )
                print(f"  OK {emotion}/{direc} {elapsed:.1f}s id={metrics.get('identity_proxy')}")
            except Exception as exc:  # noqa: BLE001
                elapsed = time.perf_counter() - t0
                times.append(elapsed)
                artifacts += 1
                rows.append({"pose": f"{emotion}/{direc}", "seed": seed, "ok": False, "error": str(exc)[:500], "artifact": True})
                print(f"  FAIL {emotion}/{direc}: {exc}")

        metric_rows = [r["metrics"] for r in rows if r.get("ok") and r.get("metrics")]
        mean_m = mean_scores(metric_rows)
        mean_t = sum(times) / len(times) if times else 0.0
        artifact_rate = artifacts / max(len(poses), 1)
        scores = score_fn(mean_m, mean_t, artifact_rate)
        cell = {
            "denoise": denoise,
            "n_ok": sum(1 for r in rows if r.get("ok")),
            "mean_metrics": mean_m,
            "scores": scores,
            "vram": _vram(),
            "variants": rows,
        }
        results.append(cell)
        (cell_dir / "cell.json").write_text(json.dumps(cell, indent=2) + "\n", encoding="utf-8")
        print(f"  SCORE composite={scores['composite']} id={scores['identity_quality']} t={scores['generation_time']}s")

    ranked = sorted(results, key=lambda c: c["scores"]["composite"], reverse=True)
    return {"label": label, "channel": channel, "grid": grid, "cells": results, "ranked": [c["denoise"] for c in ranked]}


def sweep_dc(run_dir: Path) -> dict[str, Any]:
    get_settings.cache_clear()
    settings = get_settings()
    svc = ChannelComfyGenerationService(settings)
    anchor = _ensure_dc_anchor(svc, run_dir)
    results: list[dict[str, Any]] = []

    for denoise in DC_GRID:
        cell_dir = run_dir / "dc_env" / f"denoise_{denoise:.2f}".replace(".", "p")
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

    ranked = sorted(results, key=lambda c: c["scores"]["composite"], reverse=True)
    return {"label": "dc_env", "channel": "dark_chronicles", "grid": DC_GRID, "cells": results, "ranked": [c["denoise"] for c in ranked]}


def _pick(channel_result: dict[str, Any], *, baseline_denoise: float, min_identity: float | None = None) -> dict[str, Any]:
    cells = channel_result["cells"]
    baseline = next((c for c in cells if abs(c["denoise"] - baseline_denoise) < 1e-6), None)
    baseline_id = float((baseline or {}).get("scores", {}).get("identity_quality") or 0)
    floor = (min_identity if min_identity is not None else baseline_id - 0.015)

    candidates = []
    rejected = []
    for c in cells:
        s = c["scores"]
        reason = None
        if s.get("artifact_rate", 0) > 0.34:
            reason = "artifact_rate"
        elif min_identity is not None or baseline is not None:
            # WB/WC: don't accept if identity collapses vs baseline
            if channel_result["channel"] != "dark_chronicles" and s["identity_quality"] < floor:
                reason = f"identity_below_floor_{floor:.3f}"
        if reason:
            rejected.append({"denoise": c["denoise"], "reason": reason, "scores": s})
        else:
            candidates.append(c)

    if not candidates:
        # fall back to baseline if present else best composite
        if baseline:
            chosen = baseline
            rejected.append({"denoise": "all_others", "reason": "no_candidate_kept_baseline"})
        else:
            chosen = max(cells, key=lambda x: x["scores"]["composite"])
    else:
        chosen = max(candidates, key=lambda x: x["scores"]["composite"])

    return {
        "selected_denoise": chosen["denoise"],
        "selected_scores": chosen["scores"],
        "baseline_denoise": baseline_denoise,
        "baseline_scores": (baseline or {}).get("scores"),
        "rejected": rejected,
        "ranking": [
            {"denoise": c["denoise"], "composite": c["scores"]["composite"], "identity": c["scores"]["identity_quality"]}
            for c in sorted(cells, key=lambda x: x["scores"]["composite"], reverse=True)
        ],
    }


def write_presets(selections: dict[str, dict[str, Any]]) -> None:
    payload = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    mapping = {
        "wealth_blueprint": ("WB_IMG2IMG_V1", "crops/alex_portrait_v1.png", "Alex: face/hair/clothes lock; pose/emotion freedom"),
        "wonder_crew": ("WC_IMG2IMG_V1", "crops/niko_portrait_v1.png", "Niko: max consistency"),
        "dark_chronicles": ("DC_IMG2IMG_V1", "crops/dc_env_mood_anchor_v1.png", "Environments: composition + mood"),
    }
    for channel, (pid, pref, goal) in mapping.items():
        sel = selections[channel]
        payload["presets"][pid] = {
            "channel": channel,
            "preset_id": pid,
            "status": "active",
            "goal": goal,
            "reference_mode": "core_img2img",
            "reference_denoise": sel["selected_denoise"],
            "preferred_reference": pref,
            "selected_scores": sel["selected_scores"],
            "rejected_variants": sel["rejected"],
            "ranking": sel["ranking"],
            "updated_at": datetime.now(UTC).isoformat(),
        }
    PRESETS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    for channel, (pid, _, _) in mapping.items():
        ch = profiles.setdefault("channels", {}).setdefault(channel, {})
        ch["img2img_preset"] = pid
        # Keep denoise mirrored for older readers; preset is source of truth
        ch["reference_mode"] = "core_img2img"
        ch["reference_denoise"] = selections[channel]["selected_denoise"]
    PROFILES_PATH.write_text(json.dumps(profiles, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    run_id = datetime.now(UTC).strftime("sweep_%Y%m%dT%H%M%SZ")
    run_dir = OUT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print("IMG2IMG TUNING", run_id)

    wb = sweep_character(
        label="alex",
        channel="wealth_blueprint",
        workflow="WB_CHARACTER",
        subject="alex",
        anchor_name="alex_portrait_v1.png",
        poses=WB_POSES,
        grid=WB_GRID,
        score_fn=_score_wb,
        run_dir=run_dir,
    )
    wc = sweep_character(
        label="niko",
        channel="wonder_crew",
        workflow="CHARACTER_REFERENCE",
        subject="niko",
        anchor_name="niko_portrait_v1.png",
        poses=WC_POSES,
        grid=WC_GRID,
        score_fn=_score_wc,
        run_dir=run_dir,
    )
    dc = sweep_dc(run_dir)

    selections = {
        "wealth_blueprint": _pick(wb, baseline_denoise=0.48),
        "wonder_crew": _pick(wc, baseline_denoise=0.48),
        "dark_chronicles": _pick(dc, baseline_denoise=0.70, min_identity=None),
    }
    write_presets(selections)

    summary = {
        "run_id": run_id,
        "wb": wb,
        "wc": wc,
        "dc": dc,
        "selections": selections,
        "presets_path": str(PRESETS_PATH),
        "profiles_path": str(PROFILES_PATH),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    # Trim huge nested for top summary file — keep full in channel dumps
    (run_dir / "sweep_full.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    slim = {
        "run_id": run_id,
        "selections": selections,
        "wb_ranking": wb["ranked"],
        "wc_ranking": wc["ranked"],
        "dc_ranking": dc["ranked"],
    }
    (run_dir / "sweep_summary.json").write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
    print("\n=== SELECTIONS ===")
    for k, v in selections.items():
        print(f"  {k}: denoise={v['selected_denoise']} composite={v['selected_scores']['composite']}")
    print("Wrote", run_dir)


if __name__ == "__main__":
    main()
