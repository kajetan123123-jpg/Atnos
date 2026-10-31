from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsCalibration
from atnos.services.synthetic_variant_refinement_service import SyntheticPreviewHardControls


@dataclass(frozen=True, slots=True)
class SyntheticVariantPlanItem:
    style_key: str
    style_label: str
    reel_key: str
    reel_label: str
    goal: str
    rationale: tuple[str, ...]
    priority_score: float
    historical_feedback_score: float
    candidate_kind: str
    initial_hard_controls: SyntheticPreviewHardControls | None

    def to_dict(self) -> dict[str, object]:
        return {
            "style_key": self.style_key,
            "style_label": self.style_label,
            "reel_key": self.reel_key,
            "reel_label": self.reel_label,
            "goal": self.goal,
            "rationale": list(self.rationale),
            "priority_score": self.priority_score,
            "historical_feedback_score": self.historical_feedback_score,
            "candidate_kind": self.candidate_kind,
            "initial_hard_controls": self.initial_hard_controls.to_dict() if self.initial_hard_controls is not None else None,
        }


@dataclass(frozen=True, slots=True)
class SyntheticVariantPlanningProfile:
    sample_count: int
    strongest_style_key: str | None
    strongest_reel_key: str | None
    strategy_summary: tuple[str, ...]
    recommendations: tuple[SyntheticVariantPlanItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_count": self.sample_count,
            "strongest_style_key": self.strongest_style_key,
            "strongest_reel_key": self.strongest_reel_key,
            "strategy_summary": list(self.strategy_summary),
            "recommendations": [item.to_dict() for item in self.recommendations],
        }


class SyntheticVariantPlanningService:
    TARGET_DURATIONS: dict[str, tuple[float, float]] = {
        "talking_hook": (12.0, 18.0),
        "micro_story": (18.0, 24.0),
        "cta_teaser": (14.0, 20.0),
        "behind_the_scene": (16.0, 22.0),
    }

    DEFAULT_BODY_LINES: dict[str, int] = {
        "talking_hook": 2,
        "micro_story": 3,
        "cta_teaser": 2,
        "behind_the_scene": 3,
    }

    def build_plan(
        self,
        *,
        project_name: str,
        goal: str | None,
        project_strategy_profile: dict[str, Any] | None = None,
        styles: list[dict[str, str]],
        reels: list[dict[str, str]],
        max_items: int,
        calibration: SyntheticAnalyticsCalibration | dict[str, Any] | None = None,
    ) -> SyntheticVariantPlanningProfile:
        resolved_calibration = self._coerce_calibration(calibration)
        combinations = self._build_candidates(
            project_name=project_name,
            goal=goal,
            project_strategy_profile=project_strategy_profile,
            styles=styles,
            reels=reels,
            calibration=resolved_calibration,
        )
        recommendations = self._select_candidates(
            candidates=combinations,
            limit=max(1, min(max_items, len(combinations) or 1)),
            sample_count=resolved_calibration.sample_count,
        )
        return SyntheticVariantPlanningProfile(
            sample_count=resolved_calibration.sample_count,
            strongest_style_key=resolved_calibration.strongest_style_key,
            strongest_reel_key=resolved_calibration.strongest_reel_key,
            strategy_summary=self._build_strategy_summary(
                calibration=resolved_calibration,
                recommendations=recommendations,
            ),
            recommendations=tuple(recommendations),
        )

    def _build_candidates(
        self,
        *,
        project_name: str,
        goal: str | None,
        project_strategy_profile: dict[str, Any] | None,
        styles: list[dict[str, str]],
        reels: list[dict[str, str]],
        calibration: SyntheticAnalyticsCalibration,
    ) -> list[SyntheticVariantPlanItem]:
        items: list[SyntheticVariantPlanItem] = []
        explicit_goal = (goal or "").strip()
        strategy_brief = self._project_strategy_brief(project_strategy_profile)
        for style in styles:
            style_key = str(style.get("key") or "").strip()
            style_label = str(style.get("label") or style_key).strip() or style_key
            if not style_key:
                continue
            for reel in reels:
                reel_key = str(reel.get("key") or "").strip()
                reel_label = str(reel.get("label") or reel_key).strip() or reel_key
                if not reel_key:
                    continue
                history_score, strengths, risks = (
                    calibration.feedback_for(style_key=style_key, reel_key=reel_key)
                    if calibration.sample_count > 0
                    else (0.0, (), ())
                )
                priority_score = round(
                    history_score
                    + (1.15 if style_key == calibration.strongest_style_key else 0.0)
                    + (0.9 if reel_key == calibration.strongest_reel_key else 0.0),
                    2,
                )
                candidate_kind = "neutral"
                rationale: list[str] = []
                if history_score >= 2.0:
                    candidate_kind = "history_winner"
                    rationale.append("Ta kombinacja historycznie dowozila lepsze retention.")
                elif history_score <= -2.0:
                    candidate_kind = "history_challenger"
                    rationale.append("Ta kombinacja historycznie byla slabsza i zostaje potraktowana jako challenger.")
                if style_key == calibration.strongest_style_key and calibration.strongest_style_key:
                    rationale.append("Styl nalezy do historycznie najmocniejszych w projekcie.")
                if reel_key == calibration.strongest_reel_key and calibration.strongest_reel_key:
                    rationale.append("Typ rolki nalezy do historycznie najmocniejszych w projekcie.")
                if not rationale:
                    rationale.append("Neutralny wariant do porownania i dywersyfikacji batcha.")
                rationale.extend(list(strengths[:1]))
                rationale.extend(list(risks[:1]))

                if explicit_goal:
                    base_goal = explicit_goal
                elif strategy_brief:
                    base_goal = (
                        f"{strategy_brief} Use variant {style_label} / {reel_label} for {project_name}."
                    )
                else:
                    base_goal = f"Validate synthetic creator preview variant {style_label} / {reel_label} for {project_name}."
                if calibration.sample_count > 0:
                    goal_suffix = self._goal_suffix(
                        candidate_kind=candidate_kind,
                        strongest_style_key=calibration.strongest_style_key,
                        strongest_reel_key=calibration.strongest_reel_key,
                        strongest_hook_strategy=calibration.strongest_hook_strategy,
                        strongest_pacing_strategy=calibration.strongest_pacing_strategy,
                        strongest_cutaway_strategy=calibration.strongest_cutaway_strategy,
                        strongest_title_strategy=calibration.strongest_title_strategy,
                        style_key=style_key,
                        reel_key=reel_key,
                    )
                    candidate_goal = f"{base_goal} {goal_suffix}".strip()
                else:
                    candidate_goal = base_goal
                    rationale = ["Domyslny preset bez historii projektu."]
                initial_hard_controls = (
                    self._build_initial_hard_controls(
                        reel_key=reel_key,
                        history_score=history_score,
                        hook_strategy=calibration.strongest_hook_strategy,
                        pacing_strategy=calibration.strongest_pacing_strategy,
                        cutaway_strategy=calibration.strongest_cutaway_strategy,
                    )
                    if calibration.sample_count > 0
                    else None
                )
                items.append(
                    SyntheticVariantPlanItem(
                        style_key=style_key,
                        style_label=style_label,
                        reel_key=reel_key,
                        reel_label=reel_label,
                        goal=candidate_goal,
                        rationale=tuple(rationale[:4]),
                        priority_score=priority_score,
                        historical_feedback_score=history_score,
                        candidate_kind=candidate_kind,
                        initial_hard_controls=initial_hard_controls,
                    )
                )
        return items

    @staticmethod
    def _project_strategy_brief(project_strategy_profile: dict[str, Any] | None) -> str:
        if not isinstance(project_strategy_profile, dict):
            return ""
        raw = project_strategy_profile.get("next_generation_brief")
        if not isinstance(raw, str):
            return ""
        return " ".join(raw.strip().split())

    def _select_candidates(
        self,
        *,
        candidates: list[SyntheticVariantPlanItem],
        limit: int,
        sample_count: int,
    ) -> list[SyntheticVariantPlanItem]:
        if sample_count <= 0:
            return candidates[:limit]
        remaining = list(candidates)
        selected: list[SyntheticVariantPlanItem] = []
        seen_styles: set[str] = set()
        seen_reels: set[str] = set()
        while remaining and len(selected) < limit:
            best_index = 0
            best_score = float("-inf")
            for index, item in enumerate(remaining):
                diversity_bonus = 0.0
                if item.style_key not in seen_styles:
                    diversity_bonus += 0.85
                if item.reel_key not in seen_reels:
                    diversity_bonus += 0.7
                if item.style_key in seen_styles and item.reel_key in seen_reels:
                    diversity_bonus -= 0.35
                effective_score = item.priority_score + diversity_bonus
                if effective_score > best_score:
                    best_score = effective_score
                    best_index = index
            chosen = remaining.pop(best_index)
            selected.append(chosen)
            seen_styles.add(chosen.style_key)
            seen_reels.add(chosen.reel_key)
        return selected

    def _build_strategy_summary(
        self,
        *,
        calibration: SyntheticAnalyticsCalibration,
        recommendations: list[SyntheticVariantPlanItem],
    ) -> tuple[str, ...]:
        summary: list[str] = []
        if calibration.sample_count > 0:
            summary.append(f"Planner uwzglednil {calibration.sample_count} historycznych prob projektu.")
        else:
            summary.append("Planner nie mial historii projektu i zachowal domyslny porzadek presetow.")
        if calibration.strongest_style_key:
            summary.append(f"Najmocniejszy historyczny styl: {calibration.strongest_style_key}.")
        if calibration.strongest_reel_key:
            summary.append(f"Najmocniejszy historyczny typ rolki: {calibration.strongest_reel_key}.")
        if calibration.strongest_hook_strategy:
            summary.append(f"Dominujaca strategia hooka: {calibration.strongest_hook_strategy}.")
        if calibration.strongest_pacing_strategy:
            summary.append(f"Dominujaca strategia pacingu: {calibration.strongest_pacing_strategy}.")
        if calibration.strongest_title_strategy:
            summary.append(f"Dominujaca strategia tytulu: {calibration.strongest_title_strategy}.")
        if recommendations:
            top = recommendations[0]
            summary.append(f"Pierwszy kandydat: {top.style_key} / {top.reel_key}.")
        return tuple(summary[:7])

    @staticmethod
    def _goal_suffix(
        *,
        candidate_kind: str,
        strongest_style_key: str | None,
        strongest_reel_key: str | None,
        strongest_hook_strategy: str | None,
        strongest_pacing_strategy: str | None,
        strongest_cutaway_strategy: str | None,
        strongest_title_strategy: str | None,
        style_key: str,
        reel_key: str,
    ) -> str:
        if candidate_kind == "history_winner":
            base = "Historycznie ten kierunek dowozil lepsze retention. Utrzymaj szybki opening i nie rozwadniaj payoffu."
        elif candidate_kind == "history_challenger":
            base = "To jest challenger wobec slabszej historii. Skroc intro, dociagnij hook i wejdz szybciej w payoff."
        elif style_key == strongest_style_key or reel_key == strongest_reel_key:
            base = "To jest wariant blisko najmocniejszego historycznego kierunku projektu. Nie rozwlekaj pierwszych sekund."
        else:
            base = "To jest wariant dywersyfikacyjny. Sprawdz inny rytm i inny pacing bez utraty czytelnego hooka."
        strategy_hints = [
            hint
            for hint in (
                SyntheticVariantPlanningService._strategy_hint("hook", strongest_hook_strategy),
                SyntheticVariantPlanningService._strategy_hint("pacing", strongest_pacing_strategy),
                SyntheticVariantPlanningService._strategy_hint("cutaway", strongest_cutaway_strategy),
                SyntheticVariantPlanningService._strategy_hint("title", strongest_title_strategy),
            )
            if hint
        ]
        if strategy_hints:
            return f"{base} {' '.join(strategy_hints[:3])}".strip()
        return base

    @staticmethod
    def _strategy_hint(kind: str, value: str | None) -> str:
        mapping = {
            ("hook", "keep_hook_profile"): "Zachowaj obecny profil hooka i nie psuj pierwszej sekundy.",
            ("hook", "faster_hook"): "Wejdz szybciej w hook i nie rozgrzewaj intro.",
            ("hook", "sharper_hook"): "Zrob ostrzejszy, bardziej kontrastowy hook bez miekkiego wejscia.",
            ("pacing", "maintain_pacing"): "Nie rozwlekaj srodka i utrzymaj dotychczasowy rytm.",
            ("pacing", "tighter_intro"): "Skroc intro i przyspiesz dojscie do payoffu.",
            ("pacing", "compress_body"): "Ogranicz body do jednego mocnego beatu i szybciej zamknij pointę.",
            ("cutaway", "maintain_cutaways"): "Zostaw obecna gestosc cutawayow, bez przeinwestowania.",
            ("cutaway", "add_midbeat_cutaway"): "Dodaj wyrazny mid-beat cutaway w srodkowej czesci rolki.",
            ("cutaway", "stronger_cutaway_contrast"): "Zrob mocniejszy kontrast cutawayow, zeby przelamac plaski rytm.",
            ("title", "keep_title_direction"): "Nie rozbijaj tytulu, zachowaj obecny kierunek metadata.",
            ("title", "test_hook_first_title"): "Przygotuj metadata tak, jakby hook mial otwierac tytul.",
            ("title", "hook_first_title"): "Ustaw tytul w formule hook-first, bez dlugiego wstepu.",
        }
        return mapping.get((kind, value or ""), "")

    def _build_initial_hard_controls(
        self,
        *,
        reel_key: str,
        history_score: float,
        hook_strategy: str | None,
        pacing_strategy: str | None,
        cutaway_strategy: str | None,
    ) -> SyntheticPreviewHardControls:
        return self.build_initial_hard_controls(
            reel_key=reel_key,
            history_score=history_score,
            hook_strategy=hook_strategy,
            pacing_strategy=pacing_strategy,
            cutaway_strategy=cutaway_strategy,
        )

    def build_initial_hard_controls(
        self,
        *,
        reel_key: str,
        history_score: float,
        hook_strategy: str | None,
        pacing_strategy: str | None,
        cutaway_strategy: str | None,
    ) -> SyntheticPreviewHardControls:
        target_window = self.TARGET_DURATIONS.get(reel_key, (14.0, 24.0))
        minimum, maximum = target_window
        target_cap = round(max(minimum, maximum - 0.35), 2)
        max_hook_words = 10 if reel_key in {"micro_story", "behind_the_scene"} else 9
        max_body_lines = self.DEFAULT_BODY_LINES.get(reel_key, 2)
        drop_outro = False
        voice_speed_multiplier = 1.0
        hook_duration_cap_sec = 4.0 if reel_key in {"micro_story", "behind_the_scene"} else 3.4
        max_cutaways = 2 if reel_key in {"talking_hook", "cta_teaser"} else 3
        cutaway_display_duration_sec = 1.0 if reel_key in {"micro_story", "behind_the_scene"} else 0.9

        if hook_strategy == "faster_hook":
            max_hook_words = max(6, max_hook_words - 2)
            hook_duration_cap_sec = round(max(2.8, hook_duration_cap_sec - 0.5), 2)
            voice_speed_multiplier = max(voice_speed_multiplier, 1.04)
        elif hook_strategy == "sharper_hook":
            max_hook_words = max(6, max_hook_words - 3)
            hook_duration_cap_sec = round(max(2.6, hook_duration_cap_sec - 0.7), 2)
            voice_speed_multiplier = max(voice_speed_multiplier, 1.06)

        if pacing_strategy == "tighter_intro":
            max_body_lines = max(1, max_body_lines - 1)
            target_cap = round(max(minimum, target_cap - 0.45), 2)
            voice_speed_multiplier = max(voice_speed_multiplier, 1.05)
        elif pacing_strategy == "compress_body":
            max_body_lines = 1
            target_cap = round(max(minimum, target_cap - 1.1), 2)
            drop_outro = True
            voice_speed_multiplier = max(voice_speed_multiplier, 1.08)

        if cutaway_strategy == "add_midbeat_cutaway":
            max_cutaways = max(max_cutaways, 3 if reel_key in {"micro_story", "behind_the_scene"} else 2)
        elif cutaway_strategy == "stronger_cutaway_contrast":
            max_cutaways = max(max_cutaways, 3)
            cutaway_display_duration_sec = round(max(0.75, cutaway_display_duration_sec - 0.15), 2)

        if history_score <= -2.0:
            target_cap = round(max(minimum, target_cap - 0.55), 2)
            max_hook_words = max(6, max_hook_words - 1)
            voice_speed_multiplier = max(voice_speed_multiplier, 1.06)
        elif history_score >= 2.0:
            voice_speed_multiplier = max(1.0, voice_speed_multiplier)

        return SyntheticPreviewHardControls(
            target_duration_window_sec=target_window,
            target_duration_cap_sec=target_cap,
            max_hook_words=max_hook_words,
            max_body_lines=max_body_lines,
            drop_outro=drop_outro,
            voice_speed_multiplier=round(voice_speed_multiplier, 2),
            hook_duration_cap_sec=hook_duration_cap_sec,
            max_cutaways=max_cutaways,
            cutaway_display_duration_sec=cutaway_display_duration_sec,
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
