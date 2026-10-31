"""Remotion selection for Wealth Blueprint Production Orders.

REMOTION_REQUIRED
  When production.renderer == \"remotion\", the order MUST use Remotion.
  Failures (preflight, manifest, render, ffprobe) FAIL the order.
  There is NO automatic fallback to editorial.

  A future optional production fallback (if ever added) must use a different,
  explicit mode name — never silent fallback under remotion / REMOTION_REQUIRED.
"""

from __future__ import annotations

from typing import Any

# Explicit test/migration mode: remotion path is fail-closed.
REMOTION_REQUIRED = "REMOTION_REQUIRED"


def is_remotion_renderer(production: dict[str, Any] | None) -> bool:
    value = str((production or {}).get("renderer") or "editorial").strip().casefold()
    return value == "remotion"


def remotion_mode_label(production: dict[str, Any] | None) -> str:
    """Human-readable mode for logs/errors when remotion is selected."""
    if is_remotion_renderer(production):
        return REMOTION_REQUIRED
    return "editorial"
