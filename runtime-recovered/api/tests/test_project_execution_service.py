from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from atnos.db.models.account_action_draft import AccountActionDraft
from atnos.db.models.rollback_point import RollbackPoint
from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.project_execution_service import ProjectExecutionService
from atnos.services.project_verification_service import ProjectVerificationResult
from atnos.services.self_coding_draft_service import SelfCodingDraftService

from .test_support import bootstrap_test_app, create_test_app


class StaticVerifier:
    def __init__(self, result: ProjectVerificationResult) -> None:
        self.result = result
        self.received_recipe: str | None = None

    def run(self, recipe: str) -> ProjectVerificationResult:
        self.received_recipe = recipe
        return self.result


def _verification(status: str) -> ProjectVerificationResult:
    return ProjectVerificationResult(
        recipe="backend_project_command_tests",
        status=status,  # type: ignore[arg-type]
        command=("pytest", "tests/test_project_command_api.py", "-q"),
        cwd="/controlled/atnos",
        exit_code=0 if status == "verified" else 1,
        duration_ms=12,
        summary="Verification passed." if status == "verified" else "Verification failed.",
        output="",
        next_step="Inspect the controlled verification result.",
    )


class ProjectExecutionServiceTest(unittest.TestCase):
    def _create_approved_preview(self, session, settings, project_root: Path, target: Path) -> int:
        payload = {
            "summary": "Update application output",
            "files": [{"path": target.name, "content": "print('new')\n"}],
        }
        with patch(
            "atnos.services.self_coding_draft_service.WorkflowLLMService.generate_text",
            return_value=SimpleNamespace(content=json.dumps(payload)),
        ):
            preview = SelfCodingDraftService(
                session,
                settings=settings,
                provider_registry=SimpleNamespace(),
            ).edit_existing_app(
                project_root=str(project_root),
                task_description="Change output safely.",
                files_to_modify=[target.name],
            )
        draft = session.get(AccountActionDraft, preview.action_draft_id)
        assert draft is not None
        draft.preview_payload_json = {
            **draft.preview_payload_json,
            "verification_recipe": "backend_project_command_tests",
        }
        AccountActionBrokerService(session, settings).approve(preview.action_draft_id)
        session.flush()
        return preview.action_draft_id

    def test_verified_change_creates_snapshot_and_keeps_new_file(self) -> None:
        with TemporaryDirectory(prefix="atnos-safe-execution-") as tmpdir:
            root = Path(tmpdir)
            project_root = root / "project"
            project_root.mkdir()
            target = project_root / "main.py"
            target.write_text("print('old')\n", encoding="utf-8")
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                with app.state.database.session_scope() as session:
                    draft_id = self._create_approved_preview(session, settings, project_root, target)
                    verifier = StaticVerifier(_verification("verified"))
                    result = ProjectExecutionService(session, settings, verifier=verifier).execute_approved_code_apply(
                        draft_id=draft_id
                    )
                    rollback = session.get(RollbackPoint, result.rollback_point_id)
                    self.assertEqual(result.status, "verified")
                    self.assertEqual(result.verification_status, "verified")
                    self.assertEqual(verifier.received_recipe, "backend_project_command_tests")
                    self.assertEqual(target.read_text(encoding="utf-8"), "print('new')\n")
                    self.assertIsNotNone(rollback)
                    assert rollback is not None
                    self.assertEqual(rollback.status, "verified")
                    self.assertTrue(Path(rollback.manifest_path).is_file())
            finally:
                app.state.database.dispose()

    def test_failed_verification_restores_original_file_and_audits_failure(self) -> None:
        with TemporaryDirectory(prefix="atnos-safe-execution-") as tmpdir:
            root = Path(tmpdir)
            project_root = root / "project"
            project_root.mkdir()
            target = project_root / "main.py"
            target.write_text("print('old')\n", encoding="utf-8")
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                with app.state.database.session_scope() as session:
                    draft_id = self._create_approved_preview(session, settings, project_root, target)
                    result = ProjectExecutionService(
                        session,
                        settings,
                        verifier=StaticVerifier(_verification("failed")),
                    ).execute_approved_code_apply(draft_id=draft_id)
                    rollback = session.get(RollbackPoint, result.rollback_point_id)
                    draft = session.get(AccountActionDraft, draft_id)
                    self.assertEqual(result.status, "rolled_back")
                    self.assertEqual(result.rollback_status, "rolled_back")
                    self.assertEqual(target.read_text(encoding="utf-8"), "print('old')\n")
                    self.assertIsNotNone(rollback)
                    assert rollback is not None
                    self.assertEqual(rollback.status, "rolled_back")
                    self.assertTrue(Path(rollback.manifest_path).is_file())
                    self.assertIsNotNone(draft)
                    assert draft is not None
                    self.assertEqual(draft.status, "approved")
                    self.assertEqual(draft.audit_log_json[-1]["event"], "execution_failed_rolled_back")
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
