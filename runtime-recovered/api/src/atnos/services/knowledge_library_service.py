from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING
import unicodedata

if TYPE_CHECKING:
    from atnos.core.config import Settings

MAX_KNOWLEDGE_FILE_BYTES = 200 * 1024
DEFAULT_MAX_DOCS = 3
DEFAULT_MAX_CHARS = 4_500
DEFAULT_PER_DOC_CHARS = 1_400
REQUIRED_FRONTMATTER_FIELDS = (
    "title",
    "topic",
    "summary",
    "tags",
    "keywords",
    "source_label",
    "license",
)
QUERY_STOPWORDS = {
    "a",
    "aby",
    "ale",
    "and",
    "bo",
    "by",
    "czy",
    "co",
    "dla",
    "do",
    "how",
    "i",
    "in",
    "is",
    "jak",
    "jest",
    "kto",
    "lub",
    "na",
    "nie",
    "o",
    "of",
    "or",
    "po",
    "sie",
    "się",
    "the",
    "to",
    "w",
    "what",
    "when",
    "who",
    "why",
    "z",
    "za",
}
TOKEN_ALIAS_GROUPS = (
    ("kurs", "course"),
    ("inwestowanie", "inwestowania", "investing", "investment"),
    ("ryzyko", "risk", "downside"),
    ("pozycja", "pozycji", "position", "sizing"),
    ("modelka", "modelki", "model"),
    ("rolka", "rolki", "reel", "reels", "short", "shorts"),
    ("zdjecie", "zdjecia", "photo", "photos", "image", "images"),
    ("sprzedaz", "sprzedaje", "selling", "sales", "sell"),
    ("teaser", "zajawka", "hook"),
    ("ekskluzywnosc", "exclusive", "exclusivity", "premium"),
    ("awaria", "awarii", "crash", "failure", "outage"),
    (
        "odzyskiwanie",
        "odzyskuje",
        "recovery",
        "recover",
        "restore",
        "restores",
        "watchdog",
        "restart",
        "restarts",
        "ponownie",
        "uruchamia",
    ),
    ("usluga", "uslugi", "service", "backend", "runtime"),
    ("generowanie", "generation", "generator", "render", "renderer"),
    ("film", "filmy", "filmow", "video", "videos", "mp4"),
    ("blokuje", "blokowac", "blocking", "blocks", "failed"),
    ("polaczony", "polaczenie", "connected", "connection", "connector", "oauth"),
    ("odpowiada", "responsible", "handles", "keeps", "guard"),
)
TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    token: tuple(alias for alias in group if alias != token)
    for group in TOKEN_ALIAS_GROUPS
    for token in group
}
ASCII_TRANSLITERATION = str.maketrans({"ł": "l", "Ł": "L"})
DEFAULT_SEED_DOCUMENTS: dict[str, str] = {
    "product/personal-agent-principles.md": """---
title: Personal agent product principles
topic: product
summary: Core product rules for a personal AI agent that should stay simple, contextual and execution-focused.
tags:
  - product
  - personal-agent
  - ux
keywords:
  - personal ai
  - chat first
  - settings
  - context
source_label: Atnos curated note
source_url: local://atnos/product-principles
license: CC0-1.0
updated_at: 2026-05-11
---
# Personal agent product principles

- Chat is the main surface. Settings should configure behavior, not compete with Chat.
- Prefer one decisive answer over arena-style comparisons.
- Surface only the controls needed for the current task. Hide debug surfaces and secondary machinery.
- Preserve context, projects and persona behavior without forcing the user to reconfigure every turn.
- Optimize for clarity, continuity and execution readiness.
""",
    "content/short-form-hooks.md": """---
title: Short-form hooks and retention heuristics
topic: content
summary: Practical heuristics for short-form videos: first-frame clarity, fast payoff and repeatable hook families.
tags:
  - shorts
  - hooks
  - retention
keywords:
  - first frame
  - viewed vs swiped
  - payoff
  - short video
source_label: Atnos curated note
source_url: local://atnos/short-form-hooks
license: CC0-1.0
updated_at: 2026-05-11
---
# Short-form hooks and retention heuristics

- The first frame must communicate the promise immediately.
- One clear tension or curiosity gap beats three weak ideas stacked together.
- Most weak shorts fail because the payoff arrives too late or the intro looks generic.
- Strong formats are serializable: the viewer quickly recognizes the structure and reward pattern.
- Evaluate every short on viewed-vs-swiped, average percentage viewed and whether the payoff lands inside the expected duration.
""",
    "creator/premium-tease-positioning.md": """---
title: Premium tease positioning for creator funnels
topic: creator-monetization
summary: A safe, premium positioning note for creator funnels that rely on exclusivity and retention rather than generic explicit escalation.
tags:
  - creator
  - monetization
  - positioning
keywords:
  - premium tease
  - luxury soft glam
  - retention
  - exclusivity
source_label: Atnos curated note
source_url: local://atnos/premium-tease-positioning
license: CC0-1.0
updated_at: 2026-05-11
---
# Premium tease positioning for creator funnels

- The monetization edge comes from a clear persona, not generic attractiveness.
- Premium tease works best when the public surface sells mood, exclusivity and consistency.
- Do not force explicit escalation as the default growth loop.
- Strong lanes usually combine one visual identity, one emotional promise and one repeatable CTA style.
- Retention, DM monetization and loyal niche fit matter more than broad but shallow appeal.
""",
}


def _load_obsidian_read_service():
    from atnos.services.obsidian_read_service import ObsidianReadService

    return ObsidianReadService


@dataclass(slots=True, frozen=True)
class KnowledgeDocumentEntry:
    doc_id: str
    path: str
    title: str
    topic: str
    summary: str
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    source_label: str
    source_url: str | None
    license: str
    updated_at: str
    size_bytes: int
    source_sha256: str
    source_mtime: str
    source_mtime_ns: int
    indexed_at: str


@dataclass(slots=True, frozen=True)
class KnowledgeMatch:
    doc_id: str
    path: str
    title: str
    topic: str
    summary: str
    score: int
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    excerpt: str
    source_label: str
    source_url: str | None
    source_updated_at: str
    indexed_at: str
    citation: str
    excerpt_line_start: int
    excerpt_line_end: int


class KnowledgeLibraryService:
    INDEX_FILENAME = "index.json"
    INDEX_VERSION = 2

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.knowledge_dir = settings.knowledge_dir
        self.index_path = self.knowledge_dir / self.INDEX_FILENAME

    def ensure_library(self) -> dict[str, object]:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_seed_documents()
        if not self.index_path.exists():
            return self.rebuild_index()
        payload = self._read_index_file()
        if not payload.get("documents"):
            return self.rebuild_index()
        return self.refresh_index()

    def rebuild_index(self) -> dict[str, object]:
        """Buduje pelny indeks od nowa, np. po jawnym poleceniu operatora."""
        return self._build_index(force=True)

    def refresh_index(self) -> dict[str, object]:
        """Aktualizuje tylko dokumenty zmienione od poprzedniego indeksowania."""
        return self._build_index(force=False)

    def _build_index(self, *, force: bool) -> dict[str, object]:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_seed_documents()
        documents: list[dict[str, object]] = []
        invalid_files: list[dict[str, object]] = []
        previous_payload = self._read_index_file() if self.index_path.exists() else {}
        previous_documents = {
            str(item.get("path")): item
            for item in previous_payload.get("documents") or []
            if isinstance(item, dict) and item.get("path")
        }
        discovered_paths: set[str] = set()
        parsed_count = 0
        reused_count = 0

        for candidate in sorted(self.knowledge_dir.rglob("*.md")):
            if candidate.name.lower() == "readme.md":
                continue
            relative_path = str(candidate.relative_to(self.knowledge_dir)).replace("\\", "/")
            discovered_paths.add(relative_path)
            stat = candidate.stat()
            if stat.st_size > MAX_KNOWLEDGE_FILE_BYTES:
                invalid_files.append(
                    {
                        "path": relative_path,
                        "reason": f"File exceeds {MAX_KNOWLEDGE_FILE_BYTES} bytes.",
                    }
                )
                continue

            previous = previous_documents.get(relative_path)
            if self._can_reuse(previous, stat=stat, force=force):
                documents.append(previous)
                reused_count += 1
                continue

            try:
                parsed = self._parse_document(candidate, stat=stat)
            except ValueError as exc:
                invalid_files.append(
                    {
                        "path": relative_path,
                        "reason": str(exc),
                    }
                )
                continue
            documents.append(asdict(parsed))
            parsed_count += 1

        documents.sort(key=lambda item: str(item.get("path") or ""))
        removed_count = len(set(previous_documents) - discovered_paths)
        refreshed_at = datetime.now(UTC).isoformat()
        payload = {
            "index_version": self.INDEX_VERSION,
            "generated_at": refreshed_at,
            "refreshed_at": refreshed_at,
            "knowledge_dir": str(self.knowledge_dir),
            "required_frontmatter": list(REQUIRED_FRONTMATTER_FIELDS),
            "document_count": len(documents),
            "invalid_file_count": len(invalid_files),
            "documents": documents,
            "invalid_files": invalid_files,
            "refresh_stats": {
                "mode": "full" if force else "incremental",
                "parsed_count": parsed_count,
                "reused_count": reused_count,
                "removed_count": removed_count,
                "invalid_count": len(invalid_files),
            },
        }
        self.index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def read_index(self) -> dict[str, object]:
        self.ensure_library()
        return self._read_index_file()

    def _read_index_file(self) -> dict[str, object]:
        try:
            raw = self.index_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def list_documents(self) -> list[KnowledgeDocumentEntry]:
        payload = self.read_index()
        documents: list[KnowledgeDocumentEntry] = []
        for raw_item in payload.get("documents") or []:
            if not isinstance(raw_item, dict):
                continue
            documents.append(
                KnowledgeDocumentEntry(
                    doc_id=str(raw_item.get("doc_id") or ""),
                    path=str(raw_item.get("path") or ""),
                    title=str(raw_item.get("title") or ""),
                    topic=str(raw_item.get("topic") or ""),
                    summary=str(raw_item.get("summary") or ""),
                    tags=tuple(str(item) for item in raw_item.get("tags") or []),
                    keywords=tuple(str(item) for item in raw_item.get("keywords") or []),
                    source_label=str(raw_item.get("source_label") or ""),
                    source_url=str(raw_item.get("source_url")) if raw_item.get("source_url") else None,
                    license=str(raw_item.get("license") or ""),
                    updated_at=str(raw_item.get("updated_at") or ""),
                    size_bytes=int(raw_item.get("size_bytes") or 0),
                    source_sha256=str(raw_item.get("source_sha256") or ""),
                    source_mtime=str(raw_item.get("source_mtime") or ""),
                    source_mtime_ns=int(raw_item.get("source_mtime_ns") or 0),
                    indexed_at=str(raw_item.get("indexed_at") or ""),
                )
            )
        return documents

    def lookup(
        self,
        query: str,
        *,
        max_docs: int = DEFAULT_MAX_DOCS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> list[KnowledgeMatch]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        scored: list[tuple[int, KnowledgeDocumentEntry]] = []
        for document in self.list_documents():
            score = self._score_document(document, query_tokens=query_tokens, query=query)
            if score <= 0:
                continue
            scored.append((score, document))

        scored.sort(key=lambda item: (-item[0], item[1].title.lower()))
        matches: list[KnowledgeMatch] = []
        remaining_chars = max_chars
        for score, document in scored[: max_docs * 2]:
            if remaining_chars <= 0 or len(matches) >= max_docs:
                break
            excerpt_budget = min(DEFAULT_PER_DOC_CHARS, remaining_chars)
            excerpt, line_start, line_end = self._build_excerpt(
                document.path,
                query_tokens=query_tokens,
                max_chars=excerpt_budget,
            )
            if not excerpt:
                continue
            matches.append(
                KnowledgeMatch(
                    doc_id=document.doc_id,
                    path=document.path,
                    title=document.title,
                    topic=document.topic,
                    summary=document.summary,
                    score=score,
                    tags=document.tags,
                    keywords=document.keywords,
                    excerpt=excerpt,
                    source_label=document.source_label,
                    source_url=document.source_url,
                    source_updated_at=document.updated_at,
                    indexed_at=document.indexed_at,
                    citation=self._build_citation(document, line_start=line_start, line_end=line_end),
                    excerpt_line_start=line_start,
                    excerpt_line_end=line_end,
                )
            )
            remaining_chars -= len(excerpt)
        return matches

    def build_system_section(
        self,
        query: str,
        *,
        max_docs: int = DEFAULT_MAX_DOCS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> str | None:
        matches = self.lookup(query, max_docs=max_docs, max_chars=max_chars)
        if not matches:
            return None
        blocks = [
            "Local curated knowledge library matches:",
            "Use the following local notes only when they are relevant. Prefer them over generic internet knowledge for this turn.",
        ]
        for match in matches:
            blocks.append(
                "\n".join(
                    [
                        f"- {match.title} [{match.topic}]",
                        f"  path: knowledge/{match.path}",
                        f"  source: {match.source_label}",
                        f"  citation: {match.citation}",
                        f"  source updated: {match.source_updated_at or 'unknown'} | indexed: {match.indexed_at or 'unknown'}",
                        f"  summary: {match.summary}",
                        f"  tags: {', '.join(match.tags) if match.tags else 'none'}",
                        "  relevant excerpt:",
                        self._indent_block(match.excerpt, prefix="    "),
                    ]
                )
            )
        return "\n\n".join(blocks)

    def _ensure_seed_documents(self) -> None:
        for relative_path, content in DEFAULT_SEED_DOCUMENTS.items():
            target = self.knowledge_dir / relative_path
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def _parse_document(self, path: Path, *, stat: object | None = None) -> KnowledgeDocumentEntry:
        ObsidianReadService = _load_obsidian_read_service()
        raw_text = ObsidianReadService._read_markdown_file(path)
        frontmatter, body = ObsidianReadService._parse_frontmatter(raw_text)
        missing = [field for field in REQUIRED_FRONTMATTER_FIELDS if not frontmatter.get(field)]
        if missing:
            raise ValueError(f"Missing required frontmatter fields: {', '.join(missing)}")
        tags = self._coerce_list(frontmatter.get("tags"))
        keywords = self._coerce_list(frontmatter.get("keywords"))
        if not tags:
            raise ValueError("Frontmatter field `tags` must contain at least one value.")
        if not keywords:
            raise ValueError("Frontmatter field `keywords` must contain at least one value.")
        if not body.strip():
            raise ValueError("Knowledge document body is empty.")
        file_stat = stat if stat is not None else path.stat()
        size_bytes = int(getattr(file_stat, "st_size"))
        modified_at = datetime.fromtimestamp(float(getattr(file_stat, "st_mtime")), UTC).isoformat()
        relative_path = str(path.relative_to(self.knowledge_dir)).replace("\\", "/")
        doc_id = self._normalize_token(relative_path.replace("/", "-").replace(".md", ""))
        return KnowledgeDocumentEntry(
            doc_id=doc_id,
            path=relative_path,
            title=self._sanitize_inline_text(str(frontmatter["title"]), limit=160),
            topic=self._sanitize_inline_text(str(frontmatter["topic"]), limit=80),
            summary=self._sanitize_inline_text(str(frontmatter["summary"]), limit=320),
            tags=tuple(tags[:12]),
            keywords=tuple(keywords[:16]),
            source_label=self._sanitize_inline_text(str(frontmatter["source_label"]), limit=120),
            source_url=self._normalize_optional_url(frontmatter.get("source_url")),
            license=self._sanitize_inline_text(str(frontmatter["license"]), limit=64),
            updated_at=self._sanitize_inline_text(str(frontmatter.get("updated_at") or ""), limit=48),
            size_bytes=size_bytes,
            source_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            source_mtime=modified_at,
            source_mtime_ns=int(getattr(file_stat, "st_mtime_ns")),
            indexed_at=datetime.now(UTC).isoformat(),
        )

    def _build_excerpt(
        self,
        relative_path: str,
        *,
        query_tokens: set[str],
        max_chars: int,
    ) -> tuple[str, int, int]:
        path = self.knowledge_dir / relative_path
        ObsidianReadService = _load_obsidian_read_service()
        raw_text = ObsidianReadService._read_markdown_file(path)
        body, body_line_start = self._document_body_with_line_offset(raw_text)
        paragraphs = self._paragraphs_with_lines(body, body_line_start=body_line_start)
        scored: list[tuple[int, str, int, int]] = []
        for paragraph, line_start, line_end in paragraphs:
            if not paragraph:
                continue
            score = len(query_tokens & self._tokenize(paragraph))
            if score <= 0:
                continue
            scored.append((score, paragraph, line_start, line_end))
        scored.sort(key=lambda item: (-item[0], -len(item[1])))
        selected: list[str] = []
        total = 0
        selected_start = 0
        selected_end = 0
        for _, paragraph, line_start, line_end in scored[:2]:
            truncated = paragraph[:max_chars].strip()
            if len(truncated) + total > max_chars:
                truncated = truncated[: max(0, max_chars - total - 3)].rstrip() + "..."
            if not truncated:
                continue
            selected.append(truncated)
            selected_start = line_start if selected_start == 0 else min(selected_start, line_start)
            selected_end = max(selected_end, line_end)
            total += len(truncated)
            if total >= max_chars:
                break
        if not selected:
            fallback = self._sanitize_paragraph(body)[:max_chars].strip()
            return fallback, body_line_start, body_line_start + max(0, len(body.splitlines()) - 1)
        return "\n\n".join(selected), selected_start, selected_end

    @staticmethod
    def _can_reuse(previous: object, *, stat: object, force: bool) -> bool:
        if force or not isinstance(previous, dict):
            return False
        return (
            int(previous.get("size_bytes") or -1) == int(getattr(stat, "st_size"))
            and int(previous.get("source_mtime_ns") or -1) == int(getattr(stat, "st_mtime_ns"))
            and bool(previous.get("source_sha256"))
        )

    @classmethod
    def _document_body_with_line_offset(cls, raw_text: str) -> tuple[str, int]:
        lines = raw_text.splitlines()
        if lines and lines[0].strip() == "---":
            for index, line in enumerate(lines[1:], start=1):
                if line.strip() == "---":
                    return "\n".join(lines[index + 1 :]), index + 2
        return raw_text, 1

    @classmethod
    def _paragraphs_with_lines(cls, body: str, *, body_line_start: int) -> list[tuple[str, int, int]]:
        paragraphs: list[tuple[str, int, int]] = []
        collected: list[str] = []
        paragraph_start = body_line_start
        for index, line in enumerate(body.splitlines(), start=body_line_start):
            if line.strip():
                if not collected:
                    paragraph_start = index
                collected.append(line)
                continue
            if collected:
                paragraphs.append((cls._sanitize_paragraph("\n".join(collected)), paragraph_start, index - 1))
                collected = []
        if collected:
            paragraphs.append(
                (cls._sanitize_paragraph("\n".join(collected)), paragraph_start, body_line_start + len(body.splitlines()) - 1)
            )
        return paragraphs

    @staticmethod
    def _build_citation(document: KnowledgeDocumentEntry, *, line_start: int, line_end: int) -> str:
        location = f"knowledge/{document.path}:L{line_start}-L{line_end}"
        source = document.source_label or "local knowledge document"
        return f"{location} ({source})"

    def _score_document(
        self,
        document: KnowledgeDocumentEntry,
        *,
        query_tokens: set[str],
        query: str,
    ) -> int:
        title_tokens = self._tokenize(document.title)
        topic_tokens = self._tokenize(document.topic)
        summary_tokens = self._tokenize(document.summary)
        tag_tokens = {
            token
            for tag in document.tags
            for token in self._tokenize(tag)
        }
        keyword_tokens = {
            token
            for keyword in document.keywords
            for token in self._tokenize(keyword)
        }
        score = 0
        score += 5 * len(query_tokens & title_tokens)
        score += 4 * len(query_tokens & tag_tokens)
        score += 4 * len(query_tokens & keyword_tokens)
        score += 3 * len(query_tokens & topic_tokens)
        score += 2 * len(query_tokens & summary_tokens)
        normalized_query = self._normalize_token(query)
        if normalized_query and normalized_query in self._normalize_token(document.title):
            score += 8
        if normalized_query and any(normalized_query in self._normalize_token(tag) for tag in document.tags):
            score += 5
        if normalized_query and any(normalized_query in self._normalize_token(keyword) for keyword in document.keywords):
            score += 5
        return score

    @staticmethod
    def _coerce_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [
                token.strip()
                for token in re.split(r"[,\n]+", value)
                if token.strip()
            ]
        return []

    @staticmethod
    def _normalize_optional_url(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        return candidate or None

    @staticmethod
    def _sanitize_inline_text(value: str, *, limit: int) -> str:
        text = value.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text[:limit].strip()

    @classmethod
    def _sanitize_paragraph(cls, value: str) -> str:
        cleaned = value.replace("\r", "\n")
        cleaned = re.sub(r"\n{2,}", "\n", cleaned)
        cleaned = re.sub(r"^\s*#+\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    @classmethod
    def _normalize_token(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.translate(ASCII_TRANSLITERATION))
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        ascii_text = ascii_text.lower()
        ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
        return ascii_text

    @classmethod
    def _tokenize(cls, value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKD", value.translate(ASCII_TRANSLITERATION))
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
        tokens = {
            token
            for token in re.findall(r"[a-z0-9]{3,}", ascii_text)
            if token not in QUERY_STOPWORDS
        }
        expanded = set(tokens)
        for token in list(tokens):
            expanded.update(TOKEN_ALIASES.get(token, ()))
        return expanded

    @staticmethod
    def _indent_block(value: str, *, prefix: str) -> str:
        return "\n".join(f"{prefix}{line}" if line.strip() else "" for line in value.splitlines())
