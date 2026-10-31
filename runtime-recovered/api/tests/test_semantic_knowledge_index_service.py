from __future__ import annotations

import json
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.knowledge_library_service import KnowledgeLibraryService
from atnos.services.semantic_knowledge_index_service import (
    SemanticKnowledgeIndexService,
    SemanticKnowledgeUnavailableError,
)
from atnos.services.semantic_knowledge_source_service import SemanticKnowledgeSourceService


def test_semantic_source_normalizes_pre_migration_atnos_paths() -> None:
    source = (
        "Use F:/Apps/Atnos and F:\\obsydian\\atnos for project context; "
        "the memory index on F: must not remain authoritative."
    )

    normalized = SemanticKnowledgeSourceService._normalize_operational_paths(source)

    assert "F:/Apps/Atnos" not in normalized
    assert "F:\\obsydian\\atnos" not in normalized
    assert "index on F:" not in normalized
    assert "E:/Atnos/App" in normalized
    assert "E:\\Atnos\\Knowledge\\Obsidian\\Atnos-Brain" in normalized


def test_semantic_source_keeps_canonical_atnos_paths_unchanged() -> None:
    source = "Use E:/Atnos/App with E:\\Atnos\\Knowledge\\Obsidian\\Atnos-Brain."

    assert SemanticKnowledgeSourceService._normalize_operational_paths(source) == source


class FakeEmbeddingClient:
    """Kontrolowane embeddingi: podobne znaczenie nie wymaga wspólnych słów."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.embed_calls: list[tuple[str, tuple[str, ...]]] = []

    def status(self, model: str) -> dict[str, object]:
        if not self.available:
            raise SemanticKnowledgeUnavailableError("embedding_model_missing", "Test embedding model is missing.")
        return {"runtime": "fake-ollama", "model": model, "installed_models": [model]}

    def embed(self, texts, model: str) -> list[list[float]]:
        self.embed_calls.append((model, tuple(texts)))
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = text.lower()
        if any(token in normalized for token in ("oauth", "token", "credential", "credentials expired", "youtube disconnect", "youtube stopped")):
            return [1.0, 0.0, 0.0]
        if any(token in normalized for token in ("8 bytes", "blocking video", "invalid render", "empty mp4")):
            return [0.0, 1.0, 0.0]
        if any(token in normalized for token in ("watchdog", "service recovery", "restart", "heartbeat")):
            return [0.0, 0.0, 1.0]
        return [0.2, 0.2, 0.2]


def _write_document(
    path: Path,
    *,
    title: str,
    topic: str,
    body: str,
    project_id: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
title: {title}
topic: {topic}
summary: Test material for semantic knowledge retrieval.
tags:
  - test
keywords:
  - semantic retrieval
source_label: Test knowledge source
source_url: local://test/{path.stem}
source_type: project_note
project_id: {project_id or ''}
license: CC0-1.0
updated_at: 2026-07-17
---
{body}
""",
        encoding="utf-8",
    )


class TestSemanticKnowledgeIndexService:
    def test_polish_operational_terms_expand_without_losing_l_stroke(self) -> None:
        tokens = KnowledgeLibraryService._tokenize(
            "Kto odpowiada za odzyskiwanie usługi po awarii i dlaczego YouTube nie jest połączony?"
        )

        assert {"uslugi", "service", "backend", "awarii", "failure", "recovery", "oauth"} <= tokens
        assert SemanticKnowledgeIndexService._lexical_score(
            KnowledgeLibraryService._tokenize("Kto odpowiada za odzyskiwanie usługi po awarii?"),
            title="Atnos Runtime Watchdog",
            text="The watchdog keeps the backend alive and restarts it after a failure.",
        ) == 1.0
        assert SemanticKnowledgeIndexService._lexical_score(
            KnowledgeLibraryService._tokenize("Kto odpowiada za odzyskiwanie usługi po awarii?"),
            title="Atnos Runtime Watchdog",
            text="",
        ) == 0.5

    def test_retrieves_semantic_answer_with_citation_and_match_reason(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            library = KnowledgeLibraryService(settings)
            _write_document(
                settings.knowledge_dir / "runtime" / "youtube-oauth.md",
                title="YouTube OAuth refresh failure",
                topic="runtime",
                body="# OAuth recovery\n\nA lapsed refresh token makes the YouTube connector disconnect until the owner completes OAuth again.",
            )
            _write_document(
                settings.knowledge_dir / "runtime" / "invalid-render.md",
                title="Invalid video artefacts",
                topic="runtime",
                body="# Render guard\n\nAn MP4 with only 8 bytes is an invalid render and must block upload before QA.",
            )
            client = FakeEmbeddingClient()
            service = SemanticKnowledgeIndexService(settings, library=library, embedding_client=client)

            reindex = service.reindex()
            matches = service.lookup("why did the video platform stop working after credentials expired?", max_results=1)

            assert reindex["status"] == "ready"
            assert reindex["refresh"]["inserted_chunks"] >= 2
            assert reindex["embedding_dimension"] == 3
            assert reindex["last_index_duration_ms"] is not None
            assert reindex["language_support"]["quality_status"] == "not_benchmarked_by_runtime"
            assert matches[0].title == "YouTube OAuth refresh failure"
            assert "semantic_similarity" in matches[0].match_reasons
            assert matches[0].citation.endswith("(Test knowledge source)")
            assert matches[0].lexical_score == 0.0
            assert service.status()["last_query_duration_ms"] is not None

    def test_incremental_sync_removes_missing_sources_and_reembeds_on_model_change(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            library = KnowledgeLibraryService(settings)
            document = settings.knowledge_dir / "runtime" / "watchdog.md"
            _write_document(
                document,
                title="Watchdog recovery",
                topic="runtime",
                body="# Recovery\n\nThe watchdog restarts a failed local service and records a heartbeat recovery event.",
            )
            client = FakeEmbeddingClient()
            service = SemanticKnowledgeIndexService(settings, library=library, embedding_client=client)
            initial = service.reindex()
            initial_calls = len(client.embed_calls)
            unchanged = service.reindex()
            assert unchanged["refresh"]["reused_chunks"] >= 1
            assert len(client.embed_calls) == initial_calls

            document.unlink()
            removed = service.reindex()
            assert removed["refresh"]["removed_chunks"] >= 1

            settings.semantic_knowledge_embedding_model = "bge-m3-v2"
            changed_model = SemanticKnowledgeIndexService(settings, library=library, embedding_client=client).reindex()
            assert changed_model["refresh"]["model_changed"] is True
            assert initial["embedding_model"] == "bge-m3"

    def test_reports_blocked_state_when_embedding_model_is_not_available(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            service = SemanticKnowledgeIndexService(
                settings,
                embedding_client=FakeEmbeddingClient(available=False),
            )

            status = service.status()
            assert status["status"] == "blocked_technical"
            assert status["blocked_reason"]["code"] == "embedding_model_missing"

    def test_rejects_overlapping_reindex_without_corrupting_the_index(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            service = SemanticKnowledgeIndexService(
                settings,
                embedding_client=FakeEmbeddingClient(),
            )
            assert service._reindex_lock.acquire(blocking=False)
            try:
                try:
                    service.reindex()
                except SemanticKnowledgeUnavailableError as exc:
                    assert exc.code == "semantic_reindex_in_progress"
                else:
                    raise AssertionError("Concurrent reindex should be rejected explicitly.")
            finally:
                service._reindex_lock.release()

            assert service.status()["status"] == "ready"

    def test_filters_by_project_source_type_and_path(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            library = KnowledgeLibraryService(settings)
            _write_document(
                settings.knowledge_dir / "projects" / "alpha-watchdog.md",
                title="Alpha service recovery",
                topic="runtime",
                project_id="alpha",
                body="# Recovery\n\nThe watchdog restores the backend after a failed heartbeat.",
            )
            _write_document(
                settings.knowledge_dir / "projects" / "beta-watchdog.md",
                title="Beta service recovery",
                topic="runtime",
                project_id="beta",
                body="# Recovery\n\nThe watchdog restores the backend after a failed heartbeat.",
            )
            service = SemanticKnowledgeIndexService(
                settings,
                library=library,
                embedding_client=FakeEmbeddingClient(),
            )
            service.reindex()

            matches = service.lookup(
                "who restores a service after a crash?",
                project_ids=("alpha",),
                source_types=("project_note",),
                document_paths=("projects/alpha-watchdog.md",),
            )

            assert len(matches) == 1
            assert matches[0].project_id == "alpha"
            assert matches[0].source_type == "project_note"

    def test_persistent_index_serves_semantic_results_after_service_restart(self) -> None:
        """SQLite ma zachować wektory między instancjami, nie tylko w pamięci procesu."""
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            library = KnowledgeLibraryService(settings)
            _write_document(
                settings.knowledge_dir / "runtime" / "recovery.md",
                title="Service restart recovery",
                topic="runtime",
                body="# Recovery\n\nThe watchdog restores a failed backend after its heartbeat is lost.",
            )
            client = FakeEmbeddingClient()
            initial = SemanticKnowledgeIndexService(settings, library=library, embedding_client=client)
            initial.reindex()
            initial_call_count = len(client.embed_calls)

            restarted = SemanticKnowledgeIndexService(settings, library=library, embedding_client=client)
            matches = restarted.lookup(
                "what handles service recovery after a restart?",
                max_results=1,
                document_paths=("runtime/recovery.md",),
            )

            assert settings.semantic_knowledge_index_path.exists()
            assert matches[0].title == "Service restart recovery"
            assert "semantic_similarity" in matches[0].match_reasons
            assert len(client.embed_calls) == initial_call_count + 1  # Only the query embedding is new.

    def test_discovers_repository_obsidian_and_runtime_sources_with_filters(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            repository_note = root / "docs" / "youtube-runtime.md"
            repository_note.parent.mkdir(parents=True)
            repository_note.write_text(
                "# YouTube connection recovery\n\nThe OAuth refresh token expired, so the connector is disconnected.",
                encoding="utf-8",
            )
            vault = root / "obsidian"
            vault.mkdir()
            (vault / "service-recovery.md").write_text(
                "---\nproject_id: alpha\n---\n# Service recovery\n\nThe watchdog restores the backend after a lost heartbeat.",
                encoding="utf-8",
            )
            validation = settings.data_dir / "validation"
            validation.mkdir(parents=True)
            (validation / "render-result.txt").write_text(
                "The renderer returned an MP4 with only 8 bytes. The invalid render blocks video generation.",
                encoding="utf-8",
            )
            (validation / "live-semantic-required-queries.json").write_text(
                json.dumps({"query": "service recovery", "matches": ["stale self-referential result"]}),
                encoding="utf-8",
            )
            library = KnowledgeLibraryService(settings)
            service = SemanticKnowledgeIndexService(
                settings,
                library=library,
                embedding_client=FakeEmbeddingClient(),
                source_catalog=SemanticKnowledgeSourceService(
                    settings,
                    library=library,
                    obsidian_path_resolver=lambda: vault,
                    include_extended_sources=True,
                ),
            )

            reindex = service.reindex()
            youtube = service.lookup(
                "why did the video platform stop working after credentials expired?",
                source_types=("project_documentation",),
                project_ids=("atnos",),
                max_results=1,
            )
            recovery = service.lookup(
                "who handles service recovery after a crash?",
                source_types=("obsidian_note",),
                project_ids=("alpha",),
                max_results=1,
            )
            render = service.lookup(
                "what is blocking video generation?",
                source_types=("test_result",),
                max_results=1,
            )

            assert reindex["refresh"]["catalog_document_count"] >= 6
            assert youtube[0].path == "repo/docs/youtube-runtime.md"
            assert youtube[0].citation.startswith("repo/docs/youtube-runtime.md:L")
            assert "authoritative_source" in youtube[0].match_reasons
            assert recovery[0].path == "obsidian/service-recovery.md"
            assert recovery[0].project_id == "alpha"
            assert render[0].path == "runtime/validation/render-result.txt"
            assert all("live-semantic-" not in match.path for match in youtube + recovery + render)

    def test_indexes_configured_learning_archive_without_duplicate_default_source(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            archive = root / "learning-archive"
            archive.mkdir()
            (archive / "quality-findings.md").write_text(
                "# Quality findings\n\nReference Shorts require topic-matched scenes and an immediate hook.",
                encoding="utf-8",
            )
            settings.learning_archive_dir = archive
            library = KnowledgeLibraryService(settings)
            source_catalog = SemanticKnowledgeSourceService(
                settings,
                library=library,
                include_extended_sources=True,
            )
            service = SemanticKnowledgeIndexService(
                settings,
                library=library,
                embedding_client=FakeEmbeddingClient(),
                source_catalog=source_catalog,
            )

            reindex = service.reindex()
            matches = service.lookup(
                "what visual quality did the Shorts research require?",
                source_types=("learning_result",),
                max_results=5,
            )

            assert reindex["refresh"]["catalog_document_count"] >= 1
            assert any(match.path == "runtime/learning/quality-findings.md" for match in matches)
            assert sum(
                root_path == str(archive.resolve())
                for root_path in reindex["refresh"]["catalog_roots_scanned"]
            ) == 1

            (archive / "quality-findings.md").unlink()
            removed = service.reindex()

            assert removed["refresh"]["removed_chunks"] >= 1
            assert not service.lookup(
                "what visual quality did the Shorts research require?",
                source_types=("learning_result",),
                document_paths=("runtime/learning/quality-findings.md",),
            )

            (archive / "quality-findings.md").write_text(
                "# Quality findings\n\nReference Shorts require topic-matched scenes and an immediate hook.",
                encoding="utf-8",
            )
            restored = service.reindex()
            restored_matches = service.lookup(
                "what visual quality did the Shorts research require?",
                source_types=("learning_result",),
                document_paths=("runtime/learning/quality-findings.md",),
            )

            assert restored["refresh"]["inserted_chunks"] == 0
            assert restored["refresh"]["reused_chunks"] >= 1
            assert restored_matches[0].path == "runtime/learning/quality-findings.md"

    def test_redacts_config_values_and_excludes_credential_files_before_persistence(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            docs = root / "docs"
            docs.mkdir()
            exposed_value = "super-secret-test-value"
            (docs / "runtime-config.json").write_text(
                json.dumps(
                    {
                        "service": "youtube",
                        "api_key": exposed_value,
                        "recovery": "watchdog restarts the service",
                    }
                ),
                encoding="utf-8",
            )
            (docs / "client_secret.json").write_text(
                json.dumps({"client_secret": "must-never-enter-index"}),
                encoding="utf-8",
            )
            library = KnowledgeLibraryService(settings)
            service = SemanticKnowledgeIndexService(
                settings,
                library=library,
                embedding_client=FakeEmbeddingClient(),
                source_catalog=SemanticKnowledgeSourceService(
                    settings,
                    library=library,
                    include_extended_sources=True,
                ),
            )

            result = service.reindex()
            stored = settings.semantic_knowledge_index_path.read_bytes()

            assert result["refresh"]["catalog_skipped_sensitive_files"] == 1
            assert exposed_value.encode() not in stored
            assert b"must-never-enter-index" not in stored
            assert b"[REDACTED]" in stored

    def test_removes_deleted_external_source_from_active_index(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            document = root / "docs" / "watchdog.md"
            document.parent.mkdir(parents=True)
            document.write_text(
                "# Watchdog\n\nThe watchdog restarts a failed local service.",
                encoding="utf-8",
            )
            library = KnowledgeLibraryService(settings)
            service = SemanticKnowledgeIndexService(
                settings,
                library=library,
                embedding_client=FakeEmbeddingClient(),
                source_catalog=SemanticKnowledgeSourceService(
                    settings,
                    library=library,
                    include_extended_sources=True,
                ),
            )
            service.reindex()

            document.unlink()
            removed = service.reindex()

            assert removed["refresh"]["removed_chunks"] >= 1
            assert not service.lookup(
                "who handles service recovery after a crash?",
                document_paths=("repo/docs/watchdog.md",),
            )
