from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING
import unicodedata

from atnos.services.knowledge_library_service import QUERY_STOPWORDS

if TYPE_CHECKING:
    from atnos.core.config import Settings
    from atnos.services.attachment_understanding_service import AttachmentUnderstandingService
    from atnos.services.knowledge_library_service import KnowledgeLibraryService

WORKSPACE_BUCKETS = ("courses", "onlyfans", "instagram", "investing", "general")
TOPIC_BY_BUCKET = {
    "courses": "learning",
    "onlyfans": "creator-monetization",
    "instagram": "content",
    "investing": "investing",
    "general": "reference",
}
TAGS_BY_BUCKET = {
    "courses": ("learning", "course"),
    "onlyfans": ("creator", "onlyfans"),
    "instagram": ("instagram", "short-form"),
    "investing": ("investing", "trading"),
    "general": ("reference",),
}
README_TEXT = """# Atnos Library Workspace

To jest staging area dla materiałów źródłowych użytkownika.

## Przepływ

1. wrzucasz pliki do `inbox/`
2. Atnos kopiuje je do `sources/`
3. Atnos buduje z nich curated notatki w `curated/`
4. te notatki są mirroringowane do wewnętrznego `knowledge/` i trafiają do wyszukiwarki czatu

## Zasady

- wrzucaj tylko materiały, do których masz prawo dostępu
- prywatne, zalogowane karty w Twojej przeglądarce nie są automatycznie przejmowane
- jeśli chcesz użyć materiału z webu, pobierz go albo wrzuć screenshot / PDF / eksport do `inbox/`
"""


@dataclass(frozen=True, slots=True)
class KnowledgeWorkspaceItem:
    item_id: str
    bucket: str
    inbox_path: str
    source_path: str
    curated_path: str | None
    knowledge_path: str | None
    filename: str
    title: str | None
    status: str
    detected_kind: str | None
    understanding_status: str | None
    preview_chars: int
    imported_at: str


class KnowledgeWorkspaceService:
    CATALOG_FILENAME = "catalog.json"

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.workspace_dir = settings.knowledge_workspace_dir
        self.inbox_dir = self.workspace_dir / "inbox"
        self.sources_dir = self.workspace_dir / "sources"
        self.curated_dir = self.workspace_dir / "curated"
        self.manifests_dir = self.workspace_dir / "manifests"
        self.catalog_path = self.workspace_dir / self.CATALOG_FILENAME
        self.readme_path = self.workspace_dir / "README.md"
        self._understanding: "AttachmentUnderstandingService | None" = None
        self._knowledge: "KnowledgeLibraryService | None" = None

    @property
    def understanding(self) -> "AttachmentUnderstandingService":
        if self._understanding is None:
            from atnos.services.attachment_understanding_service import AttachmentUnderstandingService

            self._understanding = AttachmentUnderstandingService()
        return self._understanding

    @property
    def knowledge(self) -> "KnowledgeLibraryService":
        if self._knowledge is None:
            from atnos.services.knowledge_library_service import KnowledgeLibraryService

            self._knowledge = KnowledgeLibraryService(self.settings)
        return self._knowledge

    def ensure_workspace(self) -> dict[str, object]:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        for root in (self.inbox_dir, self.sources_dir, self.curated_dir, self.manifests_dir):
            root.mkdir(parents=True, exist_ok=True)
            for bucket in WORKSPACE_BUCKETS:
                (root / bucket).mkdir(parents=True, exist_ok=True)
        if not self.readme_path.exists():
            self.readme_path.write_text(README_TEXT, encoding="utf-8")
        if not self.catalog_path.exists():
            self._write_catalog(items=[])
        return self._build_workspace_status()

    def get_workspace_status(self) -> dict[str, object]:
        if not self.catalog_path.exists() or not self.readme_path.exists():
            return self.ensure_workspace()
        return self._build_workspace_status()

    def save_curated_note(
        self,
        *,
        bucket: str,
        title: str,
        summary: str,
        body_markdown: str,
        keywords: list[str] | tuple[str, ...] | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        source_label: str = "Atnos learned observation",
        source_url: str = "local://atnos/observation",
        license_label: str = "Derived local observation",
    ) -> dict[str, str]:
        self.ensure_workspace()
        normalized_bucket = bucket if bucket in WORKSPACE_BUCKETS else "general"
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        slug = self._slugify(title) or "learned-note"
        note_id = f"{normalized_bucket}-{slug}-{timestamp}"
        curated_path = self.curated_dir / normalized_bucket / f"{note_id}.md"
        knowledge_path = self.settings.knowledge_dir / "imported" / normalized_bucket / f"{note_id}.md"
        curated_path.parent.mkdir(parents=True, exist_ok=True)
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)

        cleaned_summary = self._normalize_line(summary)[:320] or "Atnos saved a learned observation."
        keyword_values = list(dict.fromkeys((keywords or []) or self._derive_keywords(f"{title}\n{body_markdown}", bucket=normalized_bucket)))
        tag_values = list(
            dict.fromkeys(
                (
                    "learned",
                    "local-library",
                    normalized_bucket,
                    *TAGS_BY_BUCKET.get(normalized_bucket, tuple()),
                    *((tags or []) if tags is not None else tuple()),
                )
            )
        )
        updated_at = datetime.now(UTC).date().isoformat()
        note = "\n".join(
            [
                "---",
                f"title: {title}",
                f"topic: {TOPIC_BY_BUCKET.get(normalized_bucket, 'reference')}",
                f"summary: {cleaned_summary}",
                "tags:",
                *[f"  - {tag}" for tag in tag_values],
                "keywords:",
                *[f"  - {keyword}" for keyword in keyword_values[:12]],
                f"source_label: {source_label}",
                f"source_url: {source_url}",
                f"license: {license_label}",
                f"updated_at: {updated_at}",
                "---",
                f"# {title}",
                "",
                body_markdown.strip(),
                "",
                f"## Library item id",
                f"`{note_id}`",
                "",
            ]
        )
        curated_path.write_text(note, encoding="utf-8")
        knowledge_path.write_text(note, encoding="utf-8")
        self.knowledge.rebuild_index()
        return {
            "note_id": note_id,
            "curated_path": str(curated_path),
            "knowledge_path": str(knowledge_path),
        }

    def _build_workspace_status(self) -> dict[str, object]:
        catalog = self._read_catalog()
        return {
            "workspace_dir": str(self.workspace_dir),
            "inbox_dir": str(self.inbox_dir),
            "sources_dir": str(self.sources_dir),
            "curated_dir": str(self.curated_dir),
            "manifests_dir": str(self.manifests_dir),
            "catalog_path": str(self.catalog_path),
            "readme_path": str(self.readme_path),
            "bucket_names": list(WORKSPACE_BUCKETS),
            "item_count": len(catalog.get("items") or []),
        }

    def sync_inbox(self) -> dict[str, object]:
        self.ensure_workspace()
        imported_items: list[KnowledgeWorkspaceItem] = []
        processed_count = 0
        unsupported_count = 0
        mirrored_count = 0
        skipped_count = 0

        for candidate in sorted(self.inbox_dir.rglob("*")):
            if not candidate.is_file():
                continue
            processed_count += 1
            item = self._import_candidate(candidate)
            imported_items.append(item)
            if item.status == "unsupported":
                unsupported_count += 1
            elif item.status == "mirrored":
                mirrored_count += 1
            elif item.status == "skipped":
                skipped_count += 1

        self._write_catalog(items=imported_items)
        index_payload = self.knowledge.rebuild_index()
        return {
            **self.get_workspace_status(),
            "processed_count": processed_count,
            "imported_count": len(imported_items),
            "unsupported_count": unsupported_count,
            "mirrored_count": mirrored_count,
            "skipped_count": skipped_count,
            "knowledge_document_count": int(index_payload.get("document_count") or 0),
            "items": [asdict(item) for item in imported_items],
        }

    def _import_candidate(self, path: Path) -> KnowledgeWorkspaceItem:
        relative_inbox_path = path.relative_to(self.inbox_dir)
        bucket = relative_inbox_path.parts[0].lower() if relative_inbox_path.parts else "general"
        if bucket not in WORKSPACE_BUCKETS:
            bucket = "general"

        file_bytes = path.read_bytes()
        digest = hashlib.sha1(file_bytes).hexdigest()
        stem_slug = self._slugify(path.stem) or "source"
        item_id = f"{bucket}-{stem_slug}-{digest[:10]}"
        source_dir = self.sources_dir / bucket / item_id
        source_dir.mkdir(parents=True, exist_ok=True)
        mirrored_source_path = source_dir / path.name
        shutil.copy2(path, mirrored_source_path)

        understanding = self.understanding.understand(
            filename=path.name,
            content=file_bytes,
            content_type=None,
        )

        extracted_preview = (
            understanding.extracted_text
            or understanding.preview_text
            or ""
        ).strip()
        extracted_preview = self._repair_mojibake(extracted_preview)
        imported_at = datetime.now(UTC).isoformat()
        curated_note_path: Path | None = None
        knowledge_note_path: Path | None = None
        title: str | None = None
        status = "unsupported"

        if extracted_preview:
            title = self._resolve_title(path, extracted_preview)
            note_markdown = self._build_curated_note(
                bucket=bucket,
                item_id=item_id,
                original_path=path,
                mirrored_source_path=mirrored_source_path,
                title=title,
                extracted_preview=extracted_preview,
                imported_at=imported_at,
            )
            curated_note_path = self.curated_dir / bucket / f"{item_id}.md"
            curated_note_path.parent.mkdir(parents=True, exist_ok=True)
            curated_note_path.write_text(note_markdown, encoding="utf-8")

            knowledge_note_path = self.settings.knowledge_dir / "imported" / bucket / f"{item_id}.md"
            knowledge_note_path.parent.mkdir(parents=True, exist_ok=True)
            knowledge_note_path.write_text(note_markdown, encoding="utf-8")
            status = "mirrored"

        manifest_path = self.manifests_dir / bucket / f"{item_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "item_id": item_id,
                    "bucket": bucket,
                    "inbox_path": str(relative_inbox_path).replace("\\", "/"),
                    "source_path": str(mirrored_source_path),
                    "curated_path": str(curated_note_path) if curated_note_path is not None else None,
                    "knowledge_path": str(knowledge_note_path) if knowledge_note_path is not None else None,
                    "filename": path.name,
                    "title": title,
                    "status": status,
                    "detected_kind": understanding.detected_kind,
                    "understanding_status": understanding.status,
                    "understanding_reason": understanding.status_reason,
                    "preview_chars": len(extracted_preview),
                    "imported_at": imported_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return KnowledgeWorkspaceItem(
            item_id=item_id,
            bucket=bucket,
            inbox_path=str(relative_inbox_path).replace("\\", "/"),
            source_path=str(mirrored_source_path),
            curated_path=str(curated_note_path) if curated_note_path is not None else None,
            knowledge_path=str(knowledge_note_path) if knowledge_note_path is not None else None,
            filename=path.name,
            title=title,
            status=status,
            detected_kind=understanding.detected_kind,
            understanding_status=understanding.status,
            preview_chars=len(extracted_preview),
            imported_at=imported_at,
        )

    def _build_curated_note(
        self,
        *,
        bucket: str,
        item_id: str,
        original_path: Path,
        mirrored_source_path: Path,
        title: str,
        extracted_preview: str,
        imported_at: str,
    ) -> str:
        topic = TOPIC_BY_BUCKET.get(bucket, "reference")
        summary = self._build_summary(extracted_preview)
        keywords = self._derive_keywords(extracted_preview, bucket=bucket)
        tags = ("imported", "local-library", bucket, *TAGS_BY_BUCKET.get(bucket, tuple()))
        source_rel = self._safe_relative(original_path, self.workspace_dir)
        mirrored_rel = self._safe_relative(mirrored_source_path, self.workspace_dir)
        updated_at = imported_at.split("T", maxsplit=1)[0]
        frontmatter = [
            "---",
            f"title: {title}",
            f"topic: {topic}",
            f"summary: {summary}",
            "tags:",
            *[f"  - {tag}" for tag in dict.fromkeys(tags)],
            "keywords:",
            *[f"  - {keyword}" for keyword in keywords],
            "source_label: User supplied library source",
            f"source_url: local://atnos-library/{source_rel}",
            "license: User-supplied",
            f"updated_at: {updated_at}",
            "---",
            f"# {title}",
            "",
            "## Source",
            f"- inbox path: `{source_rel}`",
            f"- mirrored source: `{mirrored_rel}`",
            f"- imported at: `{imported_at}`",
            "",
            "## Curated summary",
            summary,
            "",
            "## Extracted reference excerpt",
            extracted_preview[:7800].strip(),
            "",
            f"## Library item id",
            f"`{item_id}`",
            "",
        ]
        return "\n".join(frontmatter)

    def _resolve_title(self, path: Path, extracted_preview: str) -> str:
        lines = [self._normalize_line(line) for line in extracted_preview.splitlines()]
        cleaned = [
            line for line in lines
            if line
            and not line.startswith("[Page ")
            and line.lower() not in {"spis treści", "disclaimer"}
            and len(line) >= 4
        ]
        if cleaned:
            first = cleaned[0]
            if len(cleaned) > 1 and cleaned[1].isupper() and len(cleaned[1]) < 80:
                first = f"{first} — {cleaned[1].title()}"
            return first[:160]
        return self._humanize_stem(path.stem)

    def _build_summary(self, extracted_preview: str) -> str:
        normalized = re.sub(r"\s+", " ", extracted_preview).strip()
        if not normalized:
            return "Imported local source prepared for Atnos knowledge lookup."
        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        summary = " ".join(sentence.strip() for sentence in sentences[:2] if sentence.strip())
        if not summary:
            summary = normalized[:260].strip()
        return summary[:320].rstrip()

    def _derive_keywords(self, extracted_preview: str, *, bucket: str) -> list[str]:
        tokens = []
        for match in re.finditer(r"[A-Za-zÀ-ÿ0-9]{3,}", extracted_preview.lower()):
            token = self._slugify(match.group(0))
            if not token or token in QUERY_STOPWORDS:
                continue
            tokens.append(token)
        frequency: dict[str, int] = {}
        for token in tokens:
            frequency[token] = frequency.get(token, 0) + 1
        ranked = sorted(frequency.items(), key=lambda item: (-item[1], item[0]))
        keywords = [bucket, *TAGS_BY_BUCKET.get(bucket, tuple())]
        for token, _ in ranked:
            if token not in keywords:
                keywords.append(token)
            if len(keywords) >= 8:
                break
        return keywords[:8]

    def _write_catalog(self, *, items: list[KnowledgeWorkspaceItem]) -> None:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "workspace_dir": str(self.workspace_dir),
            "items": [asdict(item) for item in items],
        }
        self.catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_catalog(self) -> dict[str, object]:
        try:
            raw = self.catalog_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
        return re.sub(r"-{2,}", "-", cleaned)

    @staticmethod
    def _humanize_stem(value: str) -> str:
        text = re.sub(r"[_\-]+", " ", value).strip()
        return text[:1].upper() + text[1:] if text else "Imported source"

    @staticmethod
    def _normalize_line(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip(" -\t")

    @staticmethod
    def _safe_relative(path: Path, root: Path) -> str:
        try:
            return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
        except ValueError:
            return path.as_posix()

    @classmethod
    def _repair_mojibake(cls, value: str) -> str:
        text = value.strip()
        if not text:
            return text
        if cls._mojibake_score(text) == 0:
            return text

        candidates = [text]
        for encoding in ("cp1252", "latin1"):
            try:
                candidates.append(text.encode(encoding).decode("utf-8"))
            except Exception:
                continue

        return min(candidates, key=cls._repair_score)

    @staticmethod
    def _repair_score(value: str) -> tuple[int, int, int]:
        return (
            KnowledgeWorkspaceService._mojibake_score(value),
            value.count("?") + value.count("\uFFFD"),
            len(value),
        )

    @staticmethod
    def _mojibake_score(value: str) -> int:
        markers = ("Ã", "Â", "Å", "Ä", "â", "Ă", "Ĺ", "\uFFFD")
        return sum(value.count(marker) for marker in markers)
