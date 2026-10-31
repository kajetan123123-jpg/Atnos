from __future__ import annotations

import unittest

from atnos.services.chat_orchestration_service import ChatOrchestrationService


class StructuredQuestionParserTest(unittest.TestCase):
    def test_valid_atnos_question_block_is_extracted_into_metadata(self) -> None:
        content = (
            "Pomoge Ci wybrac kierunek.\n\n"
            "```atnos-question\n"
            '{\n'
            '  "question_id": "3b8143fa-a4d4-4f03-bd9a-7ec7464936df",\n'
            '  "prompt": "Ktory tor wybierasz?",\n'
            '  "options": [\n'
            '    {"id": "a", "label": "Backend"},\n'
            '    {"id": "b", "label": "Frontend"}\n'
            "  ],\n"
            '  "multi_select": false,\n'
            '  "required": true\n'
            "}\n"
            "```\n"
            "Czekam na wybor."
        )

        cleaned, metadata = ChatOrchestrationService._parse_structured_question(content)

        self.assertEqual(cleaned, "Pomoge Ci wybrac kierunek.\n\n\nCzekam na wybor.")
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata["kind"], "question_choice")
        self.assertEqual(metadata["question_id"], "3b8143fa-a4d4-4f03-bd9a-7ec7464936df")
        self.assertEqual(len(metadata["options"]), 2)
        self.assertEqual(metadata["options"][0]["id"], "a")

    def test_malformed_json_falls_back_to_plain_content(self) -> None:
        content = "```atnos-question\n{not-json}\n```"

        cleaned, metadata = ChatOrchestrationService._parse_structured_question(content)

        self.assertEqual(cleaned, content)
        self.assertIsNone(metadata)

    def test_invalid_option_count_falls_back_to_plain_content(self) -> None:
        content = (
            "```atnos-question\n"
            '{\n'
            '  "question_id": "only-one",\n'
            '  "prompt": "Za malo opcji?",\n'
            '  "options": [{"id": "a", "label": "Tylko jedna"}]\n'
            "}\n"
            "```"
        )

        cleaned, metadata = ChatOrchestrationService._parse_structured_question(content)

        self.assertEqual(cleaned, content)
        self.assertIsNone(metadata)

    def test_plain_message_without_block_stays_untouched(self) -> None:
        content = "To jest zwykla odpowiedz bez karty wyboru."

        cleaned, metadata = ChatOrchestrationService._parse_structured_question(content)

        self.assertEqual(cleaned, content)
        self.assertIsNone(metadata)
