from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from atnos.core.config import Settings

from memory.obsidian_writer import ObsidianWriter


MAX_RECORDS_IN_DASHBOARD = 40
MEMORY_INDEX_DIR = "Atnos/Memory"
MEMORY_INDEX_FILENAME = "_Atnos Memory Index.md"


@dataclass(frozen=True, slots=True)
class ObsidianMemoryRecord:
    memory_id: str
    task_id: str | None
    domain: str
    source: str
    summary: str
    tags: tuple[str, ...]
    priority: str
    local_path: str | None
    obsidian_path: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "task_id": self.task_id,
            "domain": self.domain,
            "source": self.source,
            "summary": self.summary,
            "tags": list(self.tags),
            "priority": self.priority,
            "local_path": self.local_path,
            "obsidian_path": self.obsidian_path,
            "created_at": self.created_at,
        }


class ObsidianMemoryBridge:
    """Keeps Obsidian useful for humans and compactly searchable for Atnos.

    Markdown notes stay human-readable in the vault. This bridge also writes a
    sanitized JSONL index on F: so the agent can retrieve only compact memory
    facts instead of re-reading large notes or raw logs.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.writer = ObsidianWriter(self.settings)
        self.index_path = (self.settings.data_dir / "memory" / "obsidian_memory_index.jsonl").resolve()
        self.snapshot_path = (self.settings.data_dir / "memory" / "obsidian_memory_snapshot.json").resolve()

    def record(
        self,
        *,
        domain: str,
        source: str,
        summary: Any,
        task_id: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        priority: str = "medium",
        local_path: str | None = None,
        obsidian_path: str | None = None,
    ) -> ObsidianMemoryRecord:
        from memory.agent_memory import redact_sensitive

        safe_summary = _compact_text(redact_sensitive(summary), limit=700)
        safe_tags = tuple(_safe_tag(item) for item in (tags or []) if _safe_tag(item))
        created_at = datetime.now(UTC).isoformat()
        record = ObsidianMemoryRecord(
            memory_id=f"mem_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{_short_hash(safe_summary)}",
            task_id=task_id,
            domain=_safe_tag(domain) or "operational_fact",
            source=_safe_tag(source) or "unknown",
            summary=safe_summary,
            tags=safe_tags,
            priority=_safe_tag(priority) or "medium",
            local_path=local_path,
            obsidian_path=obsidian_path,
            created_at=created_at,
        )
        self._append_record(record)
        self._write_snapshot()
        self._write_obsidian_dashboard()
        return record

    def search(self, query: str, *, limit: int = 8, domains: set[str] | None = None) -> list[dict[str, Any]]:
        normalized_query = _normalize(query)
        domain_filter = {_safe_tag(item) for item in domains} if domains else None
        records = self._load_records()
        scored: list[tuple[int, ObsidianMemoryRecord]] = []
        for record in records:
            if domain_filter is not None and record.domain not in domain_filter:
                continue
            haystack = _normalize(
                " ".join(
                    [
                        record.summary,
                        record.domain,
                        record.source,
                        " ".join(record.tags),
                        record.task_id or "",
                    ]
                )
            )
            score = _score_match(normalized_query, haystack)
            if score > 0 or not normalized_query:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [record.to_dict() for _, record in scored[:limit]]

    def _append_record(self, record: ObsidianMemoryRecord) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")

    def _load_records(self) -> list[ObsidianMemoryRecord]:
        if not self.index_path.exists():
            return []
        records: list[ObsidianMemoryRecord] = []
        for line in self.index_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                records.append(
                    ObsidianMemoryRecord(
                        memory_id=str(raw.get("memory_id") or ""),
                        task_id=raw.get("task_id"),
                        domain=str(raw.get("domain") or "operational_fact"),
                        source=str(raw.get("source") or "unknown"),
                        summary=str(raw.get("summary") or ""),
                        tags=tuple(str(item) for item in raw.get("tags") or []),
                        priority=str(raw.get("priority") or "medium"),
                        local_path=raw.get("local_path"),
                        obsidian_path=raw.get("obsidian_path"),
                        created_at=str(raw.get("created_at") or ""),
                    )
                )
            except (TypeError, json.JSONDecodeError):
                continue
        return records

    def _write_snapshot(self) -> None:
        records = self._load_records()
        latest = sorted(records, key=lambda item: item.created_at, reverse=True)[:MAX_RECORDS_IN_DASHBOARD]
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "record_count": len(records),
            "latest": [record.to_dict() for record in latest],
        }
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_obsidian_dashboard(self) -> None:
        records = sorted(self._load_records(), key=lambda item: item.created_at, reverse=True)[:MAX_RECORDS_IN_DASHBOARD]
        markdown = self._format_dashboard(records)
        self.writer.write_note(MEMORY_INDEX_DIR, MEMORY_INDEX_FILENAME, markdown)

    def _format_dashboard(self, records: list[ObsidianMemoryRecord]) -> str:
        lines = [
            "---",
            'type: "atnos_memory_index"',
            f'updated_at: "{datetime.now(UTC).isoformat()}"',
            "---",
            "",
            "# Atnos Memory Index",
            "",
            "This note is a human-readable dashboard. Atnos reads the compact JSONL index on F:, not raw secrets or noisy logs.",
            "",
            "## Rules",
            "- Keep decisions, goals, preferences, task state, and useful operational facts.",
            "- Do not store API keys, passwords, OAuth tokens, private keys, bank details, or recovery codes.",
            "- Prefer compact summaries over raw terminal output.",
            "",
            "## Recent Memory",
        ]
        if not records:
            lines.append("_No memory records yet._")
            return "\n".join(lines) + "\n"
        for record in records:
            tags = ", ".join(f"`{tag}`" for tag in record.tags) if record.tags else "_none_"
            link = _obsidian_link(record.obsidian_path)
            lines.extend(
                [
                    f"### {record.domain} | {record.priority}",
                    f"- created: `{record.created_at}`",
                    f"- source: `{record.source}`",
                    f"- task_id: `{record.task_id or 'none'}`",
                    f"- tags: {tags}",
                    f"- note: {link}",
                    f"- summary: {record.summary}",
                    "",
                ]
            )
        return "\n".join(lines)


def _compact_text(value: Any, *, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _safe_tag(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-._")


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _score_match(query: str, haystack: str) -> int:
    if not query:
        return 1
    if query in haystack:
        return 100 + len(query)
    score = 0
    for token in query.split():
        if len(token) >= 3 and token in haystack:
            score += 10
    return score


def _short_hash(value: str) -> str:
    total = 0
    for char in value:
        total = (total * 33 + ord(char)) % 0xFFFFFFFF
    return f"{total:08x}"


def _obsidian_link(path: str | None) -> str:
    if not path:
        return "_not written_"
    return f"`{path}`"
