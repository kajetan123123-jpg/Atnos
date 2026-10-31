from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.knowledge_library_service import KnowledgeLibraryService


class TestKnowledgeLibraryService:
    def test_rebuild_index_creates_catalog_and_matches_relevant_doc(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            service = KnowledgeLibraryService(settings)
            knowledge_dir = settings.knowledge_dir
            assert knowledge_dir is not None
            custom_doc = knowledge_dir / "finance" / "course-risk-framework.md"
            custom_doc.parent.mkdir(parents=True, exist_ok=True)
            custom_doc.write_text(
                """---
title: Investing course risk framework
topic: investing
summary: A compact note about risk, position sizing and avoiding impulsive entries.
tags:
  - investing
  - risk
keywords:
  - position sizing
  - impulsive entry
  - downside
source_label: User supplied course note
source_url: local://course/risk-framework
license: CC-BY-4.0
updated_at: 2026-05-11
---
# Investing course risk framework

Always define downside before upside.

Position sizing matters more than finding a perfect entry.

If a setup requires emotional hope to work, the setup is weak.
""",
                encoding="utf-8",
            )

            payload = service.rebuild_index()

            assert payload["document_count"] >= 4
            assert service.index_path.exists()

            matches = service.lookup("kurs inwestowania ryzyko pozycja", max_docs=2, max_chars=1600)

            assert matches
            assert matches[0].title == "Investing course risk framework"
            section = service.build_system_section("inwestowanie ryzyko pozycja", max_docs=1, max_chars=1200)
            assert section is not None
            assert "Local curated knowledge library matches" in section
            assert "Investing course risk framework" in section

    def test_refresh_index_reuses_unchanged_sources_and_records_citations(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            service = KnowledgeLibraryService(settings)
            knowledge_dir = settings.knowledge_dir
            assert knowledge_dir is not None
            custom_doc = knowledge_dir / "finance" / "incremental-risk-note.md"
            custom_doc.parent.mkdir(parents=True, exist_ok=True)
            custom_doc.write_text(
                """---
title: Incremental risk note
topic: investing
summary: Evidence-backed notes about risk limits and paper trading.
tags:
  - investing
  - risk
keywords:
  - paper trading
  - risk limit
source_label: Operator supplied strategy note
source_url: local://course/incremental-risk-note
license: CC-BY-4.0
updated_at: 2026-07-16
---
# Incremental risk note

Paper trading results must include fees, drawdown and an out-of-sample period.

Do not switch a strategy to live execution from a single profitable week.
""",
                encoding="utf-8",
            )

            initial = service.rebuild_index()
            initial_item = next(item for item in initial["documents"] if item["path"] == "finance/incremental-risk-note.md")
            assert initial_item["source_sha256"]
            assert initial_item["indexed_at"]

            unchanged = service.refresh_index()
            unchanged_stats = unchanged["refresh_stats"]
            assert unchanged_stats["mode"] == "incremental"
            assert unchanged_stats["parsed_count"] == 0
            assert unchanged_stats["reused_count"] == unchanged["document_count"]

            custom_doc.write_text(
                custom_doc.read_text(encoding="utf-8").replace(
                    "single profitable week",
                    "single profitable month",
                ),
                encoding="utf-8",
            )
            changed = service.refresh_index()
            changed_item = next(item for item in changed["documents"] if item["path"] == "finance/incremental-risk-note.md")
            assert changed["refresh_stats"]["parsed_count"] == 1
            assert changed_item["source_sha256"] != initial_item["source_sha256"]

            matches = service.lookup("paper trading risk limit", max_docs=1, max_chars=600)
            assert matches
            assert matches[0].citation == "knowledge/finance/incremental-risk-note.md:L16-L18 (Operator supplied strategy note)"
            assert matches[0].source_updated_at == "2026-07-16"

            custom_doc.unlink()
            removed = service.refresh_index()
            assert removed["refresh_stats"]["removed_count"] == 1
