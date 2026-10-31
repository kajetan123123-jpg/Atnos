from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsCalibration
from atnos.services.synthetic_variant_planning_service import SyntheticVariantPlanningService


class SyntheticProjectStrategyService:
    REEL_KEYS: tuple[str, ...] = ("talking_hook", "micro_story", "cta_teaser", "behind_the_scene")

    def __init__(self, planner: SyntheticVariantPlanningService | None = None) -> None:
        self.planner = planner or SyntheticVariantPlanningService()

    def build_profile(
        self,
        *,
        project_name: str,
        calibration: SyntheticAnalyticsCalibration | dict[str, Any] | None,
    ) -> dict[str, object]:
        payload = self._coerce_calibration(calibration)
        if payload.sample_count <= 0:
            return {
                "version": 1,
                "derived_sample_count": 0,
                "project_name": project_name,
                "updated_at": self._now().isoformat(),
                "hook_template": None,
                "pacing_policy": None,
                "cutaway_policy": None,
                "title_policy": None,
                "next_generation_brief": None,
                "strongest_style_key": None,
                "strongest_reel_key": None,
                "initial_hard_controls_by_reel": {},
                "summary": [
                    "Projekt nie ma jeszcze gotowych analytics, wiec domyslne template'y pozostaja neutralne."
                ],
            }
        controls_by_reel: dict[str, object] = {}
        for reel_key in self.REEL_KEYS:
            controls = self.planner.build_initial_hard_controls(
                reel_key=reel_key,
                history_score=float(payload.reel_feedback.get(reel_key) or 0.0),
                hook_strategy=payload.strongest_hook_strategy,
                pacing_strategy=payload.strongest_pacing_strategy,
                cutaway_strategy=payload.strongest_cutaway_strategy,
            )
            controls_by_reel[reel_key] = controls.to_dict()
        summary = [
            f"Profil oparty na {payload.sample_count} historycznych analytics projektu.",
        ]
        if payload.strongest_style_key:
            summary.append(f"Najmocniejszy styl: {payload.strongest_style_key}.")
        if payload.strongest_reel_key:
            summary.append(f"Najmocniejszy typ rolki: {payload.strongest_reel_key}.")
        for entry in (
            self._policy_line("hooka", payload.strongest_hook_strategy),
            self._policy_line("pacingu", payload.strongest_pacing_strategy),
            self._policy_line("cutawayów", payload.strongest_cutaway_strategy),
            self._policy_line("tytułu", payload.strongest_title_strategy),
        ):
            if entry:
                summary.append(entry)
        return {
            "version": 1,
            "derived_sample_count": payload.sample_count,
            "project_name": project_name,
            "updated_at": self._now().isoformat(),
            "hook_template": self._build_template_policy(
                kind="hook",
                strategy_key=payload.strongest_hook_strategy,
            ),
            "pacing_policy": self._build_template_policy(
                kind="pacing",
                strategy_key=payload.strongest_pacing_strategy,
            ),
            "cutaway_policy": self._build_template_policy(
                kind="cutaway",
                strategy_key=payload.strongest_cutaway_strategy,
            ),
            "title_policy": self._build_template_policy(
                kind="title",
                strategy_key=payload.strongest_title_strategy,
            ),
            "next_generation_brief": self._build_next_generation_brief(
                strongest_style_key=payload.strongest_style_key,
                strongest_reel_key=payload.strongest_reel_key,
                strongest_hook_strategy=payload.strongest_hook_strategy,
                strongest_pacing_strategy=payload.strongest_pacing_strategy,
                strongest_cutaway_strategy=payload.strongest_cutaway_strategy,
                strongest_title_strategy=payload.strongest_title_strategy,
            ),
            "strongest_style_key": payload.strongest_style_key,
            "strongest_reel_key": payload.strongest_reel_key,
            "initial_hard_controls_by_reel": controls_by_reel,
            "summary": summary[:8],
        }

    @staticmethod
    def _build_template_policy(*, kind: str, strategy_key: str | None) -> dict[str, object] | None:
        if not strategy_key:
            return None
        mapping = {
            ("hook", "keep_hook_profile"): ("current_winner", "Zachowaj obecny profil hooka i nie rozwadniaj pierwszej sekundy."),
            ("hook", "faster_hook"): ("direct_payoff", "Hook ma wchodzic szybciej i bez miekkiego rozgrzewania intro."),
            ("hook", "sharper_hook"): ("contrast_first", "Hook ma byc ostrzejszy, krotszy i bardziej kontrastowy."),
            ("pacing", "maintain_pacing"): ("steady_payoff", "Utrzymaj obecny rytm i nie dokladaj pustego srodka."),
            ("pacing", "tighter_intro"): ("tight_intro", "Intro ma byc ciasniejsze, a payoff ma wejsc szybciej."),
            ("pacing", "compress_body"): ("single_beat_body", "Body ma zostac scisniete do jednego mocnego beatu."),
            ("cutaway", "maintain_cutaways"): ("steady_support", "Trzymaj obecna gestosc cutawayow bez przeinwestowania."),
            ("cutaway", "add_midbeat_cutaway"): ("midbeat_support", "W polowie rolki dodawaj wyrazny cutaway przelamujacy rytm."),
            ("cutaway", "stronger_cutaway_contrast"): ("contrast_breaker", "Cutawaye maja mocniej kontrastowac i przerywac plaski rytm."),
            ("title", "keep_title_direction"): ("current_direction", "Trzymaj obecny kierunek tytulu i nie rozwlekaj metadata."),
            ("title", "test_hook_first_title"): ("hook_first_test", "Testuj tytuly, ktore zaczynaja od hooka albo payoffu."),
            ("title", "hook_first_title"): ("hook_first", "Domyslnie zaczynaj tytul od hooka bez dlugiego wstepu."),
        }
        template_key, instruction = mapping.get((kind, strategy_key), (strategy_key, strategy_key))
        return {
            "strategy_key": strategy_key,
            "template_key": template_key,
            "instruction": instruction,
        }

    @staticmethod
    def _policy_line(label: str, strategy_key: str | None) -> str | None:
        if not strategy_key:
            return None
        return f"Dominująca strategia {label}: {strategy_key}."

    @classmethod
    def _build_next_generation_brief(
        cls,
        *,
        strongest_style_key: str | None,
        strongest_reel_key: str | None,
        strongest_hook_strategy: str | None,
        strongest_pacing_strategy: str | None,
        strongest_cutaway_strategy: str | None,
        strongest_title_strategy: str | None,
    ) -> str:
        segments: list[str] = []
        if strongest_style_key and strongest_reel_key:
            segments.append(
                f"Priorytetem kolejnego batcha jest styl {strongest_style_key} i typ rolki {strongest_reel_key}."
            )
        elif strongest_style_key:
            segments.append(f"Priorytetem kolejnego batcha jest styl {strongest_style_key}.")
        elif strongest_reel_key:
            segments.append(f"Priorytetem kolejnego batcha jest typ rolki {strongest_reel_key}.")
        for hint in (
            cls._brief_hint("hook", strongest_hook_strategy),
            cls._brief_hint("pacing", strongest_pacing_strategy),
            cls._brief_hint("cutaway", strongest_cutaway_strategy),
            cls._brief_hint("title", strongest_title_strategy),
        ):
            if hint:
                segments.append(hint)
        if not segments:
            return "Utrzymaj neutralny batch i sprawdz kilka roznych wariantow bez rozwlekania hooka."
        return " ".join(segments[:5]).strip()

    @staticmethod
    def _brief_hint(kind: str, strategy_key: str | None) -> str | None:
        mapping = {
            ("hook", "keep_hook_profile"): "Zachowaj obecny profil hooka i nie rozwadniaj pierwszej sekundy.",
            ("hook", "faster_hook"): "Wejdz szybciej w hook i nie rozgrzewaj intro.",
            ("hook", "sharper_hook"): "Zrob ostrzejszy, bardziej kontrastowy hook.",
            ("pacing", "maintain_pacing"): "Nie rozwlekaj srodka i utrzymaj czytelny payoff.",
            ("pacing", "tighter_intro"): "Scisnij intro i szybciej dojdz do payoffu.",
            ("pacing", "compress_body"): "Scisnij body do jednego mocnego beatu.",
            ("cutaway", "maintain_cutaways"): "Nie przeinwestowuj cutawayow i trzymaj stabilny support.",
            ("cutaway", "add_midbeat_cutaway"): "Dodaj wyrazny mid-beat cutaway w srodkowej czesci rolki.",
            ("cutaway", "stronger_cutaway_contrast"): "Zrob mocniejszy kontrast cutawayow.",
            ("title", "keep_title_direction"): "Nie rozwlekaj metadata i trzymaj obecny kierunek tytulu.",
            ("title", "test_hook_first_title"): "Testuj tytuly w formule hook-first.",
            ("title", "hook_first_title"): "Domyslnie zaczynaj tytul od hooka.",
        }
        return mapping.get((kind, strategy_key or ""))

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
    def _now() -> datetime:
        return datetime.now(UTC)
