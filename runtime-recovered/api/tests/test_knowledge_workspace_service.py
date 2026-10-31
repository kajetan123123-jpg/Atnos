from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_pdf, build_test_settings, make_temp_dir

from atnos.services.knowledge_library_service import KnowledgeLibraryService
from atnos.services.knowledge_workspace_service import KnowledgeWorkspaceService


class TestKnowledgeWorkspaceService:
    def test_repair_mojibake_normalizes_common_utf8_misdecode(self) -> None:
        repaired = KnowledgeWorkspaceService._repair_mojibake(
            "Przewaga w 2026 roku dziÄ™ki AI â€” Warsztatnowoczesnegotradera"
        )

        assert "dzięki" in repaired
        assert "—" in repaired

    def test_sync_inbox_imports_pdf_into_curated_and_knowledge(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            service = KnowledgeWorkspaceService(settings)

            inbox_pdf = settings.knowledge_workspace_dir / "inbox" / "investing" / "warsztat-ai.pdf"
            inbox_pdf.parent.mkdir(parents=True, exist_ok=True)
            build_pdf(
                inbox_pdf,
                "Przewaga w 2026 roku dzieki AI",
                "Warsztat nowoczesnego tradera",
                "AI jako partner, nie zastepca tradera",
            )

            report = service.sync_inbox()

            assert report["processed_count"] == 1
            assert report["mirrored_count"] == 1
            assert report["unsupported_count"] == 0
            assert len(report["items"]) == 1
            item = report["items"][0]
            assert item["bucket"] == "investing"
            assert item["curated_path"] is not None
            assert item["knowledge_path"] is not None
            assert Path(item["curated_path"]).exists()
            assert Path(item["knowledge_path"]).exists()

            matches = KnowledgeLibraryService(settings).lookup(
                "trader ai przewaga investing",
                max_docs=2,
                max_chars=1200,
            )
            assert matches
            assert any("tradera" in match.title.lower() or "ai" in match.title.lower() for match in matches)
