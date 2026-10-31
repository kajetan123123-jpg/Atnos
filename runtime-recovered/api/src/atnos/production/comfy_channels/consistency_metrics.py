"""Lightweight image similarity helpers for character QC (no new heavy models)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _load_rgb(path: Path, size: tuple[int, int] = (256, 256)) -> np.ndarray:
    with Image.open(path) as img:
        return np.asarray(img.convert("RGB").resize(size, Image.Resampling.LANCZOS), dtype=np.float32)


def _hist_cosine(a: np.ndarray, b: np.ndarray, bins: int = 32) -> float:
    """Cosine similarity of concatenated per-channel histograms."""
    ha = np.concatenate([np.histogram(a[:, :, c], bins=bins, range=(0, 255))[0].astype(np.float64) for c in range(3)])
    hb = np.concatenate([np.histogram(b[:, :, c], bins=bins, range=(0, 255))[0].astype(np.float64) for c in range(3)])
    na, nb = np.linalg.norm(ha), np.linalg.norm(hb)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(ha, hb) / (na * nb))


def _mse_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Map MSE to 0..1 (1 = identical)."""
    mse = float(np.mean((a - b) ** 2))
    # 255^2 scale
    return float(max(0.0, 1.0 - mse / (255.0 * 255.0)))


def _ssim_like(a: np.ndarray, b: np.ndarray) -> float:
    """Cheap luminance SSIM-like score on grayscale."""
    x = a.mean(axis=2)
    y = b.mean(axis=2)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_x, mu_y = x.mean(), y.mean()
    sigma_x = x.var()
    sigma_y = y.var()
    sigma_xy = ((x - mu_x) * (y - mu_y)).mean()
    num = (2 * mu_x * mu_y + c1) * (2 * sigma_xy + c2)
    den = (mu_x**2 + mu_y**2 + c1) * (sigma_x + sigma_y + c2)
    return float(np.clip(num / max(den, 1e-9), 0.0, 1.0))


def _silhouette_iou(a: np.ndarray, b: np.ndarray) -> float:
    """Foreground via luminance threshold; IoU of masks."""
    la = a.mean(axis=2)
    lb = b.mean(axis=2)
    # Assume lighter or mid subject on varied bg — use Otsu-ish median split
    ma = la < np.percentile(la, 65)
    mb = lb < np.percentile(lb, 65)
    # Also try inverse and take better IoU (handles light-on-dark)
    ma2 = la > np.percentile(la, 35)
    mb2 = lb > np.percentile(lb, 35)

    def iou(x: np.ndarray, y: np.ndarray) -> float:
        inter = np.logical_and(x, y).sum()
        union = np.logical_or(x, y).sum()
        return float(inter / union) if union else 0.0

    return max(iou(ma, mb), iou(ma2, mb2))


def compare_to_anchor(generated: Path, anchor: Path) -> dict[str, Any]:
    """Region-aware auxiliary scores. Not ground truth — QC signal only."""
    full_g = _load_rgb(generated)
    full_a = _load_rgb(anchor)
    h = full_g.shape[0]
    # Face / hair: upper 40%
    face_g, face_a = full_g[: int(h * 0.4)], full_a[: int(h * 0.4)]
    # Outfit: lower 55%
    outfit_g, outfit_a = full_g[int(h * 0.45) :], full_a[int(h * 0.45) :]
    # Hair band: top 25%
    hair_g, hair_a = full_g[: int(h * 0.25)], full_a[: int(h * 0.25)]

    scores = {
        "perceptual_mse_sim": round(_mse_similarity(full_g, full_a), 4),
        "perceptual_ssim_like": round(_ssim_like(full_g, full_a), 4),
        "color_palette": round(_hist_cosine(full_g, full_a), 4),
        "face_region_color": round(_hist_cosine(face_g, face_a), 4),
        "face_region_ssim": round(_ssim_like(face_g, face_a), 4),
        "hair_region_color": round(_hist_cosine(hair_g, hair_a), 4),
        "outfit_color": round(_hist_cosine(outfit_g, outfit_a), 4),
        "silhouette_iou": round(_silhouette_iou(full_g, full_a), 4),
    }
    # Composite for ranking only (equal weights on identity-ish signals)
    identity = (
        scores["face_region_ssim"]
        + scores["face_region_color"]
        + scores["hair_region_color"]
        + scores["outfit_color"]
        + scores["silhouette_iou"]
        + scores["perceptual_ssim_like"]
    ) / 6.0
    scores["identity_proxy"] = round(identity, 4)
    scores["method"] = "pil_numpy_region_hist_ssim_iou"
    scores["note"] = "Auxiliary QC only — not face recognition embeddings"
    return scores


def mean_scores(rows: list[dict[str, Any]]) -> dict[str, float]:
    keys = [
        "perceptual_mse_sim",
        "perceptual_ssim_like",
        "color_palette",
        "face_region_color",
        "face_region_ssim",
        "hair_region_color",
        "outfit_color",
        "silhouette_iou",
        "identity_proxy",
    ]
    out: dict[str, float] = {}
    for key in keys:
        vals = [float(r[key]) for r in rows if key in r]
        out[key] = round(sum(vals) / len(vals), 4) if vals else 0.0
    return out
