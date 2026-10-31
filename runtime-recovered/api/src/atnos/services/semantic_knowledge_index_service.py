from __future__ import annotations

"""Lokalny, przyrostowy indeks semantyczny dla materiałów wiedzy Atnosa.

Indeks korzysta wyłącznie z lokalnego endpointu Ollamy. Brak modelu embeddingów
jest stanem technicznym, a nie pretekstem do udawania wyszukiwania semantycznego.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from contextlib import closing
import hashlib
import json
from math import sqrt
from pathlib import Path
import sqlite3
from threading import Lock
from time import perf_counter
from typing import TYPE_CHECKING, Protocol, Sequence

import httpx

from atnos.services.knowledge_library_service import KnowledgeLibraryService, TOKEN_ALIAS_GROUPS
from atnos.services.semantic_knowledge_source_service import (
    SemanticKnowledgeSourceDocument,
    SemanticKnowledgeSourceService,
)

if TYPE_CHECKING:
    from atnos.core.config import Settings


INDEX_VERSION = 2
MAX_CHUNK_CHARS = 1_100
MAX_QUERY_CHUNKS = 5_000
SOURCE_RANK_MULTIPLIERS = {
    "project_documentation": 1.08,
    "project_note": 1.05,
    "obsidian_note": 1.03,
    "knowledge_workspace": 1.02,
    "runtime_report": 1.0,
    "worker_result": 0.98,
    "learning_result": 0.96,
    "test_result": 0.90,
    "runtime_log": 0.86,
}


class SemanticKnowledgeUnavailableError(RuntimeError):
    """Zwracany, gdy lokalny model embeddingów nie jest gotowy do użycia."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EmbeddingClient(Protocol):
    """Minimalny kontrakt pozwalający testować indeks bez uruchamiania Ollamy."""

    def status(self, model: str) -> dict[str, object]: ...

    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]: ...


class OllamaEmbeddingClient:
    """Adapter `/api/embed` dla lokalnej instancji Ollamy."""

    def __init__(self, base_url: str, *, timeout_s: float, keep_alive: int | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive

    def status(self, model: str) -> dict[str, object]:
        try:
            with httpx.Client(timeout=min(self.timeout_s, 5.0)) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SemanticKnowledgeUnavailableError(
                "ollama_unreachable",
                "Lokalny runtime Ollamy nie odpowiada; indeks semantyczny nie został uruchomiony.",
            ) from exc

        payload = response.json()
        models = [item for item in payload.get("models") or [] if isinstance(item, dict)]
        installed = [str(item.get("name") or "") for item in models]
        normalized = {name.split(":", 1)[0] for name in installed}
        if model not in installed and model.split(":", 1)[0] not in normalized:
            raise SemanticKnowledgeUnavailableError(
                "embedding_model_missing",
                f"Brakuje lokalnego modelu embeddingów `{model}`. Zainstaluj go w Ollamie przed indeksowaniem.",
            )
        selected = next(
            (
                item
                for item in models
                if str(item.get("name") or "") == model
                or str(item.get("name") or "").split(":", 1)[0] == model.split(":", 1)[0]
            ),
            {},
        )
        details = selected.get("details") if isinstance(selected.get("details"), dict) else {}
        return {
            "runtime": "ollama",
            "model": model,
            "installed_models": installed,
            "model_metadata": {
                "resolved_name": str(selected.get("name") or model),
                "digest": str(selected.get("digest") or "") or None,
                "size_bytes": int(selected["size"]) if isinstance(selected.get("size"), int | float) else None,
                "parameter_size": str(details.get("parameter_size") or "") or None,
                "quantization_level": str(details.get("quantization_level") or "") or None,
            },
        }

    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        if not texts:
            return []
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": model, "input": list(texts),
                          **({"keep_alive": self.keep_alive} if self.keep_alive is not None else {})},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SemanticKnowledgeUnavailableError(
                "embedding_request_failed",
                "Ollama nie wygenerowała embeddingów dla lokalnego indeksu wiedzy.",
            ) from exc

        payload = response.json()
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise SemanticKnowledgeUnavailableError(
                "embedding_response_invalid",
                "Ollama zwróciła nieprawidłową odpowiedź embeddingów.",
            )
        vectors: list[list[float]] = []
        for raw_vector in embeddings:
            if not isinstance(raw_vector, list) or not raw_vector:
                raise SemanticKnowledgeUnavailableError(
                    "embedding_response_invalid",
                    "Ollama zwróciła pusty wektor embeddingów.",
                )
            try:
                vectors.append([float(value) for value in raw_vector])
            except (TypeError, ValueError) as exc:
                raise SemanticKnowledgeUnavailableError(
                    "embedding_response_invalid",
                    "Ollama zwróciła wektor embeddingów o niepoprawnym formacie.",
                ) from exc
        return vectors


@dataclass(frozen=True, slots=True)
class SemanticKnowledgeMatch:
    doc_id: str
    path: str
    title: str
    topic: str
    chunk_id: str
    text: str
    source_label: str
    source_url: str | None
    source_updated_at: str
    source_mtime: str
    source_type: str
    project_id: str | None
    citation: str
    line_start: int
    line_end: int
    score: float
    semantic_score: float
    lexical_score: float
    exact_score: float
    match_reasons: tuple[str, ...]


class SemanticKnowledgeIndexService:
    """Przechowuje fragmenty Markdown i wektory lokalnie w SQLite."""

    def __init__(
        self,
        settings: "Settings",
        *,
        library: KnowledgeLibraryService | None = None,
        embedding_client: EmbeddingClient | None = None,
        source_catalog: SemanticKnowledgeSourceService | None = None,
    ) -> None:
        self.settings = settings
        self.library = library or KnowledgeLibraryService(settings)
        self.index_path = settings.semantic_knowledge_index_path
        self.embedding_model = settings.semantic_knowledge_embedding_model
        self.embedding_client = embedding_client or OllamaEmbeddingClient(
            settings.ollama_base_url,
            timeout_s=settings.ollama_timeout_s,
        )
        self.source_catalog = source_catalog or SemanticKnowledgeSourceService(settings, library=self.library)
        self._reindex_lock = Lock()

    def catalog_snapshot(self, *, limit: int = 80, source_type: str | None = None) -> dict[str, object]:
        """Read the persisted index for the UI, without scanning sources or contacting Ollama."""
        empty: dict[str, object] = {
            "status": "missing", "document_count": None, "source_counts": {},
            "documents": [], "last_indexed_at": None,
            "source": "semantic_knowledge_index", "truncated": False,
        }
        if not self.index_path.is_file():
            return empty
        try:
            with closing(sqlite3.connect(f"{self.index_path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)) as connection:
                connection.row_factory = sqlite3.Row
                # Counts, metadata and the sample must describe the same persisted
                # snapshot, even if a normal reindex commits during this request.
                connection.execute("BEGIN")
                counts = connection.execute(
                    "SELECT source_type, COUNT(*) AS count FROM knowledge_documents WHERE active = 1 GROUP BY source_type"
                ).fetchall()
                rows = connection.execute(
                    "WITH ranked AS (SELECT doc_id, path, title, topic, source_type, project_id, citation_path, "
                    "source_mtime, indexed_at, ROW_NUMBER() OVER (PARTITION BY source_type "
                    "ORDER BY source_mtime DESC, path) AS source_rank FROM knowledge_documents "
                    "WHERE active = 1 AND (? IS NULL OR source_type = ?)) "
                    "SELECT doc_id, path, title, topic, source_type, project_id, citation_path, source_mtime, indexed_at "
                    "FROM ranked ORDER BY source_rank, source_type, path LIMIT ?",
                    (source_type, source_type, max(1, min(int(limit), 120))),
                ).fetchall()
                meta = self._read_meta(connection)
            total = sum(int(row["count"]) for row in counts)
            matching = sum(int(row["count"]) for row in counts if source_type is None or row["source_type"] == source_type)
            return {
                **empty, "status": "indexed", "document_count": total,
                "source_counts": {str(row["source_type"]): int(row["count"]) for row in counts},
                "documents": [dict(row) for row in rows],
                "last_indexed_at": meta.get("last_indexed_at"), "truncated": matching > len(rows),
            }
        except (sqlite3.Error, OSError):
            # An unreadable/migrating index is unknown, never a fabricated empty memory.
            return {**empty, "status": "unavailable"}

    def status(self) -> dict[str, object]:
        self._initialize_schema()
        try:
            runtime = self.embedding_client.status(self.embedding_model)
            readiness = "ready"
            blocked_reason = None
        except SemanticKnowledgeUnavailableError as exc:
            runtime = {"runtime": "ollama", "model": self.embedding_model}
            readiness = "blocked_technical"
            blocked_reason = {"code": exc.code, "message": exc.message}
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM knowledge_chunks WHERE active = 1").fetchone()
            meta = self._read_meta(connection)
        return {
            "status": readiness,
            "index_version": INDEX_VERSION,
            "index_path": str(self.index_path),
            "embedding_model": self.embedding_model,
            "indexed_embedding_model": meta.get("embedding_model"),
            "embedding_dimension": self._as_int(meta.get("embedding_dimension")),
            "chunk_count": int(row["count"] if row else 0),
            "last_indexed_at": meta.get("last_indexed_at"),
            "last_index_duration_ms": self._as_float(meta.get("last_index_duration_ms")),
            "last_query_duration_ms": self._as_float(meta.get("last_query_duration_ms")),
            "language_support": {
                "declared": ["Polish", "English"],
                "quality_status": "not_benchmarked_by_runtime",
                "limitation": "Validate retrieval quality against the project corpus before relying on it for a workflow.",
            },
            "hardware_requirements": {
                "runtime": "local_ollama",
                "network_after_model_install": False,
                "accelerator": "selected by the local Ollama runtime; not inferred from model tags",
            },
            "blocked_reason": blocked_reason,
            "runtime": runtime,
        }

    def reindex(self, *, force: bool = False) -> dict[str, object]:
        """Synchronizuje tylko zmienione dokumenty; zmiana modelu wymusza re-embedding."""
        if not self._reindex_lock.acquire(blocking=False):
            raise SemanticKnowledgeUnavailableError(
                "semantic_reindex_in_progress",
                "Indeks wiedzy jest już przebudowywany. Poczekaj na zakończenie bieżącej operacji.",
            )
        try:
            return self._reindex(force=force)
        finally:
            self._reindex_lock.release()

    def _reindex(self, *, force: bool = False) -> dict[str, object]:
        started_at = perf_counter()
        self.embedding_client.status(self.embedding_model)
        self._initialize_schema()
        catalog = self.source_catalog.discover()
        documents = catalog.documents
        now = datetime.now(UTC).isoformat()
        inserted_chunks = 0
        reused_chunks = 0
        removed_chunks = 0
        embedding_dimension = 0

        with closing(self._connect()) as connection:
            meta = self._read_meta(connection)
            model_changed = meta.get("embedding_model") not in {None, self.embedding_model}
            effective_force = force or model_changed
            active_doc_paths: set[str] = set()

            for document in documents:
                active_doc_paths.add(document.path)
                existing = connection.execute(
                    "SELECT source_sha256, embedding_model, active FROM knowledge_documents WHERE path = ?",
                    (document.path,),
                ).fetchone()
                unchanged = (
                    existing is not None
                    and str(existing["source_sha256"]) == document.source_sha256
                    and str(existing["embedding_model"]) == self.embedding_model
                    and not effective_force
                )
                if unchanged:
                    # A source can disappear for one refresh and later return with
                    # identical content. Its hash is unchanged, but the previous
                    # stale-source pass marked both rows inactive.
                    connection.execute(
                        "UPDATE knowledge_documents SET active = 1 WHERE path = ?",
                        (document.path,),
                    )
                    connection.execute(
                        "UPDATE knowledge_chunks SET active = 1 WHERE document_path = ?",
                        (document.path,),
                    )
                    reused_chunks += int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM knowledge_chunks WHERE document_path = ? AND active = 1",
                            (document.path,),
                        ).fetchone()["count"]
                    )
                    continue

                chunks = self._build_document_chunks(document)
                vectors = self.embedding_client.embed(
                    [self._embedding_input(document, chunk["text"]) for chunk in chunks],
                    self.embedding_model,
                )
                if len(chunks) != len(vectors):
                    raise SemanticKnowledgeUnavailableError(
                        "embedding_count_mismatch",
                        "Liczba embeddingów nie odpowiada liczbie fragmentów dokumentu.",
                    )
                if vectors and any(len(vector) != len(vectors[0]) for vector in vectors):
                    raise SemanticKnowledgeUnavailableError(
                        "embedding_dimension_mismatch",
                        "Ollama zwróciła embeddingi o niespójnych wymiarach.",
                    )
                if vectors:
                    embedding_dimension = len(vectors[0])

                connection.execute("DELETE FROM knowledge_chunks WHERE document_path = ?", (document.path,))
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        path, doc_id, title, topic, source_label, source_url, source_updated_at, source_mtime,
                        source_type, project_id, citation_path,
                        source_sha256, embedding_model, indexed_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(path) DO UPDATE SET
                        doc_id = excluded.doc_id,
                        title = excluded.title,
                        topic = excluded.topic,
                        source_label = excluded.source_label,
                        source_url = excluded.source_url,
                        source_updated_at = excluded.source_updated_at,
                        source_mtime = excluded.source_mtime,
                        source_type = excluded.source_type,
                        project_id = excluded.project_id,
                        citation_path = excluded.citation_path,
                        source_sha256 = excluded.source_sha256,
                        embedding_model = excluded.embedding_model,
                        indexed_at = excluded.indexed_at,
                        active = 1
                    """,
                    (
                        document.path,
                        document.doc_id,
                        document.title,
                        document.topic,
                        document.source_label,
                        document.source_url,
                        document.updated_at,
                        document.source_mtime,
                        document.source_type,
                        document.project_id,
                        document.citation_path,
                        document.source_sha256,
                        self.embedding_model,
                        now,
                    ),
                )
                for chunk, vector in zip(chunks, vectors, strict=True):
                    connection.execute(
                        """
                        INSERT INTO knowledge_chunks (
                            chunk_id, document_path, text, line_start, line_end, content_sha256,
                            embedding_json, embedding_dimension, active, indexed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            chunk["chunk_id"],
                            document.path,
                            chunk["text"],
                            chunk["line_start"],
                            chunk["line_end"],
                            chunk["content_sha256"],
                            json.dumps(vector, separators=(",", ":")),
                            len(vector),
                            now,
                        ),
                    )
                    inserted_chunks += 1

            stale_rows = connection.execute("SELECT path FROM knowledge_documents WHERE active = 1").fetchall()
            for stale in stale_rows:
                path = str(stale["path"])
                if path in active_doc_paths:
                    continue
                removed_chunks += int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM knowledge_chunks WHERE document_path = ? AND active = 1",
                        (path,),
                    ).fetchone()["count"]
                )
                connection.execute("UPDATE knowledge_documents SET active = 0 WHERE path = ?", (path,))
                connection.execute("UPDATE knowledge_chunks SET active = 0 WHERE document_path = ?", (path,))

            self._write_meta(connection, "embedding_model", self.embedding_model)
            if embedding_dimension:
                self._write_meta(connection, "embedding_dimension", str(embedding_dimension))
            self._write_meta(connection, "last_indexed_at", now)
            self._write_meta(connection, "last_index_duration_ms", str(round((perf_counter() - started_at) * 1_000, 3)))
            connection.commit()

        return {
            **self.status(),
            "refresh": {
                "force": force,
                "model_changed": model_changed,
                "inserted_chunks": inserted_chunks,
                "reused_chunks": reused_chunks,
                "removed_chunks": removed_chunks,
                "catalog_document_count": catalog.document_count,
                "catalog_skipped_files": catalog.skipped_files,
                "catalog_skipped_sensitive_files": catalog.skipped_sensitive_files,
                "catalog_roots_scanned": list(catalog.roots_scanned),
                "indexed_at": now,
            },
        }

    def lookup(
        self,
        query: str,
        *,
        max_results: int = 5,
        topics: tuple[str, ...] = (),
        document_paths: tuple[str, ...] = (),
        source_types: tuple[str, ...] = (),
        project_ids: tuple[str, ...] = (),
        modified_after: str | None = None,
        modified_before: str | None = None,
    ) -> list[SemanticKnowledgeMatch]:
        """Łączy kosinus embeddingów z wynikami słów kluczowych i dopasowania dokładnego."""
        started_at = perf_counter()
        normalized_query = query.strip()
        if not normalized_query:
            return []
        self.embedding_client.status(self.embedding_model)
        self._initialize_schema()
        query_vector = self.embedding_client.embed([normalized_query], self.embedding_model)[0]
        query_tokens = KnowledgeLibraryService._tokenize(normalized_query)
        normalized_topics = {item.strip().lower() for item in topics if item.strip()}
        normalized_paths = {item.strip().replace("\\", "/") for item in document_paths if item.strip()}
        normalized_source_types = {item.strip().lower() for item in source_types if item.strip()}
        normalized_project_ids = {item.strip() for item in project_ids if item.strip()}

        # Scope the persisted source set before spending the chunk budget.
        # Filtering after LIMIT can hide a requested project/document behind
        # unrelated rows, even when the requested source is indexed correctly.
        predicates = ["chunk.active = 1", "document.active = 1", "document.embedding_model = ?"]
        parameters: list[object] = [self.embedding_model]
        for column, values in (
            ("atnos_lower(document.topic)", normalized_topics),
            ("document.path", normalized_paths),
            ("atnos_lower(document.source_type)", normalized_source_types),
            ("document.project_id", normalized_project_ids),
        ):
            if values:
                predicates.append(f"{column} IN ({','.join('?' for _ in values)})")
                parameters.extend(sorted(values))
        for boundary, operator in ((modified_after, ">="), (modified_before, "<=")):
            if boundary:
                # Preserve the existing unknown-date behavior; no fabricated date.
                predicates.append(f"(COALESCE(document.source_mtime, '') = '' OR document.source_mtime {operator} ?)")
                parameters.append(boundary)

        with closing(self._connect()) as connection:
            # SQLite LOWER is ASCII-only; retain Python's previous Unicode matching.
            connection.create_function("atnos_lower", 1, lambda value: str(value or "").lower(), deterministic=True)
            rows = connection.execute(
                """
                SELECT chunk.chunk_id, chunk.document_path, chunk.text, chunk.line_start, chunk.line_end,
                       chunk.embedding_json, document.doc_id, document.title, document.topic,
                       document.source_label, document.source_url, document.source_updated_at,
                       document.source_mtime, document.source_type, document.project_id,
                       document.citation_path
                  FROM knowledge_chunks AS chunk
                  JOIN knowledge_documents AS document ON document.path = chunk.document_path
                 WHERE """ + " AND ".join(predicates) + " LIMIT ?",
                (*parameters, MAX_QUERY_CHUNKS),
            ).fetchall()
            has_indexed_chunks = bool(rows) or connection.execute(
                "SELECT 1 FROM knowledge_chunks AS chunk JOIN knowledge_documents AS document "
                "ON document.path = chunk.document_path WHERE chunk.active=1 AND document.active=1 "
                "AND document.embedding_model=? LIMIT 1", (self.embedding_model,),
            ).fetchone() is not None
        if not rows:
            if has_indexed_chunks:
                return []
            raise SemanticKnowledgeUnavailableError(
                "semantic_index_empty",
                "Indeks semantyczny nie zawiera fragmentów dla aktywnego modelu. Uruchom ponowne indeksowanie.",
            )

        matches: list[SemanticKnowledgeMatch] = []
        normalized_query_text = KnowledgeLibraryService._normalize_token(normalized_query)
        for row in rows:
            topic = str(row["topic"] or "")
            path = str(row["document_path"])
            if normalized_topics and topic.lower() not in normalized_topics:
                continue
            if normalized_paths and path not in normalized_paths:
                continue
            source_type = str(row["source_type"] or "")
            project_id = str(row["project_id"]) if row["project_id"] else None
            source_mtime = str(row["source_mtime"] or "")
            if normalized_source_types and source_type.lower() not in normalized_source_types:
                continue
            if normalized_project_ids and project_id not in normalized_project_ids:
                continue
            if modified_after and source_mtime and source_mtime < modified_after:
                continue
            if modified_before and source_mtime and source_mtime > modified_before:
                continue
            vector = self._parse_vector(str(row["embedding_json"]))
            semantic_score = max(0.0, self._cosine_similarity(query_vector, vector))
            text = str(row["text"])
            title = str(row["title"])
            lexical_score = self._lexical_score(query_tokens, text=text, title=title)
            title_score = self._lexical_score(query_tokens, text="", title=title)
            exact_score = 1.0 if normalized_query_text and normalized_query_text in KnowledgeLibraryService._normalize_token(
                f"{title} {text}"
            ) else 0.0
            base_score = (
                (semantic_score * 0.60)
                + (lexical_score * 0.25)
                + (title_score * 0.10)
                + (exact_score * 0.05)
            )
            score = base_score * SOURCE_RANK_MULTIPLIERS.get(source_type.casefold(), 1.0)
            if score <= 0:
                continue
            reasons: list[str] = []
            if semantic_score >= 0.55:
                reasons.append("semantic_similarity")
            if lexical_score > 0:
                reasons.append("lexical_overlap")
            if title_score > 0:
                reasons.append("title_overlap")
            if exact_score:
                reasons.append("exact_phrase")
            if SOURCE_RANK_MULTIPLIERS.get(source_type.casefold(), 1.0) > 1.0:
                reasons.append("authoritative_source")
            line_start = int(row["line_start"])
            line_end = int(row["line_end"])
            source_label = str(row["source_label"] or "local knowledge document")
            matches.append(
                SemanticKnowledgeMatch(
                    doc_id=str(row["doc_id"]),
                    path=path,
                    title=str(row["title"]),
                    topic=topic,
                    chunk_id=str(row["chunk_id"]),
                    text=text,
                    source_label=source_label,
                    source_url=str(row["source_url"]) if row["source_url"] else None,
                    source_updated_at=str(row["source_updated_at"] or ""),
                    source_mtime=source_mtime,
                    source_type=source_type,
                    project_id=project_id,
                    citation=f"{str(row['citation_path'] or path)}:L{line_start}-L{line_end} ({source_label})",
                    line_start=line_start,
                    line_end=line_end,
                    score=round(score, 6),
                    semantic_score=round(semantic_score, 6),
                    lexical_score=round(lexical_score, 6),
                    exact_score=exact_score,
                    match_reasons=tuple(reasons or ["low_confidence_similarity"]),
                )
            )
        result = sorted(matches, key=lambda item: (-item.score, item.path, item.line_start))[:max_results]
        with closing(self._connect()) as connection:
            self._write_meta(connection, "last_query_duration_ms", str(round((perf_counter() - started_at) * 1_000, 3)))
            connection.commit()
        return result

    def _initialize_schema(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS semantic_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    path TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    source_url TEXT,
                    source_updated_at TEXT NOT NULL,
                    source_mtime TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'markdown',
                    project_id TEXT,
                    citation_path TEXT NOT NULL DEFAULT '',
                    source_sha256 TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_path TEXT NOT NULL,
                    text TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    embedding_dimension INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    indexed_at TEXT NOT NULL,
                    FOREIGN KEY(document_path) REFERENCES knowledge_documents(path)
                );
                CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_document_active
                    ON knowledge_chunks(document_path, active);
                CREATE INDEX IF NOT EXISTS ix_knowledge_documents_model_active
                    ON knowledge_documents(embedding_model, active);
                """
            )
            self._ensure_column(connection, "knowledge_documents", "source_mtime", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "knowledge_documents", "source_type", "TEXT NOT NULL DEFAULT 'markdown'")
            self._ensure_column(connection, "knowledge_documents", "project_id", "TEXT")
            self._ensure_column(connection, "knowledge_documents", "citation_path", "TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "UPDATE knowledge_documents SET citation_path = 'knowledge/' || path WHERE citation_path = ''"
            )
            self._write_meta(connection, "index_version", str(INDEX_VERSION))
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @staticmethod
    def _read_meta(connection: sqlite3.Connection) -> dict[str, str]:
        rows = connection.execute("SELECT key, value FROM semantic_index_meta").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    @staticmethod
    def _write_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO semantic_index_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _build_document_chunks(self, document: SemanticKnowledgeSourceDocument) -> list[dict[str, object]]:
        raw = document.raw_text
        body, body_line_start = KnowledgeLibraryService._document_body_with_line_offset(raw)
        blocks = self._structured_blocks(body, body_line_start=body_line_start)
        chunks: list[dict[str, object]] = []
        collected: list[str] = []
        line_start = 0
        line_end = 0
        section = ""

        def flush() -> None:
            if not collected:
                return
            text = "\n".join(part for part in ([section] if section else []) + collected).strip()
            if not text:
                return
            content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunks.append(
                {
                    "chunk_id": f"{document.doc_id}:{line_start}-{line_end}:{content_sha256[:12]}",
                    "text": text,
                    "line_start": line_start,
                    "line_end": line_end,
                    "content_sha256": content_sha256,
                }
            )

        for block, start, end, heading in blocks:
            if heading:
                section = heading
            projected = len("\n".join(collected + [block])) + len(section)
            if collected and projected > MAX_CHUNK_CHARS:
                flush()
                collected = []
                line_start = 0
                line_end = 0
            if not collected:
                line_start = start
            collected.append(block)
            line_end = end
        flush()
        return chunks

    @staticmethod
    def _structured_blocks(body: str, *, body_line_start: int) -> list[tuple[str, int, int, str | None]]:
        """Dzieli Markdown po nagłówkach i akapitach, nie rozrywając bloków kodu."""
        blocks: list[tuple[str, int, int, str | None]] = []
        collected: list[str] = []
        start = body_line_start
        active_heading: str | None = None
        in_code_block = False

        def flush(end: int) -> None:
            nonlocal collected, active_heading
            text = "\n".join(collected).strip()
            if text:
                blocks.append((text, start, end, active_heading))
            collected = []
            active_heading = None

        for line_number, line in enumerate(body.splitlines(), start=body_line_start):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
            if not in_code_block and stripped.startswith("#") and stripped.lstrip("#").startswith(" "):
                flush(line_number - 1)
                active_heading = stripped.lstrip("#").strip()
                start = line_number
                collected = [stripped]
                continue
            if not in_code_block and not stripped:
                flush(line_number - 1)
                start = line_number + 1
                continue
            if not collected:
                start = line_number
            collected.append(line)
        flush(body_line_start + len(body.splitlines()) - 1)
        return blocks

    @staticmethod
    def _embedding_input(document: SemanticKnowledgeSourceDocument, chunk_text: str) -> str:
        return f"Title: {document.title}\nTopic: {document.topic}\n{chunk_text}"

    @staticmethod
    def _parse_vector(raw: str) -> list[float]:
        try:
            parsed = json.loads(raw)
            return [float(value) for value in parsed]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SemanticKnowledgeUnavailableError(
                "stored_embedding_invalid",
                "Lokalny indeks zawiera nieprawidłowy wektor i wymaga ponownego indeksowania.",
            ) from exc

    @staticmethod
    def _as_int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    @staticmethod
    def _as_float(value: str | None) -> float | None:
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    @staticmethod
    def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left or not right:
            raise SemanticKnowledgeUnavailableError(
                "embedding_dimension_mismatch",
                "Wektor zapytania nie pasuje do modelu użytego do indeksu. Uruchom ponowne indeksowanie.",
            )
        denominator = sqrt(sum(value * value for value in left)) * sqrt(sum(value * value for value in right))
        return sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0.0

    @staticmethod
    def _lexical_score(query_tokens: set[str], *, text: str, title: str) -> float:
        if not query_tokens:
            return 0.0
        target_tokens = KnowledgeLibraryService._tokenize(f"{title} {text}")
        concepts: list[set[str]] = []
        grouped_tokens: set[str] = set()
        for alias_group in TOKEN_ALIAS_GROUPS:
            group = set(alias_group)
            if query_tokens & group:
                concepts.append(group)
                grouped_tokens.update(group)
        concepts.extend({token} for token in query_tokens - grouped_tokens)
        if not concepts:
            return 0.0
        matched = sum(1 for concept in concepts if concept & target_tokens)
        return matched / len(concepts)
