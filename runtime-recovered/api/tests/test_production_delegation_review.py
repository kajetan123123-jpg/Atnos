from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.ai_workers.models import WorkerExecutionResult, WorkerStatusSnapshot  # noqa: E402
from atnos.ai_workers.ollama_worker import OllamaWorker  # noqa: E402
from atnos.intelligence.storage import connect, utc_now_iso  # noqa: E402
from test_support import create_test_app, make_temp_dir  # noqa: E402


class ProductionDelegationReviewTest(unittest.TestCase):
    def test_review_plan_endpoint_creates_evidence_backed_child_tasks(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with connect(app.state.settings) as db:
                now = utc_now_iso()
                db.execute(
                    """
                    INSERT INTO topic_queue (
                        id, account_id, title, description, status, total_score,
                        clickbait_risk, evergreen, shorts_potential, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'approved', 82, 0, 1, 1, ?, ?)
                    """,
                    (
                        "topic-quality-1",
                        "acct-quality-1",
                        "Why most budget advice fails",
                        "English finance Short about behavior and cashflow.",
                        now,
                        now,
                    ),
                )
                db.execute(
                    """
                    INSERT OR REPLACE INTO intelligence_accounts (
                        account_id, niches, region, timeframe, last_requested_at, enabled
                    )
                    VALUES (?, ?, 'US', 'now 7-d', ?, 1)
                    """,
                    ("acct-quality-1", '["personal finance", "cashflow"]', now),
                )
                db.commit()

            response = client.post(
                "/api/production/delegation-review/plan",
                json={
                    "account_id": "acct-quality-1",
                    "topic_id": "topic-quality-1",
                    "allow_browser_ai": False,
                    "max_steps": 4,
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["source"], "production_preflight")
            self.assertEqual(payload["evidence"]["topic"]["title"], "Why most budget advice fails")
            self.assertIn("signal_counts", payload["evidence"])
            self.assertEqual(len(payload["root_task"]["children"]), 4)
            self.assertTrue(all(child["worker_key"] == "ollama" for child in payload["root_task"]["children"]))

            status = client.get(
                "/api/production/delegation-review/status",
                params={"account_id": "acct-quality-1", "topic_id": "topic-quality-1"},
            )
            self.assertEqual(status.status_code, 200)
            status_payload = status.json()
            self.assertTrue(status_payload["has_review_plan"])
            self.assertFalse(status_payload["ready_for_production_start"])
            self.assertEqual(status_payload["counts"]["latest_child_tasks"], 4)
            self.assertEqual(status_payload["counts"]["latest_queued_child_tasks"], 4)
            self.assertIn("production_review_tasks_queued:4", status_payload["production_gate_blockers"])
            self.assertEqual(status_payload["review_summary"]["verdict"], "blocked")
            self.assertFalse(status_payload["review_summary"]["can_start_production"])
            self.assertEqual(status_payload["review_summary"]["first_action"], "Run all review tasks.")
            self.assertEqual(status_payload["review_summary"]["completed_tasks"], 0)
            self.assertEqual(status_payload["review_summary"]["total_tasks"], 4)
            self.assertEqual(status_payload["review_summary"]["evidence"]["source"], "ai_worker_tasks")

            ready = WorkerStatusSnapshot(
                worker_key="ollama",
                label="Ollama Local",
                status="ready",
                ready=True,
                reason="mocked ready",
                capabilities=("local_llm", "text"),
            )
            result = WorkerExecutionResult(
                worker_key="ollama",
                status="completed",
                summary="Review task completed with evidence-backed fixes.",
                result={"content": "Use concrete finance visuals and a less generic hook."},
            )
            with (
                patch.object(OllamaWorker, "status", AsyncMock(return_value=ready)),
                patch.object(OllamaWorker, "run", AsyncMock(return_value=result)),
            ):
                run_next = client.post(
                    "/api/production/delegation-review/run-next",
                    json={"account_id": "acct-quality-1", "topic_id": "topic-quality-1"},
                )
                for _ in range(3):
                    final_run = client.post(
                        "/api/production/delegation-review/run-next",
                        json={"account_id": "acct-quality-1", "topic_id": "topic-quality-1"},
                    )

            self.assertEqual(run_next.status_code, 200)
            run_payload = run_next.json()
            self.assertEqual(run_payload["status"], "completed")
            self.assertEqual(run_payload["ran_task"]["verification_status"], "completed")
            self.assertEqual(run_payload["latest"]["counts"]["latest_completed_child_tasks"], 1)
            self.assertEqual(run_payload["latest"]["counts"]["latest_queued_child_tasks"], 3)
            self.assertEqual(final_run.status_code, 200)
            final_payload = final_run.json()
            self.assertTrue(final_payload["latest"]["ready_for_production_start"])
            self.assertEqual(final_payload["latest"]["production_gate_blockers"], [])
            self.assertEqual(final_payload["latest"]["counts"]["latest_completed_review_tasks"], 1)
            self.assertEqual(final_payload["review_summary"]["verdict"], "production_ready")
            self.assertTrue(final_payload["review_summary"]["can_start_production"])
            self.assertEqual(final_payload["review_summary"]["completed_tasks"], 4)
            self.assertEqual(final_payload["review_summary"]["total_tasks"], 4)
            self.assertEqual(
                final_payload["review_summary"]["evidence"]["latest_result_summaries"][0],
                "Review task completed with evidence-backed fixes.",
            )

    def test_run_all_review_tasks_executes_sequentially_until_gate_passes(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with connect(app.state.settings) as db:
                now = utc_now_iso()
                db.execute(
                    """
                    INSERT INTO topic_queue (
                        id, account_id, title, description, status, total_score,
                        clickbait_risk, evergreen, shorts_potential, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'approved', 91, 0, 1, 1, ?, ?)
                    """,
                    (
                        "topic-quality-all",
                        "acct-quality-all",
                        "Why emergency funds fail",
                        "English finance Short about emergency funds.",
                        now,
                        now,
                    ),
                )
                db.commit()

            plan_response = client.post(
                "/api/production/delegation-review/plan",
                json={
                    "account_id": "acct-quality-all",
                    "topic_id": "topic-quality-all",
                    "allow_browser_ai": False,
                    "max_steps": 4,
                },
            )
            self.assertEqual(plan_response.status_code, 200)

            ready = WorkerStatusSnapshot(
                worker_key="ollama",
                label="Ollama Local",
                status="ready",
                ready=True,
                reason="mocked ready",
                capabilities=("local_llm", "text"),
            )
            result = WorkerExecutionResult(
                worker_key="ollama",
                status="completed",
                summary="Sequential review task completed.",
                result={"content": "Evidence-backed review output."},
            )
            with (
                patch.object(OllamaWorker, "status", AsyncMock(return_value=ready)),
                patch.object(OllamaWorker, "run", AsyncMock(return_value=result)),
            ):
                run_all = client.post(
                    "/api/production/delegation-review/run-all",
                    json={"account_id": "acct-quality-all", "topic_id": "topic-quality-all", "limit": 6},
                )

            self.assertEqual(run_all.status_code, 200)
            payload = run_all.json()
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["ran_count"], 4)
            self.assertTrue(payload["latest"]["ready_for_production_start"])
            self.assertEqual(payload["latest"]["counts"]["latest_completed_child_tasks"], 4)
            self.assertEqual(payload["latest"]["counts"]["latest_queued_child_tasks"], 0)
            self.assertEqual(payload["latest"]["production_gate_blockers"], [])
            self.assertEqual(payload["review_summary"]["verdict"], "production_ready")
            self.assertTrue(payload["review_summary"]["can_start_production"])
            self.assertEqual(payload["review_summary"]["first_action"], "Start production or review generated task outputs before rendering.")
            self.assertEqual(payload["review_summary"]["completed_review_tasks"], 1)


if __name__ == "__main__":
    unittest.main()
