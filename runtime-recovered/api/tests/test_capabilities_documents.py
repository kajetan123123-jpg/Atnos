from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, enable_local_file_write, make_temp_dir

from atnos.db.migrations import upgrade_database
from atnos.db.session import DatabaseManager
from atnos.services.bootstrap_service import BootstrapService
from atnos.services.capability_action_service import CapabilityActionService


class DocumentCapabilitiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.database = DatabaseManager(self.settings)
        self.addCleanup(self.database.dispose)
        self.database.initialize()
        upgrade_database(self.settings)
        with self.database.session_scope() as session:
            BootstrapService(session, self.settings).ensure_local_first_defaults()
        self.app = type("App", (), {"state": type("State", (), {"database": self.database})()})()
        enable_local_file_write(self.app)

    def test_generates_docx_pptx_xlsx_and_pdf_outputs(self) -> None:
        with self.database.session_scope() as session:
            service = CapabilityActionService(session, self.settings)

            docx = service.execute(
                action="generate_docx_document",
                conversation_id=11,
                project_id=None,
                payload={
                    "title": "Raport Word",
                    "sections": [{"heading": "Decyzje", "paragraphs": ["A", "B"]}],
                },
            )["artifact"]
            pptx = service.execute(
                action="generate_pptx_deck",
                conversation_id=11,
                project_id=None,
                payload={
                    "title": "Deck",
                    "slides": [
                        {"layout": "title", "heading": "Deck", "body": "Intro"},
                        {"layout": "bullets", "heading": "Agenda", "bullets": ["A", "B"]},
                    ],
                },
            )["artifact"]
            xlsx = service.execute(
                action="generate_xlsx_workbook",
                conversation_id=11,
                project_id=None,
                payload={
                    "sheets": [
                        {
                            "name": "Sheet1",
                            "columns": ["Kategoria", "Wartosc"],
                            "rows": [["A", 1], ["B", 2]],
                            "freeze_top": True,
                        }
                    ]
                },
            )["artifact"]
            pdf = service.execute(
                action="generate_pdf_document",
                conversation_id=11,
                project_id=None,
                payload={
                    "title": "Raport PDF",
                    "sections": [{"heading": "Tresc", "paragraphs": ["Linia 1", "Linia 2"]}],
                },
            )["artifact"]

        self.assertEqual(docx["file_extension"], ".docx")
        self.assertEqual(pptx["file_extension"], ".pptx")
        self.assertEqual(xlsx["file_extension"], ".xlsx")
        self.assertEqual(pdf["file_extension"], ".pdf")
        self.assertTrue((self.root / docx["storage_path"]).exists())
        self.assertTrue((self.root / pptx["storage_path"]).exists())
        self.assertTrue((self.root / xlsx["storage_path"]).exists())
        self.assertTrue((self.root / pdf["storage_path"]).exists())

    def test_rejects_invalid_storage_path_traversal(self) -> None:
        with self.database.session_scope() as session:
            service = CapabilityActionService(session, self.settings)
            with self.assertRaisesRegex(Exception, "storage_path"):
                service.execute(
                    action="extract_pdf_content",
                    conversation_id=None,
                    project_id=None,
                    payload={"storage_path": "../secret.pdf"},
                    enforce_local_write=False,
                )
