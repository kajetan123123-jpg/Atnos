from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atnos.services.creative_trend_evaluator_service import CreativeTrendEvaluatorService
from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsCalibration


@dataclass(frozen=True, slots=True)
class SyntheticVariantScore:
    variant_index: int
    score: float
    confidence: str
    strengths: tuple[str, ...]
    risks: tuple[str, ...]
    breakdown: dict[str, float]
    style_key: str | None
    reel_key: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "variant_index": self.variant_index,
            "score": self.score,
            "confidence": self.confidence,
            "strengths": list(self.strengths),
            "risks": list(self.risks),
            "breakdown": dict(self.breakdown),
            "style_key": self.style_key,
            "reel_key": self.reel_key,
        }


class SyntheticVariantScoringService:
    TARGET_DURATIONS: dict[str, tuple[float, float]] = {
        "talking_hook": (12.0, 18.0),
        "micro_story": (18.0, 24.0),
        "cta_teaser": (14.0, 20.0),
        "behind_the_scene": (16.0, 22.0),
    }

    def __init__(self) -> None:
        self.trend_evaluator = CreativeTrendEvaluatorService()

    def rank_variants(
        self,
        variants: list[dict[str, Any]],
        *,
        calibration: SyntheticAnalyticsCalibration | dict[str, Any] | None = None,
        trend_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        resolved_calibration = self._coerce_calibration(calibration)
        scores = [
            self.score_variant(
                item,
                index=index,
                calibration=resolved_calibration,
                trend_snapshot=trend_snapshot,
            )
            for index, item in enumerate(variants)
        ]
        ranked = sorted(scores, key=lambda item: item.score, reverse=True)
        best = ranked[0] if ranked else None
        return {
            "best_variant_index": best.variant_index if best is not None else None,
            "best_score": best.score if best is not None else None,
            "best_confidence": best.confidence if best is not None else None,
            "ranked_variants": [item.to_dict() for item in ranked],
        }

    def score_variant(
        self,
        preview: dict[str, Any],
        *,
        index: int,
        calibration: SyntheticAnalyticsCalibration | dict[str, Any] | None = None,
        trend_snapshot: dict[str, Any] | None = None,
    ) -> SyntheticVariantScore:
        resolved_calibration = self._coerce_calibration(calibration)
        style = dict(preview.get("style") or {})
        reel = dict(preview.get("reel_type") or {})
        script = dict(preview.get("script_bundle") or {})
        artifacts = dict(preview.get("artifacts") or {})
        motion_reel = dict(artifacts.get("motion_reel") or {})
        qa = dict(motion_reel.get("qa") or {})
        cutaways = dict(artifacts.get("cutaways") or {})
        cutaway_assets = list(cutaways.get("assets") or [])
        warnings = list(qa.get("warnings") or []) + list(cutaways.get("warnings") or [])

        hook = str(script.get("hook") or "").strip()
        body = [str(item).strip() for item in list(script.get("body") or []) if str(item).strip()]
        duration = float(qa.get("duration_sec") or 0.0)
        width = int(qa.get("width") or 0)
        height = int(qa.get("height") or 0)
        has_audio = bool(qa.get("has_audio"))
        reel_key = str(reel.get("key") or "").strip() or None
        style_key = str(style.get("key") or "").strip() or None
        historical_feedback, history_strengths, history_risks = (
            resolved_calibration.feedback_for(style_key=style_key, reel_key=reel_key)
            if resolved_calibration.sample_count > 0
            else (0.0, (), ())
        )
        trend_evaluation = self.trend_evaluator.evaluate(preview=preview, trend_snapshot=trend_snapshot)

        breakdown = {
            "technical": self._technical_score(width=width, height=height, has_audio=has_audio, warnings=warnings),
            "retention_proxy": self._retention_score(
                reel_key=reel_key,
                duration=duration,
                hook=hook,
                body_count=len(body),
                cutaways_count=len(cutaway_assets),
            ),
            "creative_support": self._creative_support_score(
                hook=hook,
                body_count=len(body),
                cutaways_count=len(cutaway_assets),
            ),
            "historical_feedback": historical_feedback,
            "market_alignment": float(trend_evaluation.get("score") or 0.0),
        }
        score = round(max(0.0, min(100.0, sum(breakdown.values()))), 2)
        strengths = tuple(
            [
                *history_strengths,
                *tuple(str(item) for item in list(trend_evaluation.get("strengths") or [])),
                *self._strengths(
                    width=width,
                    height=height,
                    duration=duration,
                    has_audio=has_audio,
                    warnings=warnings,
                    cutaways_count=len(cutaway_assets),
                    hook=hook,
                    reel_key=reel_key,
                ),
            ][:4]
        )
        risks = tuple(
            [
                *history_risks,
                *tuple(str(item) for item in list(trend_evaluation.get("risks") or [])),
                *self._risks(
                    width=width,
                    height=height,
                    duration=duration,
                    has_audio=has_audio,
                    warnings=warnings,
                    cutaways_count=len(cutaway_assets),
                    hook=hook,
                    reel_key=reel_key,
                ),
            ][:4]
        )
        return SyntheticVariantScore(
            variant_index=index,
            score=score,
            confidence=self._confidence(score),
            strengths=strengths,
            risks=risks,
            breakdown=breakdown,
            style_key=style_key,
            reel_key=reel_key,
        )

    @staticmethod
    def _coerce_calibration(
        calibration: SyntheticAnalyticsCalibration | dict[str, Any] | None,
    ) -> SyntheticAnalyticsCalibration:
        if isinstance(calibration, SyntheticAnalyticsCalibration):
            return calibration
        if isinstance(calibration, dict):
            return SyntheticAnalyticsCalibration.from_dict(calibration)
        return SyntheticAnalyticsCalibration.empty()

    @staticmethod
    def _technical_score(*, width: int, height: int, has_audio: bool, warnings: list[str]) -> float:
        score = 0.0
        if width == 1080 and height == 1920:
            score += 35.0
        elif width >= 720 and height >= 1280:
            score += 26.0
        elif width > 0 and height > 0:
            score += 14.0
        if has_audio:
            score += 10.0
        warning_penalty = min(15.0, float(len(warnings)) * 5.0)
        return max(0.0, min(45.0, score - warning_penalty))

    def _retention_score(
        self,
        *,
        reel_key: str | None,
        duration: float,
        hook: str,
        body_count: int,
        cutaways_count: int,
    ) -> float:
        score = 0.0
        min_target, max_target = self.TARGET_DURATIONS.get(reel_key or "", (14.0, 24.0))
        if min_target <= duration <= max_target:
            score += 24.0
        else:
            distance = min(abs(duration - min_target), abs(duration - max_target))
            score += max(8.0, 24.0 - min(distance * 1.35, 16.0))

        hook_words = len([part for part in hook.split() if part.strip()])
        if 6 <= hook_words <= 13:
            score += 10.0
        elif 4 <= hook_words <= 16:
            score += 6.0
        else:
            score += 2.0

        if body_count <= 3:
            score += 4.0
        elif body_count == 4:
            score += 2.0

        if cutaways_count >= 2:
            score += 6.0
        elif cutaways_count == 1:
            score += 3.0
        return min(44.0, score)

    @staticmethod
    def _creative_support_score(*, hook: str, body_count: int, cutaways_count: int) -> float:
        score = 0.0
        lowered = hook.lower()
        if any(marker in lowered for marker in ("zatrzymaj", "hej", "w kilka sekund", "jesli", "to jest")):
            score += 5.0
        if "?" not in hook and "..." not in hook:
            score += 3.0
        if body_count in {2, 3}:
            score += 4.0
        if cutaways_count >= 3:
            score += 4.0
        elif cutaways_count == 2:
            score += 2.0
        return min(11.0, score)

    def _strengths(
        self,
        *,
        width: int,
        height: int,
        duration: float,
        has_audio: bool,
        warnings: list[str],
        cutaways_count: int,
        hook: str,
        reel_key: str | None,
    ) -> tuple[str, ...]:
        strengths: list[str] = []
        if width == 1080 and height == 1920:
            strengths.append("Pelny pionowy format 1080x1920.")
        if has_audio:
            strengths.append("Render ma poprawna sciezke audio.")
        if cutaways_count >= 2:
            strengths.append(f"Rolka ma {cutaways_count} wspierajace cutawaye zamiast samego talking-heada.")
        min_target, max_target = self.TARGET_DURATIONS.get(reel_key or "", (14.0, 24.0))
        if min_target <= duration <= max_target:
            strengths.append("Dlugosc miesci sie w sensownym zakresie dla tego typu rolki.")
        hook_words = len([part for part in hook.split() if part.strip()])
        if 6 <= hook_words <= 13:
            strengths.append("Hook jest wystarczajaco krotki, zeby wejsc szybko w pierwszych sekundach.")
        if not warnings:
            strengths.append("QA nie zglosilo ostrzezen technicznych.")
        return tuple(strengths[:4])

    def _risks(
        self,
        *,
        width: int,
        height: int,
        duration: float,
        has_audio: bool,
        warnings: list[str],
        cutaways_count: int,
        hook: str,
        reel_key: str | None,
    ) -> tuple[str, ...]:
        risks: list[str] = []
        if width < 1080 or height < 1920:
            risks.append("Output nie jest w pelnym docelowym formacie 1080x1920.")
        if not has_audio:
            risks.append("Brak stabilnej sciezki audio.")
        if cutaways_count == 0:
            risks.append("Brak cutawayow - rolka za bardzo opiera sie na jednym kadrze.")
        elif cutaways_count == 1:
            risks.append("Tylko jeden cutaway - rytm rolki nadal moze byc zbyt plaski.")
        min_target, max_target = self.TARGET_DURATIONS.get(reel_key or "", (14.0, 24.0))
        if duration < min_target or duration > max_target:
            risks.append("Dlugosc odbiega od docelowego okna dla tego typu rolki.")
        hook_words = len([part for part in hook.split() if part.strip()])
        if hook_words < 4 or hook_words > 16:
            risks.append("Hook ma slaby rozmiar pod retention proxy.")
        if warnings:
            risks.extend(str(item) for item in warnings[:2])
        return tuple(risks[:4])

    @staticmethod
    def _confidence(score: float) -> str:
        if score >= 82:
            return "high"
        if score >= 68:
            return "medium"
        return "low"
