from __future__ import annotations

"""Weryfikuje lokalny indeks semantyczny na prawdziwym modelu Ollamy.

Skrypt tworzy izolowany katalog walidacyjny, więc nie dotyka produkcyjnej
bazy ani właściwej biblioteki wiedzy Atnosa.
"""

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from atnos.core.config import Settings
from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService


INITIAL_BODY = (
    "When OAuth refresh credentials expire, the video connector must pause uploads "
    "until the account owner completes a new authorization flow."
)
UPDATED_BODY = (
    "The GPU resource coordinator unloads language models before image generation "
    "so the graphics backend receives enough available video memory."
)
SEMANTIC_QUERY = "What caused the channel delivery pipeline to halt after its access grant lapsed?"
UPDATED_QUERY = "How does the system free graphics memory before rendering images?"
DOCUMENT_PATH = "projects/wealth-blueprint-runtime.md"


def _settings(workspace: Path, model: str) -> Settings:
    data_dir = workspace / "data"
    settings = Settings(
        _env_file=None,
        app_env="test",
        app_root=workspace,
        data_dir=data_dir,
        uploads_dir=data_dir / "uploads",
        sqlite_file=data_dir / "atnos-live-semantic.db",
        knowledge_dir=workspace / "knowledge",
        semantic_knowledge_index_path=data_dir / "semantic-index.sqlite",
        semantic_knowledge_embedding_model=model,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_timeout_s=180.0,
        scheduled_tasks_enabled=False,
    )
    settings.ensure_directories()
    return settings


def _write_document(settings: Settings, body: str) -> Path:
    document = settings.knowledge_dir / DOCUMENT_PATH
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(
        "\n".join(
            (
                "---",
                "title: Wealth Blueprint runtime authorization",
                "topic: runtime",
                "summary: Controlled live semantic-memory validation document.",
                "tags:",
                "  - validation",
                "  - semantic-memory",
                "keywords:",
                "  - authorization recovery",
                "  - GPU resource coordination",
                "source_label: Atnos live semantic validation",
                "source_url: local://validation/wealth-blueprint-runtime",
                "source_type: project_note",
                "project_id: wealth-blueprint",
                "license: CC0-1.0",
                "updated_at: 2026-07-26",
                "---",
                "# Runtime rule",
                "",
                body,
                "",
            )
        ),
        encoding="utf-8",
    )
    return document


def _match_payload(match) -> dict[str, object]:
    payload = asdict(match)
    payload["match_reasons"] = list(match.match_reasons)
    return payload


def _probe(workspace: Path, model: str, query: str) -> dict[str, object]:
    settings = _settings(workspace, model)
    service = SemanticKnowledgeIndexService(settings)
    matches = service.lookup(
        query,
        max_results=1,
        document_paths=(DOCUMENT_PATH,),
        project_ids=("wealth-blueprint",),
    )
    if not matches or matches[0].path != DOCUMENT_PATH:
        raise RuntimeError("Fresh process did not retrieve the expected indexed document.")
    return {
        "status": "verified",
        "process_id": os.getpid(),
        "match": _match_payload(matches[0]),
        "index_status": service.status(),
    }


def _run_probe_process(workspace: Path, model: str, query: str) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--probe",
            "--workspace",
            str(workspace),
            "--model",
            model,
            "--query",
            query,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Restart probe failed. "
            f"exit_code={result.returncode}; stderr={result.stderr.strip()[:500]}"
        )
    return json.loads(result.stdout)


def _active_counts(index_path: Path, path: str) -> dict[str, int]:
    with sqlite3.connect(index_path) as connection:
        document = connection.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE path = ? AND active = 1",
            (path,),
        ).fetchone()
        chunks = connection.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE document_path = ? AND active = 1",
            (path,),
        ).fetchone()
        old_text = connection.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE document_path = ? AND text LIKE ?",
            (path, "%OAuth refresh credentials expire%"),
        ).fetchone()
    return {
        "active_documents": int(document[0] if document else 0),
        "active_chunks": int(chunks[0] if chunks else 0),
        "old_text_chunks": int(old_text[0] if old_text else 0),
    }


def run_verification(output_root: Path, model: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    workspace = (output_root / f"semantic-memory-live-{timestamp}").resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    settings = _settings(workspace, model)
    document = _write_document(settings, INITIAL_BODY)
    service = SemanticKnowledgeIndexService(settings)

    initial_reindex = service.reindex(force=True)
    initial_matches = service.lookup(
        SEMANTIC_QUERY,
        max_results=1,
        document_paths=(DOCUMENT_PATH,),
        project_ids=("wealth-blueprint",),
    )
    if not initial_matches or initial_matches[0].path != DOCUMENT_PATH:
        raise RuntimeError("Live semantic query did not retrieve the controlled source.")

    restart_probe = _run_probe_process(workspace, model, SEMANTIC_QUERY)

    _write_document(settings, UPDATED_BODY)
    updated_reindex = SemanticKnowledgeIndexService(settings).reindex()
    updated_matches = SemanticKnowledgeIndexService(settings).lookup(
        UPDATED_QUERY,
        max_results=1,
        document_paths=(DOCUMENT_PATH,),
        project_ids=("wealth-blueprint",),
    )
    changed_counts = _active_counts(settings.semantic_knowledge_index_path, DOCUMENT_PATH)
    if not updated_matches or updated_matches[0].path != DOCUMENT_PATH:
        raise RuntimeError("Updated semantic content was not retrieved.")
    if changed_counts["old_text_chunks"] != 0:
        raise RuntimeError("The stale semantic chunk survived a document update.")

    document.unlink()
    removed_reindex = SemanticKnowledgeIndexService(settings).reindex()
    removed_counts = _active_counts(settings.semantic_knowledge_index_path, DOCUMENT_PATH)
    if removed_counts["active_documents"] != 0 or removed_counts["active_chunks"] != 0:
        raise RuntimeError("Removed source remains active in the semantic index.")

    missing_model_settings = _settings(workspace / "negative-model", "atnos-model-that-does-not-exist")
    missing_model_status = SemanticKnowledgeIndexService(missing_model_settings).status()
    if missing_model_status["status"] != "blocked_technical":
        raise RuntimeError("Missing embedding model was not reported as blocked_technical.")

    report = {
        "status": "verified",
        "created_at": datetime.now(UTC).isoformat(),
        "model": model,
        "workspace": str(workspace),
        "index_path": str(settings.semantic_knowledge_index_path),
        "initial": {
            "reindex": initial_reindex,
            "query": SEMANTIC_QUERY,
            "match": _match_payload(initial_matches[0]),
        },
        "restart_probe": restart_probe,
        "updated_source": {
            "reindex": updated_reindex,
            "query": UPDATED_QUERY,
            "match": _match_payload(updated_matches[0]),
            "database_counts": changed_counts,
        },
        "removed_source": {
            "reindex": removed_reindex,
            "database_counts": removed_counts,
        },
        "negative_test": {
            "missing_model_status": missing_model_status,
        },
        "limitations": [
            "This validates retrieval and persistence on a controlled local corpus, not answer quality across the full production corpus.",
            "The script does not modify the production semantic index.",
        ],
    }
    report_path = workspace / "semantic-memory-live-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("data") / "validation")
    parser.add_argument("--model", default="bge-m3")
    parser.add_argument("--query", default=SEMANTIC_QUERY)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()

    if args.probe:
        if args.workspace is None:
            parser.error("--workspace is required with --probe")
        print(json.dumps(_probe(args.workspace.resolve(), args.model, args.query), ensure_ascii=False))
        return 0

    report_path = run_verification(args.output_root.resolve(), args.model)
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
