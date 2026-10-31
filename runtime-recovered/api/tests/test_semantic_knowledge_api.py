from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, create_test_app, make_temp_dir

from atnos.services.knowledge_library_service import KnowledgeLibraryService
from atnos.services.semantic_knowledge_index_service import (
    SemanticKnowledgeIndexService,
    SemanticKnowledgeUnavailableError,
)


class ApiEmbeddingClient:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def status(self, model: str) -> dict[str, object]:
        if not self.available:
            raise SemanticKnowledgeUnavailableError("embedding_model_missing", "Embedding model is missing.")
        return {"runtime": "test", "model": model, "installed_models": [model]}

    def embed(self, texts, model: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            normalized = text.lower()
            vectors.append([1.0, 0.0] if "credential" in normalized or "oauth" in normalized else [0.0, 1.0])
        return vectors


def _write_oauth_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
title: OAuth refresh token recovery
topic: runtime
summary: Explains recovery after OAuth credentials expire.
tags:
  - oauth
keywords:
  - refresh token
source_label: API test note
source_url: local://api-test/oauth
source_type: project_note
project_id: youtube-alpha
license: CC0-1.0
updated_at: 2026-07-17
---
# Recovery

When an OAuth refresh token expires, the YouTube connection requires an owner re-authorization.
""",
        encoding="utf-8",
    )


class TestSemanticKnowledgeApi:
    def test_reindex_and_hybrid_lookup_return_evidence(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            library = KnowledgeLibraryService(settings)
            _write_oauth_note(settings.knowledge_dir / "runtime" / "oauth.md")
            app = create_test_app(Path(temp_dir))
            app.state.semantic_knowledge_index = SemanticKnowledgeIndexService(
                settings,
                library=library,
                embedding_client=ApiEmbeddingClient(),
            )

            with TestClient(app) as client:
                indexed = client.post("/knowledge/semantic/reindex", json={"force": True})
                lookup = client.post(
                    "/knowledge/hybrid-lookup",
                    json={
                        "query": "why did the video account disconnect after credentials lapsed?",
                        "project_ids": ["youtube-alpha"],
                        "source_types": ["project_note"],
                    },
                )

            assert indexed.status_code == 200
            assert indexed.json()["refresh"]["inserted_chunks"] >= 1
            assert lookup.status_code == 200
            payload = lookup.json()
            assert payload["total_matches"] >= 1
            assert payload["matches"][0]["path"] == "runtime/oauth.md"
            assert payload["matches"][0]["project_id"] == "youtube-alpha"
            assert payload["matches"][0]["citation"].endswith("(API test note)")

    def test_reindex_returns_explicit_conflict_when_embedding_model_is_missing(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            app = create_test_app(Path(temp_dir))
            app.state.semantic_knowledge_index = SemanticKnowledgeIndexService(
                settings,
                embedding_client=ApiEmbeddingClient(available=False),
            )

            with TestClient(app) as client:
                response = client.post("/knowledge/semantic/reindex", json={})

            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "embedding_model_missing"
