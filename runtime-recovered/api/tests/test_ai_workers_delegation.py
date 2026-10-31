from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select

TESTS_DIR = Path(__file__).resolve().parent
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.ai_workers.delegation_router import (  # noqa: E402
    AiDelegationRouter,
    DelegatePlanInput,
    DelegateTaskInput,
)
from atnos.ai_workers.browser_ai_worker import BrowserAiWorker  # noqa: E402
from atnos.ai_workers.codex_worker import CodexWorker  # noqa: E402
from atnos.ai_workers.models import WorkerExecutionResult, WorkerStatusSnapshot  # noqa: E402
from atnos.ai_workers.ollama_worker import OllamaWorker  # noqa: E402
from atnos.ai_workers.redaction import redact_payload, redact_text  # noqa: E402
from atnos.core.config import Settings  # noqa: E402
from atnos.db.base import Base  # noqa: E402
from atnos.db.session import DatabaseManager  # noqa: E402
from atnos.db.models.ai_worker_task import AiWorkerRun, AiWorkerTask  # noqa: F401, E402
from atnos.db.models.execution_trace import ExecutionTrace  # noqa: E402
from atnos.db.models.project import Project, ProjectMemory  # noqa: E402
from test_support import create_test_app, make_temp_dir  # noqa: E402


class AiWorkersDelegationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="atnos-ai-workers-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            app_env="test",
            app_root=self.root,
            data_dir=self.root / "data",
            sqlite_file=self.root / "data" / "atnos-ai-workers.db",
        )
        self.settings.ensure_directories()
        self.database = DatabaseManager(self.settings)
        self.addCleanup(self.database.dispose)
        Base.metadata.create_all(self.database.engine)

    def test_redaction_removes_secrets_from_text_and_payload(self) -> None:
        test_api_key = "sk-proj-" + ("a" * 32)
        test_card = " ".join(["4242"] * 4)
        redacted = redact_text(f"OpenAI key {test_api_key} and card {test_card}.")

        self.assertNotIn(test_api_key, redacted)
        self.assertNotIn(test_card, redacted)
        self.assertIn("[REDACTED]", redacted)

        labeled_secret = "TEST_SECRET_DO_NOT_SEND"
        labeled_redacted = redact_text(f"Password: {labeled_secret}; token is {labeled_secret}")
        self.assertNotIn(labeled_secret, labeled_redacted)
        self.assertEqual(labeled_redacted, "Password: [REDACTED]; token is [REDACTED]")

        payload = redact_payload(
            {
                "password": "plain-secret",
                "has\u0142o": "polish-secret",
                "notes": ["token abcdefghijklmnopqrstuvwxyz1234567890"],
                "safe": "market research",
            }
        )

        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertEqual(payload["has\u0142o"], "[REDACTED]")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234567890", str(payload))
        self.assertEqual(payload["safe"], "market research")

    def test_redaction_preserves_long_structural_ids_without_leaking_explicit_secrets(self) -> None:
        topic_id = "topic-real-dark-smoke-v3-20260606041751"
        account_id = "youtube:account-d6add2f8@example.invalid"
        explicit_secret = "sk-proj-" + ("a" * 32)
        test_card = " ".join(["4242"] * 4)

        payload = redact_payload(
            {
                "topic_id": topic_id,
                "account_id": account_id,
                "job_id": explicit_secret,
                "task_id": test_card,
                "opaque_note": topic_id,
            }
        )

        self.assertEqual(payload["topic_id"], topic_id)
        self.assertEqual(payload["account_id"], account_id)
        self.assertEqual(payload["job_id"], "[REDACTED]")
        self.assertEqual(payload["task_id"], "[REDACTED]")
        self.assertEqual(payload["opaque_note"], "[REDACTED]")

    def test_ollama_worker_reports_missing_model_when_runtime_has_empty_catalog(self) -> None:
        response = MagicMock(status_code=200)
        response.json.return_value = {"models": []}
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=response)

        with patch("atnos.ai_workers.ollama_worker.httpx.AsyncClient", return_value=client):
            status = asyncio.run(OllamaWorker(self.settings).status())

        self.assertFalse(status.ready)
        self.assertEqual(status.status, "missing_model")
        self.assertIn("no local model", status.reason)

    def test_ollama_worker_is_not_ready_when_only_a_different_model_is_installed(self) -> None:
        response = MagicMock(status_code=200)
        response.json.return_value = {"models": [{"name": "bge-m3:latest"}]}
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=response)

        with patch("atnos.ai_workers.ollama_worker.httpx.AsyncClient", return_value=client):
            status = asyncio.run(OllamaWorker(self.settings).status())

        self.assertFalse(status.ready)
        self.assertEqual(status.status, "missing_model")
        self.assertIn("qwen3:8b", status.reason)
        self.assertEqual(status.metadata["models"], ["bge-m3:latest"])

    def test_high_risk_task_is_blocked_and_stored_without_worker_execution(self) -> None:
        test_card = " ".join(["4242"] * 4)
        test_api_key = "sk-proj-" + ("a" * 32)
        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            result = asyncio.run(
                router.delegate(
                    DelegateTaskInput(
                        task_type="public_upload_and_trading",
                        prompt=(
                            f"Publish this public upload and buy BTC with card {test_card}. "
                            f"Use key {test_api_key}."
                        ),
                        context={"password": "super-secret", "note": "safe context"},
                        preferred_worker=None,
                        allowed_workers=("ollama",),
                        privacy_mode="strict_local",
                        priority="normal",
                        requested_by="test",
                        requires_approval_for_result=False,
                    )
                )
            )

            self.assertEqual(result["status"], "blocked")
            self.assertIn("high-risk", result["blocked_reason"])
            self.assertEqual(result["runs"], [])
            self.assertNotIn(test_card, result["redacted_prompt"])
            self.assertNotIn(test_api_key, result["redacted_prompt"])
            self.assertEqual(result["payload_json"]["context"]["password"], "[REDACTED]")

            tasks = router.list_tasks(status="blocked", limit=10)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], result["id"])

    def test_closed_local_review_treats_source_material_as_data_but_keeps_action_guard(self) -> None:
        from dataclasses import replace
        from unittest.mock import AsyncMock, patch
        payload = DelegateTaskInput(task_type='review', prompt='Return three concrete editorial risks in the supplied plan.',
            context={'direction': 'Draw a trial card; do not publish or make payments.'}, preferred_worker='ollama',
            allowed_workers=('ollama',), privacy_mode='strict_local', priority='normal', requested_by='test',
            requires_approval_for_result=True)
        self.assertIsNone(AiDelegationRouter._high_risk_reason(payload))
        for other in [replace(payload, prompt='Publish this and buy BTC with a card.'),
                      replace(payload, task_type='planning'), replace(payload, requires_approval_for_result=False),
                      replace(payload, allowed_workers=('ollama', 'codex')), replace(payload, privacy_mode='allow_cloud_api')]:
            with self.subTest(other=other):
                self.assertIsNotNone(AiDelegationRouter._high_risk_reason(other))
        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            reason = 'AI delegation blocked a high-risk action. Original false-positive receipt.'
            with patch.object(router, '_high_risk_reason', return_value=reason):
                blocked = asyncio.run(router.delegate(payload))
            self.assertEqual(blocked['status'], 'blocked')
            self.assertEqual(blocked['runs'], [])
            ready = WorkerStatusSnapshot(worker_key='ollama', label='local', status='ready', ready=True,
                reason='fixture only', capabilities=('text',))
            output = WorkerExecutionResult(worker_key='ollama', status='completed', summary='Three risks for review.', result={'content': 'Fixture findings; no action executed.'})
            with patch.object(router, '_worker_status', new=AsyncMock(return_value=ready)), \
                 patch.object(router, '_run_worker_persistently', new=AsyncMock(return_value=output)):
                result = asyncio.run(router.run_task(blocked['id']))
            self.assertEqual(result['id'], blocked['id'])
            self.assertEqual(len(result['runs']), 1)
            self.assertEqual(result['verification_status'], 'awaiting_human_review')
            self.assertIsNone(result['blocked_reason'])
            self.assertIsNone(result['error_message'])
            # Old completed rows may still carry the pre-success diagnostic.
            # Present it as history only, without rewriting the stored record.
            row = session.get(AiWorkerTask, blocked['id'])
            row.blocked_reason = 'Historical false-positive block'
            row.error_message = 'Historical error'
            legacy = router._serialize_task(row)
            self.assertIsNone(legacy['blocked_reason'])
            self.assertIsNone(legacy['error_message'])
            self.assertEqual(legacy['historical_diagnostics']['blocked_reason'], row.blocked_reason)
            self.assertEqual(row.error_message, 'Historical error')
            row.status = 'blocked'
            self.assertEqual(router._serialize_task(row)['blocked_reason'], 'Historical false-positive block')
            row.status = 'completed'
            row.runs[-1].status = 'failed'
            self.assertEqual(router._serialize_task(row)['error_message'], 'Historical error')

    def test_project_delegation_persists_trace_and_memory_writeback(self) -> None:
        ready = WorkerStatusSnapshot(
            worker_key="ollama",
            label="Ollama Local",
            status="ready",
            ready=True,
            reason="mocked local runtime",
            capabilities=("local_llm", "text"),
        )
        completed = WorkerExecutionResult(
            worker_key="ollama",
            status="completed",
            summary="The hook is clear and creates immediate financial tension.",
            result={
                "content": "The hook is clear and creates immediate financial tension.",
                "output_validation": {
                    "status": "passed",
                    "semantic_correctness_verified": False,
                },
            },
        )
        with self.database.session_scope() as session:
            project = Project(name="Wealth Blueprint", slug="wealth-blueprint")
            session.add(project)
            session.flush()
            project_id = int(project.id)
            router = AiDelegationRouter(session, self.settings)
            with (
                patch.object(router, "_worker_status", AsyncMock(return_value=ready)),
                patch.object(router.ollama, "run", AsyncMock(return_value=completed)),
            ):
                result = asyncio.run(
                    router.delegate(
                        DelegateTaskInput(
                            task_type="review",
                            prompt="Review this English finance hook.",
                            context={"language": "English"},
                            preferred_worker="ollama",
                            allowed_workers=("ollama",),
                            privacy_mode="strict_local",
                            priority="normal",
                            requested_by="test",
                            requires_approval_for_result=False,
                            project_id=project_id,
                        )
                    )
                )

            self.assertEqual(result["status"], "completed")
            self.assertIsNotNone(result["trace_id"])
            trace = session.get(ExecutionTrace, result["trace_id"])
            self.assertIsNotNone(trace)
            self.assertEqual(trace.project_id, project_id)
            self.assertEqual(trace.status.value, "completed")
            memory = session.scalar(
                select(ProjectMemory).where(ProjectMemory.project_id == project_id)
            )
            self.assertIsNotNone(memory)
            self.assertIn("completed by ollama", memory.content)

    def test_project_delegation_rejects_unknown_project_without_creating_task(self) -> None:
        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            with self.assertRaisesRegex(ValueError, "Project 999 does not exist"):
                asyncio.run(
                    router.delegate(
                        DelegateTaskInput(
                            task_type="review",
                            prompt="Review this safe draft.",
                            context={},
                            preferred_worker="ollama",
                            allowed_workers=("ollama",),
                            privacy_mode="strict_local",
                            priority="normal",
                            requested_by="test",
                            requires_approval_for_result=False,
                            project_id=999,
                        )
                    )
                )
            self.assertEqual(session.query(AiWorkerTask).count(), 0)

    def test_browser_handoff_remains_pending_external_until_real_result_arrives(self) -> None:
        ready = WorkerStatusSnapshot(
            worker_key="browser_claude",
            label="Claude Browser",
            status="ready",
            ready=True,
            reason="mocked browser ready",
            capabilities=("browser_ai", "review"),
        )
        handoff = WorkerExecutionResult(
            worker_key="browser_claude",
            status="pending_external",
            summary="Prepared supervised browser handoff for Claude Browser.",
            result={"handoff_mode": "supervised_browser_prompt", "target_url": "https://claude.ai/"},
        )
        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            with (
                patch.object(router, "_worker_status", AsyncMock(return_value=ready)),
                patch("atnos.ai_workers.browser_ai_worker.BrowserAiWorker.run", AsyncMock(return_value=handoff)),
            ):
                result = asyncio.run(
                    router.delegate(
                        DelegateTaskInput(
                            task_type="review",
                            prompt="Review this safe English script draft.",
                            context={},
                            preferred_worker="browser_claude",
                            allowed_workers=("browser_claude",),
                            privacy_mode="allow_browser_ai",
                            priority="normal",
                            requested_by="test",
                            requires_approval_for_result=False,
                        )
                    )
                )

            self.assertEqual(result["status"], "pending_external")
            self.assertEqual(result["verification_status"], "pending_external")
            self.assertIsNone(result["completed_at"])
            self.assertEqual(result["runs"][0]["status"], "pending_external")

    def test_browser_worker_reports_handoff_without_claiming_execution_readiness(self) -> None:
        runtime_controls = MagicMock(
            emergency_stop_enabled=False,
            pause_all_jobs=False,
            browser_control_enabled=True,
            external_actions_enabled=True,
        )
        worker = BrowserAiWorker(
            self.settings,
            worker_key="browser_claude",
            runtime_controls=runtime_controls,
        )
        with patch.object(worker.browser_runtime, "describe") as describe:
            describe.return_value = MagicMock(ready=True, status_reason="ready")
            status = worker.status()

        self.assertEqual(status.status, "handoff_only")
        self.assertFalse(status.ready)
        self.assertTrue(status.metadata["can_prepare_handoff"])
        self.assertFalse(status.metadata["can_execute"])

    def test_codex_delegation_records_received_result_without_false_verification(self) -> None:
        """Codex może zakończyć run, ale jego opis nie jest samodzielną walidacją."""
        ready = WorkerStatusSnapshot(
            worker_key="codex",
            label="Codex Read-only",
            status="ready",
            ready=True,
            reason="mocked Codex CLI ready",
            capabilities=("code_review",),
        )
        received = WorkerExecutionResult(
            worker_key="codex",
            status="completed",
            summary="Found one scoped issue and supplied evidence.",
            result={
                "run_id": "safe-run-1",
                "execution_mode": "sanitized_bundle_read_only",
                "copied_files": ["runtime-recovered/api/src/atnos/sample.py"],
                "last_message": "Evidence from the sanitized source bundle.",
            },
        )

        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            with (
                patch.object(CodexWorker, "status", AsyncMock(return_value=ready)),
                patch.object(CodexWorker, "run", AsyncMock(return_value=received)),
            ):
                result = asyncio.run(
                    router.delegate(
                        DelegateTaskInput(
                            task_type="code_review",
                            prompt="Review the scoped source without changing it.",
                            context={"source_files": ["runtime-recovered/api/src/atnos/sample.py"]},
                            preferred_worker="codex",
                            allowed_workers=("codex",),
                            privacy_mode="allow_cloud_api",
                            priority="normal",
                            requested_by="test",
                            requires_approval_for_result=True,
                        )
                    )
                )

            self.assertEqual(result["status"], "completed", msg=result)
            self.assertEqual(result["runs"][0]["status"], "completed")
            self.assertEqual(result["worker_key"], "codex")
            self.assertEqual(result["verification_status"], "awaiting_human_review")
            self.assertEqual(result["result_json"]["execution_mode"], "sanitized_bundle_read_only")

            rejected = router.review_codex_task(
                result["id"],
                decision="reject",
                reviewer="operator",
                note="Read-only finding is not actionable.",
            )
            self.assertIsNotNone(rejected)
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(rejected["verification_status"], "rejected")
            self.assertFalse(rejected["result_json"]["review"]["apply_authorized"])

    def test_codex_result_is_persisted_when_http_request_is_cancelled(self) -> None:
        """Rozłączenie klienta nie może pozostawić zadania na zawsze jako running."""
        ready = WorkerStatusSnapshot(
            worker_key="codex",
            label="Codex Controlled",
            status="ready",
            ready=True,
            reason="mocked Codex CLI ready",
        )
        completed = WorkerExecutionResult(
            worker_key="codex",
            status="completed",
            summary="Worker finished after the client disconnected.",
            result={
                "execution_mode": "sanitized_bundle_read_only",
                "last_message": "Independent result received.",
                "prompt_equals_result": False,
            },
        )

        async def scenario(router: AiDelegationRouter) -> dict[str, Any]:
            started = asyncio.Event()
            release = asyncio.Event()

            async def delayed_run(**_: Any) -> WorkerExecutionResult:
                started.set()
                await release.wait()
                return completed

            with (
                patch.object(router.codex, "status", AsyncMock(return_value=ready)),
                patch.object(router.codex, "run", AsyncMock(side_effect=delayed_run)),
            ):
                delegated = asyncio.create_task(
                    router.delegate(
                        DelegateTaskInput(
                            task_type="code_review",
                            prompt="Review one sanitized source file.",
                            context={"source_files": ["runtime-recovered/api/src/atnos/sample.py"]},
                            preferred_worker="codex",
                            allowed_workers=("codex",),
                            privacy_mode="allow_cloud_api",
                            priority="normal",
                            requested_by="disconnect-test",
                            requires_approval_for_result=True,
                        )
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=2.0)
                delegated.cancel()
                release.set()
                return await asyncio.wait_for(delegated, timeout=5.0)

        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            result = asyncio.run(scenario(router))

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["runs"][0]["status"], "completed")
            self.assertEqual(result["verification_status"], "awaiting_human_review")

    def test_verified_codex_proposal_requires_review_and_can_be_safely_rolled_back(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)
        ready = WorkerStatusSnapshot(
            worker_key="codex",
            label="Codex Controlled",
            status="ready",
            ready=True,
            reason="mocked Codex CLI ready",
            capabilities=("isolated_patch_proposal",),
        )
        phases = [
            "prompt_prepared",
            "started",
            "running",
            "completed",
            "result_received",
            "verifying",
            "verified",
        ]
        verified = WorkerExecutionResult(
            worker_key="codex",
            status="completed",
            summary="Scoped patch proposal passed independent verification.",
            result={
                "run_id": "patch-run-1",
                "execution_mode": "isolated_worktree_patch_proposal",
                "last_message": "Fixed the scoped parser defect and reported the independent test.",
                "changed_paths": ["runtime-recovered/api/src/atnos/sample.py"],
                "diff_path": str(Path(temp_dir.name) / "proposal.diff"),
                "diff_sha256": "diff-hash",
                "prompt_sha256": "prompt-hash",
                "result_sha256": "result-hash",
                "prompt_equals_result": False,
                "lifecycle": [
                    {"phase": phase, "at": f"2026-07-17T00:00:0{index}+00:00", "evidence": {}}
                    for index, phase in enumerate(phases)
                ],
                "verification": {
                    "status": "passed",
                    "reason": "Allowlist, diff capture, and independent pytest passed.",
                    "command": [sys.executable, "-m", "pytest", "tests/test_sample.py"],
                },
                "proposal_state": "awaiting_review",
                "rollback": {
                    "main_worktree_modified": False,
                    "isolated_worktree_removed": True,
                    "apply_to_main_requires_human_approval": True,
                },
            },
        )

        with (
            patch.object(CodexWorker, "status", AsyncMock(return_value=ready)),
            patch.object(CodexWorker, "run", AsyncMock(return_value=verified)),
            TestClient(app) as client,
        ):
            delegated = client.post(
                "/api/ai-workers/delegate",
                json={
                    "task_type": "safe_code_fix",
                    "prompt": "Fix the scoped parser defect.",
                    "context": {
                        "allowed_paths": ["runtime-recovered/api/src/atnos/sample.py"],
                        "verification_command": [sys.executable, "-m", "pytest", "tests/test_sample.py"],
                    },
                    "preferred_worker": "codex",
                    "allowed_workers": ["codex"],
                    "privacy_mode": "allow_cloud_api",
                    "requires_approval_for_result": True,
                },
            )
            self.assertEqual(delegated.status_code, 200)
            payload = delegated.json()
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["verification_status"], "verified_proposal_not_applied")
            self.assertEqual(payload["result_json"]["proposal_state"], "awaiting_review")
            self.assertFalse(payload["result_json"]["rollback"]["main_worktree_modified"])
            self.assertNotEqual(payload["result_json"]["prompt_sha256"], payload["result_json"]["result_sha256"])
            self.assertFalse(payload["result_json"]["prompt_equals_result"])
            self.assertEqual(
                [item["phase"] for item in payload["runs"][0]["metadata_json"]["lifecycle"]],
                phases,
            )

            approved = client.post(
                f"/api/ai-workers/tasks/{payload['id']}/review",
                json={"decision": "approve", "reviewer": "operator", "note": "Evidence reviewed."},
            )
            self.assertEqual(approved.status_code, 200)
            approved_payload = approved.json()
            self.assertEqual(approved_payload["status"], "approved")
            self.assertEqual(approved_payload["verification_status"], "approved_not_applied")
            self.assertFalse(approved_payload["result_json"]["review"]["apply_authorized"])

            rolled_back = client.post(
                f"/api/ai-workers/tasks/{payload['id']}/rollback-proposal",
                json={"reviewer": "operator", "reason": "Superseded by a smaller proposal."},
            )
            self.assertEqual(rolled_back.status_code, 200)
            rollback_payload = rolled_back.json()
            self.assertEqual(rollback_payload["status"], "rolled_back")
            self.assertEqual(rollback_payload["verification_status"], "rolled_back")
            self.assertFalse(rollback_payload["result_json"]["rollback"]["main_worktree_modified"])
            self.assertTrue(rollback_payload["result_json"]["rollback"]["proposal_artifact_retained_for_audit"])

    def test_failed_codex_verification_cannot_be_approved(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)
        ready = WorkerStatusSnapshot(
            worker_key="codex",
            label="Codex Controlled",
            status="ready",
            ready=True,
            reason="mocked Codex CLI ready",
        )
        failed = WorkerExecutionResult(
            worker_key="codex",
            status="failed",
            summary="Independent verification command failed.",
            result={
                "execution_mode": "isolated_worktree_patch_proposal",
                "last_message": "A patch was proposed, but the test failed.",
                "prompt_sha256": "prompt-hash",
                "result_sha256": "result-hash",
                "prompt_equals_result": False,
                "diff_path": str(Path(temp_dir.name) / "proposal.diff"),
                "diff_sha256": "diff-hash",
                "proposal_state": "awaiting_review",
                "lifecycle": [
                    {"phase": phase, "at": "2026-07-17T00:00:00+00:00", "evidence": {}}
                    for phase in (
                        "prompt_prepared",
                        "started",
                        "running",
                        "completed",
                        "result_received",
                        "verifying",
                        "verification_failed",
                    )
                ],
                "verification": {"status": "failed", "reason": "pytest failed"},
                "rollback": {"main_worktree_modified": False, "isolated_worktree_removed": True},
            },
        )

        with (
            patch.object(CodexWorker, "status", AsyncMock(return_value=ready)),
            patch.object(CodexWorker, "run", AsyncMock(return_value=failed)),
            TestClient(app) as client,
        ):
            delegated = client.post(
                "/api/ai-workers/delegate",
                json={
                    "task_type": "safe_code_fix",
                    "prompt": "Fix one scoped defect.",
                    "context": {
                        "allowed_paths": ["runtime-recovered/api/src/atnos/sample.py"],
                        "verification_command": [sys.executable, "-m", "pytest", "tests/test_sample.py"],
                    },
                    "preferred_worker": "codex",
                    "allowed_workers": ["codex"],
                    "privacy_mode": "allow_cloud_api",
                },
            )
            self.assertEqual(delegated.status_code, 200)
            payload = delegated.json()
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["verification_status"], "verification_failed")
            self.assertEqual(payload["result_json"]["lifecycle"][-1]["phase"], "verification_failed")

            approval = client.post(
                f"/api/ai-workers/tasks/{payload['id']}/review",
                json={"decision": "approve", "reviewer": "operator", "note": "Attempted approval."},
            )
            self.assertEqual(approval.status_code, 409)
            self.assertIn("cannot be approved", approval.json()["detail"])

    def test_failed_worker_result_is_not_recorded_as_completed(self) -> None:
        """Nieudany worker musi pozostawić audytowalny stan failed."""
        ready = WorkerStatusSnapshot(
            worker_key="ollama",
            label="Ollama Local",
            status="ready",
            ready=True,
            reason="mocked ready",
            capabilities=("local_llm",),
        )
        failed = WorkerExecutionResult(
            worker_key="ollama",
            status="failed",
            summary="Independent verification command failed.",
            result={"verification": {"status": "failed", "reason": "test failed"}},
        )

        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            with (
                patch.object(router, "_worker_status", AsyncMock(return_value=ready)),
                patch.object(router.ollama, "run", AsyncMock(return_value=failed)),
            ):
                result = asyncio.run(
                    router.delegate(
                        DelegateTaskInput(
                            task_type="review",
                            prompt="Review this local draft.",
                            context={},
                            preferred_worker="ollama",
                            allowed_workers=("ollama",),
                            privacy_mode="strict_local",
                            priority="normal",
                            requested_by="test",
                            requires_approval_for_result=False,
                        )
                    )
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["verification_status"], "verification_failed")
            self.assertEqual(result["runs"][0]["status"], "failed")

    def test_api_routes_expose_runtime_health_and_delegation_guardrail(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            health = client.get("/api/runtime/health")
            self.assertEqual(health.status_code, 200)
            health_payload = health.json()
            self.assertIn("components", health_payload)
            self.assertIn("readiness", health_payload)
            self.assertIn("verdict", health_payload["readiness"])
            self.assertEqual(health_payload["readiness"]["evidence"]["source"], "runtime_health_components")
            component_keys = {component["key"] for component in health_payload["components"]}
            self.assertIn("desktop_web", component_keys)
            self.assertIn("desktop_shell", component_keys)
            self.assertIn("gmail_connection", component_keys)
            self.assertIn("youtube_connection", component_keys)
            self.assertIn("xtb_demo_market_data", component_keys)
            self.assertTrue(health_payload["launcher"]["script_path"].endswith("Launch-Atnos.ps1"))
            for component in health_payload["components"]:
                self.assertIn("verdict", component)
                self.assertIn("evidence", component)
                self.assertIn("first_action", component)

            response = client.post(
                "/api/ai-workers/delegate",
                json={
                    "task_type": "trading",
                    "prompt": "Buy EURUSD live using password test-secret",
                    "context": {"password": "test-secret"},
                    "privacy_mode": "strict_local",
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "blocked")
            self.assertIn("high-risk", payload["blocked_reason"])
            self.assertEqual(payload["payload_json"]["context"]["password"], "[REDACTED]")

    def test_delegate_plan_creates_root_task_with_children(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with app.state.database.session_scope() as session:
                project = Project(name="Delegation plan", slug="delegation-plan")
                session.add(project)
                session.flush()
                project_id = int(project.id)
            response = client.post(
                "/api/ai-workers/delegate-plan",
                json={
                    "task_type": "content_short_quality",
                    "prompt": "Prepare an English YouTube Shorts quality review plan.",
                    "context": {"api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz123456"},
                    "privacy_mode": "strict_local",
                    "allowed_workers": ["ollama", "browser_claude"],
                    "max_steps": 4,
                    "project_id": project_id,
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["task_role"], "planner")
            self.assertEqual(len(payload["children"]), 4)
            self.assertTrue(all(child["worker_key"] == "ollama" for child in payload["children"]))
            self.assertNotIn("sk-proj-abcdefghijklmnopqrstuvwxyz123456", str(payload))
            contract = payload["execution_contract"]
            self.assertEqual(contract["scope"], {})
            self.assertEqual(contract["max_iterations"], 4)
            self.assertIn("ollama_local_api", contract["allowed_tools"])
            self.assertIn("source citations", " ".join(contract["required_evidence"]))
            self.assertIn("rollback", contract)
            self.assertEqual(payload["children"][0]["execution_contract"]["max_iterations"], 1)
            self.assertEqual(payload["children"][0]["execution_contract"]["allowed_workers"], ["ollama"])
            self.assertEqual(payload["payload_json"]["project_id"], project_id)
            with app.state.database.session_scope() as session:
                child_tasks = list(
                    session.scalars(
                        select(AiWorkerTask).where(AiWorkerTask.parent_task_id == payload["id"])
                    )
                )
                self.assertEqual(len(child_tasks), 4)
                self.assertTrue(
                    all(child.payload_json["project_id"] == project_id for child in child_tasks)
                )

            queue = client.get("/api/ai-workers/tasks", params={"roots_only": "true"})
            self.assertEqual(queue.status_code, 200)
            roots = queue.json()["tasks"]
            self.assertEqual(len(roots), 1)
            self.assertEqual(roots[0]["id"], payload["id"])

    def test_planned_child_task_can_be_run_and_stores_real_result(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)

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
            summary="Specific script fixes backed by the provided context.",
            result={"content": "Improve hook, replace generic visuals, keep English voiceover."},
        )

        with (
            patch.object(OllamaWorker, "status", AsyncMock(return_value=ready)),
            patch.object(OllamaWorker, "run", AsyncMock(return_value=result)),
            TestClient(app) as client,
        ):
            with app.state.database.session_scope() as session:
                project = Project(name="Executed plan", slug="executed-plan")
                session.add(project)
                session.flush()
                project_id = int(project.id)
            plan_response = client.post(
                "/api/ai-workers/delegate-plan",
                json={
                    "task_type": "content_short_quality",
                    "prompt": "Prepare an English Shorts quality review plan.",
                    "context": {"topic": "cashflow mistakes"},
                    "privacy_mode": "strict_local",
                    "allowed_workers": ["ollama"],
                    "max_steps": 3,
                    "requires_approval_for_result": True,
                    "project_id": project_id,
                },
            )
            self.assertEqual(plan_response.status_code, 200)
            child_id = plan_response.json()["children"][0]["id"]

            run_response = client.post(f"/api/ai-workers/tasks/{child_id}/run")
            self.assertEqual(run_response.status_code, 200)
            payload = run_response.json()
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["verification_status"], "completed")
            self.assertEqual(payload["runs"][0]["status"], "completed")
            self.assertIsNotNone(payload["trace_id"])

        with app.state.database.session_scope() as session:
            trace = session.get(ExecutionTrace, payload["trace_id"])
            self.assertIsNotNone(trace)
            self.assertEqual(trace.project_id, project_id)
            self.assertEqual(trace.status.value, "completed")
            memories = list(
                session.scalars(
                    select(ProjectMemory).where(ProjectMemory.project_id == project_id)
                )
            )
            self.assertEqual(len(memories), 1)
            self.assertIn("completed by ollama", memories[0].content)
            self.assertIn("Specific script fixes", payload["result_summary"])

    def test_safe_plan_runs_only_local_steps_before_review(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)
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
            summary="Local research completed.",
            result={"content": "Safe local result."},
        )

        with (
            patch.object(OllamaWorker, "status", AsyncMock(return_value=ready)),
            patch.object(OllamaWorker, "run", AsyncMock(return_value=result)) as run_worker,
            TestClient(app) as client,
        ):
            plan_response = client.post(
                "/api/ai-workers/delegate-plan",
                json={
                    "task_type": "investment_operator_command",
                    "prompt": "Prepare a paper-only market data research plan.",
                    "privacy_mode": "strict_local",
                    "allowed_workers": ["ollama"],
                    "max_steps": 4,
                },
            )
            self.assertEqual(plan_response.status_code, 200)
            root_id = plan_response.json()["id"]

            run_response = client.post(f"/api/ai-workers/tasks/{root_id}/run-safe-plan")
            self.assertEqual(run_response.status_code, 200)
            payload = run_response.json()

        self.assertEqual(payload["status"], "awaiting_review")
        self.assertEqual(payload["verification_status"], "awaiting_human_review")
        self.assertEqual(run_worker.await_count, 4)
        self.assertEqual([child["status"] for child in payload["children"]], ["completed"] * 4)
        self.assertIn("wynik czeka na review czlowieka", payload["result_summary"])
        final_context = run_worker.await_args_list[-1].kwargs["context"]
        self.assertEqual(len(final_context["prior_results"]), 3)
        self.assertEqual(final_context["prior_results"][0]["summary"], "Local research completed.")

    def test_project_command_plan_runs_workers_and_receives_validated_review_result(self) -> None:
        ready = WorkerStatusSnapshot(
            worker_key="ollama",
            label="Ollama Local",
            status="ready",
            ready=True,
            reason="mocked ready",
            capabilities=("local_llm", "text"),
        )

        async def run_step(*, task_type: str, prompt: str, context: dict[str, object]) -> WorkerExecutionResult:
            prior = context.get("prior_results")
            prior_count = len(prior) if isinstance(prior, list) else 0
            return WorkerExecutionResult(
                worker_key="ollama",
                status="completed",
                summary=f"Executed {task_type} with {prior_count} prior results.",
                result={"content": f"Independent output for {task_type}; prior_results={prior_count}."},
            )

        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            with (
                patch.object(router.ollama, "status", AsyncMock(return_value=ready)),
                patch.object(router.ollama, "run", AsyncMock(side_effect=run_step)) as run_worker,
            ):
                payload = router.delegate_plan(
                    DelegatePlanInput(
                        task_type="content_short_quality",
                        prompt="Review an English Shorts draft using local evidence.",
                        context={"topic": "compound interest"},
                        preferred_worker="ollama",
                        allowed_workers=("ollama",),
                        privacy_mode="strict_local",
                        priority="normal",
                        requested_by="project_command_chat",
                        requires_approval_for_result=True,
                        max_steps=4,
                    )
                )

            self.assertEqual(payload["status"], "awaiting_review")
            self.assertEqual(payload["verification_status"], "awaiting_human_review")
            self.assertEqual(run_worker.await_count, 4)
            self.assertEqual([child["status"] for child in payload["children"]], ["completed"] * 4)

            root = router.repository.get_task(payload["id"])
            self.assertIsNotNone(root)
            final_child = sorted(root.children, key=lambda child: child.sequence_index)[-1]
            self.assertEqual(final_child.result_json["output_validation"]["status"], "passed")
            self.assertFalse(
                final_child.result_json["output_validation"]["semantic_correctness_verified"]
            )
            self.assertEqual(
                len(final_child.payload_json["context"]["prior_results"]),
                3,
            )

    def test_worker_timeout_fails_task_and_closes_run(self) -> None:
        ready = WorkerStatusSnapshot(
            worker_key="ollama",
            label="Ollama Local",
            status="ready",
            ready=True,
            reason="mocked ready",
        )

        async def never_finishes(**_: object) -> WorkerExecutionResult:
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

        contract = {
            "max_duration_s": 1,
            "allowed_workers": ["ollama"],
            "allowed_tools": ["ollama_local_api"],
        }
        with self.database.session_scope() as session:
            router = AiDelegationRouter(session, self.settings)
            with (
                patch.object(router, "_worker_status", AsyncMock(return_value=ready)),
                patch.object(router, "_build_execution_contract", return_value=contract),
                patch.object(router.ollama, "run", AsyncMock(side_effect=never_finishes)),
            ):
                result = asyncio.run(
                    router.delegate(
                        DelegateTaskInput(
                            task_type="review",
                            prompt="Review this safe local draft.",
                            context={},
                            preferred_worker="ollama",
                            allowed_workers=("ollama",),
                            privacy_mode="strict_local",
                            priority="normal",
                            requested_by="timeout-test",
                            requires_approval_for_result=False,
                        )
                    )
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["verification_status"], "verification_failed")
            self.assertIn("timed out after 1 seconds", result["error_message"])
            self.assertEqual(result["runs"][0]["status"], "failed")
            self.assertIsNotNone(result["runs"][0]["completed_at"])
            self.assertEqual(
                result["result_json"]["verification"]["reason"],
                "worker_timeout",
            )


if __name__ == "__main__":
    unittest.main()
