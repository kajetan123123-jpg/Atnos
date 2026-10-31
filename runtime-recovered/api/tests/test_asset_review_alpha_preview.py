"""Review sheets must show the alpha that the renderer actually receives."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from atnos.production.asset_brain.approval import build_review_contact_sheet
from atnos.production.asset_brain.models import AssetRecord


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
def test_review_sheet_exposes_transparent_holes_and_preserves_opaque_pixels(
    tmp_path: Path, mode: str
):
    """A cream-colored transparent hole must not masquerade as cream paper."""
    source = tmp_path / "source.png"
    size = (80, 80)
    cream = (237, 221, 191)
    if mode == "RGBA":
        image = Image.new(mode, size, (*cream, 0))
        opaque = (*cream, 255)
        expected_opaque = cream
    else:
        image = Image.new(mode, size, (221, 0))
        opaque = (221, 255)
        expected_opaque = (221, 221, 221)
    # Only the lower half is genuine visible paper. The upper half carries
    # plausible paper RGB/luminance beneath zero alpha, as failed cutouts can.
    ImageDraw.Draw(image).rectangle((0, 40, 79, 79), fill=opaque)
    image.save(source)
    original_bytes = source.read_bytes()
    record = AssetRecord(
        id="alpha_hole_review_fixture",
        type="icon",
        file_path=str(source),
        channel="wealth_blueprint",
        transparent_background=True,
        width=80,
        height=80,
        approved=False,
        quality_status="AWAITING_REVIEW",
    )

    dest = tmp_path / "review" / "contact-sheet.png"
    result = build_review_contact_sheet(
        [record], resolve_file=lambda asset: Path(asset.file_path),
        dest=dest, cols=1, cell=80,
    )

    with Image.open(result) as sheet:
        # No resizing or JPEG approximation: these are exact interior pixels.
        assert sheet.getpixel((10, 60)) == expected_opaque
        assert sheet.getpixel((30, 60)) == expected_opaque
        # The two transparent regions expose different checker tiles; hidden
        # source color must never be shown as an intact part of the object.
        transparent_left = sheet.getpixel((10, 10))
        transparent_right = sheet.getpixel((30, 10))
        assert transparent_left != transparent_right
        assert transparent_left != expected_opaque
        assert transparent_right != expected_opaque
        assert sheet.size == (80, 136)
    assert source.read_bytes() == original_bytes
    metadata = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["asset_ids"] == [record.id]
    assert "checkerboard" in metadata["alpha_preview"]


def test_review_sheet_keeps_an_opaque_rgb_source_opaque(tmp_path: Path):
    source = tmp_path / "opaque-source.png"
    color = (170, 96, 40)
    Image.new("RGB", (80, 80), color).save(source)
    original_bytes = source.read_bytes()
    record = AssetRecord(
        id="opaque_review_fixture", type="background", file_path=str(source),
        channel="wealth_blueprint", width=80, height=80,
        approved=False, quality_status="AWAITING_REVIEW",
    )

    sheet_path = build_review_contact_sheet(
        [record], resolve_file=lambda asset: Path(asset.file_path),
        dest=tmp_path / "opaque-review.png", cols=1, cell=80,
    )
    with Image.open(sheet_path) as sheet:
        assert sheet.getpixel((10, 10)) == color
        assert sheet.getpixel((30, 10)) == color
        assert sheet.getpixel((10, 60)) == color
    assert source.read_bytes() == original_bytes
