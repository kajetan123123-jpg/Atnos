"""After-tuning consistency run: portrait crops + lower denoise, same seeds as baseline."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")
os.environ.setdefault("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
os.environ.setdefault("COMFYUI_MODELS_DIR", r"E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Shared\models")
os.environ.setdefault("COMFYUI_INSTALL_DIR", r"E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Installs\Atnos")
os.environ.setdefault("COMFYUI_ALLOW_MANAGED_LAUNCH", "true")
os.environ.setdefault("DATA_DIR", r"E:\Atnos\App\data")
os.environ.setdefault("COMFYUI_LOWVRAM", "true")
os.environ.setdefault("COMFYUI_DEFAULT_CHECKPOINT", "sd_xl_turbo_1.0_fp16.safetensors")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atnos.core.config import get_settings
from atnos.production.comfy_channels.profiles import persist_builtin_profiles
from atnos.production.comfy_channels.reference_preprocess import build_portrait_crops
from run_character_consistency_benchmark import ALEX_POSES, NIKO_POSES, run_character


def main() -> None:
    crops = build_portrait_crops()
    print("CROPS", crops)
    get_settings.cache_clear()
    persist_builtin_profiles(get_settings())

    run_id = datetime.now(UTC).strftime("tuned_%Y%m%dT%H%M%SZ")
    print("TUNED", run_id, "denoise=0.48 portrait crops")
    alex = run_character(
        label="alex",
        channel="wealth_blueprint",
        workflow="WB_CHARACTER",
        subject="alex",
        anchor_name="alex_portrait_v1.png",
        poses=ALEX_POSES,
        denoise=0.48,
        run_id=run_id,
    )
    niko = run_character(
        label="niko",
        channel="wonder_crew",
        workflow="CHARACTER_REFERENCE",
        subject="niko",
        anchor_name="niko_portrait_v1.png",
        poses=NIKO_POSES,
        denoise=0.48,
        run_id=run_id,
    )
    base_path = Path(
        r"E:\Atnos\App\data\generated\_consistency_bench\baseline_20260918T061319Z\baseline_summary.json"
    )
    baseline = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else {}
    report = {
        "phase": "tuned_portrait_crop_denoise_048",
        "run_id": run_id,
        "fix": {
            "root_cause": "Full expression sheets used as img2img init — multi-face+text diluted identity",
            "minimal_fix": "Portrait crops from sheets + denoise 0.58/0.55 -> 0.48",
        },
        "alex": alex,
        "niko": niko,
        "delta": {
            "alex_identity_proxy": {
                "before": (baseline.get("alex") or {}).get("mean_metrics", {}).get("identity_proxy"),
                "after": alex["mean_metrics"].get("identity_proxy"),
            },
            "niko_identity_proxy": {
                "before": (baseline.get("niko") or {}).get("mean_metrics", {}).get("identity_proxy"),
                "after": niko["mean_metrics"].get("identity_proxy"),
            },
            "alex_time_s": {
                "before": (baseline.get("alex") or {}).get("mean_generation_time_s"),
                "after": alex["mean_generation_time_s"],
            },
            "niko_time_s": {
                "before": (baseline.get("niko") or {}).get("mean_generation_time_s"),
                "after": niko["mean_generation_time_s"],
            },
        },
    }
    out = Path(r"E:\Atnos\App\data\generated\_consistency_bench") / run_id / "tuned_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("WROTE", out)
    print("DELTA", json.dumps(report["delta"], indent=2))


if __name__ == "__main__":
    main()
