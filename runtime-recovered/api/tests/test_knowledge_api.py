from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_pdf, create_test_app, make_temp_dir


class TestKnowledgeApi:
    def test_catalog_and_lookup_endpoints_return_local_knowledge_matches(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            app = create_test_app(root)
            knowledge_dir = app.state.settings.knowledge_dir
            assert knowledge_dir is not None
            doc_path = knowledge_dir / "creator" / "luxury-soft-glam.md"
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(
                """---
title: Luxury Soft-Glam lane
topic: creator-monetization
summary: Positioning lane for premium soft-glam content and exclusivity-first teaser funnels.
tags:
  - creator
  - luxury
keywords:
  - soft glam
  - exclusivity
  - premium tease
source_label: User curated note
source_url: local://creator/luxury-soft-glam
license: CC-BY-4.0
updated_at: 2026-05-11
---
# Luxury Soft-Glam lane

Use clean styling, expensive mood and direct eye contact.

The teaser should promise access, not explicit escalation.
""",
                encoding="utf-8",
            )
            with TestClient(app) as client:
                rebuild_response = client.post("/knowledge/rebuild-index")
                assert rebuild_response.status_code == 200
                assert rebuild_response.json()["document_count"] >= 4

                catalog_response = client.get("/knowledge/catalog")
                assert catalog_response.status_code == 200
                assert any(
                    item["title"] == "Luxury Soft-Glam lane"
                    for item in catalog_response.json()["documents"]
                )

                lookup_response = client.post(
                    "/knowledge/lookup",
                    json={
                        "query": "soft glam premium tease exclusivity",
                        "max_docs": 2,
                        "max_chars": 1200,
                    },
                )
                assert lookup_response.status_code == 200
                payload = lookup_response.json()
                assert payload["total_matches"] >= 1
                assert any(
                    item["title"] == "Luxury Soft-Glam lane"
                    for item in payload["matches"]
                )
                assert "Local curated knowledge library matches" in payload["system_section"]
                assert "Luxury Soft-Glam lane" in payload["system_section"]

    def test_workspace_endpoints_create_structure_and_sync_inbox(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            app = create_test_app(root)
            workspace_dir = app.state.settings.knowledge_workspace_dir
            assert workspace_dir is not None
            inbox_pdf = workspace_dir / "inbox" / "investing" / "kurs-ai.pdf"
            inbox_pdf.parent.mkdir(parents=True, exist_ok=True)
            build_pdf(
                inbox_pdf,
                "Kurs inwestowania z AI",
                "Ryzyko i pozycja",
                "AI pomaga w analizie rynku",
            )

            with TestClient(app) as client:
                workspace_response = client.get("/knowledge/workspace")
                assert workspace_response.status_code == 200
                assert workspace_response.json()["workspace_dir"] == str(workspace_dir)

                sync_response = client.post("/knowledge/workspace/sync")
                assert sync_response.status_code == 200
                payload = sync_response.json()
                assert payload["processed_count"] == 1
                assert payload["mirrored_count"] == 1
                assert payload["knowledge_document_count"] >= 4
                assert len(payload["items"]) == 1
                assert payload["items"][0]["bucket"] == "investing"
