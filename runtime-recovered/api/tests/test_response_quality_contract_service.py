from __future__ import annotations

import unittest

from atnos.services.response_quality_contract_service import ResponseQualityContractService


class ResponseQualityContractServiceTest(unittest.TestCase):
    def test_frustrated_user_gets_empathethic_fix_mode(self) -> None:
        profile = ResponseQualityContractService.classify(
            "Do cholery, czemu chat dalej nie dziala po zmianie zakladki?"
        )

        self.assertEqual(profile.emotion, "frustrated")
        self.assertEqual(profile.intent_kind, "emotional_friction")
        self.assertEqual(profile.answer_mode, "empathetic_fix")
        self.assertIn("no defensiveness", profile.tone)

    def test_short_question_gets_concise_mode(self) -> None:
        profile = ResponseQualityContractService.classify("Jak odpalic Claude?")

        self.assertEqual(profile.intent_kind, "simple_answer")
        self.assertEqual(profile.answer_mode, "concise")
        self.assertEqual(profile.verbosity, "short")

    def test_product_strategy_gets_diagnosis_decision_plan_mode(self) -> None:
        profile = ResponseQualityContractService.classify(
            "Zaprojektuj lepsza architekture agenta i priorytety rozwoju produktu."
        )

        self.assertEqual(profile.intent_kind, "product_strategy")
        self.assertEqual(profile.answer_mode, "diagnose_decide_plan")

    def test_overwhelmed_user_gets_stabilize_and_simplify_mode(self) -> None:
        profile = ResponseQualityContractService.classify(
            "Mam chaos i za duzo rzeczy naraz, co mam zrobic najpierw?"
        )

        self.assertEqual(profile.emotion, "overwhelmed")
        self.assertEqual(profile.answer_mode, "stabilize_and_simplify")
        self.assertIn("low cognitive load", profile.tone)

    def test_decision_need_gets_decision_support_intent(self) -> None:
        profile = ResponseQualityContractService.classify("Nie wiem co najpierw. Zdecyduj za mnie.")

        self.assertEqual(profile.emotion, "needs_decision")
        self.assertEqual(profile.intent_kind, "decision_support")
        self.assertEqual(profile.answer_mode, "diagnose_decide_plan")

    def test_creative_work_gets_creative_direction_mode(self) -> None:
        profile = ResponseQualityContractService.classify(
            "Zrob plan shorta na YouTube o lokalnym AI."
        )

        self.assertEqual(profile.intent_kind, "creative_work")
        self.assertEqual(profile.answer_mode, "creative_direction")

    def test_system_section_contains_truth_and_structure_rules(self) -> None:
        section = ResponseQualityContractService.build_system_section(
            user_message="Napraw backend i powiedz co sprawdziles.",
            work_mode="execute",
            response_style="professional-clear",
            quality_profile="premium",
        )

        self.assertIn("ATNOS_RESPONSE_QUALITY_STANDARD", section)
        self.assertIn("Do not invent runtime facts", section)
        self.assertIn("ATNOS_EMOTIONAL_INTELLIGENCE", section)
        self.assertIn("Empathy means reducing friction", section)
        self.assertIn("technical_execution/debugging", section)
        self.assertIn("work_mode: execute", section)
        self.assertIn("response_style: professional-clear", section)
        self.assertIn("quality_profile: premium", section)


if __name__ == "__main__":
    unittest.main()
