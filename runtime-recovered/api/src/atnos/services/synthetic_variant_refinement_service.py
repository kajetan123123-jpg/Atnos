from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsCalibration


@dataclass(frozen=True, slots=True)
class SyntheticPreviewHardControls:
    target_duration_window_sec: tuple[float, float] | None
    target_duration_cap_sec: float | None
    max_hook_words: int | None
    max_body_lines: int | None
    drop_outro: bool
    voice_speed_multiplier: float
    hook_duration_cap_sec: float | None
    max_cutaways: int | None
    cutaway_display_duration_sec: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "target_duration_window_sec": list(self.target_duration_window_sec) if self.target_duration_window_sec else None,
            "target_duration_cap_sec": self.target_duration_cap_sec,
            "max_hook_words": self.max_hook_words,
            "max_body_lines": self.max_body_lines,
            "drop_outro": self.drop_outro,
            "voice_speed_multiplier": self.voice_speed_multiplier,
            "hook_duration_cap_sec": self.hook_duration_cap_sec,
            "max_cutaways": self.max_cutaways,
            "cutaway_display_duration_sec": self.cutaway_display_duration_sec,
        }

    def escalate(self) -> "SyntheticPreviewHardControls":
        target_window = self.target_duration_window_sec
        minimum = target_window[0] if target_window is not None else 8.0
        current_cap = self.target_duration_cap_sec
        next_cap = max(minimum, round((current_cap or minimum) - 1.6, 2)) if current_cap is not None else None
        next_hook_words = max(6, (self.max_hook_words or 10) - 2) if self.max_hook_words is not None else 8
        next_body_lines = max(1, (self.max_body_lines or 2) - 1) if self.max_body_lines is not None else 1
        next_hook_duration = (
            max(2.4, round((self.hook_duration_cap_sec or 4.0) - 0.45, 2))
            if self.hook_duration_cap_sec is not None
            else 3.0
        )
        next_cutaway_duration = (
            max(0.7, round((self.cutaway_display_duration_sec or 1.0) - 0.15, 2))
            if self.cutaway_display_duration_sec is not None
            else 0.8
        )
        return SyntheticPreviewHardControls(
            target_duration_window_sec=target_window,
            target_duration_cap_sec=next_cap,
            max_hook_words=next_hook_words,
            max_body_lines=next_body_lines,
            drop_outro=True,
            voice_speed_multiplier=round(min(1.18, max(1.06, self.voice_speed_multiplier + 0.06)), 2),
            hook_duration_cap_sec=next_hook_duration,
            max_cutaways=1 if (self.max_cutaways or 2) <= 2 else 2,
            cutaway_display_duration_sec=next_cutaway_duration,
        )


@dataclass(frozen=True, slots=True)
class SyntheticVariantRefinementPlan:
    source_variant_index: int
    style_key: str | None
    reel_key: str | None
    goal: str
    directives: tuple[str, ...]
    target_duration_window_sec: tuple[float, float] | None
    score_before: float | None
    confidence_before: str | None
    hard_controls: SyntheticPreviewHardControls | None
    historical_feedback_score: float | None
    historical_strengths: tuple[str, ...]
    historical_risks: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_variant_index": self.source_variant_index,
            "style_key": self.style_key,
            "reel_key": self.reel_key,
            "goal": self.goal,
            "directives": list(self.directives),
            "target_duration_window_sec": list(self.target_duration_window_sec) if self.target_duration_window_sec else None,
            "score_before": self.score_before,
            "confidence_before": self.confidence_before,
            "hard_controls": self.hard_controls.to_dict() if self.hard_controls is not None else None,
            "historical_feedback_score": self.historical_feedback_score,
            "historical_strengths": list(self.historical_strengths),
            "historical_risks": list(self.historical_risks),
        }


class SyntheticVariantRefinementService:
    TARGET_DURATIONS: dict[str, tuple[float, float]] = {
        "talking_hook": (12.0, 18.0),
        "micro_story": (18.0, 24.0),
        "cta_teaser": (14.0, 20.0),
        "behind_the_scene": (16.0, 22.0),
    }

    def build_plan(
        self,
        *,
        project_name: str,
        best_variant: dict[str, Any],
        selection: dict[str, object],
        analytics_learning_profile: SyntheticAnalyticsCalibration | dict[str, Any] | None = None,
    ) -> SyntheticVariantRefinementPlan:
        calibration = self._coerce_calibration(analytics_learning_profile)
        style = dict(best_variant.get("style") or {})
        reel = dict(best_variant.get("reel_type") or {})
        script = dict(best_variant.get("script_bundle") or {})
        artifacts = dict(best_variant.get("artifacts") or {})
        motion_reel = dict(artifacts.get("motion_reel") or {})
        qa = dict(motion_reel.get("qa") or {})
        cutaways = dict(artifacts.get("cutaways") or {})
        cutaway_assets = list(cutaways.get("assets") or [])

        source_variant_index = self._best_variant_index(selection)
        style_key = self._clean(style.get("key"))
        reel_key = self._clean(reel.get("key"))
        duration = float(qa.get("duration_sec") or 0.0)
        hook = self._clean(script.get("hook"))
        hook_words = len([part for part in hook.split() if part.strip()])
        target_window = self.TARGET_DURATIONS.get(reel_key or "")
        directives: list[str] = []

        historical_feedback_score = 0.0
        historical_strengths: tuple[str, ...] = ()
        historical_risks: tuple[str, ...] = ()
        if calibration.sample_count > 0:
            historical_feedback_score, historical_strengths, historical_risks = calibration.feedback_for(
                style_key=style_key,
                reel_key=reel_key,
            )
            if historical_feedback_score <= -2.0:
                directives.append(
                    "Ten styl lub typ rolki historycznie dowozil slabsze retention. Wejdz szybciej w payoff, tnij body i nie zostawiaj miekkiego intro."
                )
            elif historical_feedback_score >= 2.0:
                directives.append(
                    "Ta kombinacja historycznie dowozila lepsze retention. Zachowaj jej rytm i nie rozwadniaj pierwszych sekund."
                )

        if target_window is not None:
            minimum, maximum = target_window
            if duration > maximum:
                directives.append(
                    f"Skroc pacing i body tak, zeby finalna rolka zamknela sie w {int(minimum)}-{int(maximum)} sekundach."
                )
            elif 0 < duration < minimum:
                directives.append(
                    f"Dodaj jeden krotki beat wsparcia, zeby dobic do {int(minimum)}-{int(maximum)} sekund bez rozwlekania."
                )
            else:
                directives.append(
                    f"Utrzymaj timing w oknie {int(minimum)}-{int(maximum)} sekund i wejdz w payoff w pierwszych 2 sekundach."
                )
        if hook_words > 13:
            directives.append("Skroc hook do 6-11 slow i zostaw jeden mocny obraz lub kontrast.")
        elif hook_words < 6:
            directives.append("Wzmocnij hook do 6-10 slow, ale bez pytajnikow i bez marketingowego tonu.")
        else:
            directives.append("Zostaw krotki hook, ale podnies kontrast jezyka i czytelnosc pierwszego kadru.")

        if len(cutaway_assets) < 2:
            directives.append("Dodaj 2-3 wspierajace cutawaye, zeby rolka nie opierala sie tylko na jednym kadrze.")
        else:
            directives.append("Przestaw cutawaye tak, zeby mocniej zmienialy rytm w srodkowych 40-75% rolki.")

        directives.append("Spal napisy ciasniej do dolnej strefy i nie przykrywaj nimi twarzy ani hook blocku.")

        ranked = list(selection.get("ranked_variants") or [])
        current_row = next(
            (dict(item) for item in ranked if int(dict(item).get("variant_index", -1)) == source_variant_index),
            {},
        )
        score_before = current_row.get("score")
        confidence_before = current_row.get("confidence")
        style_label = self._clean(style.get("label")) or "selected style"
        reel_label = self._clean(reel.get("label")) or "selected reel"

        goal = (
            f"Dopracuj zwycieski synthetic creator reel dla projektu {project_name}. "
            f"Zachowaj styl {style_label} i typ rolki {reel_label}. "
            + " ".join(directives)
        )
        hard_controls = self._build_hard_controls(
            reel_key=reel_key,
            duration=duration,
            hook_words=hook_words,
            cutaways_count=len(cutaway_assets),
            historical_feedback_score=historical_feedback_score,
        )
        return SyntheticVariantRefinementPlan(
            source_variant_index=source_variant_index,
            style_key=style_key,
            reel_key=reel_key,
            goal=goal,
            directives=tuple(directives),
            target_duration_window_sec=target_window or None,
            score_before=float(score_before) if isinstance(score_before, (int, float)) else None,
            confidence_before=str(confidence_before) if confidence_before is not None else None,
            hard_controls=hard_controls,
            historical_feedback_score=historical_feedback_score if calibration.sample_count > 0 else None,
            historical_strengths=historical_strengths,
            historical_risks=historical_risks,
        )

    def _build_hard_controls(
        self,
        *,
        reel_key: str | None,
        duration: float,
        hook_words: int,
        cutaways_count: int,
        historical_feedback_score: float,
    ) -> SyntheticPreviewHardControls:
        target_window = self.TARGET_DURATIONS.get(reel_key or "", (14.0, 24.0))
        minimum, maximum = target_window
        target_cap = round(max(minimum, maximum - 0.35), 2)
        too_long = duration > maximum if duration > 0 else False
        hook_limits = {
            "talking_hook": 8,
            "cta_teaser": 9,
            "micro_story": 10,
            "behind_the_scene": 10,
        }
        body_limits = {
            "talking_hook": 2,
            "cta_teaser": 1,
            "micro_story": 2,
            "behind_the_scene": 2,
        }
        hook_caps = {
            "talking_hook": 3.1,
            "cta_teaser": 3.4,
            "micro_story": 4.0,
            "behind_the_scene": 4.2,
        }
        cutaway_durations = {
            "talking_hook": 0.82,
            "cta_teaser": 0.9,
            "micro_story": 1.0,
            "behind_the_scene": 1.05,
        }
        max_hook_words = hook_limits.get(reel_key or "", 9)
        if 0 < hook_words < max_hook_words:
            max_hook_words = max(6, hook_words)
        max_cutaways = 2 if reel_key in {"talking_hook", "cta_teaser"} else 3
        if cutaways_count <= 1:
            max_cutaways = max(max_cutaways, 2)
        voice_speed = 1.05 if too_long else 1.02

        if historical_feedback_score <= -2.0:
            target_cap = round(max(minimum, target_cap - 0.65), 2)
            max_hook_words = max(6, max_hook_words - 1)
            max_cutaways = max(max_cutaways, 3 if reel_key in {"micro_story", "behind_the_scene"} else 2)
            voice_speed = round(min(1.12, voice_speed + 0.03), 2)
        elif historical_feedback_score >= 2.0:
            voice_speed = round(max(1.0, voice_speed - 0.01), 2)

        return SyntheticPreviewHardControls(
            target_duration_window_sec=target_window,
            target_duration_cap_sec=target_cap,
            max_hook_words=max_hook_words,
            max_body_lines=body_limits.get(reel_key or "", 2),
            drop_outro=too_long,
            voice_speed_multiplier=voice_speed,
            hook_duration_cap_sec=hook_caps.get(reel_key or "", 3.6),
            max_cutaways=max_cutaways,
            cutaway_display_duration_sec=cutaway_durations.get(reel_key or "", 0.95),
        )

    @staticmethod
    def _best_variant_index(selection: dict[str, object]) -> int:
        value = selection.get("best_variant_index")
        return int(value) if isinstance(value, int) or (isinstance(value, float) and value.is_integer()) else 0

    @staticmethod
    def _coerce_calibration(
        value: SyntheticAnalyticsCalibration | dict[str, Any] | None,
    ) -> SyntheticAnalyticsCalibration:
        if isinstance(value, SyntheticAnalyticsCalibration):
            return value
        if isinstance(value, dict):
            return SyntheticAnalyticsCalibration.from_dict(value)
        return SyntheticAnalyticsCalibration.empty()

    @staticmethod
    def _clean(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        return normalized or None
