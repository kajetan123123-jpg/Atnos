from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_pdf, build_test_settings, enable_local_file_write, make_temp_dir

from atnos.db.migrations import upgrade_database
from atnos.db.session import DatabaseManager
from atnos.services.bootstrap_service import BootstrapService
from atnos.services.capability_action_service import CapabilityActionService


class PdfExtractionCapabilityTest(unittest.TestCase):
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

    def test_extracts_pdf_pages_and_text(self) -> None:
        uploads_dir = self.settings.uploads_dir
        pdf_path = uploads_dir / "sample.pdf"
        build_pdf(pdf_path, "To jest pierwsza linia PDF.", "Druga linia raportu.")
        storage_path = pdf_path.relative_to(self.settings.app_root).as_posix()

        with self.database.session_scope() as session:
            result = CapabilityActionService(session, self.settings).execute(
                action="extract_pdf_content",
                conversation_id=None,
                project_id=None,
                payload={"storage_path": storage_path},
                enforce_local_write=False,
            )

        self.assertGreaterEqual(result["total_pages"], 1)
        self.assertGreater(result["extracted_chars"], 0)
        self.assertIn("pierwsza linia", result["extracted_text"].lower())

    def test_extracts_text_from_generated_pdf_output(self) -> None:
        with self.database.session_scope() as session:
            service = CapabilityActionService(session, self.settings)
            generated = service.execute(
                action="generate_pdf_document",
                conversation_id=7,
                project_id=None,
                payload={
                    "title": "Raport z outputu",
                    "sections": [
                        {
                            "heading": "Wnioski",
                            "paragraphs": ["ATNOS zapisal ten PDF na kontrolowanym storage."],
                        }
                    ],
                },
            )["artifact"]

            extracted = service.execute(
                action="extract_pdf_content",
                conversation_id=7,
                project_id=None,
                payload={"storage_path": generated["storage_path"]},
                enforce_local_write=False,
            )

        self.assertGreaterEqual(extracted["total_pages"], 1)
        self.assertGreater(extracted["extracted_chars"], 0)
        self.assertIn("kontrolowanym storage", extracted["extracted_text"].lower())
