from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from atnos.services.self_coding_draft_service import SelfCodingDraftService, SelfCodingExecutionError

from .test_support import create_test_app


class CodegenRunTestsTest(unittest.TestCase):
    def test_detects_pytest_and_returns_failure_summary(self) -> None:
        with TemporaryDirectory(prefix="atnos-codegen-tests-") as tmpdir:
            root = Path(tmpdir)
            project_root = root / "project"
            tests_dir = project_root / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_sample.py").write_text(
                "def test_failure():\n    assert 1 == 2\n",
                encoding="utf-8",
            )

            app = create_test_app(root)
            settings = app.state.settings
            database = app.state.database

            with database.session_scope() as session:
                service = SelfCodingDraftService(
                    session,
                    settings=settings,
                    provider_registry=SimpleNamespace(),
                )
                result = service.run_project_tests(project_root=str(project_root), timeout_s=120)

                self.assertEqual(result.runner, "pytest")
                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("Tests failed.", result.summary)
                self.assertTrue(result.first_failures)

    def test_rejects_elevated_test_commands(self) -> None:
        with TemporaryDirectory(prefix="atnos-codegen-elevated-") as tmpdir:
            root = Path(tmpdir)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)

            app = create_test_app(root)
            settings = app.state.settings
            database = app.state.database

            class ElevatedSelfCodingDraftService(SelfCodingDraftService):
                @staticmethod
                def _detect_test_runner(root: Path):
                    return "custom", ("sudo", "pytest")

            with database.session_scope() as session:
                service = ElevatedSelfCodingDraftService(
                    session,
                    settings=settings,
                    provider_registry=SimpleNamespace(),
                )
                with self.assertRaises(SelfCodingExecutionError):
                    service.run_project_tests(project_root=str(project_root), timeout_s=5)


if __name__ == "__main__":
    unittest.main()
