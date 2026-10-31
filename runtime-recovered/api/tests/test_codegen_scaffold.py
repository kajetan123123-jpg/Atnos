from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atnos.services.self_coding_draft_service import SelfCodingDraftService

from .test_support import bootstrap_test_app, create_test_app


class CodegenScaffoldTest(unittest.TestCase):
    def test_generate_python_cli_scaffold_preview_creates_file_tree(self) -> None:
        with TemporaryDirectory(prefix="atnos-codegen-scaffold-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                database = app.state.database

                with database.session_scope() as session:
                    preview = SelfCodingDraftService(session, settings=settings).generate_application_scaffold(
                        app_type="python-cli",
                        language="python",
                        framework="argparse",
                        description="Utility CLI for Atnos.",
                        target_dir=str(root / "generated-app"),
                    )

                    self.assertEqual(preview.mode, "generate_application_scaffold")
                    self.assertGreater(preview.action_draft_id, 0)
                    self.assertEqual(preview.metadata["kind"], "code_diff")
                    self.assertEqual(preview.metadata["draft_status"], "draft")
                    self.assertEqual(preview.metadata["file_tree"], ["main.py", "setup.py"])
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
