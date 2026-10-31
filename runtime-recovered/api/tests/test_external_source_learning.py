from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, create_test_app, make_temp_dir

from atnos.services.external_source_learning_service import CrawledSource, ExternalSourceLearningService
from atnos.services.knowledge_library_service import KnowledgeLibraryService


class TestExternalSourceLearningService:
    def test_public_api_catalog_ingests_into_knowledge_library(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            service = ExternalSourceLearningService(settings)

            result = service.ingest_public_api_catalog()

            assert result["status"] == "ingested"
            assert result["entry_count"] >= 4
            assert Path(result["knowledge_path"]).exists()

            matches = KnowledgeLibraryService(settings).lookup(
                "public APIs YouTube Data API research sources",
                max_docs=2,
                max_chars=1400,
            )
            assert matches
            assert any("Public API source catalog" in match.title for match in matches)

    def test_crawl_and_ingest_persists_crawl4ai_ready_source(self, monkeypatch) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            service = ExternalSourceLearningService(settings)

            async def fake_crawl(url: str) -> CrawledSource:
                return CrawledSource(
                    url=url,
                    final_url=url,
                    title="Crawl4AI documentation",
                    text="Crawl4AI helps Atnos capture web pages for education research and RAG indexing.",
                    crawler="crawl4ai",
                    status="ok",
                )

            monkeypatch.setattr(service, "crawl_url", fake_crawl)

            payload = asyncio.run(
                service.crawl_and_ingest(
                    url="https://docs.crawl4ai.example/start",
                    title="Crawl4AI source for Atnos",
                    research_focus="education research RAG",
                )
            )

            assert payload["status"] == "ingested"
            assert payload["crawl"]["crawler"] == "crawl4ai"
            assert Path(payload["knowledge_path"]).exists()

            matches = KnowledgeLibraryService(settings).lookup("crawl4ai RAG indexing", max_docs=1, max_chars=900)
            assert matches
            assert "Crawl4AI source for Atnos" in matches[0].title

    def test_rejects_non_http_urls(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            service = ExternalSourceLearningService(settings)

            try:
                service._validate_url("file:///C:/secret.txt")
            except ValueError as exc:
                assert "http(s)" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("file URLs must not be accepted")


class TestExternalSourceLearningApi:
    def test_external_sources_status_and_catalog_endpoints(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            with TestClient(app) as client:
                status = client.get("/sources/external/status")
                catalog = client.get("/sources/external/public-apis")

            assert status.status_code == 200
            assert status.json()["public_apis"]["available"] is True
            assert catalog.status_code == 200
            assert any(item["key"] == "public_apis_index" for item in catalog.json()["entries"])

    def test_external_crawl_endpoint_rejects_file_url(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            with TestClient(app) as client:
                response = client.post(
                    "/sources/external/crawl",
                    json={"url": "file:///C:/Users/localuser/.env"},
                )

            assert response.status_code == 422
