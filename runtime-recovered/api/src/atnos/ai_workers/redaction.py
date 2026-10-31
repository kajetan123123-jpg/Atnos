from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


SECRET_KEY_TOKENS = (
    "password",
    "passwd",
    "haslo",
    "hasło",
    "token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "authorization",
    "bearer",
    "cookie",
    "card",
    "cvv",
    "cvc",
    "pin",
    "recovery",
    "totp",
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
    re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    re.compile(r"\b\d{3,4}\b(?=\s*(?:cvv|cvc|kod|security))", re.IGNORECASE),
)

STRUCTURAL_IDENTIFIER_KEYS = frozenset(
    {
        "account_id",
        "job_id",
        "parent_task_id",
        "task_id",
        "topic_id",
    }
)

# Structural IDs are often long slug-like strings. Keep those identifiers usable while
# still removing explicit provider keys and payment data accidentally placed in an ID field.
STRUCTURAL_IDENTIFIER_SECRET_PATTERNS = (
    SECRET_PATTERNS[0],
    SECRET_PATTERNS[1],
    SECRET_PATTERNS[4],
)

FULL_PAYMENT_CARD_PATTERN = re.compile(r"^(?:\d[ -]?){13,19}$")

LABELED_SECRET_PATTERN = re.compile(
    r"(?P<label>password|passwd|haslo|hasło|token|api[_-]?key|apikey|secret|client_secret|"
    r"authorization|bearer|cookie|card[_ -]?number|cvv|cvc|pin|totp)"
    r"(?P<separator>\s*(?::|=|\bis\b|\bto\b)\s*)"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)


def redact_text(value: str, *, replacement: str = "[REDACTED]") -> str:
    redacted = LABELED_SECRET_PATTERN.sub(
        lambda match: f"{match.group('label')}{match.group('separator')}{replacement}",
        value,
    )
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[REDACTED_DEPTH]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_secret_key(key):
                sanitized[key] = "[REDACTED]"
            elif key.strip().casefold() in STRUCTURAL_IDENTIFIER_KEYS and isinstance(raw_value, str):
                sanitized[key] = _redact_structural_identifier(raw_value)
            else:
                sanitized[key] = redact_payload(raw_value, depth=depth + 1)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact_payload(item, depth=depth + 1) for item in value]
    return str(value)


def _is_secret_key(key: str) -> bool:
    lowered = key.strip().casefold().replace("ł", "l")
    return any(token.replace("ł", "l") in lowered for token in SECRET_KEY_TOKENS)


def _redact_structural_identifier(value: str, *, replacement: str = "[REDACTED]") -> str:
    if FULL_PAYMENT_CARD_PATTERN.fullmatch(value.strip()):
        return replacement
    redacted = LABELED_SECRET_PATTERN.sub(
        lambda match: f"{match.group('label')}{match.group('separator')}{replacement}",
        value,
    )
    for pattern in STRUCTURAL_IDENTIFIER_SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
