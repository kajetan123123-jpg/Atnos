from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.quick_chat_operator_service import QuickChatOperatorService


class QuickChatOperatorServiceTest(unittest.TestCase):
    def test_handles_simple_greeting_locally(self) -> None:
        result = QuickChatOperatorService().maybe_handle(content="hej")
        self.assertTrue(result.activated)
        self.assertEqual(result.action, "greeting")
        self.assertIn("Jestem", result.assistant_content or "")

    def test_handles_capability_question_locally(self) -> None:
        result = QuickChatOperatorService().maybe_handle(content="co umiesz?")
        self.assertTrue(result.activated)
        self.assertEqual(result.action, "capabilities")
        self.assertIn("Obsidian", result.assistant_content or "")
        self.assertIn("Ubuntu SSH sandbox", result.assistant_content or "")
        self.assertIn("Lokalny pipeline shortow", result.assistant_content or "")
        self.assertIn("GitHub, Linear, Gmail, Google Calendar i YouTube", result.assistant_content or "")
        self.assertIn("approval", result.assistant_content or "")
        self.assertIn("wymaga konfiguracji", result.assistant_content or "")
        self.assertIn("artefakt, log, sciezke pliku", result.assistant_content or "")
        self.assertIn("capability_sections", result.metadata.get("quick_chat", {}))

    def test_does_not_steal_actionable_request(self) -> None:
        result = QuickChatOperatorService().maybe_handle(content="otworz obsydiana")
        self.assertFalse(result.activated)

    def test_solves_explicit_arithmetic_without_an_llm(self) -> None:
        for prompt, expected in (("7+8", "7+8 = 15"), ("ile jest 7 * 8?", "7 * 8 = 56"), ("oblicz 1,5 + 2", "1.5 + 2 = 3.5")):
            with self.subTest(prompt=prompt):
                result = QuickChatOperatorService().maybe_handle(content=prompt)
                self.assertTrue(result.activated)
                self.assertEqual(result.action, "calculation")
                self.assertEqual(result.assistant_content, expected)
                self.assertTrue(result.metadata["quick_chat"]["deterministic"])

    def test_rejects_unsupported_or_unsafe_math_as_quick_chat(self) -> None:
        for prompt in ("open(1)", "2 ** 100", "1 / 0", "jaki jest wzrost 7 + 8 procent"):
            with self.subTest(prompt=prompt):
                result = QuickChatOperatorService().maybe_handle(content=prompt)
                self.assertFalse(result.activated)
