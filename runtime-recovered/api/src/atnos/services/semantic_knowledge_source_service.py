from __future__ import annotations

"""Safe local source discovery for the semantic project knowledge index."""

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from atnos.ai_workers.redaction import redact_payload, redact_text
from atnos.core.paths import normalize_atnos_legacy_paths
from atnos.services.knowledge_library_service import KnowledgeLibraryService
from atnos.services.obsidian_read_service import ObsidianReadService

if TYPE_CHECKING:
    from atnos.core.config import Settings


MAX_SOURCE_FILE_BYTES = 512 * 1024
MAX_LOG_FILE_BYTES = 256 * 1024
MAX_DISCOVERED_FILES = 650
SUPPORTED_SUFFIXES = frozenset({".json", ".jsonl", ".log", ".md", ".toml", ".txt", ".yaml", ".yml"})
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".obsidian",
        ".pytest_cache",
        ".venv",
        ".vite",
        "account-secrets",
        "audio",
        "browser-downloads",
        "browser-profile",
        "browser-traces",
        "browser_downloads",
        "browser_profiles",
        "browser_screenshots",
        "build",
        "cache",
        "caches",
        "dist",
        "images",
        "models",
        "node_modules",
        "outputs",
        "playwright-browsers",
        "quarantine",
        "shorts",
        "temp",
        "tmp",
        "uploads",
        "video",
        "videos",
        "__pycache__",
    }
)
SENSITIVE_FILE_PATTERNS = (
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r"client[_-]?secret", re.IGNORECASE),
    re.compile(r"(?:^|[_-])credentials?(?:[_-]|\.|$)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])token(?:[_-]|\.|$)", re.IGNORECASE),
    re.compile(r"firebase.*(?:admin|service).*\.json$", re.IGNORECASE),
)
NON_INDEXABLE_FILE_PATTERNS = (
    # Query evidence contains previous search responses. Indexing it would create
    # a self-reinforcing result that outranks the source documents being tested.
    re.compile(r"^live-semantic-.+\.json$", re.IGNORECASE),
)

@dataclass(frozen=True, slots=True)
class SemanticKnowledgeSourceDocument:
    doc_id: str
    path: str
    citation_path: str
    title: str
    topic: str
    source_label: str
    source_url: str | None
    updated_at: str
    source_mtime: str
    source_type: str
    project_id: str | None
    source_sha256: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class SemanticKnowledgeSourceCatalog:
    documents: tuple[SemanticKnowledgeSourceDocument, ...]
    skipped_files: int
    skipped_sensitive_files: int
    roots_scanned: tuple[str, ...]

    @property
    def document_count(self) -> int:
        return len(self.documents)


@dataclass(frozen=True, slots=True)
class _SourceRoot:
    path: Path
    logical_prefix: str
    source_type: str
    source_label: str
    default_project_id: str | None
    max_files: int
    newest_first: bool = False


class SemanticKnowledgeSourceService:
    """Discovers bounded local text sources and redacts them before indexing."""

    def __init__(
        self,
        settings: "Settings",
        *,
        library: KnowledgeLibraryService | None = None,
        obsidian_path_resolver: Callable[[], Path | None] | None = None,
        include_extended_sources: bool | None = None,
    ) -> None:
        self.settings = settings
        self.library = library or KnowledgeLibraryService(settings)
        self.obsidian_path_resolver = obsidian_path_resolver
        self.include_extended_sources = (
            settings.app_env != "test" if include_extended_sources is None else include_extended_sources
        )

    def discover(self) -> SemanticKnowledgeSourceCatalog:
        self.library.refresh_index()
        documents: list[SemanticKnowledgeSourceDocument] = []
        seen_files: set[Path] = set()
        seen_content_hashes: set[str] = set()
        skipped_files = 0
        skipped_sensitive_files = 0

        for entry in self.library.list_documents():
            candidate = (self.library.knowledge_dir / entry.path).resolve()
            if not candidate.is_file():
                continue
            seen_files.add(candidate)
            raw_text = self._read_and_redact(candidate)
            if raw_text is None:
                skipped_files += 1
                continue
            frontmatter, _ = ObsidianReadService._parse_frontmatter(raw_text)
            source_type = self._inline(frontmatter.get("source_type"), fallback="markdown")
            project_id = self._optional_inline(frontmatter.get("project_id") or frontmatter.get("project"))
            source_sha256 = self._sha256(raw_text)
            seen_content_hashes.add(source_sha256)
            documents.append(
                SemanticKnowledgeSourceDocument(
                    doc_id=entry.doc_id,
                    path=entry.path,
                    citation_path=f"knowledge/{entry.path}",
                    title=entry.title,
                    topic=entry.topic,
                    source_label=entry.source_label,
                    source_url=entry.source_url,
                    updated_at=entry.updated_at,
                    source_mtime=entry.source_mtime,
                    source_type=source_type,
                    project_id=project_id,
                    source_sha256=source_sha256,
                    raw_text=raw_text,
                )
            )

        roots = self._source_roots()
        for root in roots:
            if len(documents) >= MAX_DISCOVERED_FILES:
                break
            root_document_count = 0
            for candidate in self._iter_candidates(root.path, newest_first=root.newest_first):
                if len(documents) >= MAX_DISCOVERED_FILES:
                    break
                if root_document_count >= root.max_files:
                    break
                resolved = candidate.resolve()
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                if self._is_non_indexable_path(candidate):
                    skipped_files += 1
                    continue
                if self._is_sensitive_path(candidate):
                    skipped_sensitive_files += 1
                    continue
                raw_text = self._read_and_redact(candidate)
                if raw_text is None:
                    skipped_files += 1
                    continue
                source_sha256 = self._sha256(raw_text)
                if source_sha256 in seen_content_hashes:
                    skipped_files += 1
                    continue
                seen_content_hashes.add(source_sha256)
                documents.append(self._external_document(root, candidate, raw_text, source_sha256=source_sha256))
                root_document_count += 1

        documents.sort(key=lambda item: item.path)
        return SemanticKnowledgeSourceCatalog(
            documents=tuple(documents),
            skipped_files=skipped_files,
            skipped_sensitive_files=skipped_sensitive_files,
            roots_scanned=tuple(str(root.path) for root in roots if root.path.is_dir()),
        )

    def _source_roots(self) -> tuple[_SourceRoot, ...]:
        if not self.include_extended_sources:
            return ()
        app_root = self.settings.app_root.resolve()
        data_dir = self.settings.data_dir.resolve()
        candidates = [
            _SourceRoot(
                app_root / "docs",
                "repo/docs",
                "project_documentation",
                "Atnos repository documentation",
                "atnos",
                300,
            ),
            _SourceRoot(
                app_root / "runtime-recovered" / "api" / "docs",
                "repo/runtime-recovered/api/docs",
                "project_documentation",
                "Atnos backend documentation",
                "atnos",
                300,
            ),
            _SourceRoot(
                self.settings.knowledge_workspace_dir.resolve(),
                "workspace",
                "knowledge_workspace",
                "Atnos knowledge workspace",
                None,
                150,
            ),
            _SourceRoot(
                data_dir / "reports",
                "runtime/reports",
                "runtime_report",
                "Atnos runtime report",
                "atnos",
                100,
                True,
            ),
            _SourceRoot(
                data_dir / "learning",
                "runtime/learning",
                "learning_result",
                "Atnos learning result",
                "atnos",
                100,
                True,
            ),
            _SourceRoot(
                self.settings.learning_archive_dir.resolve(),
                "runtime/learning",
                "learning_result",
                "Atnos learning archive",
                "atnos",
                100,
                True,
            ),
            _SourceRoot(
                data_dir / "ai-workers",
                "runtime/ai-workers",
                "worker_result",
                "Atnos worker result",
                "atnos",
                100,
                True,
            ),
            _SourceRoot(
                data_dir / "validation",
                "runtime/validation",
                "test_result",
                "Atnos validation result",
                "atnos",
                100,
                True,
            ),
        ]
        obsidian_root = self._resolve_obsidian_root()
        if obsidian_root is not None:
            candidates.append(
                _SourceRoot(
                    obsidian_root.resolve(),
                    "obsidian",
                    "obsidian_note",
                    "Configured Obsidian vault",
                    None,
                    200,
                    True,
                )
            )

        unique: list[_SourceRoot] = []
        seen: set[Path] = set()
        for item in candidates:
            if item.path in seen or not item.path.is_dir():
                continue
            seen.add(item.path)
            unique.append(item)
        return tuple(unique)

    @staticmethod
    def _iter_candidates(root: Path, *, newest_first: bool) -> Iterable[Path]:
        candidates: list[Path] = []
        for current_root, directory_names, file_names in os.walk(root):
            directory_names[:] = [
                name
                for name in directory_names
                if not name.startswith(".") and name.casefold() not in EXCLUDED_DIRECTORY_NAMES
            ]
            current = Path(current_root)
            for file_name in file_names:
                if file_name.startswith("."):
                    continue
                candidate = current / file_name
                if candidate.suffix.lower() in SUPPORTED_SUFFIXES:
                    candidates.append(candidate)
        if newest_first:
            candidates.sort(key=lambda item: (-item.stat().st_mtime_ns, item.as_posix()))
        else:
            candidates.sort()
        yield from candidates

    @staticmethod
    def _is_sensitive_path(path: Path) -> bool:
        name = path.name
        return any(pattern.search(name) for pattern in SENSITIVE_FILE_PATTERNS)

    @staticmethod
    def _is_non_indexable_path(path: Path) -> bool:
        return any(pattern.search(path.name) for pattern in NON_INDEXABLE_FILE_PATTERNS)

    def _read_and_redact(self, path: Path) -> str | None:
        try:
            size = path.stat().st_size
        except OSError:
            return None
        size_limit = MAX_LOG_FILE_BYTES if path.suffix.lower() in {".log", ".jsonl"} else MAX_SOURCE_FILE_BYTES
        if size <= 0 or size > size_limit:
            return None
        try:
            raw_text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return None
        if "\x00" in raw_text:
            return None
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                return self._normalize_operational_paths(redact_text(raw_text))
            redacted = json.dumps(redact_payload(payload), ensure_ascii=False, indent=2)
            return self._normalize_operational_paths(redacted)
        return self._normalize_operational_paths(redact_text(raw_text))

    @staticmethod
    def _normalize_operational_paths(value: str) -> str:
        """Map known pre-migration paths before text becomes operational memory."""
        return normalize_atnos_legacy_paths(value)

    def _external_document(
        self,
        root: _SourceRoot,
        path: Path,
        raw_text: str,
        *,
        source_sha256: str,
    ) -> SemanticKnowledgeSourceDocument:
        relative = path.relative_to(root.path).as_posix()
        logical_path = f"{root.logical_prefix}/{relative}"
        frontmatter, body = ObsidianReadService._parse_frontmatter(raw_text)
        stat = path.stat()
        source_mtime = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        title = self._inline(frontmatter.get("title"), fallback=self._first_heading(body) or path.stem)
        source_type = self._inline(frontmatter.get("source_type"), fallback=root.source_type).casefold()
        project_id = self._optional_inline(frontmatter.get("project_id") or frontmatter.get("project"))
        if project_id is None:
            project_id = root.default_project_id
        topic = self._inline(frontmatter.get("topic"), fallback=source_type.replace("_", "-"))
        source_label = self._inline(frontmatter.get("source_label"), fallback=root.source_label)
        source_url = self._optional_inline(frontmatter.get("source_url"))
        updated_at = self._inline(frontmatter.get("updated_at"), fallback=source_mtime)
        doc_id = f"{source_type}-{hashlib.sha256(logical_path.encode('utf-8')).hexdigest()[:20]}"
        return SemanticKnowledgeSourceDocument(
            doc_id=doc_id,
            path=logical_path,
            citation_path=logical_path,
            title=title,
            topic=topic,
            source_label=source_label,
            source_url=source_url,
            updated_at=updated_at,
            source_mtime=source_mtime,
            source_type=source_type,
            project_id=project_id,
            source_sha256=source_sha256,
            raw_text=raw_text,
        )

    @staticmethod
    def _first_heading(body: str) -> str | None:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                if heading:
                    return heading[:160]
        return None

    @staticmethod
    def _inline(value: object, *, fallback: str) -> str:
        candidate = str(value or fallback).replace("\r", " ").replace("\n", " ")
        return re.sub(r"\s+", " ", candidate).strip()[:240] or fallback

    @classmethod
    def _optional_inline(cls, value: object) -> str | None:
        if value is None:
            return None
        candidate = cls._inline(value, fallback="")
        return candidate or None

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _resolve_obsidian_root(self) -> Path | None:
        configured = self.settings.obsidian_vault_path
        if self.obsidian_path_resolver is not None:
            try:
                configured = self.obsidian_path_resolver() or configured
            except Exception:
                configured = self.settings.obsidian_vault_path
        if configured is None:
            return None
        resolved = configured.resolve()
        return resolved if resolved.is_dir() else None
