"""Build WB cutout QC dataset + benchmark U2-Net current vs improved postprocess.

Does not download new segmentation models. Alternatives listed only if present on E:.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

os.environ.setdefault("APP_ROOT", r"E:\Atnos\App")

from atnos.production.cutout_quality import compute_cutout_metrics, quality_score, run_alpha_qc
from atnos.services.local_cutout_service import remove_background

ROOT = Path(r"E:\Atnos\App\data\generated\_cutout_qc")
SRC = ROOT / "dataset"
OUT = ROOT / "results"
REFS = Path(r"E:\Atnos\App\config\channel_references\20260916")


def _paste_on_bg(fg: Image.Image, bg_color: tuple[int, int, int], size=(640, 640)) -> Image.Image:
    canvas = Image.new("RGB", size, bg_color)
    fg_rgb = fg.convert("RGBA")
    # Fit subject
    max_side = int(min(size) * 0.78)
    fg_rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    x = (size[0] - fg_rgb.width) // 2
    y = (size[1] - fg_rgb.height) // 2
    canvas.paste(fg_rgb, (x, y), fg_rgb if fg_rgb.mode == "RGBA" else None)
    return canvas


def _synth_complex(size=(640, 640)) -> Image.Image:
    img = Image.new("RGB", size, (245, 240, 230))
    d = ImageDraw.Draw(img)
    d.ellipse((180, 120, 460, 420), fill=(40, 90, 160))
    d.rectangle((250, 300, 390, 520), fill=(200, 60, 50))
    d.polygon([(320, 80), (380, 200), (260, 200)], fill=(230, 190, 40))
    d.ellipse((200, 160, 280, 240), fill=(250, 250, 250))
    d.ellipse((360, 160, 440, 240), fill=(250, 250, 250))
    return img


def _synth_thin(size=(640, 640)) -> Image.Image:
    img = Image.new("RGB", size, (250, 248, 242))
    d = ImageDraw.Draw(img)
    # Thin plant-like stems / wires
    for i, x in enumerate(range(220, 420, 28)):
        d.line([(x, 520), (x + (i % 3 - 1) * 20, 140)], fill=(30, 120, 50), width=3)
        d.ellipse((x - 8, 120, x + 12, 145), fill=(200, 40, 40))
    return img


def _synth_high_contrast(size=(640, 640)) -> Image.Image:
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((160, 100, 480, 540), radius=40, fill=(10, 10, 12))
    d.ellipse((250, 160, 390, 300), fill=(30, 30, 35))
    return img


def _synth_low_contrast(size=(640, 640)) -> Image.Image:
    img = Image.new("RGB", size, (236, 230, 218))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((170, 120, 470, 520), radius=36, fill=(210, 200, 185))
    d.ellipse((250, 180, 390, 320), fill=(200, 190, 175))
    return img


def build_dataset() -> list[dict]:
    SRC.mkdir(parents=True, exist_ok=True)
    portrait = REFS / "crops" / "alex_portrait_v1.png"
    front = REFS / "crops" / "alex_front_v1.png"
    sheet = REFS / "image-1.png"
    rows = []

    def add(case_id: str, image: Image.Image, notes: str) -> None:
        path = SRC / f"{case_id}.png"
        image.save(path)
        rows.append({"case_id": case_id, "path": str(path), "notes": notes})

    if portrait.is_file():
        with Image.open(portrait) as im:
            # Hair: upper crop on cream paper
            hair = im.crop((0, 0, im.width, int(im.height * 0.45)))
            add("alex_hair", _paste_on_bg(hair, (245, 240, 230)), "Alex hair wisps on cream")
            # Torso-ish: full portrait
            add("alex_torso", _paste_on_bg(im, (245, 240, 230)), "Alex bust/torso on cream")
    if front.is_file():
        with Image.open(front) as im:
            add("alex_hands", _paste_on_bg(im, (250, 248, 242)), "Alex full-front (hands in frame) on cream")
    elif sheet.is_file():
        with Image.open(sheet) as im:
            # Approximate hand/gesture panel
            w, h = im.size
            panel = im.crop((int(0.35 * w), int(0.15 * h), int(0.55 * w), int(0.55 * h)))
            add("alex_hands", _paste_on_bg(panel, (250, 248, 242)), "Alex gesture panel from sheet")

    add("complex_object", _synth_complex(), "Multi-shape overlapping object")
    add("thin_object", _synth_thin(), "Thin stems / wires")
    add("high_contrast", _synth_high_contrast(), "Dark subject on white")
    add("low_contrast", _synth_low_contrast(), "Low-contrast cream subject")

    (SRC / "manifest.json").write_text(json.dumps({"cases": rows, "created_at": datetime.now(UTC).isoformat()}, indent=2) + "\n")
    return rows


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return None


def _vram() -> dict:
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:8188/system_stats", timeout=3.0)
        devices = (r.json().get("devices") or [])
        if devices:
            d0 = devices[0]
            return {"torch_vram_free": d0.get("torch_vram_free"), "name": d0.get("name")}
    except Exception:
        pass
    return {"note": "cutout runs on CPU (onnxruntime CPUExecutionProvider)"}


def discover_local_alternatives() -> list[dict]:
    root = Path(r"E:\Atnos\Models\background-removal")
    found = []
    if root.is_dir():
        for p in root.glob("*.onnx"):
            found.append({"path": str(p), "name": p.name, "bytes": p.stat().st_size})
    return found


def run_benchmark(cases: list[dict]) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    modes = ["legacy", "improved"]
    results = []
    alts = discover_local_alternatives()
    # Warmup once
    remove_background(Path(cases[0]["path"]), OUT / "_warmup.png", postprocess="legacy", run_qc=False)

    for case in cases:
        src = Path(case["path"])
        row = {"case_id": case["case_id"], "notes": case["notes"], "modes": {}}
        for mode in modes:
            dest = OUT / f"{case['case_id']}__{mode}.png"
            rss0 = _rss_mb()
            t0 = time.perf_counter()
            meta = remove_background(src, dest, postprocess=mode, run_qc=True)  # type: ignore[arg-type]
            elapsed = time.perf_counter() - t0
            rss1 = _rss_mb()
            metrics = compute_cutout_metrics(dest)
            qc = run_alpha_qc(dest)
            row["modes"][mode] = {
                "time_s": round(elapsed, 3),
                "rss_mb_delta": None if rss0 is None or rss1 is None else round(rss1 - rss0, 1),
                "rss_mb": rss1,
                "quality_score": quality_score(metrics),
                "metrics": metrics,
                "alpha_qc": qc.as_dict(),
                "output": str(dest),
                "cutout_meta": {k: meta.get(k) for k in ("postprocess", "decontaminate", "method")},
            }
            print(
                f"  {case['case_id']:16s} {mode:9s} t={elapsed:5.2f}s "
                f"q={quality_score(metrics):.3f} qc={qc.verdict}"
            )
        results.append(row)

    # Aggregate
    summary_modes = {}
    for mode in modes:
        times = [r["modes"][mode]["time_s"] for r in results]
        quals = [r["modes"][mode]["quality_score"] for r in results]
        passes = sum(1 for r in results if r["modes"][mode]["alpha_qc"]["passed"])
        summary_modes[mode] = {
            "mean_time_s": round(sum(times) / len(times), 3),
            "mean_quality": round(sum(quals) / len(quals), 4),
            "alpha_qc_pass": passes,
            "alpha_qc_total": len(results),
        }

    improved_better = sum(
        1
        for r in results
        if r["modes"]["improved"]["quality_score"] >= r["modes"]["legacy"]["quality_score"]
    )
    selected = "improved" if summary_modes["improved"]["mean_quality"] >= summary_modes["legacy"]["mean_quality"] else "legacy"

    report = {
        "run_id": datetime.now(UTC).strftime("cutout_%Y%m%dT%H%M%SZ"),
        "cases": results,
        "summary": summary_modes,
        "improved_wins_or_ties": improved_better,
        "selected_solution": selected,
        "local_onnx_models": alts,
        "alternative_benchmark": {
            "attempted": False,
            "reason": (
                "Only u2net.onnx present under E:/Atnos/Models/background-removal. "
                "No local isnet/bria/rembg-alt to compare without downloading a new large model."
            ),
        },
        "vram": _vram(),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    (OUT / "benchmark_summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    print("Building cutout QC dataset...")
    cases = build_dataset()
    print(f"{len(cases)} cases")
    print("Benchmarking U2-Net legacy vs improved...")
    try:
        report = run_benchmark(cases)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps(report["summary"], indent=2))
    print("selected=", report["selected_solution"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
