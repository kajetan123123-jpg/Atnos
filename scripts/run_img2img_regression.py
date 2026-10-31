"""Regression: full Alex/Niko consistency series after img2img preset change.

Reject if series mean identity drops vs tuned_20260918 portrait+0.48 baseline.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")
os.environ.setdefault("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
os.environ.setdefault("COMFYUI_MODELS_DIR", r"E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Shared\models")
os.environ.setdefault("COMFYUI_INSTALL_DIR", r"E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Installs\Atnos")
os.environ.setdefault("COMFYUI_ALLOW_MANAGED_LAUNCH", "true")
os.environ.setdefault("DATA_DIR", r"E:\Atnos\App\data")
os.environ.setdefault("COMFYUI_LOWVRAM", "true")

from atnos.production.comfy_channels.model_profiles import load_model_profile
from atnos.core.config import get_settings
from run_character_consistency_benchmark import ALEX_POSES, NIKO_POSES, run_character

PRIOR = Path(r"E:\Atnos\App\data\generated\_consistency_bench\tuned_20260918T062720Z\tuned_summary.json")
OUT_ROOT = Path(r"E:\Atnos\App\data\generated\_consistency_bench")


def main() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    wb = load_model_profile(settings, "wealth_blueprint")  # type: ignore[arg-type]
    wc = load_model_profile(settings, "wonder_crew")  # type: ignore[arg-type]
    wb_d = float(wb.get("reference_denoise") or 0.48)
    wc_d = float(wc.get("reference_denoise") or 0.48)

    run_id = datetime.now(UTC).strftime("regress_img2img_%Y%m%dT%H%M%SZ")
    print(f"REGRESSION {run_id} WB denoise={wb_d} WC denoise={wc_d}")

    alex = run_character(
        label="alex",
        channel="wealth_blueprint",
        workflow="WB_CHARACTER",
        subject="alex",
        anchor_name="alex_portrait_v1.png",
        poses=ALEX_POSES,
        denoise=wb_d,
        run_id=run_id,
    )
    niko = run_character(
        label="niko",
        channel="wonder_crew",
        workflow="CHARACTER_REFERENCE",
        subject="niko",
        anchor_name="niko_portrait_v1.png",
        poses=NIKO_POSES,
        denoise=wc_d,
        run_id=run_id,
    )

    prior = json.loads(PRIOR.read_text(encoding="utf-8")) if PRIOR.is_file() else {}
    prior_alex = float((((prior.get("alex") or {}).get("mean_metrics") or {}).get("identity_proxy") or 0.462))
    prior_niko = float((((prior.get("niko") or {}).get("mean_metrics") or {}).get("identity_proxy") or 0.620))
    # Also accept from nested structure of tuned_summary
    if "characters" in prior:
        for ch in prior.get("characters") or []:
            if ch.get("character_id") == "alex":
                prior_alex = float((ch.get("mean_metrics") or {}).get("identity_proxy") or prior_alex)
            if ch.get("character_id") == "niko":
                prior_niko = float((ch.get("mean_metrics") or {}).get("identity_proxy") or prior_niko)

    alex_id = float((alex.get("mean_metrics") or {}).get("identity_proxy") or 0)
    niko_id = float((niko.get("mean_metrics") or {}).get("identity_proxy") or 0)
    prior_mean = (prior_alex + prior_niko) / 2
    new_mean = (alex_id + niko_id) / 2

    # Reject if series mean drops by more than 0.01 (absolute)
    accept = new_mean >= (prior_mean - 0.01) and alex_id >= (prior_alex - 0.02) and niko_id >= (prior_niko - 0.02)

    summary = {
        "run_id": run_id,
        "presets": {
            "WB_IMG2IMG_V1_denoise": wb_d,
            "WC_IMG2IMG_V1_denoise": wc_d,
            "wb_preset": wb.get("img2img_preset"),
            "wc_preset": wc.get("img2img_preset"),
        },
        "prior": {"alex_identity": prior_alex, "niko_identity": prior_niko, "series_mean": prior_mean},
        "after": {
            "alex_identity": alex_id,
            "niko_identity": niko_id,
            "series_mean": new_mean,
            "alex_time": alex.get("mean_generation_time_s"),
            "niko_time": niko.get("mean_generation_time_s"),
        },
        "delta_series_mean": round(new_mean - prior_mean, 4),
        "accept": accept,
        "alex": alex,
        "niko": niko,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    out = OUT_ROOT / run_id / "regression_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"prior_mean={prior_mean:.4f} after_mean={new_mean:.4f} delta={new_mean-prior_mean:+.4f} ACCEPT={accept}")
    if not accept:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
