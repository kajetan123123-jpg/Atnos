"""Live Comfy channel stabilization suite (still images only)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

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
from atnos.production.comfy_channels import (
    ChannelComfyGenerationService,
    GenerationRequest,
    run_all_channel_comfy_health,
)
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService

OUT = Path(r"E:\Atnos\App\data\generated\_stabilization")
OUT.mkdir(parents=True, exist_ok=True)


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contact_sheet(paths: list[str], dest: Path, cols: int = 2) -> str:
    imgs = [Image.open(p).convert("RGB").resize((384, 384)) for p in paths]
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 384, rows * 384), (20, 20, 20))
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols) * 384, (i // cols) * 384))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return str(dest)


def main() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    svc = ChannelComfyGenerationService(settings)
    images = ComfyUiImageGenerationService(settings)
    report: dict = {"health_before": run_all_channel_comfy_health(settings), "tests": []}

    print("REF_INFLUENCE Alex...")
    meta_with = svc.generate(
        GenerationRequest(
            channel="wealth_blueprint",
            workflow="WB_CHARACTER",
            subject="alex",
            emotion="neutral",
            direction="front",
            width=512,
            height=512,
            seed=900001,
            references=["image-1.png"],
            force_regenerate=True,
        )
    )
    out_no = OUT / "alex_no_ref.png"
    images.generate_background(
        prompt=meta_with.prompt,
        output_path=out_no,
        width=512,
        height=512,
        seed=900001,
        negative_prompt=meta_with.negative_prompt,
        preferred_checkpoint="sd_xl_turbo_1.0_fp16.safetensors",
    )
    influence = sha(meta_with.output_path) != sha(out_no)
    report["tests"].append(
        {
            "name": "alex_reference_influence",
            "pass": influence
            and meta_with.generation_settings.get("reference_conditioning") == "core_img2img",
            "conditioning": meta_with.generation_settings.get("reference_conditioning"),
            "hashes_differ": influence,
            "with_ref": meta_with.output_path,
            "without_ref": str(out_no),
        }
    )
    print("influence", influence, meta_with.generation_settings.get("reference_conditioning"))

    print("REF_INFLUENCE Niko...")
    meta_n = svc.generate(
        GenerationRequest(
            channel="wonder_crew",
            workflow="CHARACTER_REFERENCE",
            subject="niko",
            emotion="neutral",
            direction="3/4",
            width=512,
            height=512,
            seed=900002,
            references=["image-3.png"],
            force_regenerate=True,
        )
    )
    out_n0 = OUT / "niko_no_ref.png"
    images.generate_background(
        prompt=meta_n.prompt,
        output_path=out_n0,
        width=512,
        height=512,
        seed=900002,
        negative_prompt=meta_n.negative_prompt,
        preferred_checkpoint="sd_xl_turbo_1.0_fp16.safetensors",
    )
    inf_n = sha(meta_n.output_path) != sha(out_n0)
    report["tests"].append(
        {
            "name": "niko_reference_influence",
            "pass": inf_n
            and meta_n.generation_settings.get("reference_conditioning") == "core_img2img",
            "hashes_differ": inf_n,
        }
    )
    print("niko influence", inf_n)

    print("ALEX BENCH...")
    alex_paths: list[str] = []
    alex_meta: list[dict] = []
    for emo, direc, seed in [
        ("neutral", "front", 910001),
        ("explaining", "right", 910002),
        ("confused", "front", 910003),
        ("pointing", "right", 910004),
    ]:
        meta = svc.generate(
            GenerationRequest(
                channel="wealth_blueprint",
                workflow="WB_CHARACTER",
                subject="alex",
                emotion=emo,
                direction=direc,
                width=512,
                height=512,
                seed=seed,
                references=["image-1.png"],
                force_regenerate=True,
            )
        )
        alex_paths.append(meta.output_path)
        alex_meta.append(meta.model_dump(mode="json"))
        print(" alex", emo, meta.generation_settings.get("validation_verdict"), meta.backend)
    sheet_a = contact_sheet(alex_paths, OUT / "alex_contact_sheet.jpg")
    report["tests"].append(
        {
            "name": "alex_consistency_bench",
            "pass": all(Path(p).is_file() for p in alex_paths),
            "contact_sheet": sheet_a,
            "variants": alex_meta,
        }
    )

    print("NIKO BENCH...")
    niko_paths: list[str] = []
    niko_meta: list[dict] = []
    for emo, direc, seed in [
        ("neutral", "front", 920001),
        ("happy", "front", 920002),
        ("surprised", "front", 920003),
        ("friendly", "3/4", 920004),
    ]:
        meta = svc.generate(
            GenerationRequest(
                channel="wonder_crew",
                workflow="CHARACTER_REFERENCE",
                subject="niko",
                emotion=emo,
                direction=direc,
                width=512,
                height=512,
                seed=seed,
                references=["image-3.png"],
                force_regenerate=True,
            )
        )
        niko_paths.append(meta.output_path)
        niko_meta.append(meta.model_dump(mode="json"))
        print(" niko", emo, meta.backend)
    sheet_n = contact_sheet(niko_paths, OUT / "niko_contact_sheet.jpg")
    report["tests"].append(
        {
            "name": "niko_consistency_bench",
            "pass": all(Path(p).is_file() for p in niko_paths),
            "contact_sheet": sheet_n,
            "variants": niko_meta,
        }
    )

    print("WB QUALITY...")
    meta = svc.generate(
        GenerationRequest(
            channel="wealth_blueprint",
            workflow="WB_CHARACTER",
            subject="alex",
            emotion="explaining",
            direction="right",
            width=512,
            height=512,
            seed=930001,
            references=["image-1.png"],
            force_regenerate=True,
        )
    )
    report["tests"].append(
        {
            "name": "wb_alex_explaining_quality",
            "pass": meta.generation_settings.get("validation_verdict")
            in {"VALIDATED", "FAIL_QUALITY"},
            "verdict": meta.generation_settings.get("validation_verdict"),
            "alpha": meta.generation_settings.get("alpha_applied"),
            "approved": meta.generation_settings.get("approved"),
            "asset_brain": meta.generation_settings.get("asset_brain_id"),
            "path": meta.output_path,
            "gate": meta.generation_settings.get("quality_gate"),
        }
    )

    print("DC LOCS...")
    for subj, seed in [
        ("abandoned corridor at night", 940001),
        ("forest at night", 940002),
        ("motel room at night", 940003),
    ]:
        meta = svc.generate(
            GenerationRequest(
                channel="dark_chronicles",
                workflow="DC_LOCATION",
                subject=subj,
                composition="cinematic storytelling frame",
                lighting="low-key practical lights",
                width=768,
                height=512,
                seed=seed,
                force_regenerate=True,
            )
        )
        report["tests"].append(
            {
                "name": f"dc_{subj[:20].replace(' ', '_')}",
                "pass": Path(meta.output_path).is_file(),
                "path": meta.output_path,
                "model": meta.model,
                "backend": meta.backend,
            }
        )
        print(" dc", subj, meta.backend, meta.model)

    print("ROUTING...")
    wb = svc.generate(
        GenerationRequest(
            channel="wealth_blueprint",
            workflow="WB_ICON_OR_SYMBOL",
            subject="simple coin icon",
            emotion="neutral",
            width=512,
            height=512,
            seed=950001,
            force_regenerate=True,
        )
    )
    wc = svc.generate(
        GenerationRequest(
            channel="wonder_crew",
            workflow="PROP_CONCEPT",
            subject="compass prop",
            emotion="calm",
            width=512,
            height=512,
            seed=950002,
            force_regenerate=True,
        )
    )
    dc = svc.generate(
        GenerationRequest(
            channel="dark_chronicles",
            workflow="DC_ATMOSPHERE",
            subject="foggy street lamp",
            width=768,
            height=512,
            seed=950003,
            force_regenerate=True,
        )
    )
    wb_p = wb.output_path.replace("\\", "/")
    wc_p = wc.output_path.replace("\\", "/")
    dc_p = dc.output_path.replace("\\", "/")
    leak_ok = (
        "wealth_blueprint" in wb_p
        and "wonder_crew" in wc_p
        and "dark_chronicles" in dc_p
        and "wonder_crew" not in wb_p
        and "wealth_blueprint" not in wc_p
        and "wealth_blueprint" not in dc_p
    )
    report["tests"].append(
        {
            "name": "routing_isolation",
            "pass": leak_ok,
            "wb": wb.output_path,
            "wc": wc.output_path,
            "dc": dc.output_path,
        }
    )

    report["health_after"] = run_all_channel_comfy_health(settings)
    (OUT / "stabilization_live_results.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    passed = sum(1 for t in report["tests"] if t.get("pass"))
    print("DONE", passed, "/", len(report["tests"]))
    for item in report["tests"]:
        print(" ", item["name"], "PASS" if item.get("pass") else "FAIL")


if __name__ == "__main__":
    main()
