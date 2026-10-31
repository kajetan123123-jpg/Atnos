from __future__ import annotations

import unittest

from atnos.services.chat_prompt_improvement_service import ChatPromptImprovementService


class ChatPromptImprovementServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ChatPromptImprovementService()

    def test_improves_browser_prompt_with_explicit_output_and_failure_clause(self) -> None:
        result = self.service.improve("powiedz jakie karty są otwarte w przeglądarce chrome?")

        self.assertTrue(result.changed)
        self.assertIn("Google Chrome", result.improved_content)
        self.assertIn("URL", result.improved_content)
        self.assertIn("co blokuje odczyt", result.improved_content)

    def test_improves_coding_prompt_with_root_cause_and_validation(self) -> None:
        result = self.service.improve("napisz fix do buga w backend api")

        self.assertTrue(result.changed)
        self.assertIn("root cause", result.improved_content)
        self.assertIn("minimalna zmiane", result.improved_content)
        self.assertIn("zweryfikowales", result.improved_content)

    def test_rejects_empty_prompt(self) -> None:
        with self.assertRaises(ValueError):
            self.service.improve("   ")
