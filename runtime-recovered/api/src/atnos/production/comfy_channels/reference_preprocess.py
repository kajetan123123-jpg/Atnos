"""Crop single-character portrait anchors from approved multi-panel sheets.

Full sheets remain the bible; img2img conditioning uses portrait crops.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

REFS = Path(r"E:\Atnos\App\config\channel_references\20260916")
CROPS = REFS / "crops"


def _save_crop(src: Image.Image, box_frac: tuple[float, float, float, float], dest: Path) -> Path:
    w, h = src.size
    x0, y0, x1, y1 = box_frac
    cropped = src.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    # Upscale small crops for more stable VAE encode
    if min(cropped.size) < 384:
        scale = 512 / min(cropped.size)
        cropped = cropped.resize(
            (max(384, int(cropped.width * scale)), max(384, int(cropped.height * scale))),
            Image.Resampling.LANCZOS,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(dest)
    return dest


def build_portrait_crops() -> dict[str, str]:
    """Return mapping character_id -> primary portrait crop path."""
    CROPS.mkdir(parents=True, exist_ok=True)
    alex = Image.open(REFS / "image-1.png")
    # Single NEUTRAL expression cell (row0,col0) — tighter than full sheet
    alex_neutral = _save_crop(alex, (0.055, 0.175, 0.155, 0.385), CROPS / "alex_portrait_v1.png")
    # FRONT head from bottom trio
    _save_crop(alex, (0.155, 0.705, 0.285, 0.955), CROPS / "alex_front_v1.png")

    niko = Image.open(REFS / "image-3.png")
    # Large bust portrait only (exclude palette/traits)
    niko_portrait = _save_crop(niko, (0.58, 0.06, 0.88, 0.36), CROPS / "niko_portrait_v1.png")
    # Front full-body turnaround figure
    _save_crop(niko, (0.24, 0.06, 0.38, 0.40), CROPS / "niko_front_v1.png")

    return {
        "alex": str(alex_neutral.resolve()),
        "niko": str(niko_portrait.resolve()),
    }


if __name__ == "__main__":
    print(build_portrait_crops())
