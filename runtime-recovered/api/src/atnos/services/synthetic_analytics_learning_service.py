from __future__ import annotations

from dataclasses import dataclass
from math import log1p
from typing import Any

from sqlalchemy.orm import Session

from atnos.repositories.execution_run_repository import ExecutionRunRepository


@dataclass(frozen=True, slots=True)
class SyntheticAnalyticsCalibration:
    sample_count: int
    baseline_average_view_percentage: float | None
    baseline_views: float | None
    style_feedback: dict[str, float]
    style_sample_counts: dict[str, int]
    reel_feedback: dict[str, float]
    reel_sample_counts: dict[str, int]
    strongest_style_key: str | None
    strongest_reel_key: str | None
    hook_strategy_counts: dict[str, int]
    pacing_strategy_counts: dict[str, int]
    cutaway_strategy_counts: dict[str, int]
    title_strategy_counts: dict[str, int]
    strongest_hook_strategy: str | None
    strongest_pacing_strategy: str | None
    strongest_cutaway_strategy: str | None
    strongest_title_strategy: str | None
    insights: tuple[str, ...]

    @classmethod
    def empty(cls) -> SyntheticAnalyticsCalibration:
        return cls(
            sample_count=0,
            baseline_average_view_percentage=None,
            baseline_views=None,
            style_feedback={},
            style_sample_counts={},
            reel_feedback={},
            reel_sample_counts={},
            strongest_style_key=None,
            strongest_reel_key=None,
            hook_strategy_counts={},
            pacing_strategy_counts={},
            cutaway_strategy_counts={},
            title_strategy_counts={},
            strongest_hook_strategy=None,
            strongest_pacing_strategy=None,
            strongest_cutaway_strategy=None,
            strongest_title_strategy=None,
            insights=(),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> SyntheticAnalyticsCalibration:
        if not isinstance(payload, dict):
            return cls.empty()
        return cls(
            sample_count=max(0, int(payload.get("sample_count") or 0)),
            baseline_average_view_percentage=_coerce_float(payload.get("baseline_average_view_percentage")),
            baseline_views=_coerce_float(payload.get("baseline_views")),
            style_feedback={
                str(key): float(value)
                for key, value in dict(payload.get("style_feedback") or {}).items()
                if str(key).strip()
            },
            style_sample_counts={
                str(key): max(0, int(value))
                for key, value in dict(payload.get("style_sample_counts") or {}).items()
                if str(key).strip()
            },
            reel_feedback={
                str(key): float(value)
                for key, value in dict(payload.get("reel_feedback") or {}).items()
                if str(key).strip()
            },
            reel_sample_counts={
                str(key): max(0, int(value))
                for key, value in dict(payload.get("reel_sample_counts") or {}).items()
                if str(key).strip()
            },
            strongest_style_key=_clean_text(payload.get("strongest_style_key")) or None,
            strongest_reel_key=_clean_text(payload.get("strongest_reel_key")) or None,
            hook_strategy_counts={
                str(key): max(0, int(value))
                for key, value in dict(payload.get("hook_strategy_counts") or {}).items()
                if str(key).strip()
            },
            pacing_strategy_counts={
                str(key): max(0, int(value))
                for key, value in dict(payload.get("pacing_strategy_counts") or {}).items()
                if str(key).strip()
            },
            cutaway_strategy_counts={
                str(key): max(0, int(value))
                for key, value in dict(payload.get("cutaway_strategy_counts") or {}).items()
                if str(key).strip()
            },
            title_strategy_counts={
                str(key): max(0, int(value))
                for key, value in dict(payload.get("title_strategy_counts") or {}).items()
                if str(key).strip()
            },
            strongest_hook_strategy=_clean_text(payload.get("strongest_hook_strategy")) or None,
            strongest_pacing_strategy=_clean_text(payload.get("strongest_pacing_strategy")) or None,
            strongest_cutaway_strategy=_clean_text(payload.get("strongest_cutaway_strategy")) or None,
            strongest_title_strategy=_clean_text(payload.get("strongest_title_strategy")) or None,
            insights=tuple(
                str(item).strip()
                for item in list(payload.get("insights") or [])
                if str(item).strip()
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_count": self.sample_count,
            "baseline_average_view_percentage": self.baseline_average_view_percentage,
            "baseline_views": self.baseline_views,
            "style_feedback": dict(self.style_feedback),
            "style_sample_counts": dict(self.style_sample_counts),
            "reel_feedback": dict(self.reel_feedback),
            "reel_sample_counts": dict(self.reel_sample_counts),
            "strongest_style_key": self.strongest_style_key,
            "strongest_reel_key": self.strongest_reel_key,
            "hook_strategy_counts": dict(self.hook_strategy_counts),
            "pacing_strategy_counts": dict(self.pacing_strategy_counts),
            "cutaway_strategy_counts": dict(self.cutaway_strategy_counts),
            "title_strategy_counts": dict(self.title_strategy_counts),
            "strongest_hook_strategy": self.strongest_hook_strategy,
            "strongest_pacing_strategy": self.strongest_pacing_strategy,
            "strongest_cutaway_strategy": self.strongest_cutaway_strategy,
            "strongest_title_strategy": self.strongest_title_strategy,
            "insights": list(self.insights),
        }

    def feedback_for(self, *, style_key: str | None, reel_key: str | None) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
        score = 0.0
        strengths: list[str] = []
        risks: list[str] = []

        if style_key:
            style_score = float(self.style_feedback.get(style_key, 0.0))
            style_samples = int(self.style_sample_counts.get(style_key, 0))
            score += style_score * 0.6
            if style_score >= 1.25 and style_samples > 0:
                strengths.append(
                    f"Ten styl historycznie dowozil lepsze retention w projekcie ({style_samples} prob)."
                )
            elif style_score <= -1.25 and style_samples > 0:
                risks.append(
                    f"Ten styl historycznie dowozil slabsze retention w projekcie ({style_samples} prob)."
                )

        if reel_key:
            reel_score = float(self.reel_feedback.get(reel_key, 0.0))
            reel_samples = int(self.reel_sample_counts.get(reel_key, 0))
            score += reel_score * 0.4
            if reel_score >= 1.25 and reel_samples > 0:
                strengths.append(
                    f"Ten typ rolki ma lepszy historyczny wynik w projekcie ({reel_samples} prob)."
                )
            elif reel_score <= -1.25 and reel_samples > 0:
                risks.append(
                    f"Ten typ rolki ma slabszy historyczny wynik w projekcie ({reel_samples} prob)."
                )

        return (
            round(max(-8.0, min(8.0, score)), 2),
            tuple(strengths[:2]),
            tuple(risks[:2]),
        )


class SyntheticAnalyticsLearningService:
    MAX_HISTORY_RUNS = 80

    def __init__(self, session: Session) -> None:
        self.execution_runs = ExecutionRunRepository(session)

    def build_calibration(self, *, project_id: int, limit: int | None = None) -> SyntheticAnalyticsCalibration:
        runs = self.execution_runs.list_runs_for_project(
            project_id,
            limit=max(1, min(limit or self.MAX_HISTORY_RUNS, self.MAX_HISTORY_RUNS)),
        )
        entries: list[dict[str, object]] = []
        for run in runs:
            output = dict(run.output_json or {})
            analytics_summary = dict(output.get("analytics_summary") or {})
            if _clean_text(analytics_summary.get("status")).lower() != "ready":
                continue
            style_key, reel_key = self._extract_variant_keys(output)
            strategy_snapshot = dict(output.get("analytics_strategy_snapshot") or {})
            average_view_percentage = _coerce_float(analytics_summary.get("average_view_percentage"))
            views = _coerce_float(analytics_summary.get("views"))
            if average_view_percentage is None and views is None:
                continue
            entries.append(
                {
                    "style_key": style_key,
                    "reel_key": reel_key,
                    "average_view_percentage": average_view_percentage,
                    "views": views,
                    "hook_strategy": _clean_text(strategy_snapshot.get("hook_strategy")) or None,
                    "pacing_strategy": _clean_text(strategy_snapshot.get("pacing_strategy")) or None,
                    "cutaway_strategy": _clean_text(strategy_snapshot.get("cutaway_strategy")) or None,
                    "title_strategy": _clean_text(strategy_snapshot.get("title_strategy")) or None,
                }
            )
        return self._build_from_entries(entries)

    def _build_from_entries(self, entries: list[dict[str, object]]) -> SyntheticAnalyticsCalibration:
        if not entries:
            return SyntheticAnalyticsCalibration.empty()
        avp_values = [
            float(item["average_view_percentage"])
            for item in entries
            if isinstance(item.get("average_view_percentage"), (int, float))
        ]
        view_values = [
            float(item["views"])
            for item in entries
            if isinstance(item.get("views"), (int, float))
        ]
        baseline_avp = round(sum(avp_values) / len(avp_values), 2) if avp_values else None
        baseline_views = round(sum(view_values) / len(view_values), 2) if view_values else None

        style_feedback, style_counts = self._build_feedback_maps(
            entries=entries,
            key_name="style_key",
            baseline_avp=baseline_avp,
            baseline_views=baseline_views,
        )
        reel_feedback, reel_counts = self._build_feedback_maps(
            entries=entries,
            key_name="reel_key",
            baseline_avp=baseline_avp,
            baseline_views=baseline_views,
        )
        hook_strategy_counts = self._build_strategy_counts(entries=entries, key_name="hook_strategy")
        pacing_strategy_counts = self._build_strategy_counts(entries=entries, key_name="pacing_strategy")
        cutaway_strategy_counts = self._build_strategy_counts(entries=entries, key_name="cutaway_strategy")
        title_strategy_counts = self._build_strategy_counts(entries=entries, key_name="title_strategy")
        strongest_style_key = self._strongest_key(style_feedback)
        strongest_reel_key = self._strongest_key(reel_feedback)
        strongest_hook_strategy = self._strongest_count_key(hook_strategy_counts)
        strongest_pacing_strategy = self._strongest_count_key(pacing_strategy_counts)
        strongest_cutaway_strategy = self._strongest_count_key(cutaway_strategy_counts)
        strongest_title_strategy = self._strongest_count_key(title_strategy_counts)
        return SyntheticAnalyticsCalibration(
            sample_count=len(entries),
            baseline_average_view_percentage=baseline_avp,
            baseline_views=baseline_views,
            style_feedback=style_feedback,
            style_sample_counts=style_counts,
            reel_feedback=reel_feedback,
            reel_sample_counts=reel_counts,
            strongest_style_key=strongest_style_key,
            strongest_reel_key=strongest_reel_key,
            hook_strategy_counts=hook_strategy_counts,
            pacing_strategy_counts=pacing_strategy_counts,
            cutaway_strategy_counts=cutaway_strategy_counts,
            title_strategy_counts=title_strategy_counts,
            strongest_hook_strategy=strongest_hook_strategy,
            strongest_pacing_strategy=strongest_pacing_strategy,
            strongest_cutaway_strategy=strongest_cutaway_strategy,
            strongest_title_strategy=strongest_title_strategy,
            insights=self._build_insights(
                sample_count=len(entries),
                baseline_avp=baseline_avp,
                strongest_style_key=strongest_style_key,
                strongest_reel_key=strongest_reel_key,
                strongest_hook_strategy=strongest_hook_strategy,
                strongest_pacing_strategy=strongest_pacing_strategy,
                strongest_cutaway_strategy=strongest_cutaway_strategy,
                strongest_title_strategy=strongest_title_strategy,
            ),
        )

    def _build_feedback_maps(
        self,
        *,
        entries: list[dict[str, object]],
        key_name: str,
        baseline_avp: float | None,
        baseline_views: float | None,
    ) -> tuple[dict[str, float], dict[str, int]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for item in entries:
            key = _clean_text(item.get(key_name))
            if not key:
                continue
            grouped.setdefault(key, []).append(item)
        feedback: dict[str, float] = {}
        counts: dict[str, int] = {}
        for key, items in grouped.items():
            counts[key] = len(items)
            avp_values = [
                float(row["average_view_percentage"])
                for row in items
                if isinstance(row.get("average_view_percentage"), (int, float))
            ]
            view_values = [
                float(row["views"])
                for row in items
                if isinstance(row.get("views"), (int, float))
            ]
            avg_avp = sum(avp_values) / len(avp_values) if avp_values else None
            avg_views = sum(view_values) / len(view_values) if view_values else None
            feedback[key] = self._score_history(
                count=len(items),
                average_view_percentage=avg_avp,
                average_views=avg_views,
                baseline_avp=baseline_avp,
                baseline_views=baseline_views,
            )
        return feedback, counts

    @staticmethod
    def _build_strategy_counts(*, entries: list[dict[str, object]], key_name: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in entries:
            key = _clean_text(item.get(key_name))
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def _extract_variant_keys(output: dict[str, object]) -> tuple[str | None, str | None]:
        preview = (
            dict(output.get("refined_best_variant") or {})
            or dict(output.get("best_variant") or {})
        )
        style = dict(preview.get("style") or {})
        reel = dict(preview.get("reel_type") or {})
        return (
            _clean_text(style.get("key")) or None,
            _clean_text(reel.get("key")) or None,
        )

    @staticmethod
    def _score_history(
        *,
        count: int,
        average_view_percentage: float | None,
        average_views: float | None,
        baseline_avp: float | None,
        baseline_views: float | None,
    ) -> float:
        avp_component = 0.0
        if average_view_percentage is not None and baseline_avp is not None:
            avp_component = max(-4.5, min(4.5, (average_view_percentage - baseline_avp) / 4.0))
        views_component = 0.0
        if average_views is not None and baseline_views is not None and baseline_views > 0:
            views_component = max(
                -2.5,
                min(2.5, (log1p(average_views) - log1p(baseline_views)) * 3.0),
            )
        confidence = min(1.0, max(0.34, count / 3.0))
        return round((avp_component + views_component) * confidence, 2)

    @staticmethod
    def _strongest_key(feedback: dict[str, float]) -> str | None:
        if not feedback:
            return None
        key, value = max(feedback.items(), key=lambda item: item[1])
        if value <= 0:
            return None
        return key

    @staticmethod
    def _strongest_count_key(counts: dict[str, int]) -> str | None:
        if not counts:
            return None
        key, value = max(counts.items(), key=lambda item: item[1])
        if value <= 0:
            return None
        return key

    @staticmethod
    def _build_insights(
        *,
        sample_count: int,
        baseline_avp: float | None,
        strongest_style_key: str | None,
        strongest_reel_key: str | None,
        strongest_hook_strategy: str | None,
        strongest_pacing_strategy: str | None,
        strongest_cutaway_strategy: str | None,
        strongest_title_strategy: str | None,
    ) -> tuple[str, ...]:
        insights: list[str] = [f"Gotowe analytics sample: {sample_count}."]
        if baseline_avp is not None:
            insights.append(f"Sredni average_view_percentage projektu: {baseline_avp}%.")
        if strongest_style_key:
            insights.append(f"Najlepszy styl historycznie: {strongest_style_key}.")
        if strongest_reel_key:
            insights.append(f"Najlepszy typ rolki historycznie: {strongest_reel_key}.")
        if strongest_hook_strategy:
            insights.append(f"Najmocniejsza strategia hooka: {strongest_hook_strategy}.")
        if strongest_pacing_strategy:
            insights.append(f"Najmocniejsza strategia pacingu: {strongest_pacing_strategy}.")
        if strongest_cutaway_strategy:
            insights.append(f"Najmocniejsza strategia cutawayow: {strongest_cutaway_strategy}.")
        if strongest_title_strategy:
            insights.append(f"Najmocniejsza strategia tytulu: {strongest_title_strategy}.")
        return tuple(insights[:6])


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None
