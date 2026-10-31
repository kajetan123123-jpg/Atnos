from __future__ import annotations

import unittest

from atnos.db.models.enums import ProjectMemoryPriority
from atnos.services.memory_curation_service import MemoryCurationService


class MemoryCurationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MemoryCurationService()

    def test_rejects_short_acknowledgement_as_noise(self) -> None:
        candidate = self.service.classify_turn(user_text="ok")

        self.assertFalse(candidate.should_remember)
        self.assertEqual(candidate.domain, "noise")

    def test_detects_user_preference(self) -> None:
        candidate = self.service.classify_turn(
            user_text="Wole, zeby Atnos odpowiadal krotko i konkretnie."
        )

        self.assertTrue(candidate.should_remember)
        self.assertEqual(candidate.domain, "user_preference")
        self.assertEqual(candidate.priority, "high")

    def test_detects_project_goal(self) -> None:
        candidate = self.service.classify_turn(
            user_text="Budujemy Atnosa jako prostego super-agenta z Chatem i Ustawieniami."
        )

        self.assertTrue(candidate.should_remember)
        self.assertEqual(candidate.domain, "project_goal")

    def test_detects_decision_as_critical_memory(self) -> None:
        candidate = self.service.classify_turn(
            user_text="Decyzja: nie rozwijamy Areny jako glownego produktu."
        )

        self.assertTrue(candidate.should_remember)
        self.assertEqual(candidate.domain, "decision")
        self.assertEqual(candidate.priority, "critical")

    def test_rejects_secret_like_content(self) -> None:
        candidate = self.service.classify_turn(
            user_text="Zapisz haslo: supertajne123 do notatki."
        )

        self.assertFalse(candidate.should_remember)
        self.assertEqual(candidate.domain, "sensitive")
        self.assertEqual(candidate.priority, "critical")

    def test_memory_policy_section_sets_boundaries(self) -> None:
        section = self.service.build_memory_use_policy_section()

        self.assertIn("ATNOS_MEMORY_USE_POLICY", section)
        self.assertIn("Do not store or repeat secrets", section)
        self.assertIn("Prefer current user message over stale memory", section)

    def test_priority_mapping_matches_project_memory_enum(self) -> None:
        self.assertEqual(
            self.service.to_project_memory_priority("critical"),
            ProjectMemoryPriority.CRITICAL,
        )

    def test_project_writeback_requires_real_candidate_or_applied_context(self) -> None:
        noise = self.service.classify_turn(user_text="ok")
        preference = self.service.classify_turn(user_text="Domyslnie pisz do mnie bez lania wody.")

        self.assertFalse(
            self.service.should_write_project_memory(
                project_context_applied=False,
                project_summary="Projekt ma juz summary.",
                candidate=noise,
            )
        )
        self.assertTrue(
            self.service.should_write_project_memory(
                project_context_applied=False,
                project_summary="Projekt ma juz summary.",
                candidate=preference,
            )
        )
        self.assertTrue(
            self.service.should_write_project_memory(
                project_context_applied=True,
                project_summary="Projekt ma juz summary.",
                candidate=noise,
            )
        )


if __name__ == "__main__":
    unittest.main()
