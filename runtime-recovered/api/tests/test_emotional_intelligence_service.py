from __future__ import annotations

import unittest

from atnos.services.emotional_intelligence_service import EmotionalIntelligenceService


class EmotionalIntelligenceServiceTest(unittest.TestCase):
    def test_detects_frustration_without_defensive_response_style(self) -> None:
        profile = EmotionalIntelligenceService.analyze(
            "Do cholery, wciaz nie dziala i znowu skacze na dol chatu."
        )

        self.assertEqual(profile.state, "frustrated")
        self.assertEqual(profile.intensity, "high")
        self.assertIn("no defensiveness", " ".join(profile.avoid_rules))
        self.assertIn("fix", profile.response_posture)

    def test_detects_overwhelm_and_reduces_choices(self) -> None:
        profile = EmotionalIntelligenceService.analyze(
            "Mam chaos, za duzo rzeczy naraz i nie ogarniam co dalej."
        )

        self.assertEqual(profile.state, "overwhelmed")
        self.assertEqual(profile.user_need, "reduce chaos")
        self.assertTrue(any("2-3 buckets" in rule for rule in profile.do_rules))
        self.assertTrue(any("more options" in rule for rule in profile.avoid_rules))

    def test_detects_tired_state(self) -> None:
        profile = EmotionalIntelligenceService.analyze("Jestem zmeczony, powiedz tylko co mam zrobic.")

        self.assertEqual(profile.state, "tired")
        self.assertEqual(profile.response_posture, "short, grounding, minimal cognitive load")
        self.assertTrue(any("one next action" in rule for rule in profile.do_rules))

    def test_detects_excited_state_without_cheerleading(self) -> None:
        profile = EmotionalIntelligenceService.analyze("Super, jedziemy z tym projektem, ruszaj dalej.")

        self.assertEqual(profile.state, "excited")
        self.assertTrue(any("quality guardrail" in rule for rule in profile.do_rules))
        self.assertIn("cheerleading", " ".join(profile.avoid_rules))

    def test_detects_decision_need_before_uncertainty(self) -> None:
        profile = EmotionalIntelligenceService.analyze("Nie wiem, co najpierw. Zdecyduj za mnie.")

        self.assertEqual(profile.state, "needs_decision")
        self.assertEqual(profile.user_need, "decision support")

    def test_system_section_contains_core_rules(self) -> None:
        section = EmotionalIntelligenceService.build_system_section(
            "Mam chaos i stres, zrob mi jeden konkretny nastepny krok."
        )

        self.assertIn("ATNOS_EMOTIONAL_INTELLIGENCE", section)
        self.assertIn("Never diagnose the user", section)
        self.assertIn("Empathy means reducing friction", section)
        self.assertIn("emotional_state", section)


if __name__ == "__main__":
    unittest.main()
