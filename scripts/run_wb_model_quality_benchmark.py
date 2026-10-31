"""Wealth Blueprint local checkpoint quality benchmark (no downloads, no Flux).

Blind A/B/C over models already on disk under ComfyUI-Shared/models/checkpoints.
Writes sheets + metrics under App/data/generated/_wb_model_quality/.
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

from PIL import Image, ImageDraw, ImageFont

from atnos.core.config import get_settings
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
)
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

OUT = Path(r"E:\Atnos\App\data\generated\_wb_model_quality")
SHARED = Path(r"E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Shared\models\checkpoints")
REFS = Path(r"E:\Atnos\App\config\channel_references\20260916")
ALEX_REF = REFS / "crops" / "alex_portrait_v1.png"
if not ALEX_REF.is_file():
    ALEX_REF = REFS / "image-1.png"

# Blind labels — mapping revealed only in sealed key file after scoring sheet is built.
CANDIDATES: list[dict[str, Any]] = [
    {
        "blind": "A",
        "checkpoint": "sd_xl_turbo_1.0_fp16.safetensors",
        "quality_mode": False,
        "steps": None,
        "role_guess": "fast",
    },
    {
        "blind": "B",
        "checkpoint": "sd_xl_base_1.0_0.9vae.safetensors",
        "quality_mode": True,
        "steps": None,
        "role_guess": "quality_editorial",
    },
    {
        "blind": "C",
        "checkpoint": "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
        "quality_mode": True,
        "steps": None,
        "role_guess": "quality_photoreal",
    },
]

SEED = 910042
WIDTH, HEIGHT = 1024, 1024
REF_DENOISE = 0.40

NEGATIVE = (
    "lowres, blurry, text, watermark, logo, neon fintech, stock businessman, "
    "luxury flex, jpeg artifacts, deformed hands, photorealistic skin pores, "
    "3d render plastic, anime, collage"
)

PROMPTS: list[dict[str, Any]] = [
    {
        "id": "alex_explaining",
        "title": "Alex explaining",
        "use_ref": True,
        "prompt": (
            "Wealth Blueprint host Alex, premium hand-drawn editorial illustration, "
            "subtle 2.5D paper texture, restrained navy cream teal gold palette. "
            "Pose: explaining with open hand gesture, confident friendly expression, facing slightly right. "
            "Full body including sneakers when pose allows. Single character still for motion design. "
            "No baked-in text or logos."
        ),
    },
    {
        "id": "business_illustration",
        "title": "business illustration",
        "use_ref": False,
        "prompt": (
            "Wealth Blueprint reusable business illustration. Premium hand-drawn editorial illustration, "
            "subtle 2.5D, paper texture, navy cream teal gold. Small business owner reviewing a simple plan "
            "at a clean desk with notebook — symbolic, not photoreal. No charts with numbers, no UI text."
        ),
    },
    {
        "id": "house_mortgage",
        "title": "house/mortgage visual",
        "use_ref": False,
        "prompt": (
            "Wealth Blueprint house and mortgage visual. Premium editorial illustration of a modest modern home "
            "with soft paper texture, symbolic key and calm neighborhood mood. Navy cream teal gold palette. "
            "No photorealism, no luxury mansion flex, no text or numbers."
        ),
    },
    {
        "id": "investment_metaphor",
        "title": "investment metaphor",
        "use_ref": False,
        "prompt": (
            "Wealth Blueprint investment metaphor. Editorial illustration of a seedling growing into a sturdy tree "
            "beside a simple coin path on textured paper. Symbolic long-term growth, restrained palette. "
            "No charts, no percentages, no logos."
        ),
    },
    {
        "id": "credit_card",
        "title": "credit card",
        "use_ref": False,
        "prompt": (
            "Wealth Blueprint credit card object still. Clean editorial illustration of a generic payment card "
            "on soft cream paper, subtle 2.5D, no brand logos, no readable numbers or text, navy teal accents."
        ),
    },
    {
        "id": "premium_background",
        "title": "premium background",
        "use_ref": False,
        "prompt": (
            "Wealth Blueprint premium vertical background. Soft abstract paper layers, gentle navy-to-cream gradient, "
            "subtle geometric texture, calm editorial atmosphere, empty composition for motion typography overlay. "
            "No objects, no people, no text."
        ),
    },
]


def _vram() -> dict[str, Any]:
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:8188/system_stats", timeout=5.0)
        r.raise_for_status()
        devices = r.json().get("devices") or []
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
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {}


def inventory() -> dict[str, Any]:
    rows = []
    for path in sorted(SHARED.glob("*.safetensors")):
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "bytes": path.stat().st_size,
                "gib": round(path.stat().st_size / (1024**3), 2),
                "is_image_candidate": "ltx" not in path.name.casefold(),
            }
        )
    return {
        "shared_checkpoints_root": str(SHARED),
        "checkpoints": rows,
        "flux2_z_image_policy": "OPTIONAL/BLOCKED — excluded from this benchmark",
        "candidates": [
            {
                "blind": c["blind"],
                "checkpoint_exists": (SHARED / c["checkpoint"]).is_file(),
                "checkpoint": c["checkpoint"],
                "quality_mode": c["quality_mode"],
            }
            for c in CANDIDATES
        ],
    }


def _heuristic_scores(path: Path, *, prompt_id: str) -> dict[str, float]:
    """Cheap automatic proxies — human sheet still owns final judgment."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    pixels = list(img.getdata())
    n = len(pixels)
    # Cleanliness / contrast: std-ish of luminance
    lum = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in pixels]
    mean_l = sum(lum) / n
    var = sum((x - mean_l) ** 2 for x in lum) / n
    contrast = min(1.0, (var**0.5) / 80.0)
    # Flatness / blank risk
    flat = 1.0 - min(1.0, abs(mean_l - 128) / 128.0)
    # Edge density via simple downsample diff
    small = img.resize((64, 64))
    sp = list(small.getdata())
    edges = 0
    for y in range(63):
        for x in range(63):
            i = y * 64 + x
            a = sp[i]
            b = sp[i + 1]
            c = sp[i + 64]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > 40:
                edges += 1
            if abs(a[0] - c[0]) + abs(a[1] - c[1]) + abs(a[2] - c[2]) > 40:
                edges += 1
    detail = min(1.0, edges / 2500.0)
    # Palette restraint: distance from navy/cream/teal cluster (rough)
    avg = [sum(ch[i] for ch in pixels) / n for i in range(3)]
    # Prefer not neon (high sat) — sat proxy
    mx, mn = max(avg), min(avg)
    sat = (mx - mn) / max(mx, 1)
    professional = max(0.0, 1.0 - max(0.0, sat - 0.45) * 2.0)
    # Photoreal skin-noise proxy for editorial prompts: high midtone grain ≠ wanted for WB
    editorial_fit = professional
    if prompt_id == "alex_explaining":
        # Prefer illustration-like mid contrast + detail, not extreme photo sharpness
        editorial_fit = 0.55 * professional + 0.25 * contrast + 0.20 * (1.0 - min(1.0, detail * 1.2))
    return {
        "composition_proxy": round(0.5 * contrast + 0.5 * detail, 4),
        "professional_look_proxy": round(professional, 4),
        "detail_proxy": round(detail, 4),
        "cleanliness_proxy": round(0.4 * contrast + 0.3 * flat + 0.3 * (1 - min(1, sat)), 4),
        "editorial_fit_proxy": round(editorial_fit, 4),
        "mean_luminance": round(mean_l, 2),
        "width": w,
        "height": h,
    }


def _approval_proxy(scores: dict[str, float], *, gen_ok: bool, seconds: float) -> bool:
    if not gen_ok:
        return False
    # Production suitability heuristic: editorial fit + cleanliness, not raw speed
    fit = float(scores.get("editorial_fit_proxy") or 0)
    clean = float(scores.get("cleanliness_proxy") or 0)
    detail = float(scores.get("detail_proxy") or 0)
    if fit < 0.42 or clean < 0.35:
        return False
    if detail < 0.12:  # blank / collapsed
        return False
    if seconds > 300:
        return False
    return True


def run_one(
    service: ComfyUiImageGenerationService,
    *,
    candidate: dict[str, Any],
    scene: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    blind = candidate["blind"]
    ckpt = candidate["checkpoint"]
    target = out_dir / f"{scene['id']}__{blind}.png"
    meta_path = out_dir / f"{scene['id']}__{blind}.json"
    if target.is_file() and meta_path.is_file() and target.stat().st_size > 1024:
        return json.loads(meta_path.read_text(encoding="utf-8"))

    vram_before = _vram()
    started = time.perf_counter()
    error = None
    result_backend = None
    try:
        ref = ALEX_REF if scene["use_ref"] and ALEX_REF.is_file() else None
        result = service.generate_background(
            prompt=scene["prompt"],
            output_path=target,
            width=WIDTH,
            height=HEIGHT,
            seed=SEED,
            negative_prompt=NEGATIVE,
            quality_mode=bool(candidate["quality_mode"]),
            preferred_checkpoint=ckpt,
            reference_image=ref,
            reference_denoise=REF_DENOISE if ref else 0.6,
        )
        result_backend = result.backend
        ok = target.is_file() and target.stat().st_size > 1024
    except ComfyUiImageGenerationError as exc:
        ok = False
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        ok = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    vram_after = _vram()
    scores = _heuristic_scores(target, prompt_id=scene["id"]) if ok else {}
    row = {
        "blind": blind,
        "scene_id": scene["id"],
        "scene_title": scene["title"],
        "load_success": True,  # checkpoint resolved if we got past queue or explicit missing error
        "generation_success": ok,
        "error": error,
        "seconds": round(elapsed, 3),
        "resolution": [WIDTH, HEIGHT],
        "seed": SEED,
        "used_reference": bool(scene["use_ref"] and ALEX_REF.is_file()),
        "reference_path": str(ALEX_REF) if scene["use_ref"] else None,
        "backend": result_backend,
        "output_path": str(target) if ok else None,
        "vram_before": vram_before,
        "vram_after": vram_after,
        "scores": scores,
        "approval_proxy": _approval_proxy(scores, gen_ok=ok, seconds=elapsed),
        # sealed — not shown on blind sheet
        "_checkpoint": ckpt,
        "_quality_mode": candidate["quality_mode"],
    }
    if error and "No ComfyUI checkpoint" in error:
        row["load_success"] = False
    meta_path.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
    return row


def build_blind_sheet(results: list[dict[str, Any]], path: Path) -> None:
    """Contact sheet labeled only A/B/C — no model names."""
    cell_w, cell_h = 320, 320
    label_h = 36
    cols = 3
    rows = len(PROMPTS)
    sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + label_h)), (245, 242, 235))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    by_key = {(r["scene_id"], r["blind"]): r for r in results}
    for ri, scene in enumerate(PROMPTS):
        for ci, blind in enumerate(("A", "B", "C")):
            x0 = ci * cell_w
            y0 = ri * (cell_h + label_h)
            draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + label_h - 1], fill=(30, 40, 55))
            draw.text((x0 + 8, y0 + 8), f"{scene['title']} · {blind}", fill=(240, 240, 240), font=font)
            row = by_key.get((scene["id"], blind))
            box = (x0, y0 + label_h, x0 + cell_w, y0 + label_h + cell_h)
            if row and row.get("generation_success") and row.get("output_path"):
                img = Image.open(row["output_path"]).convert("RGB")
                img.thumbnail((cell_w, cell_h))
                ox = x0 + (cell_w - img.width) // 2
                oy = y0 + label_h + (cell_h - img.height) // 2
                sheet.paste(img, (ox, oy))
            else:
                draw.rectangle(box, fill=(60, 60, 60))
                msg = (row or {}).get("error") or "FAIL"
                draw.text((x0 + 12, y0 + label_h + 20), str(msg)[:48], fill=(220, 120, 120), font=font)
    sheet.save(path)


def summarize(results: list[dict[str, Any]], inv: dict[str, Any]) -> dict[str, Any]:
    by_blind: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for row in results:
        by_blind[row["blind"]].append(row)

    per_model = {}
    for blind, rows in by_blind.items():
        ok_rows = [r for r in rows if r["generation_success"]]
        times = [float(r["seconds"]) for r in ok_rows]
        approvals = sum(1 for r in rows if r.get("approval_proxy"))
        mean_t = sum(times) / len(times) if times else None
        # Extrapolate accepted assets/hour from approval_proxy and mean time
        accepted_per_hour = None
        if mean_t and mean_t > 0:
            accepted_per_hour = round(approvals / len(rows) * (3600.0 / mean_t), 2)
        ckpt = next(c["checkpoint"] for c in CANDIDATES if c["blind"] == blind)
        per_model[blind] = {
            "checkpoint": ckpt,
            "load_success_rate": round(sum(1 for r in rows if r["load_success"]) / max(len(rows), 1), 4),
            "generation_success_rate": round(len(ok_rows) / max(len(rows), 1), 4),
            "mean_seconds": round(mean_t, 3) if mean_t is not None else None,
            "approval_proxy_rate": round(approvals / max(len(rows), 1), 4),
            "accepted_assets_per_hour_proxy": accepted_per_hour,
            "mean_editorial_fit": round(
                sum(float((r.get("scores") or {}).get("editorial_fit_proxy") or 0) for r in ok_rows)
                / max(len(ok_rows), 1),
                4,
            )
            if ok_rows
            else None,
            "mean_professional": round(
                sum(float((r.get("scores") or {}).get("professional_look_proxy") or 0) for r in ok_rows)
                / max(len(ok_rows), 1),
                4,
            )
            if ok_rows
            else None,
            "mean_detail": round(
                sum(float((r.get("scores") or {}).get("detail_proxy") or 0) for r in ok_rows) / max(len(ok_rows), 1),
                4,
            )
            if ok_rows
            else None,
            "mean_cleanliness": round(
                sum(float((r.get("scores") or {}).get("cleanliness_proxy") or 0) for r in ok_rows)
                / max(len(ok_rows), 1),
                4,
            )
            if ok_rows
            else None,
            "scenes": {r["scene_id"]: {"ok": r["generation_success"], "s": r["seconds"], "approve": r.get("approval_proxy")} for r in rows},
        }

    # Rank: prioritize approval throughput, then editorial fit, then time
    ranked = sorted(
        per_model.items(),
        key=lambda kv: (
            -(kv[1]["accepted_assets_per_hour_proxy"] or 0),
            -(kv[1]["mean_editorial_fit"] or 0),
            (kv[1]["mean_seconds"] or 9999),
        ),
    )

    # Two-tier recommendation from local-only results
    # FAST: highest accepted/hour among successful gens with mean_seconds low
    fast = min(
        ((b, m) for b, m in per_model.items() if m["generation_success_rate"] >= 0.8),
        key=lambda kv: (kv[1]["mean_seconds"] or 9999),
        default=(None, None),
    )
    # QUALITY: best editorial_fit among non-fast, or best overall editorial if tied
    quality = max(
        ((b, m) for b, m in per_model.items() if m["generation_success_rate"] >= 0.5),
        key=lambda kv: (
            kv[1]["mean_editorial_fit"] or 0,
            kv[1]["mean_professional"] or 0,
            -(kv[1]["mean_seconds"] or 9999),
        ),
        default=(None, None),
    )

    return {
        "inventory": inv,
        "per_blind": per_model,
        "ranking_blind": [b for b, _ in ranked],
        "fast_blind": fast[0],
        "quality_blind": quality[0],
        "key": {c["blind"]: c["checkpoint"] for c in CANDIDATES},
        "no_flux_dependency": True,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    gens = OUT / "generations"
    gens.mkdir(parents=True, exist_ok=True)

    inv = inventory()
    (OUT / "inventory.json").write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
    missing = [c for c in inv["candidates"] if not c["checkpoint_exists"]]
    if missing:
        print("MISSING checkpoints:", missing)
        return 2

    settings = get_settings()
    service = ComfyUiImageGenerationService(settings)
    lock = GpuRuntimeLockService(settings)
    lease = lock.acquire(owner="wb-model-quality-bench", timeout_s=600)
    results: list[dict[str, Any]] = []
    try:
        for scene in PROMPTS:
            for cand in CANDIDATES:
                print(f"RUN {scene['id']} / {cand['blind']} ({cand['checkpoint']}) ...", flush=True)
                row = run_one(service, candidate=cand, scene=scene, out_dir=gens)
                # Strip sealed fields from console
                print(
                    f"  -> ok={row['generation_success']} t={row['seconds']}s approve={row.get('approval_proxy')}",
                    flush=True,
                )
                results.append(row)
    finally:
        try:
            lock.release(lease)
        except Exception:
            pass

    # Blind results (no checkpoint names)
    blind_public = []
    for row in results:
        pub = {k: v for k, v in row.items() if not k.startswith("_")}
        blind_public.append(pub)
    (OUT / "results_blind.json").write_text(json.dumps(blind_public, indent=2) + "\n", encoding="utf-8")
    (OUT / "results_full.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    sheet_path = OUT / "blind_comparison_sheet.png"
    build_blind_sheet(results, sheet_path)

    summary = summarize(results, inv)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (OUT / "blind_key.json").write_text(
        json.dumps({"reveal_after_scoring": summary["key"], "fast_blind": summary["fast_blind"], "quality_blind": summary["quality_blind"]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: summary[k] for k in ("fast_blind", "quality_blind", "ranking_blind", "per_blind")}, indent=2))
    print("sheet:", sheet_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
