from __future__ import annotations

from pathlib import Path
from typing import Any


def audit_thumbnail_quality(path: str | Path, *, profile_key: str = "") -> dict[str, Any]:
    """Oceń techniczną jakość miniatury bez odwołań do zewnętrznych modeli."""
    thumbnail_path = Path(path)
    blockers: list[str] = []
    metrics: dict[str, Any] = {
        "path": str(thumbnail_path),
        "exists": thumbnail_path.exists(),
        "size_bytes": thumbnail_path.stat().st_size if thumbnail_path.exists() else 0,
        "profile_key": profile_key,
    }
    if not thumbnail_path.exists() or not thumbnail_path.is_file():
        return _result(blockers=["thumbnail_missing"], metrics=metrics)
    if metrics["size_bytes"] < 50 * 1024:
        blockers.append("thumbnail_file_too_small")

    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError:
        return _result(blockers=[*blockers, "thumbnail_audit_pillow_missing"], metrics=metrics)

    try:
        with Image.open(thumbnail_path) as image:
            image = image.convert("RGB")
            metrics["width"] = image.width
            metrics["height"] = image.height
            sample = image.resize((160, 90))
            grayscale = sample.convert("L")
            hsv = sample.convert("HSV")
            extrema = grayscale.getextrema()
            contrast_range = int(extrema[1] - extrema[0])
            brightness = float(ImageStat.Stat(grayscale).mean[0])
            saturation = float(ImageStat.Stat(hsv.split()[1]).mean[0])
            edge_strength = float(ImageStat.Stat(grayscale.filter(ImageFilter.FIND_EDGES)).mean[0])
            color_count = len(set(sample.resize((64, 36)).getdata()))
    except Exception as exc:
        return _result(
            blockers=[*blockers, "thumbnail_open_failed"],
            metrics={**metrics, "error": f"{type(exc).__name__}: {exc}"},
        )

    metrics.update(
        {
            "contrast_range": contrast_range,
            "brightness": round(brightness, 2),
            "saturation": round(saturation, 2),
            "edge_strength": round(edge_strength, 2),
            "sample_color_count": color_count,
        }
    )

    if metrics["width"] < 1280 or metrics["height"] < 720:
        blockers.append("thumbnail_resolution_too_low")
    if contrast_range < 42:
        blockers.append("thumbnail_low_contrast")
    if edge_strength < 2.2:
        blockers.append("thumbnail_blurry_or_flat")
    if color_count < 32:
        blockers.append("thumbnail_too_flat")
    if brightness < 14:
        blockers.append("thumbnail_too_dark")
    if brightness > 228:
        blockers.append("thumbnail_too_bright")

    if profile_key == "wealth_blueprint":
        if brightness > 175:
            blockers.append("wealth_thumbnail_not_mature_dark_fintech")
        if saturation > 145:
            blockers.append("wealth_thumbnail_too_childish_saturated")
    elif profile_key == "dark_chronicles":
        if brightness > 125:
            blockers.append("dark_chronicles_thumbnail_too_bright")
    elif profile_key in {"history", "brainrot"}:
        # Te profile moga byc bardziej kolorowe, ale nadal nie moga byc plaskim blobem.
        if contrast_range < 55 and edge_strength < 3.0:
            blockers.append("thumbnail_profile_visual_floor_failed")

    return _result(blockers=blockers, metrics=metrics)


def thumbnail_quality_blockers(audit: dict[str, Any] | None) -> list[str]:
    """Zwróć blokery z payloadu audytu miniatury."""
    if not isinstance(audit, dict):
        return []
    blockers = audit.get("blockers")
    if isinstance(blockers, list):
        return [str(item) for item in blockers if str(item).strip()]
    return []


def _result(*, blockers: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    blockers = list(dict.fromkeys(blockers))
    return {
        "status": "pass" if not blockers else "fail",
        "passed": not blockers,
        "blockers": blockers,
        "metrics": metrics,
    }
