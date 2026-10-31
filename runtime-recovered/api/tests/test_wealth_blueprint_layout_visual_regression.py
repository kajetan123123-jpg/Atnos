"""Layout-id → Remotion SceneType registry + perceptual frame regression helpers."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from atnos.production.channels.wealth_blueprint.layout_registry import LAYOUT_TO_SCENE_TYPE, layout_to_scene_type
from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_IDS
from atnos.production.project_manifest import SceneType


def perceptual_frame_delta(a: Path, b: Path) -> float:
    """Mean absolute RGB difference after downscale (antialiasing-tolerant)."""
    left = Image.open(a).convert("RGB").resize((270, 480), Image.Resampling.BILINEAR)
    right = Image.open(b).convert("RGB").resize((270, 480), Image.Resampling.BILINEAR)
    diff = ImageChops.difference(left, right)
    return float(sum(ImageStat.Stat(diff).mean) / 3.0)


def test_every_wb_layout_has_registry_entry():
    for layout_id in LAYOUT_IDS:
        assert layout_id in LAYOUT_TO_SCENE_TYPE
        mapped = layout_to_scene_type(layout_id, index=1, total=3)
        assert isinstance(mapped, SceneType)


def test_perceptual_compare_detects_blank_and_large_shift(tmp_path: Path):
    good = tmp_path / "good.png"
    blank = tmp_path / "blank.png"
    shifted = tmp_path / "shifted.png"
    Image.new("RGB", (1080, 1920), (30, 40, 50)).save(good)
    # Text-like block
    canvas = Image.new("RGB", (1080, 1920), (30, 40, 50))
    for x in range(200, 880):
        for y in range(400, 520):
            canvas.putpixel((x, y), (240, 240, 240))
    canvas.save(good)
    Image.new("RGB", (1080, 1920), (0, 0, 0)).save(blank)
    shifted_img = Image.new("RGB", (1080, 1920), (30, 40, 50))
    for x in range(200, 880):
        for y in range(900, 1020):
            shifted_img.putpixel((x, y), (240, 240, 240))
    shifted_img.save(shifted)

    assert perceptual_frame_delta(good, blank) > 8.0
    assert perceptual_frame_delta(good, shifted) > 4.0
    assert perceptual_frame_delta(good, good) < 0.5


def test_layout_behavior_matrix_is_stable():
    """Explicit composition behavior table — no new layouts."""
    expected = {
        "fullscreen_statement": SceneType.HOOK,
        "quote_or_fact": SceneType.HOOK,
        "big_number": SceneType.NUMBER,
        "chart_focus": SceneType.CHART,
        "comparison": SceneType.CHART,
        "character_left_text_right": SceneType.IMAGE,
        "character_right_text_left": SceneType.IMAGE,
        "icon_explainer": SceneType.IMAGE,
        "split_screen": SceneType.IMAGE,
        "three_points": SceneType.HOOK,
    }
    assert LAYOUT_TO_SCENE_TYPE == expected
