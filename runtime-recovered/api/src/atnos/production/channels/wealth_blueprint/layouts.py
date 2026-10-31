"""Wealth Blueprint layout catalog — engine selects by ID, pack owns content."""

from __future__ import annotations

from typing import Literal

LayoutId = Literal[
    "character_left_text_right",
    "character_right_text_left",
    "fullscreen_statement",
    "big_number",
    "chart_focus",
    "comparison",
    "three_points",
    "icon_explainer",
    "split_screen",
    "quote_or_fact",
]

LAYOUT_IDS: tuple[LayoutId, ...] = (
    "character_left_text_right",
    "character_right_text_left",
    "fullscreen_statement",
    "big_number",
    "chart_focus",
    "comparison",
    "three_points",
    "icon_explainer",
    "split_screen",
    "quote_or_fact",
)

LAYOUT_META: dict[LayoutId, dict[str, object]] = {
    "character_left_text_right": {"alex": True, "max_elements": 3, "focus": "character"},
    "character_right_text_left": {"alex": True, "max_elements": 3, "focus": "character"},
    "fullscreen_statement": {"alex": False, "max_elements": 2, "focus": "statement"},
    "big_number": {"alex": False, "max_elements": 2, "focus": "number"},
    "chart_focus": {"alex": False, "max_elements": 3, "focus": "chart"},
    "comparison": {"alex": False, "max_elements": 3, "focus": "comparison"},
    "three_points": {"alex": False, "max_elements": 4, "focus": "statement"},
    "icon_explainer": {"alex": False, "max_elements": 3, "focus": "icon"},
    "split_screen": {"alex": True, "max_elements": 4, "focus": "comparison"},
    "quote_or_fact": {"alex": False, "max_elements": 2, "focus": "statement"},
}
