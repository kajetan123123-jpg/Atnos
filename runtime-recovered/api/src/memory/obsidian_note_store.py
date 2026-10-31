from __future__ import annotations

import hashlib
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter

from atnos.core.config import Settings


_CACHE_TTL_SECONDS = 30.0
_READ_CACHE: dict[tuple[str, str | None, str | None, str | None, int], tuple[float, list["NoteSchema"]]] = {}


@dataclass(frozen=True, slots=True)
class NoteSchema:
    """Standardowy schemat notatki Obsidian czytelnej dla Atnosa i Dataview."""

    title: str
    agent: str
    type: str
    created_at: str
    expires_at: str | None = None
    tags: list[str] = field(default_factory=list)
    status: str = "pending"
    linked_notes: list[str] = field(default_factory=list)
    # Read provenance only; never serialized into the note's frontmatter.
    source_path: str | None = field(default=None, compare=False, repr=False)

    def to_frontmatter(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "agent": self.agent,
            "type": self.type,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "tags": list(self.tags),
            "status": self.status,
            "linked_notes": list(self.linked_notes),
        }


def write_note(schema: NoteSchema, body: str, *, settings: Settings | None = None) -> Path:
    """Zapisuje notatke ze standardowym frontmatter i weryfikuje zapis po odczycie."""

    vault = _vault_path(settings)
    target = _note_path(vault, schema)
    target.parent.mkdir(parents=True, exist_ok=True)

    post = frontmatter.Post(body.strip() + "\n")
    post.metadata.update(schema.to_frontmatter())
    content = frontmatter.dumps(post)
    expected_hash = _sha256_text(content)
    target.write_text(content, encoding="utf-8")
    actual = target.read_text(encoding="utf-8")
    if _sha256_text(actual) != expected_hash:
        raise RuntimeError(f"Obsidian note write verification failed: {target}")
    _clear_cache()
    return target


def read_notes(
    *,
    type: str | None = None,
    status: str | None = None,
    agent: str | None = None,
    limit: int = 50,
    settings: Settings | None = None,
    refresh: bool = False,
) -> list[NoteSchema]:
    """Czyta notatki po indeksowanych katalogach agent/type, bez pelnego skanu vaulta."""

    vault = _vault_path(settings)
    cache_key = (str(vault), agent, type, status, limit)
    cached = _READ_CACHE.get(cache_key)
    now = time.monotonic()
    if not refresh and cached and now - cached[0] <= _CACHE_TTL_SECONDS:
        return list(cached[1])

    candidates = list(_candidate_dirs(vault, agent=agent, note_type=type))
    notes: list[NoteSchema] = []
    for directory in candidates:
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            schema = _read_schema(path)
            if schema is None:
                continue
            if type is not None and schema.type != type:
                continue
            if status is not None and schema.status != status:
                continue
            if agent is not None and schema.agent != agent:
                continue
            notes.append(schema)

    notes.sort(key=lambda item: item.created_at, reverse=True)
    result = notes[: max(limit, 0)]
    _READ_CACHE[cache_key] = (now, list(result))
    return result


def cleanup_expired_notes(*, settings: Settings | None = None) -> int:
    """Przenosi wygasle notatki do `_archive`, bez kasowania danych."""

    vault = _vault_path(settings)
    archived = 0
    now = datetime.now(UTC)
    for path in _iter_structured_notes(vault):
        schema = _read_schema(path)
        if schema is None or not schema.expires_at:
            continue
        expires_at = _parse_datetime(schema.expires_at)
        if expires_at is None or expires_at > now:
            continue
        _archive_note(vault, path)
        archived += 1
    if archived:
        _clear_cache()
    return archived


def deduplicate_notes(type: str, *, settings: Settings | None = None) -> int:
    """Archiwizuje starsze duplikaty notatek o tym samym `title` w danym typie."""

    vault = _vault_path(settings)
    by_title: dict[str, list[tuple[NoteSchema, Path]]] = {}
    for path in _iter_structured_notes(vault):
        schema = _read_schema(path)
        if schema is None or schema.type != type:
            continue
        by_title.setdefault(schema.title, []).append((schema, path))

    archived = 0
    for entries in by_title.values():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda item: item[0].created_at, reverse=True)
        for _, old_path in entries[1:]:
            _archive_note(vault, old_path)
            archived += 1
    if archived:
        _clear_cache()
    return archived


def _vault_path(settings: Settings | None = None) -> Path:
    resolved_settings = settings or Settings()
    if resolved_settings.obsidian_vault_path is None:
        raise RuntimeError("OBSIDIAN_VAULT_PATH is not configured.")
    vault = resolved_settings.obsidian_vault_path.expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def _note_path(vault: Path, schema: NoteSchema) -> Path:
    agent = _safe_segment(schema.agent)
    note_type = _safe_segment(schema.type)
    title = _safe_slug(schema.title)
    target = (vault / agent / note_type / f"{title}.md").resolve()
    target.relative_to(vault)
    return target


def _candidate_dirs(vault: Path, *, agent: str | None, note_type: str | None) -> list[Path]:
    if agent and note_type:
        return [vault / _safe_segment(agent) / _safe_segment(note_type)]
    if agent:
        root = vault / _safe_segment(agent)
        return [path for path in root.iterdir() if path.is_dir()] if root.exists() else []
    if note_type:
        return [root / _safe_segment(note_type) for root in vault.iterdir() if root.is_dir() and root.name != "_archive"]
    return []


def _iter_structured_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for agent_dir in vault.iterdir() if vault.exists() else []:
        if not agent_dir.is_dir() or agent_dir.name == "_archive":
            continue
        for type_dir in agent_dir.iterdir():
            if type_dir.is_dir():
                paths.extend(type_dir.glob("*.md"))
    return paths


def _read_schema(path: Path) -> NoteSchema | None:
    try:
        post = frontmatter.load(path)
    except Exception:
        return None
    metadata = post.metadata
    required = ("title", "agent", "type", "created_at")
    if any(not metadata.get(key) for key in required):
        return None
    tags = metadata.get("tags") or []
    linked_notes = metadata.get("linked_notes") or []
    return NoteSchema(
        title=str(metadata.get("title")),
        agent=str(metadata.get("agent")),
        type=str(metadata.get("type")),
        created_at=str(metadata.get("created_at")),
        expires_at=str(metadata.get("expires_at")) if metadata.get("expires_at") else None,
        tags=[str(item) for item in tags if str(item).strip()],
        status=str(metadata.get("status") or "pending"),
        linked_notes=[str(item) for item in linked_notes if str(item).strip()],
        source_path=str(path.resolve()),
    )


def _archive_note(vault: Path, path: Path) -> Path:
    relative = path.resolve().relative_to(vault)
    target = (vault / "_archive" / relative).resolve()
    if target.exists():
        target = target.with_name(f"{target.stem}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}{target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))
    return target


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_segment(value: str) -> str:
    segment = _safe_slug(value)
    if not segment or segment in {".", ".."}:
        raise ValueError("Invalid Obsidian note path segment.")
    return segment


def _safe_slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-._")
    return normalized or "note"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clear_cache() -> None:
    _READ_CACHE.clear()
