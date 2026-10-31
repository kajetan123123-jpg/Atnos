from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.integration_config_service import (
    IntegrationConfigService,
    OBSIDIAN_VAULT_NAME,
)
from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS

DEFAULT_LIST_LIMIT = 25
MAX_LIST_LIMIT = 50
MAX_EXCERPT_CHARS = 220
MAX_MARKDOWN_PREVIEW_CHARS = 2_400
MAX_CONTEXT_SUMMARY_CHARS = 420
MAX_SEARCH_TERM_CHARS = 120


@dataclass(frozen=True, slots=True)
class ObsidianVaultStatus:
    vault_name: str
    path_configured: bool
    path_exists: bool
    readable: bool
    note_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObsidianNoteListItem:
    title: str
    relative_path: str
    modified_at: str
    size_bytes: int
    tags: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True, slots=True)
class ObsidianNoteReadResult:
    title: str
    relative_path: str
    markdown_preview: str
    frontmatter: dict[str, object] | None
    links: tuple[str, ...]
    context_summary: str
    truncated: bool = False
    total_chars: int = 0
    returned_chars: int = 0


class ObsidianReadError(Exception):
    pass


class ObsidianReadConfigurationError(ObsidianReadError):
    pass


class ObsidianReadPathError(ObsidianReadError):
    pass


class ObsidianReadNotFoundError(ObsidianReadError):
    pass


class ObsidianReadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.integration_config = IntegrationConfigService(session, settings)

    def get_vault_status(self, *, include_note_count: bool = True) -> ObsidianVaultStatus:
        config = self.integration_config.get_obsidian_config()
        vault_root = self._resolve_vault_root(optional=True)
        readable = bool(vault_root and os.access(vault_root, os.R_OK))
        note_count = self._count_notes(vault_root) if include_note_count and vault_root and readable else 0
        warnings = list(self._read_warnings(config))

        if config.vault_path is not None and config.path_exists and not readable:
            warnings.append("Configured Obsidian vault path is not readable.")

        return ObsidianVaultStatus(
            vault_name=OBSIDIAN_VAULT_NAME,
            path_configured=config.vault_path is not None,
            path_exists=config.path_exists,
            readable=readable,
            note_count=note_count,
            warnings=tuple(warnings),
        )

    def list_notes(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        search: str | None = None,
    ) -> tuple[ObsidianNoteListItem, ...]:
        vault_root = self._resolve_vault_root()
        safe_search = self._sanitize_search_term(search)

        items: list[ObsidianNoteListItem] = []
        for candidate in self._iter_markdown_files(vault_root):
            try:
                raw_text = self._read_markdown_file(candidate)
            except OSError:
                continue

            frontmatter, body = self._parse_frontmatter(raw_text)
            title = self._resolve_title(candidate, frontmatter, body)
            tags = self._extract_tags(frontmatter, body)
            excerpt = self._build_excerpt(body)
            searchable = " ".join(part for part in (title, excerpt, body) if part)
            if safe_search and safe_search.lower() not in searchable.lower():
                continue

            stat = candidate.stat()
            items.append(
                ObsidianNoteListItem(
                    title=title,
                    relative_path=candidate.relative_to(vault_root).as_posix(),
                    modified_at=self._format_mtime(stat.st_mtime),
                    size_bytes=stat.st_size,
                    tags=tags,
                    excerpt=excerpt,
                )
            )

        items.sort(key=lambda item: item.modified_at, reverse=True)
        return tuple(items[:limit])

    def read_note(self, relative_path: str, *, max_chars: int = MAX_MARKDOWN_PREVIEW_CHARS) -> ObsidianNoteReadResult:
        vault_root = self._resolve_vault_root()
        note_path = self._resolve_note_path(vault_root, relative_path)

        try:
            raw_text = self._read_markdown_file(note_path)
        except OSError as exc:
            raise ObsidianReadNotFoundError("Obsidian note could not be read.") from exc

        frontmatter, body = self._parse_frontmatter(raw_text)
        title = self._resolve_title(note_path, frontmatter, body)
        links = self._extract_links(body)
        max_chars = max(200, min(int(max_chars), 50_000))
        safe_body = self._sanitize_markdown_preview(body, max_chars=None)
        truncated = len(safe_body) > max_chars
        markdown_preview = safe_body[:max_chars - 3].rstrip() + '...' if truncated else safe_body
        context_summary = self._build_context_summary(title=title, body=body, links=links)

        return ObsidianNoteReadResult(
            title=title,
            relative_path=note_path.relative_to(vault_root).as_posix(),
            markdown_preview=markdown_preview,
            frontmatter=frontmatter or None,
            links=links,
            context_summary=context_summary,
            truncated=truncated,
            total_chars=len(safe_body),
            returned_chars=len(markdown_preview),
        )

    def _resolve_vault_root(self, *, optional: bool = False) -> Path | None:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None:
            if optional:
                return None
            raise ObsidianReadConfigurationError("Obsidian vault path is not configured.")
        if not config.path_exists:
            if optional:
                return None
            raise ObsidianReadConfigurationError("Configured Obsidian vault path does not exist.")

        vault_root = config.vault_path.resolve()
        if not vault_root.is_dir():
            if optional:
                return None
            raise ObsidianReadConfigurationError("Configured Obsidian vault path is not a directory.")
        if not os.access(vault_root, os.R_OK):
            if optional:
                return vault_root
            raise ObsidianReadConfigurationError("Configured Obsidian vault path is not readable.")
        return vault_root

    @staticmethod
    def _read_warnings(config) -> tuple[str, ...]:
        return tuple(
            warning
            for warning in config.warnings
            if warning != "OBSIDIAN_EXPORT_ENABLED is false; confirm export will be unavailable."
        )

    def _count_notes(self, vault_root: Path) -> int:
        return sum(1 for _ in self._iter_markdown_files(vault_root))

    def _iter_markdown_files(self, vault_root: Path):
        for candidate in vault_root.rglob("*"):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() != ".md":
                continue

            resolved = candidate.resolve()
            try:
                resolved.relative_to(vault_root)
            except ValueError:
                continue

            if ".obsidian" in resolved.relative_to(vault_root).parts:
                continue
            yield resolved

    @staticmethod
    def _sanitize_search_term(search: str | None) -> str | None:
        if search is None:
            return None
        normalized = " ".join(search.split())
        if not normalized:
            return None
        if len(normalized) > MAX_SEARCH_TERM_CHARS:
            normalized = normalized[:MAX_SEARCH_TERM_CHARS].rstrip()
        if ".." in normalized or "/" in normalized or "\\" in normalized:
            raise ObsidianReadPathError("Search term cannot contain path traversal or separators.")
        return normalized

    @staticmethod
    def _resolve_note_path(vault_root: Path, relative_path: str) -> Path:
        candidate_raw = Path(relative_path.strip())
        if not relative_path.strip():
            raise ObsidianReadPathError("Note path is required.")
        if candidate_raw.is_absolute():
            raise ObsidianReadPathError("Absolute note paths are not allowed.")
        if candidate_raw.suffix.lower() != ".md":
            raise ObsidianReadPathError("Only markdown notes can be read.")
        if ".." in candidate_raw.parts:
            raise ObsidianReadPathError("Note path cannot contain traversal segments.")
        if ".obsidian" in candidate_raw.parts:
            raise ObsidianReadPathError("Notes inside .obsidian are not readable.")

        resolved = (vault_root / candidate_raw).resolve()
        try:
            resolved.relative_to(vault_root)
        except ValueError as exc:
            raise ObsidianReadPathError("Requested note path escapes the configured vault.") from exc
        if not resolved.exists() or not resolved.is_file():
            raise ObsidianReadNotFoundError("Requested Obsidian note was not found.")
        return resolved

    @staticmethod
    def _parse_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
        if not markdown.startswith("---"):
            return {}, markdown

        lines = markdown.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, markdown

        closing_index = None
        for index in range(1, min(len(lines), 80)):
            if lines[index].strip() == "---":
                closing_index = index
                break

        if closing_index is None:
            return {}, markdown

        parsed: dict[str, object] = {}
        current_list_key: str | None = None
        current_list_values: list[str] = []
        for raw_line in lines[1:closing_index]:
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if current_list_key and stripped.startswith("- "):
                current_list_values.append(stripped[2:].strip())
                parsed[current_list_key] = list(current_list_values)
                continue
            current_list_key = None
            current_list_values = []

            if ":" not in line:
                continue

            key, raw_value = line.split(":", 1)
            normalized_key = key.strip()
            value = raw_value.strip()
            if not normalized_key:
                continue
            if value:
                parsed[normalized_key] = value
                continue

            current_list_key = normalized_key
            parsed[current_list_key] = []

        body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
        return parsed, body

    @staticmethod
    def _read_markdown_file(path: Path) -> str:
        return path.read_text(encoding="utf-8-sig", errors="ignore")

    def _resolve_title(
        self,
        path: Path,
        frontmatter: dict[str, object],
        body: str,
    ) -> str:
        raw_title = frontmatter.get("title")
        if isinstance(raw_title, str):
            title = self._sanitize_inline_text(raw_title, limit=160)
            if title:
                return title

        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line.startswith("#"):
                title = self._sanitize_inline_text(line.lstrip("#").strip(), limit=160)
                if title:
                    return title

        return self._sanitize_inline_text(path.stem.replace("-", " ").replace("_", " "), limit=160) or "Untitled note"

    def _extract_tags(
        self,
        frontmatter: dict[str, object],
        body: str,
    ) -> tuple[str, ...]:
        tags: list[str] = []
        raw_tags = frontmatter.get("tags")
        if isinstance(raw_tags, list):
            tags.extend(str(item).strip() for item in raw_tags if str(item).strip())
        elif isinstance(raw_tags, str):
            tags.extend(
                token.strip()
                for token in re.split(r"[,\s]+", raw_tags)
                if token.strip()
            )

        if not tags:
            tags.extend(match.group(2) for match in re.finditer(r"(^|\s)#([A-Za-z0-9_\-]+)", body))

        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tag in tags:
            tag = str(raw_tag).strip().lstrip("#")
            if not tag:
                continue
            tag = self._sanitize_inline_text(tag, limit=40)
            if not tag:
                continue
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(tag)
        return tuple(normalized[:8])

    def _build_excerpt(self, body: str) -> str:
        lines: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
            if len(lines) >= 3:
                break
        excerpt_source = " ".join(lines)
        return self._sanitize_inline_text(excerpt_source, limit=MAX_EXCERPT_CHARS) or "No safe excerpt available."

    def _sanitize_markdown_preview(self, body: str, *, max_chars: int | None = MAX_MARKDOWN_PREVIEW_CHARS) -> str:
        preview = self._redact_text(body)
        preview = preview.replace("\r\n", "\n").replace("\r", "\n")
        preview = re.sub(r"\n{3,}", "\n\n", preview).strip()
        if max_chars is not None and len(preview) > max_chars:
            preview = preview[: max_chars - 3].rstrip() + "..."
        return preview or "No safe markdown preview available."

    @staticmethod
    def _extract_links(body: str) -> tuple[str, ...]:
        matches = re.findall(r"\[\[([^\]]+)\]\]", body)
        links: list[str] = []
        seen: set[str] = set()
        for match in matches:
            candidate = match.strip()
            if not candidate:
                continue
            lowered = candidate.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            links.append(candidate)
        return tuple(links[:12])

    def _build_context_summary(
        self,
        *,
        title: str,
        body: str,
        links: tuple[str, ...],
    ) -> str:
        excerpt = self._build_excerpt(body)
        links_summary = ", ".join(links[:3]) if links else "none"
        summary = f"{title}. Excerpt: {excerpt} Links: {links_summary}."
        return self._sanitize_inline_text(summary, limit=MAX_CONTEXT_SUMMARY_CHARS) or title

    def _sanitize_inline_text(self, value: str, *, limit: int) -> str:
        normalized = " ".join(self._redact_text(value).split())
        if not normalized:
            return ""
        if len(normalized) > limit:
            return normalized[: limit - 3].rstrip() + "..."
        return normalized

    @staticmethod
    def _redact_text(value: str) -> str:
        redacted = value
        redacted = re.sub(r"(?i)\bBearer\s+\S+", "[REDACTED]", redacted)
        redacted = re.sub(r"(?i)\bsk-[A-Za-z0-9_\-]{6,}\b", "[REDACTED]", redacted)
        redacted = re.sub(
            r"(?i)\b(password|token|api_key|secret_ref|client_secret|private_key|account_secret|secret_value|credential|authorization|headers|cookies)\b\s*[:=]?\s*\S*",
            "[REDACTED]",
            redacted,
        )
        for token in FORBIDDEN_KEY_TOKENS:
            redacted = re.sub(rf"(?i)\b{re.escape(token)}\b", "[REDACTED]", redacted)
        return redacted

    @staticmethod
    def _format_mtime(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
