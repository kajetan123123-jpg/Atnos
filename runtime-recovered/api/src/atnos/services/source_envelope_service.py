from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS

MAX_TITLE_CHARS = 180
MAX_CONTENT_TYPE_CHARS = 80
MAX_AUTHOR_CHARS = 120
MAX_PARTICIPANT_CHARS = 120
MAX_TAG_CHARS = 40
MAX_ALLOWED_ACTION_CHARS = 64
MAX_METADATA_STRING_CHARS = 200
MAX_CONTENT_PREVIEW_CHARS = 800
MAX_CHUNK_TEXT_CHARS = 500
MAX_CITATION_TEXT_CHARS = 220
MAX_METADATA_KEYS = 24
MAX_METADATA_LIST_ITEMS = 16
MAX_CHUNKS = 24
MAX_CITATIONS = 16


class SourceKind(StrEnum):
    LOCAL_FILE = "local_file"
    OBSIDIAN = "obsidian"
    GITHUB = "github"
    GOOGLE_DRIVE = "google_drive"
    GMAIL = "gmail"
    CALENDAR = "calendar"
    NOTION = "notion"
    SLACK = "slack"
    LINEAR = "linear"
    FIGMA = "figma"
    AIRTABLE = "airtable"
    MANUAL_NOTE = "manual_note"
    CODEX_REPORT = "codex_report"
    UNKNOWN = "unknown"


class PrivacyClass(StrEnum):
    PUBLIC = "public"
    PERSONAL = "personal"
    WORK_INTERNAL = "work_internal"
    SENSITIVE = "sensitive"
    SECRET_NEVER_STORE = "secret_never_store"


class RetentionPolicy(StrEnum):
    SESSION_ONLY = "session_only"
    SHORT_LIVED = "short_lived"
    PROJECT_MEMORY_CANDIDATE = "project_memory_candidate"
    LONG_LIVED = "long_lived"
    NEVER_PERSIST = "never_persist"


class PermissionMode(StrEnum):
    READ_ONLY = "read_only"
    DRAFT_WRITE = "draft_write"
    CONFIRMED_WRITE_LATER = "confirmed_write_later"
    BLOCKED = "blocked"


class MemoryEligibility(StrEnum):
    NEVER = "never"
    TEMPORARY_CONTEXT = "temporary_context"
    PROJECT_MEMORY_CANDIDATE = "project_memory_candidate"
    USER_CONFIRMED_MEMORY = "user_confirmed_memory"


class PromptInjectionRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNTRUSTED_EXTERNAL = "untrusted_external"


class SourceChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str | None = Field(default=None, max_length=120)
    text: str = Field(min_length=1)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)


class SourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=120)
    locator: str | None = Field(default=None, max_length=160)
    origin_uri: str | None = Field(default=None, max_length=1024)
    quote: str | None = Field(default=None, max_length=400)


class SourceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceKind
    source_item_id: str | None = Field(default=None, max_length=160)
    project_id: int | None = Field(default=None, ge=1)
    origin_uri: str | None = Field(default=None, max_length=1024)
    title: str | None = Field(default=None, max_length=240)
    content_type: str | None = Field(default=None, max_length=120)
    captured_at: datetime | None = None
    updated_at: datetime | None = None
    author: str | None = Field(default=None, max_length=160)
    participants: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    privacy_class: PrivacyClass = PrivacyClass.WORK_INTERNAL
    retention_policy: RetentionPolicy = RetentionPolicy.SHORT_LIVED
    permission_mode: PermissionMode = PermissionMode.READ_ONLY
    memory_eligibility: MemoryEligibility = MemoryEligibility.TEMPORARY_CONTEXT
    prompt_injection_risk: PromptInjectionRisk | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_preview: str | None = None
    chunks: list[SourceChunk] = Field(default_factory=list)
    citations: list[SourceCitation] = Field(default_factory=list)


class SourceContextSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceKind
    title: str
    content_type: str
    privacy_class: PrivacyClass
    memory_eligibility: MemoryEligibility
    prompt_injection_risk: PromptInjectionRisk
    chunk_count: int
    citation_count: int
    safe_excerpt: str


SAFE_PROJECT_MEMORY_SOURCES = frozenset(
    {
        SourceKind.OBSIDIAN,
        SourceKind.LOCAL_FILE,
        SourceKind.GITHUB,
        SourceKind.GOOGLE_DRIVE,
        SourceKind.NOTION,
        SourceKind.LINEAR,
        SourceKind.FIGMA,
        SourceKind.AIRTABLE,
        SourceKind.MANUAL_NOTE,
        SourceKind.CODEX_REPORT,
    }
)

TEMPORARY_CONTEXT_SOURCES = frozenset(
    {
        SourceKind.GMAIL,
        SourceKind.SLACK,
        SourceKind.CALENDAR,
    }
)


class SourceEnvelopeService:
    def preview_envelope(self, envelope: SourceEnvelope) -> tuple[SourceEnvelope, SourceContextSummary]:
        sanitized = self._sanitize_envelope(envelope)
        summary = build_source_context_summary(sanitized)
        return sanitized, summary

    def schema(self) -> dict[str, object]:
        return {
            "fields": list(SourceEnvelope.model_fields.keys()),
            "allowed_sources": [item.value for item in SourceKind],
            "privacy_classes": [item.value for item in PrivacyClass],
            "retention_policies": [item.value for item in RetentionPolicy],
            "permission_modes": [item.value for item in PermissionMode],
            "memory_eligibility": [item.value for item in MemoryEligibility],
            "prompt_injection_risks": [item.value for item in PromptInjectionRisk],
            "preview_limits": {
                "content_preview_chars": MAX_CONTENT_PREVIEW_CHARS,
                "chunk_text_chars": MAX_CHUNK_TEXT_CHARS,
                "citation_quote_chars": MAX_CITATION_TEXT_CHARS,
                "chunks": MAX_CHUNKS,
                "citations": MAX_CITATIONS,
            },
        }

    def _sanitize_envelope(self, envelope: SourceEnvelope) -> SourceEnvelope:
        risk = envelope.prompt_injection_risk or self._default_prompt_injection_risk(envelope.source)
        retention = envelope.retention_policy
        privacy = envelope.privacy_class
        if privacy == PrivacyClass.SECRET_NEVER_STORE:
            retention = RetentionPolicy.NEVER_PERSIST

        sanitized = SourceEnvelope(
            source=envelope.source,
            source_item_id=_sanitize_inline_text(envelope.source_item_id, MAX_METADATA_STRING_CHARS),
            project_id=envelope.project_id,
            origin_uri=_sanitize_inline_text(envelope.origin_uri, 1024),
            title=_sanitize_inline_text(envelope.title, MAX_TITLE_CHARS),
            content_type=_sanitize_inline_text(envelope.content_type, MAX_CONTENT_TYPE_CHARS),
            captured_at=_normalize_datetime(envelope.captured_at),
            updated_at=_normalize_datetime(envelope.updated_at),
            author=_sanitize_inline_text(envelope.author, MAX_AUTHOR_CHARS),
            participants=_sanitize_string_list(envelope.participants, MAX_PARTICIPANT_CHARS, limit=12),
            tags=_sanitize_string_list(envelope.tags, MAX_TAG_CHARS, limit=16),
            privacy_class=privacy,
            retention_policy=retention,
            permission_mode=envelope.permission_mode,
            memory_eligibility=classify_source_for_memory(
                source=envelope.source,
                privacy_class=privacy,
                retention_policy=retention,
                requested_memory_eligibility=envelope.memory_eligibility,
                prompt_injection_risk=risk,
            ),
            prompt_injection_risk=risk,
            allowed_actions=_sanitize_string_list(
                envelope.allowed_actions,
                MAX_ALLOWED_ACTION_CHARS,
                limit=12,
            ),
            metadata=_sanitize_metadata(envelope.metadata),
            content_preview=_sanitize_preview_text(envelope.content_preview, MAX_CONTENT_PREVIEW_CHARS),
            chunks=self._sanitize_chunks(envelope.chunks),
            citations=self._sanitize_citations(envelope.citations),
        )
        return sanitized

    @staticmethod
    def _default_prompt_injection_risk(source: SourceKind) -> PromptInjectionRisk:
        if source in TEMPORARY_CONTEXT_SOURCES:
            return PromptInjectionRisk.UNTRUSTED_EXTERNAL
        if source in {SourceKind.OBSIDIAN, SourceKind.LOCAL_FILE, SourceKind.MANUAL_NOTE, SourceKind.CODEX_REPORT}:
            return PromptInjectionRisk.LOW
        if source == SourceKind.UNKNOWN:
            return PromptInjectionRisk.MEDIUM
        return PromptInjectionRisk.MEDIUM

    @staticmethod
    def _sanitize_chunks(chunks: list[SourceChunk]) -> list[SourceChunk]:
        sanitized: list[SourceChunk] = []
        for chunk in chunks[:MAX_CHUNKS]:
            text = _sanitize_preview_text(chunk.text, MAX_CHUNK_TEXT_CHARS)
            if not text:
                continue
            sanitized.append(
                SourceChunk(
                    chunk_id=_sanitize_inline_text(chunk.chunk_id, 120),
                    text=text,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                )
            )
        return sanitized

    @staticmethod
    def _sanitize_citations(citations: list[SourceCitation]) -> list[SourceCitation]:
        sanitized: list[SourceCitation] = []
        for citation in citations[:MAX_CITATIONS]:
            sanitized.append(
                SourceCitation(
                    label=_sanitize_inline_text(citation.label, 120),
                    locator=_sanitize_inline_text(citation.locator, 160),
                    origin_uri=_sanitize_inline_text(citation.origin_uri, 1024),
                    quote=_sanitize_preview_text(citation.quote, MAX_CITATION_TEXT_CHARS),
                )
            )
        return sanitized


def classify_source_for_memory(
    *,
    source: SourceKind,
    privacy_class: PrivacyClass,
    retention_policy: RetentionPolicy,
    requested_memory_eligibility: MemoryEligibility,
    prompt_injection_risk: PromptInjectionRisk,
) -> MemoryEligibility:
    if privacy_class == PrivacyClass.SECRET_NEVER_STORE:
        return MemoryEligibility.NEVER
    if retention_policy == RetentionPolicy.NEVER_PERSIST:
        return MemoryEligibility.NEVER
    if prompt_injection_risk == PromptInjectionRisk.UNTRUSTED_EXTERNAL:
        return MemoryEligibility.TEMPORARY_CONTEXT
    if source in TEMPORARY_CONTEXT_SOURCES:
        return MemoryEligibility.TEMPORARY_CONTEXT
    if source in SAFE_PROJECT_MEMORY_SOURCES:
        if requested_memory_eligibility == MemoryEligibility.USER_CONFIRMED_MEMORY:
            return MemoryEligibility.USER_CONFIRMED_MEMORY
        return MemoryEligibility.PROJECT_MEMORY_CANDIDATE
    if requested_memory_eligibility == MemoryEligibility.NEVER:
        return MemoryEligibility.NEVER
    return MemoryEligibility.TEMPORARY_CONTEXT


def build_source_context_summary(envelope: SourceEnvelope) -> SourceContextSummary:
    safe_excerpt = _first_non_empty(
        envelope.content_preview,
        *(chunk.text for chunk in envelope.chunks),
        default="No safe excerpt available.",
    )
    return SourceContextSummary(
        source=envelope.source,
        title=envelope.title or "Untitled source item",
        content_type=envelope.content_type or "unknown",
        privacy_class=envelope.privacy_class,
        memory_eligibility=envelope.memory_eligibility,
        prompt_injection_risk=envelope.prompt_injection_risk or PromptInjectionRisk.MEDIUM,
        chunk_count=len(envelope.chunks),
        citation_count=len(envelope.citations),
        safe_excerpt=_sanitize_preview_text(safe_excerpt, MAX_CONTENT_PREVIEW_CHARS)
        or "No safe excerpt available.",
    )


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in list(metadata.items())[:MAX_METADATA_KEYS]:
        key = _sanitize_metadata_key(raw_key)
        if not key:
            continue
        value = _sanitize_metadata_value(raw_value)
        if value is _DROP:
            continue
        sanitized[key] = value
    return sanitized


def _sanitize_metadata_key(raw_key: object) -> str | None:
    if not isinstance(raw_key, str):
        return None
    normalized = raw_key.strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    if any(token in lowered for token in FORBIDDEN_KEY_TOKENS):
        return None
    return normalized[:80]


_DROP = object()


def _sanitize_metadata_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 2:
        return _DROP
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        sanitized = _sanitize_inline_text(value, MAX_METADATA_STRING_CHARS)
        return sanitized if sanitized else _DROP
    if isinstance(value, list):
        items: list[Any] = []
        for item in value[:MAX_METADATA_LIST_ITEMS]:
            sanitized_item = _sanitize_metadata_value(item, depth=depth + 1)
            if sanitized_item is _DROP:
                continue
            items.append(sanitized_item)
        return items or _DROP
    if isinstance(value, dict):
        nested: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:MAX_METADATA_KEYS]:
            key = _sanitize_metadata_key(raw_key)
            if not key:
                continue
            nested_value = _sanitize_metadata_value(raw_value, depth=depth + 1)
            if nested_value is _DROP:
                continue
            nested[key] = nested_value
        return nested or _DROP
    return _DROP


def _sanitize_string_list(values: list[str], limit_chars: int, *, limit: int) -> list[str]:
    sanitized: list[str] = []
    seen: set[str] = set()
    for raw_value in values[:limit]:
        value = _sanitize_inline_text(raw_value, limit_chars)
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        sanitized.append(value)
    return sanitized


def _sanitize_preview_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    preview = _redact_text(value)
    preview = preview.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    preview = re.sub(r"\n{3,}", "\n\n", preview).strip()
    if not preview:
        return None
    if len(preview) > limit:
        return preview[: limit - 3].rstrip() + "..."
    return preview


def _sanitize_inline_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(_redact_text(value).lstrip("\ufeff").split())
    if not normalized:
        return None
    if len(normalized) > limit:
        return normalized[: limit - 3].rstrip() + "..."
    return normalized


def _redact_text(value: str) -> str:
    redacted = value
    redacted = re.sub(r"(?i)\bBearer\s+\S+", "[REDACTED]", redacted)
    redacted = re.sub(r"(?i)\bsk-[A-Za-z0-9_\-]{6,}\b", "[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)\b(password|token|api_key|secret_ref|client_secret|private_key|account_secret|secret_value|credential|authorization|headers|cookies)\b\s*[:=]?\s*\S*",
        "[REDACTED]",
        redacted,
    )
    return redacted


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _first_non_empty(*values: str | None, default: str) -> str:
    for value in values:
        if value:
            return value
    return default
