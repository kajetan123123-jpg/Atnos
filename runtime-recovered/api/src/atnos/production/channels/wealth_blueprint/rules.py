"""Deterministic Wealth Blueprint scene-planning rules (validation thresholds)."""

from __future__ import annotations

# 1. Do not change scenes every few frames
MIN_SCENE_DURATION_SEC = 2.2
MAX_SCENES = 14

# 2. Do not show too many elements at once
MAX_ELEMENTS_PER_SCENE = 4

# 3. Dominant information — enforced via visual_focus + element budget

# 4. On-screen text must not copy full VO
MAX_ON_SCREEN_WORDS = 8
MAX_ON_SCREEN_RATIO = 0.45  # vs narration excerpt word count

# 5. Numbers / comparisons should be visualized — planner + validator

# 6. Alex helps narration; not always on screen
MAX_ALEX_SCENE_RATIO = 0.4

# 7. Avoid repeating the same layout continuously
MAX_CONSECUTIVE_SAME_LAYOUT = 1

# 8–9. Motion restraint; animation follows narration
MAX_ANIMATIONS_PER_SCENE = 3
MAX_SFX_PER_SCENE = 2

# 10. Channel profile
CHANNEL = "wealth_blueprint"

NUMBER_PATTERN_HINTS = (
    "percent",
    "%",
    "dollar",
    "thousand",
    "million",
    "billion",
    "rate",
    "years",
    "months",
)

COMPARISON_HINTS = (
    "vs",
    "versus",
    "compared",
    "instead of",
    "rather than",
    "more than",
    "less than",
    "keep versus",
    "keep vs",
    "spend versus",
    "spend vs",
    "early saver",
    "late saver",
    "worked example",
    "side by side",
)

CHART_HINTS = (
    "growth",
    "compound",
    "portfolio",
    "allocation",
    "cash flow",
    "cashflow",
    "trend",
    "over time",
    "invest",
)

ICON_HINTS = (
    "habit",
    "budget",
    "save",
    "account",
    "fund",
    "etf",
    "emergency",
    "transfer",
    "transfers",
    "automatic",
    "automate",
    "weekly",
    "choose",
    "open your",
)
