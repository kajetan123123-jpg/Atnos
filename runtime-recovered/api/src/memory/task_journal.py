from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TaskJournal:
    def __init__(self, data_dir: Path) -> None:
        self.memory_dir = (data_dir / "memory").resolve()
        self.log_path = (data_dir / "logs" / "agent_memory.jsonl").resolve()

    def write_note(self, category: str, filename: str, markdown: str) -> Path:
        target_dir = (self.memory_dir / category).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / filename).resolve()
        target.write_text(markdown, encoding="utf-8")
        return target

    def append_event(self, event: dict[str, Any]) -> Path:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": datetime.now(UTC).isoformat(), **event}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        return self.log_path

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        normalized_query = query.strip().lower()
        candidates = sorted(
            self.memory_dir.rglob("*.md"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ) if self.memory_dir.exists() else []

        results: list[dict[str, Any]] = []
        for path in candidates:
            content = path.read_text(encoding="utf-8", errors="replace")
            if normalized_query and normalized_query not in content.lower() and normalized_query not in path.name.lower():
                continue
            results.append(
                {
                    "path": str(path),
                    "title": _extract_title(content, path.stem),
                    "task_id": _extract_task_id(content),
                    "excerpt": _build_excerpt(content, normalized_query),
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                }
            )
            if len(results) >= limit:
                break
        return results


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _extract_task_id(content: str) -> str | None:
    for line in content.splitlines()[:20]:
        if line.startswith("task_id:"):
            return line.split(":", maxsplit=1)[1].strip().strip('"')
    return None


def _build_excerpt(content: str, query: str) -> str:
    normalized = " ".join(content.split())
    if not normalized:
        return ""
    if not query:
        return normalized[:300]
    index = normalized.lower().find(query)
    if index < 0:
        return normalized[:300]
    start = max(index - 120, 0)
    end = min(index + 180, len(normalized))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized) else ""
    return f"{prefix}{normalized[start:end]}{suffix}"
