from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.self_coding_draft_service import SelfCodingDraftService

from .test_support import bootstrap_test_app, create_test_app


class CodegenEditExistingAppTest(unittest.TestCase):
    def test_edit_preview_and_atomic_apply_work(self) -> None:
        with TemporaryDirectory(prefix="atnos-codegen-edit-") as tmpdir:
            root = Path(tmpdir)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            target_file = project_root / "main.py"
            target_file.write_text("print('old')\n", encoding="utf-8")

            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                settings.self_improvement_allow_self_apply = True
                database = app.state.database

                payload = {
                    "summary": "Update CLI output",
                    "files": [
                        {
                            "path": "main.py",
                            "content": "print('new')\n",
                        }
                    ],
                }

                with database.session_scope() as session, patch(
                    "atnos.services.self_coding_draft_service.WorkflowLLMService.generate_text",
                    return_value=SimpleNamespace(content=json.dumps(payload)),
                ):
                    service = SelfCodingDraftService(
                        session,
                        settings=settings,
                        provider_registry=SimpleNamespace(),
                    )
                    preview = service.edit_existing_app(
                        project_root=str(project_root),
                        task_description="Change output to new.",
                        files_to_modify=["main.py"],
                    )
                    self.assertEqual(preview.mode, "edit_existing_app")
                    broker = AccountActionBrokerService(session, settings)
                    broker.approve(preview.action_draft_id)
                    result = service.execute_code_apply(draft_id=preview.action_draft_id)

                    self.assertEqual(result["status"], "applied_unverified")
                    self.assertEqual(result["verification_status"], "not_requested")
                    self.assertIn(str(target_file.resolve()), result["written_files"])
                    self.assertEqual(target_file.read_text(encoding="utf-8"), "print('new')\n")
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
