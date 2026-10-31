"""Deterministic Alex/Niko character consistency benchmark (img2img baseline).

Does not set approved=true. Does not install IPAdapter.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

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
from atnos.production.comfy_channels.model_profiles import load_model_profile

OUT_ROOT = Path(r"E:\Atnos\App\data\generated\_consistency_bench")

ALEX_POSES = [
    ("neutral", "front", 810001),
    ("explaining", "right", 810002),
    ("confused", "front", 810003),
    ("pointing", "right", 810004),
    ("pointing", "left", 810005),
    ("concerned", "front", 810006),
    ("confident", "front", 810007),
    ("neutral", "3/4", 810008),
]

NIKO_POSES = [
    ("neutral", "front", 820001),
    ("happy", "front", 820002),
    ("surprised", "front", 820003),
    ("concerned", "front", 820004),
    ("pointing", "front", 820005),
    ("walking", "side", 820006),
    ("neutral", "front_ref", 820007),
    ("friendly", "3/4", 820008),
]


def _contact_sheet(paths: list[Path], dest: Path, cols: int = 4, cell: int = 320) -> str:
    imgs = [Image.open(p).convert("RGB").resize((cell, cell)) for p in paths]
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (24, 24, 24))
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols) * cell, (i // cols) * cell))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, quality=92)
    return str(dest)


def _vram_hint() -> dict[str, Any]:
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:8188/system_stats", timeout=5.0)
        r.raise_for_status()
        payload = r.json()
        devices = payload.get("devices") or []
        if devices and isinstance(devices[0], dict):
            d0 = devices[0]
            return {
                "vram_total": d0.get("vram_total"),
                "vram_free": d0.get("vram_free"),
                "torch_vram_total": d0.get("torch_vram_total"),
                "torch_vram_free": d0.get("torch_vram_free"),
                "name": d0.get("name"),
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {}


def run_character(
    *,
    label: str,
    channel: str,
    workflow: str,
    subject: str,
    anchor_name: str,
    poses: list[tuple[str, str, int]],
    denoise: float | None,
    run_id: str,
    width: int = 512,
    height: int = 512,
) -> dict[str, Any]:
    get_settings.cache_clear()
    settings = get_settings()
    svc = ChannelComfyGenerationService(settings)
    model_prof = load_model_profile(settings, channel)  # type: ignore[arg-type]
    effective_denoise = denoise if denoise is not None else float(model_prof.get("reference_denoise") or 0.6)
    out_dir = OUT_ROOT / run_id / label
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    paths: list[Path] = []
    failures = 0
    times: list[float] = []

    for emotion, direction, seed in poses:
        # Normalize front_ref → front for prompt
        direc = "front" if direction == "front_ref" else direction
        composition = ""
        if emotion == "walking":
            composition = "full body walking pose reference, readable silhouette"
        elif direction == "3/4":
            composition = "three-quarter view character reference"
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
                    width=width,
                    height=height,
                    seed=seed,
                    references=[anchor_name],
                    force_regenerate=True,
                    denoise_override=effective_denoise,
                )
            )
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            src = Path(meta.output_path)
            dest = out_dir / f"{emotion}_{direc.replace('/', '-')}_{seed}.png"
            dest.write_bytes(src.read_bytes())
            paths.append(dest)
            anchor = Path(meta.reference_paths[0]) if meta.reference_paths else Path()
            metrics = compare_to_anchor(dest, anchor) if anchor.is_file() else {}
            row = {
                "character_id": subject,
                "pose_key": f"{emotion}/{direc}",
                "anchor_asset": str(anchor),
                "model": meta.model,
                "backend": meta.backend,
                "workflow": meta.workflow_id,
                "workflow_version": meta.workflow_version,
                "seed": meta.seed,
                "denoise": meta.generation_settings.get("reference_denoise"),
                "cfg": None,
                "sampler": None,
                "scheduler": None,
                "dimensions": [meta.width, meta.height],
                "generation_time_s": round(elapsed, 3),
                "prompt": meta.prompt,
                "negative_prompt": meta.negative_prompt,
                "output_path": str(dest),
                "source_output_path": meta.output_path,
                "reference_conditioning": meta.generation_settings.get("reference_conditioning"),
                "quality_gate": meta.generation_settings.get("validation_verdict"),
                "approved": False,
                "metrics": metrics,
                "ok": True,
            }
            # Pull sampler hints from evidence if present
            evidence = Path(str(meta.generation_settings.get("evidence_path") or ""))
            if evidence.is_file():
                try:
                    ev = json.loads(evidence.read_text(encoding="utf-8"))
                    wf = ev.get("workflow") or {}
                    for node in wf.values() if isinstance(wf, dict) else []:
                        if isinstance(node, dict) and node.get("class_type") == "KSampler":
                            inp = node.get("inputs") or {}
                            row["cfg"] = inp.get("cfg")
                            row["sampler"] = inp.get("sampler_name")
                            row["scheduler"] = inp.get("scheduler")
                            break
                except (OSError, json.JSONDecodeError):
                    pass
            rows.append(row)
            print(f"  OK {label} {emotion}/{direc} {elapsed:.1f}s id={metrics.get('identity_proxy')}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            rows.append(
                {
                    "character_id": subject,
                    "pose_key": f"{emotion}/{direc}",
                    "seed": seed,
                    "denoise": effective_denoise,
                    "generation_time_s": round(elapsed, 3),
                    "ok": False,
                    "error": str(exc)[:800],
                    "approved": False,
                }
            )
            print(f"  FAIL {label} {emotion}/{direc}: {exc}")

    metric_rows = [r["metrics"] for r in rows if r.get("ok") and r.get("metrics")]
    sheet = _contact_sheet(paths, out_dir / "contact_sheet.jpg") if paths else None
    summary = {
        "character_id": subject,
        "channel": channel,
        "run_id": run_id,
        "denoise": effective_denoise,
        "primary_model": model_prof.get("primary_model"),
        "n_requested": len(poses),
        "n_ok": sum(1 for r in rows if r.get("ok")),
        "failure_rate": round(failures / max(len(poses), 1), 4),
        "mean_generation_time_s": round(sum(times) / len(times), 3) if times else None,
        "mean_metrics": mean_scores(metric_rows),
        "vram": _vram_hint(),
        "contact_sheet": sheet,
        "variants": rows,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    (out_dir / "benchmark.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    run_id = datetime.now(UTC).strftime("baseline_%Y%m%dT%H%M%SZ")
    print("BASELINE", run_id)
    alex = run_character(
        label="alex",
        channel="wealth_blueprint",
        workflow="WB_CHARACTER",
        subject="alex",
        anchor_name="image-1.png",
        poses=ALEX_POSES,
        denoise=None,  # profile default 0.58
        run_id=run_id,
    )
    niko = run_character(
        label="niko",
        channel="wonder_crew",
        workflow="CHARACTER_REFERENCE",
        subject="niko",
        anchor_name="image-3.png",
        poses=NIKO_POSES,
        denoise=None,  # profile default 0.55
        run_id=run_id,
    )
    report = {
        "phase": "baseline",
        "run_id": run_id,
        "alex": alex,
        "niko": niko,
        "vram": _vram_hint(),
    }
    out = OUT_ROOT / run_id / "baseline_summary.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("WROTE", out)
    print(
        "ALEX identity_proxy",
        alex["mean_metrics"].get("identity_proxy"),
        "time",
        alex["mean_generation_time_s"],
        "fail",
        alex["failure_rate"],
    )
    print(
        "NIKO identity_proxy",
        niko["mean_metrics"].get("identity_proxy"),
        "time",
        niko["mean_generation_time_s"],
        "fail",
        niko["failure_rate"],
    )


if __name__ == "__main__":
    main()
