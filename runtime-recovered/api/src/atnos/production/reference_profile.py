from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class ReferenceShortsProfile:
    """User-provided Shorts reference profile loaded from local learning data."""

    available: bool
    path: str | None = None
    source: str = "missing"
    sample_count: int = 0
    median_duration_sec: float | None = None
    median_lufs: float | None = None
    median_first_cut_sec: float | None = None
    median_avg_cut_interval_sec: float | None = None
    median_cuts_per_10s: float | None = None
    median_frame_delta: float | None = None
    median_p95_frame_delta: float | None = None
    median_brightness_delta: float | None = None
    median_p95_brightness_delta: float | None = None
    median_flash_events_per_10s: float | None = None
    median_brightness: float | None = None
    median_saturation: float | None = None
    rules: tuple[str, ...] = ()
    error: str | None = None

    def target_duration_sec(self, default: float = 45.0) -> float:
        value = _float(self.median_duration_sec, default)
        return round(max(20.0, min(59.0, value)), 2)

    def target_lufs(self, default: float = -14.0) -> float:
        value = _float(self.median_lufs, default)
        # Downloaded references can be quiet or clipped. Keep the target in a safe YouTube range.
        if -18.0 <= value <= -12.0:
            return round(value, 2)
        return default

    def hook_target_sec(self, default: float = 3.0) -> float:
        value = _float(self.median_first_cut_sec, default)
        return round(max(0.5, min(3.0, value)), 2)

    def avg_cut_interval_sec(self, default: float = 3.0) -> float:
        value = _float(self.median_avg_cut_interval_sec, default)
        return round(max(1.2, min(4.5, value)), 2)

    def to_payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "path": self.path,
            "source": self.source,
            "sample_count": self.sample_count,
            "median_duration_sec": self.median_duration_sec,
            "median_lufs": self.median_lufs,
            "median_first_cut_sec": self.median_first_cut_sec,
            "median_avg_cut_interval_sec": self.median_avg_cut_interval_sec,
            "median_cuts_per_10s": self.median_cuts_per_10s,
            "median_frame_delta": self.median_frame_delta,
            "median_p95_frame_delta": self.median_p95_frame_delta,
            "median_brightness_delta": self.median_brightness_delta,
            "median_p95_brightness_delta": self.median_p95_brightness_delta,
            "median_flash_events_per_10s": self.median_flash_events_per_10s,
            "median_brightness": self.median_brightness,
            "median_saturation": self.median_saturation,
            "target_duration_sec": self.target_duration_sec(),
            "target_lufs": self.target_lufs(),
            "hook_target_sec": self.hook_target_sec(),
            "avg_cut_interval_sec": self.avg_cut_interval_sec(),
            "rules": list(self.rules),
            "error": self.error,
        }


def load_reference_shorts_profile(settings: Settings) -> ReferenceShortsProfile:
    active_path = settings.data_dir / "learning" / "active-reference-shorts" / "latest_reference_profile.json"
    user_path = settings.data_dir / "learning" / "user-reference-shorts" / "latest_reference_profile.json"
    # Active references come from URLs configured in Atnos. User-supplied files remain
    # a fallback so production is not coupled to network access.
    path = active_path if active_path.exists() else user_path
    if not path.exists():
        return ReferenceShortsProfile(available=False, path=str(path), source="missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReferenceShortsProfile(available=False, path=str(path), source="invalid", error=str(exc))
    rules = data.get("production_rules_for_atnos") if isinstance(data.get("production_rules_for_atnos"), list) else []
    return ReferenceShortsProfile(
        available=True,
        path=str(path),
        source=str(data.get("source") or "user_supplied_reference_shorts"),
        sample_count=int(_float(data.get("sample_count"), 0)),
        median_duration_sec=_optional_float(data.get("median_duration_sec")),
        median_lufs=_optional_float(data.get("median_lufs")),
        median_first_cut_sec=_optional_float(data.get("median_first_cut_sec")),
        median_avg_cut_interval_sec=_optional_float(data.get("median_avg_cut_interval_sec")),
        median_cuts_per_10s=_optional_float(data.get("median_cuts_per_10s")),
        median_frame_delta=_optional_float(data.get("median_frame_delta")),
        median_p95_frame_delta=_optional_float(data.get("median_p95_frame_delta")),
        median_brightness_delta=_optional_float(data.get("median_brightness_delta")),
        median_p95_brightness_delta=_optional_float(data.get("median_p95_brightness_delta")),
        median_flash_events_per_10s=_optional_float(data.get("median_flash_events_per_10s")),
        median_brightness=_optional_float(data.get("median_brightness")),
        median_saturation=_optional_float(data.get("median_saturation")),
        rules=tuple(str(rule) for rule in rules if str(rule).strip()),
    )


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
