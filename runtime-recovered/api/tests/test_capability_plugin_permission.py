from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.db.models.enums import PluginPermissionPolicy
from atnos.repositories.plugin_repository import PluginRepository
from atnos.services.capability_action_service import CapabilityActionError, CapabilityActionService


class CapabilityPluginPermissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_document_capability_requires_enabled_plugin_permission(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                plugins = PluginRepository(session)
                document_tools = plugins.get_by_key("document-tools")
                assert document_tools is not None
                plugins.update(
                    document_tools,
                    permission_policy=PluginPermissionPolicy.DISABLED,
                )
                service = CapabilityActionService(session, app.state.settings)
                payload = {
                    "title": "Raport",
                    "sections": [
                        {
                            "heading": "Sekcja",
                            "paragraphs": ["To jest test capability permission."],
                        }
                    ],
                }

                with self.assertRaises(CapabilityActionError) as preview_error:
                    service.preview(
                        action="generate_docx_document",
                        conversation_id=None,
                        project_id=None,
                        payload=payload,
                    )
                with self.assertRaises(CapabilityActionError) as execute_error:
                    service.execute(
                        action="generate_docx_document",
                        conversation_id=None,
                        project_id=None,
                        payload=payload,
                        enforce_local_write=False,
                    )

        self.assertIn("permission policy is disabled", str(preview_error.exception))
        self.assertIn("permission policy is disabled", str(execute_error.exception))
