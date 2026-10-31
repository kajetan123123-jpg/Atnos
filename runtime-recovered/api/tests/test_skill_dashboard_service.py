from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.skill_dashboard_service import SkillDashboardService


class SkillDashboardServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)

    def test_skill_dashboard_lists_repo_skills_and_categories(self) -> None:
        with TestClient(self.app):
            with self.app.state.database.session_scope() as session:
                payload = SkillDashboardService(session, self.app.state.settings).build_payload()

        self.assertGreaterEqual(payload["summary"]["total_skills"], 30)
        category_keys = {item["key"] for item in payload["categories"]}
        self.assertTrue({"creation", "system", "research", "dev"} <= category_keys)

        items_by_key = {item["key"]: item for item in payload["items"]}
        self.assertEqual(
            items_by_key["anthropic-skills:pdf"]["category"],
            "creation",
        )
        self.assertEqual(items_by_key["atnos:ubuntu-full-control"]["readiness"], "partial")
        self.assertEqual(items_by_key["brand-voice:enforce-voice"]["repo_name"], "brand-voice")
        self.assertIn("credentiali", items_by_key["atnos:youtube-automation"]["status_reason"])


if __name__ == "__main__":
    unittest.main()
