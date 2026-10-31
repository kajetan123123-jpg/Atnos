"""Wealth Blueprint cutout / alpha quality: metrics, QC gate, safe postprocess.

Works with local U2-Net ONNX. Does not download alternate models.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageFilter

PostprocessMode = Literal["none", "legacy", "improved", "solid_paper"]


@dataclass(slots=True)
class AlphaQcResult:
    passed: bool
    verdict: str  # PASS | NEEDS_REPROCESS | REJECTED
    checks: list[dict[str, Any]]
    metrics: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "checks": self.checks,
            "metrics": self.metrics,
        }


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def load_rgba(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with Image.open(path) as img:
        rgba = np.asarray(img.convert("RGBA"), dtype=np.float32)
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3] / 255.0
    return rgb, alpha


def compute_cutout_metrics(path: Path) -> dict[str, float]:
    """Heuristic QC signals for transparent cutouts (not ground-truth matting)."""
    rgb, alpha = load_rgba(path)
    h, w = alpha.shape
    area = float(h * w)
    fg = alpha >= 0.5
    soft = (alpha > 0.08) & (alpha < 0.92)
    fg_area = float(fg.mean())
    soft_frac = float(soft.mean())

    # Border contamination: opaque-ish pixels on outer frame
    border = np.zeros_like(alpha, dtype=bool)
    band = max(2, min(h, w) // 40)
    border[:band, :] = True
    border[-band:, :] = True
    border[:, :band] = True
    border[:, -band:] = True
    border_opacity = float(alpha[border].mean()) if border.any() else 0.0
    bg_remnant = float(((alpha >= 0.5) & border).mean())

    # Holes: low-alpha islands inside dilated FG bbox (approx via FG interior)
    from PIL import Image as PILImage
    from PIL import ImageFilter as PILFilter

    hard = PILImage.fromarray((fg.astype(np.uint8) * 255))
    filled = hard.filter(PILFilter.MaxFilter(5)).filter(PILFilter.MinFilter(5))
    filled_arr = np.asarray(filled) > 127
    holes = float(((~fg) & filled_arr).mean())

    # Jaggedness: perimeter / sqrt(area) of hard mask
    erode = np.asarray(hard.filter(PILFilter.MinFilter(3))) > 127
    perimeter = float(np.logical_xor(fg, erode).sum())
    jagged = perimeter / max(np.sqrt(max(fg.sum(), 1.0)), 1.0)

    # Halo / color fringe: high chroma in semi-transparent band near edge
    edge = soft & (np.asarray(hard.filter(PILFilter.MaxFilter(3))) > 127)
    if edge.any():
        region = rgb[edge]
        # chroma proxy: max-min across channels
        chroma = (region.max(axis=1) - region.min(axis=1)).mean() / 255.0
        # bright near-white remnants (classic halo)
        bright = float(((region.mean(axis=1) > 200) & (chroma < 0.15)).mean())
    else:
        chroma = 0.0
        bright = 0.0

    # Hair / semi-transparent quality in upper third
    upper = alpha[: max(1, h // 3), :]
    hair_soft = float(((upper > 0.05) & (upper < 0.85)).mean())

    # Almost empty / giant opaque
    almost_empty = fg_area < 0.02
    giant_opaque_bg = border_opacity > 0.35 and bg_remnant > 0.025

    return {
        "fg_area_frac": round(fg_area, 5),
        "soft_edge_frac": round(soft_frac, 5),
        "border_opacity_mean": round(border_opacity, 5),
        "bg_remnant_frac": round(bg_remnant, 5),
        "hole_frac": round(holes, 5),
        "jagged_index": round(jagged, 4),
        "edge_chroma": round(float(chroma), 5),
        "halo_bright_frac": round(bright, 5),
        "hair_soft_frac": round(hair_soft, 5),
        "almost_empty": 1.0 if almost_empty else 0.0,
        "giant_opaque_bg": 1.0 if giant_opaque_bg else 0.0,
        "width": float(w),
        "height": float(h),
    }


def run_alpha_qc(path: Path, *, require_alpha: bool = True) -> AlphaQcResult:
    """Post-cutout validation. FAIL → NEEDS_REPROCESS (recoverable) or REJECTED (empty/corrupt)."""
    checks: list[dict[str, Any]] = []
    if not path.is_file():
        return AlphaQcResult(
            passed=False,
            verdict="REJECTED",
            checks=[_check("file_exists", False, str(path))],
            metrics={},
        )
    try:
        with Image.open(path) as img:
            img.load()
            mode = img.mode
            has_alpha = mode in {"RGBA", "LA"} or ("transparency" in img.info)
    except OSError as exc:
        return AlphaQcResult(
            passed=False,
            verdict="REJECTED",
            checks=[_check("decode", False, str(exc))],
            metrics={},
        )

    if require_alpha:
        checks.append(_check("alpha_exists", has_alpha, f"mode={mode}"))
        if not has_alpha:
            return AlphaQcResult(passed=False, verdict="NEEDS_REPROCESS", checks=checks, metrics={})

    metrics = compute_cutout_metrics(path)
    fg = metrics["fg_area_frac"]
    sensible = 0.04 <= fg <= 0.92
    checks.append(_check("foreground_area_sensible", sensible, f"fg_area_frac={fg:.4f}"))

    soft = metrics["soft_edge_frac"]
    # Pathological: almost all soft (mush) or zero soft with severe jagged leftover handled separately
    edge_ok = soft < 0.35
    checks.append(_check("edge_alpha_not_pathological", edge_ok, f"soft_edge_frac={soft:.4f}"))

    no_giant = metrics["giant_opaque_bg"] < 0.5
    checks.append(
        _check(
            "no_giant_opaque_background",
            no_giant,
            f"border_opacity={metrics['border_opacity_mean']:.3f} remnant={metrics['bg_remnant_frac']:.4f}",
        )
    )

    not_empty = metrics["almost_empty"] < 0.5
    checks.append(_check("no_almost_empty_result", not_empty, f"fg_area_frac={fg:.4f}"))

    # Auxiliary contamination signals (warn-level unless severe)
    halo_ok = metrics["halo_bright_frac"] <= 0.45 and metrics["edge_chroma"] <= 0.55
    checks.append(
        _check(
            "halo_bg_contamination",
            halo_ok,
            f"halo_bright={metrics['halo_bright_frac']:.3f} edge_chroma={metrics['edge_chroma']:.3f}",
        )
    )
    holes_ok = metrics["hole_frac"] <= 0.04
    checks.append(_check("no_large_holes", holes_ok, f"hole_frac={metrics['hole_frac']:.4f}"))

    failed = [c for c in checks if not c["ok"]]
    if not failed:
        return AlphaQcResult(passed=True, verdict="PASS", checks=checks, metrics=metrics)
    # Empty / no alpha → REJECTED; otherwise recoverable reprocess
    hard_fail_names = {"alpha_exists", "no_almost_empty_result", "decode", "file_exists"}
    if any(c["name"] in hard_fail_names for c in failed):
        verdict = "REJECTED"
    else:
        verdict = "NEEDS_REPROCESS"
    return AlphaQcResult(passed=False, verdict=verdict, checks=checks, metrics=metrics)


def _largest_component_mask(binary: np.ndarray) -> np.ndarray:
    """Keep largest connected True component (4-connectivity) via scipy-free BFS."""
    h, w = binary.shape
    visited = np.zeros_like(binary, dtype=bool)
    best_coords: list[tuple[int, int]] = []
    for y in range(h):
        for x in range(w):
            if not binary[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            coords = [(y, x)]
            while stack:
                cy, cx = stack.pop()
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < h and 0 <= nx < w and binary[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
                        coords.append((ny, nx))
            if len(coords) > len(best_coords):
                best_coords = coords
    out = np.zeros_like(binary, dtype=bool)
    for y, x in best_coords:
        out[y, x] = True
    return out if best_coords else binary


def improve_alpha_mask(
    rgb: Image.Image,
    alpha_img: Image.Image,
    *,
    mode: PostprocessMode = "improved",
) -> Image.Image:
    """Safe post-cutout cleanup. Avoid aggressive erode/dilate."""
    if mode == "none":
        return alpha_img

    alpha = np.asarray(alpha_img.convert("L"), dtype=np.float32) / 255.0
    if mode == 'solid_paper':
        # Explicitly opt-in for closed, convex paper props ONLY, never characters,
        # handles or perforated objects. Fill bright paper mistakenly removed by saliency.
        from PIL import ImageDraw
        points=[]
        for y,row in enumerate(alpha >= .5):
            xs=np.flatnonzero(row)
            if len(xs):points.extend([(int(xs[0]),y),(int(xs[-1]),y)])
        points=sorted(set(points))
        if len(points)<3:raise ValueError('No solid paper silhouette found')
        def cross(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
        def half(seq):
            hull=[]
            for point in seq:
                while len(hull)>=2 and cross(hull[-2],hull[-1],point)<=0:hull.pop()
                hull.append(point)
            return hull
        hull=half(points)[:-1]+half(reversed(points))[:-1]
        solid=Image.new('L',alpha_img.size,0)
        ImageDraw.Draw(solid).polygon(hull,fill=255)
        return solid.filter(ImageFilter.GaussianBlur(.6))
    if mode == "legacy":
        # Historical path from local_cutout_service
        alpha = np.clip((alpha - 0.10) / 0.80, 0.0, 1.0)
        return Image.fromarray((alpha * 255).astype(np.uint8), mode="L")

    # --- improved ---
    # 1) Soft contrast stretch (slightly gentler than legacy)
    alpha = np.clip((alpha - 0.06) / 0.88, 0.0, 1.0)

    # 2) Tiny morphological cleanup via Max/MinFilter (1px-ish)
    hard = Image.fromarray(((alpha >= 0.45) * 255).astype(np.uint8))
    if hard.mode != "L":
        hard = hard.convert("L")
    # close small holes then open speckles — radius 3 only
    cleaned = hard.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    cleaned = cleaned.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    hard_arr = np.asarray(cleaned) > 127

    # 3) Keep largest component (drops floating bg blobs)
    if hard_arr.any():
        hard_arr = _largest_component_mask(hard_arr)

    # 4) Feather edges lightly
    soft_mask = Image.fromarray((hard_arr.astype(np.uint8) * 255)).convert("L").filter(
        ImageFilter.GaussianBlur(radius=0.9)
    )
    soft = np.asarray(soft_mask, dtype=np.float32) / 255.0
    # Blend original soft alpha with cleaned feather (preserve hair wisps slightly)
    alpha = np.clip(0.65 * soft + 0.35 * alpha * soft, 0.0, 1.0)

    # 5) Mild decontamination hint stored by darkening fringe RGB later in compose
    return Image.fromarray((alpha * 255).astype(np.uint8)).convert("L")


def decontaminate_fringe(rgb: Image.Image, alpha_img: Image.Image) -> Image.Image:
    """Pull near-edge RGB toward nearby opaque foreground (reduces light halos)."""
    rgb_arr = np.asarray(rgb.convert("RGB"), dtype=np.float32)
    alpha = np.asarray(alpha_img.convert("L"), dtype=np.float32) / 255.0
    opaque = alpha >= 0.85
    fringe = (alpha > 0.08) & (alpha < 0.75)
    if not fringe.any() or not opaque.any():
        return rgb
    # Local mean of opaque neighbors via blur of masked RGB
    blurred = Image.fromarray(rgb_arr.astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius=2.0))
    blur_arr = np.asarray(blurred, dtype=np.float32)
    # Where fringe, lerp toward blurred opaque neighborhood
    out = rgb_arr.copy()
    t = 0.55
    out[fringe] = (1.0 - t) * rgb_arr[fringe] + t * blur_arr[fringe]
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def compose_rgba(rgb: Image.Image, alpha_img: Image.Image, *, decontaminate: bool = True) -> Image.Image:
    if decontaminate:
        rgb = decontaminate_fringe(rgb, alpha_img)
    out = rgb.convert("RGBA")
    out.putalpha(alpha_img.convert("L"))
    return out


def quality_score(metrics: dict[str, float]) -> float:
    """Higher is better — used for before/after ranking only."""
    score = 1.0
    score -= min(0.35, metrics.get("bg_remnant_frac", 0) * 4)
    score -= min(0.25, metrics.get("halo_bright_frac", 0) * 0.5)
    score -= min(0.20, metrics.get("hole_frac", 0) * 8)
    score -= min(0.15, max(0.0, metrics.get("jagged_index", 0) - 8.0) * 0.02)
    score -= min(0.20, max(0.0, metrics.get("soft_edge_frac", 0) - 0.12) * 1.5)
    if metrics.get("almost_empty", 0) > 0.5:
        score -= 0.5
    if metrics.get("giant_opaque_bg", 0) > 0.5:
        score -= 0.4
    # Prefer sensible FG area around 0.15–0.65
    fg = metrics.get("fg_area_frac", 0)
    if 0.1 <= fg <= 0.7:
        score += 0.05
    return round(max(0.0, min(1.0, score)), 4)
