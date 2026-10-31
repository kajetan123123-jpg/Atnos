from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atnos.api.routes.memory import router
from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService


def service(path: Path):
    # No source catalog / embedding runtime is necessary for this read-only operation.
    instance = object.__new__(SemanticKnowledgeIndexService)
    instance.index_path = path
    return instance


def test_missing_index_remains_unknown_without_creating_it(tmp_path):
    path = tmp_path / "absent" / "index.sqlite3"
    result = service(path).catalog_snapshot()
    assert result["status"] == "missing"
    assert result["document_count"] is None
    assert not path.parent.exists()


def test_map_reads_only_active_metadata_and_preserves_total_when_clipped(tmp_path):
    path = tmp_path / "index.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE semantic_index_meta(key TEXT, value TEXT);
            INSERT INTO semantic_index_meta VALUES ('last_indexed_at', '2026-10-27T10:00:00Z');
            CREATE TABLE knowledge_documents(doc_id TEXT, path TEXT, title TEXT, topic TEXT,
                source_type TEXT, project_id TEXT, citation_path TEXT, source_mtime TEXT,
                indexed_at TEXT, active INTEGER);
            INSERT INTO knowledge_documents VALUES
                ('a','a.md','WB plan','WB','obsidian_note','wb','obsidian/a.md','2026-10-26','2026-10-27',1),
                ('b','b.md','Runtime','PC','project_documentation',NULL,'knowledge/b.md','2026-10-27','2026-10-27',1),
                ('c','c.md','Removed','PC','obsidian_note',NULL,'obsidian/c.md','2026-10-28','2026-10-27',0),
                ('d','d.md','Older WB plan','WB','obsidian_note','wb','obsidian/d.md','2026-10-25','2026-10-27',1);
        """)
    before = path.read_bytes()
    result = service(path).catalog_snapshot(limit=1)
    assert result["status"] == "indexed"
    assert result["document_count"] == 3
    assert result["source_counts"] == {"obsidian_note": 2, "project_documentation": 1}
    assert result["truncated"] is True
    assert result["documents"][0]["path"] == "a.md"
    assert result["last_indexed_at"] == "2026-10-27T10:00:00Z"
    assert path.read_bytes() == before
    # A large source must not crowd every other source out of the UI sample.
    assert [doc["path"] for doc in service(path).catalog_snapshot(limit=2)["documents"]] == ["a.md", "b.md"]
    filtered = service(path).catalog_snapshot(source_type="project_documentation")
    assert [doc["path"] for doc in filtered["documents"]] == ["b.md"]
    assert filtered["source_counts"] == result["source_counts"]
    assert filtered["truncated"] is False


def test_broken_index_is_not_reported_as_empty(tmp_path):
    path = tmp_path / "index.sqlite3"
    path.write_bytes(b"broken sqlite")
    assert service(path).catalog_snapshot()["status"] == "unavailable"
    assert service(path).catalog_snapshot()["document_count"] is None


def test_map_endpoint_uses_existing_runtime_service_and_validates_limit():
    app = FastAPI()
    app.include_router(router)
    calls = []
    app.state.semantic_knowledge_index = SimpleNamespace(catalog_snapshot=lambda **kw: calls.append(kw) or {"status": "indexed"})
    with TestClient(app) as client:
        assert client.get("/memory/index-map?limit=12").json() == {"status": "indexed"}
        assert client.get("/memory/index-map?limit=4&source_type=obsidian_note").status_code == 200
        assert client.get("/memory/index-map?limit=0").status_code == 422
        assert client.get("/memory/index-map?source_type=").status_code == 422
    assert calls == [{"limit": 12, "source_type": None}, {"limit": 4, "source_type": "obsidian_note"}]
