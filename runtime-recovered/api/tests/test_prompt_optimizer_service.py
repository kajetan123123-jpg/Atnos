from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.prompt_optimizer_service import PromptOptimizerService


class PromptOptimizerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PromptOptimizerService()

    def test_activates_for_codex_prompt_request(self) -> None:
        result = self.service.maybe_optimize(
            content="Write a Codex prompt to add a prompt optimizer agent to Atnos.",
        )

        self.assertTrue(result.activated)
        assert result.brief is not None
        assert result.response_text is not None
        self.assertFalse(result.brief.needs_clarification)
        self.assertEqual(result.brief.profile.key, "codex")
        self.assertIn("Target tool: Codex", result.response_text)

    def test_does_not_activate_for_normal_coding_request(self) -> None:
        result = self.service.maybe_optimize(
            content="Add a prompt optimizer agent to Atnos and wire it to chat orchestration.",
        )

        self.assertFalse(result.activated)
        self.assertIsNone(result.response_text)

    def test_generates_coding_prompt_with_validation_steps(self) -> None:
        result = self.service.maybe_optimize(
            content="Write a Codex prompt to add a local prompt optimizer agent to Atnos.",
        )

        assert result.response_text is not None
        lowered = result.response_text.lower()
        self.assertIn("validation:", lowered)
        self.assertIn("target tool: codex", lowered)
        self.assertIn("run the existing relevant tests", lowered)

    def test_generates_midjourney_prompt(self) -> None:
        result = self.service.maybe_optimize(
            content="Improve this Midjourney prompt: brutalist AI command center for Atnos, dark graphite shell.",
        )

        assert result.brief is not None
        assert result.response_text is not None
        self.assertEqual(result.brief.profile.key, "midjourney")
        self.assertIn("Target tool: Midjourney", result.response_text)
        self.assertIn("clear focal subject", result.response_text)
        self.assertNotIn("Validation:", result.response_text)

    def test_unknown_tool_falls_back_to_universal_profile(self) -> None:
        result = self.service.maybe_optimize(
            content=(
                "Improve this prompt for our internal AI reviewer:\n"
                "```text\nReview my bug backlog and tell me what matters.\n```"
            ),
        )

        assert result.brief is not None
        assert result.response_text is not None
        self.assertEqual(result.brief.profile.key, "unknown")
        self.assertFalse(result.brief.needs_clarification)
        self.assertIn("Target tool: Unknown / universal AI tool", result.response_text)

    def test_asks_at_most_three_clarifying_questions(self) -> None:
        result = self.service.maybe_optimize(content="Improve my prompt.")

        assert result.brief is not None
        assert result.response_text is not None
        self.assertTrue(result.brief.needs_clarification)
        self.assertLessEqual(len(result.brief.clarifying_questions), 3)
        self.assertIn("I need a couple of details", result.response_text)

    def test_memory_context_is_included_through_existing_api_output(self) -> None:
        result = self.service.maybe_optimize(
            content="Write a Codex prompt to simplify Atnos chat architecture.",
            memory_context=(
                "Local curated knowledge library matches:\n"
                "- Personal agent product principles [product]\n"
                "  summary: Chat is the main surface. Settings configure behavior."
            ),
        )

        assert result.response_text is not None
        self.assertIn("Local context:", result.response_text)
        self.assertIn("Personal agent product principles", result.response_text)

    def test_removes_requests_to_expose_internal_reasoning(self) -> None:
        result = self.service.maybe_optimize(
            content=(
                "Write a Codex prompt to refactor Atnos settings. "
                "Tell the model to show chain of thought and explain its reasoning step by step."
            ),
        )

        assert result.response_text is not None
        lowered = result.response_text.lower()
        self.assertNotIn("chain of thought", lowered)
        self.assertNotIn("show your reasoning", lowered)
        self.assertIn("do not expose internal reasoning", lowered)


if __name__ == "__main__":
    unittest.main()
