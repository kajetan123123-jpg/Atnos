"""Explicit layout_id → Remotion SceneType mapping (no new layouts)."""

from __future__ import annotations

from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_IDS, LayoutId
from atnos.production.project_manifest import SceneType

# Planner layout_id → Remotion composition behavior (SceneType contract)
LAYOUT_TO_SCENE_TYPE: dict[str, SceneType] = {
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


def layout_to_scene_type(
    layout_id: str,
    *,
    visual_focus: str = "statement",
    index: int = 0,
    total: int = 1,
    has_number: bool = False,
    has_chart: bool = False,
) -> SceneType:
    """Deterministic map from WB layout_id (+ light context) to Remotion SceneType."""
    key = (layout_id or "").strip()
    if key == "big_number" and not has_number:
        # Fail-closed demotion: Remotion NumberScene requires source-backed number_value.
        if total > 1 and index == total - 1:
            return SceneType.CTA
        return SceneType.HOOK
    if key in {"chart_focus", "comparison"} and not has_chart:
        # Fail-closed: ChartScene requires claim-backed quantitative values — never invent series.
        if total > 1 and index == total - 1:
            return SceneType.CTA
        if index == 0:
            return SceneType.HOOK
        return SceneType.HOOK
    if key in LAYOUT_TO_SCENE_TYPE:
        mapped = LAYOUT_TO_SCENE_TYPE[key]
        # Last scene prefers CTA when statement-like
        if mapped == SceneType.HOOK and total > 1 and index == total - 1:
            return SceneType.CTA
        if mapped == SceneType.HOOK and index == 0:
            return SceneType.HOOK
        return mapped
    if has_number or visual_focus == "number":
        return SceneType.NUMBER
    if has_chart and visual_focus in {"chart", "comparison"}:
        return SceneType.CHART
    if visual_focus in {"character", "icon"}:
        return SceneType.IMAGE
    if total > 1 and index == total - 1:
        return SceneType.CTA
    if index == 0:
        return SceneType.HOOK
    return SceneType.IMAGE


def assert_all_layouts_mapped() -> None:
    missing = [layout for layout in LAYOUT_IDS if layout not in LAYOUT_TO_SCENE_TYPE]
    if missing:
        raise AssertionError(f"Unmapped Wealth Blueprint layouts: {missing}")


# Validate at import for fail-fast in tests
assert_all_layouts_mapped()
