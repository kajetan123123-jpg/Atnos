from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.skills import get_skill, search_skills, validate_registry
from atnos.skills.registry import DEFAULT_RULES_PATH, DEFAULT_SKILLS_PATH, build_default_skill_registry


class SkillsRegistryTest(unittest.TestCase):
    def test_default_registry_loads_yaml_and_contains_expected_skills(self) -> None:
        registry = build_default_skill_registry()
        skills = registry.list()
        self.assertGreaterEqual(len(skills), 30)
        pdf = registry.get_skill("anthropic-skills:pdf")
        self.assertIsNotNone(pdf)
        assert pdf is not None
        self.assertEqual(pdf.namespace, "anthropic-skills")
        self.assertTrue(DEFAULT_SKILLS_PATH.exists())
        self.assertTrue(DEFAULT_RULES_PATH.exists())

    def test_search_skills_matches_id_and_trigger(self) -> None:
        ids = [skill.id for skill in search_skills("pdf")]
        self.assertIn("anthropic-skills:pdf", ids)
        self.assertIn("atnos:file-analysis", ids)

        ids = [skill.id for skill in search_skills("wcag")]
        self.assertIn("design:accessibility-review", ids)

    def test_get_skill_and_validate_registry(self) -> None:
        skill = get_skill("atnos:ubuntu-full-control")
        self.assertIsNotNone(skill)
        assert skill is not None
        self.assertTrue(skill.requires_credentials)
        self.assertEqual(validate_registry(), [])


if __name__ == "__main__":
    unittest.main()
