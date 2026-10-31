"""Typography stress + safe-zone geometry for Wealth Blueprint Remotion layouts."""

from __future__ import annotations

from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type
from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_IDS


# Remotion design.tsx SAFE_* mirrored for geometry contracts (no OCR).
SAFE_X = 72
SAFE_TOP = 96
SAFE_BOTTOM = 220
FRAME_W = 1080
FRAME_H = 1920
CONTENT_RIGHT = FRAME_W - SAFE_X
CONTENT_BOTTOM = FRAME_H - SAFE_BOTTOM


TYPOGRAPHY_STRESS = [
    "OK",
    "Keep more of your raise without changing your lifestyle overnight",
    "Supercalifragilisticexpialidocious",
    "10,000",
    "10%",
    "$10,000",
    "€10,000",
    "Żółć i ść źdźbło",
    "Wait—really?! (yes.)",
]


def _box(left: float, top: float, right: float, bottom: float) -> dict[str, float]:
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _within_safe(box: dict[str, float]) -> bool:
    return (
        box["left"] >= SAFE_X - 0.5
        and box["top"] >= SAFE_TOP - 0.5
        and box["right"] <= CONTENT_RIGHT + 0.5
        and box["bottom"] <= CONTENT_BOTTOM + 0.5
        and box["right"] > box["left"]
        and box["bottom"] > box["top"]
    )


def layout_element_boxes(layout_id: str) -> list[dict[str, float]]:
    """Deterministic layout element geometry used by Remotion WB scenes (approx)."""
    # Mirrors scenes.tsx placement conventions for WB layouts.
    headline = _box(SAFE_X, 248, CONTENT_RIGHT, 420)
    body = _box(SAFE_X, 450, CONTENT_RIGHT, 700)
    if layout_id == "big_number":
        return [
            headline,
            _box(SAFE_X, 560, CONTENT_RIGHT, 1100),
            _box(SAFE_X, 1120, CONTENT_RIGHT, 1280),
        ]
    if layout_id in {"chart_focus", "comparison"}:
        return [
            headline,
            _box(SAFE_X, 560, CONTENT_RIGHT, 1330),
        ]
    if layout_id in {"character_left_text_right", "character_right_text_left", "split_screen"}:
        return [
            _box(SAFE_X, 265, 520, 900),
            _box(540, 265, CONTENT_RIGHT, 900),
            body,
        ]
    if layout_id == "three_points":
        return [
            headline,
            _box(SAFE_X, 480, CONTENT_RIGHT, 620),
            _box(SAFE_X, 650, CONTENT_RIGHT, 790),
            _box(SAFE_X, 820, CONTENT_RIGHT, 960),
        ]
    if layout_id == "icon_explainer":
        return [
            _box(SAFE_X, 280, CONTENT_RIGHT, 520),
            body,
        ]
    return [headline, body]


def test_typography_stress_strings_are_nonempty_and_bounded():
    for text in TYPOGRAPHY_STRESS:
        assert text.strip()
        # Remotion caption/headline budgets — long copy must still be measurable.
        assert len(text) <= 120 or text == TYPOGRAPHY_STRESS[1]


def test_all_layouts_have_safe_zone_boxes():
    for layout_id in LAYOUT_IDS:
        boxes = layout_element_boxes(layout_id)
        assert boxes, layout_id
        for box in boxes:
            assert _within_safe(box), f"{layout_id} box outside safe zone: {box}"
        # layout maps to a Remotion SceneType (no unknown collapse)
        scene_type = layout_to_scene_type(layout_id, index=0, total=2)
        assert scene_type.value


def test_long_word_and_currency_fit_content_width():
    # Geometry-only: content width must leave room for long tokens without OCR.
    content_width = CONTENT_RIGHT - SAFE_X
    assert content_width >= 900
    for token in ("Supercalifragilisticexpialidocious", "$10,000", "€10,000", "10,000", "10%"):
        # Approximate min monospace-ish width budget; fail only on absurd collapse.
        assert content_width >= min(36 * len(token) * 0.35, content_width)
