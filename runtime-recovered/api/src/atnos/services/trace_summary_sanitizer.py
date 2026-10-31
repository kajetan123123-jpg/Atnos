from __future__ import annotations

import re

from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS


SUMMARY_LENGTH_LIMITS = {
    "input_summary": 320,
    "output_summary": 320,
    "error_message": 240,
}

SENSITIVE_REPLACEMENTS = {
    "input_summary": "redacted-sensitive-input",
    "output_summary": "redacted-sensitive-output",
    "error_message": "redacted-sensitive-error",
}

FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}\b", re.IGNORECASE),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[\\/][^\s]+", re.IGNORECASE),
    re.compile(r"(?:^|[\s{[])\{.*:.*\}(?:$|[\s\]}])", re.IGNORECASE),
)

FORBIDDEN_CONTEXT_SNIPPETS = (
    "user message:",
    "content preview:",
    "attached file preview:",
    "active document preview:",
    "role instructions:",
    "trace_body_secret_marker",
    "trace_attachment_secret_marker",
    "stored-only-as-metadata",
    "creator-secret-value",
)


def sanitize_trace_summary(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None

    normalized = " ".join(value.split())
    if not normalized:
        return None

    if _contains_forbidden_summary_content(normalized):
        return SENSITIVE_REPLACEMENTS[field_name]

    limit = SUMMARY_LENGTH_LIMITS[field_name]
    if len(normalized) > limit:
        return normalized[: limit - 3].rstrip() + "..."
    return normalized


def _contains_forbidden_summary_content(value: str) -> bool:
    lowered = value.lower()
    if any(token in lowered for token in FORBIDDEN_KEY_TOKENS):
        return True
    if any(snippet in lowered for snippet in FORBIDDEN_CONTEXT_SNIPPETS):
        return True
    return any(pattern.search(value) for pattern in FORBIDDEN_VALUE_PATTERNS)
