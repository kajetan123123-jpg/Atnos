from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.self_coding_draft_service import SelfCodingDraftService, SelfCodingExecutionError
from test_support import create_test_app, make_temp_dir


class SelfImprovementGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_governance_defaults_require_codex_review(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/self-improvement/governance")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["final_reviewer"], "codex")
        self.assertFalse(payload["self_apply_allowed"])
        self.assertTrue(payload["handoff_dir"].endswith("data\\self-improvement\\codex-handoff"))
        self.assertIn("Final product/code changes go through Codex review and validation.", payload["rules"])

    def test_seed_creates_selected_codex_supervised_note(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/self-improvement/governance/seed")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "codex-supervised-self-development")
        self.assertEqual(payload["view"], "global-self-improvement")
        self.assertEqual(payload["decision"], "follow_up")
        self.assertEqual(payload["status"], "selected")
        self.assertIn("Codex reviews", payload["chosen_next_patch"])

    def test_codex_handoff_endpoint_writes_package(self) -> None:
        with TestClient(self.app) as client:
            seed = client.post("/self-improvement/governance/seed").json()
            response = client.post("/self-improvement/codex-handoff", json={"note_id": seed["id"]})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "codex_review_required")
        self.assertTrue(Path(payload["handoff_path"]).exists())
        self.assertTrue(Path(payload["report_path"]).exists())
        self.assertIn("Codex", Path(payload["handoff_path"]).read_text(encoding="utf-8"))

    def test_self_coding_execute_is_blocked_by_default(self) -> None:
        with TestClient(self.app) as client:
            client.get("/self-improvement/governance")
            with client.app.state.database.session_scope() as session:
                service = SelfCodingDraftService(session, settings=client.app.state.settings)
                with self.assertRaisesRegex(SelfCodingExecutionError, "Final changes require codex review"):
                    service.execute_code_apply(draft_id=123)


if __name__ == "__main__":
    unittest.main()
