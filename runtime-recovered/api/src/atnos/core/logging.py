from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from logging.config import dictConfig
from pathlib import Path
from typing import Any

from atnos.core.config import Settings

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)([?&](?:key|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
            r"auth[_-]?token|webhook[_-]?token|token|client[_-]?secret|smtp[_-]?password|"
            r"password|has(?:l|ł)o|secret|cookie|session|code[_-]?verifier|"
            r"card[_-]?number|cvv|cvc|pin)=)[^&\s\"']+"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
            r"auth[_-]?token|webhook[_-]?token|token|client[_-]?secret|smtp[_-]?password|"
            r"password|has(?:l|ł)o|secret|authorization|cookie|session|code[_-]?verifier|"
            r"card[_ -]?number|cvv|cvc|pin)"
            r"\s*[:=]\s*[^\s,;&\"']+"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"), "[REDACTED_GOOGLE_API_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), "[REDACTED_PAYMENT_CARD]"),
)

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "secret",
    "password",
    "haslo",
    "hasło",
    "card",
    "card_number",
    "cvv",
    "cvc",
    "pin",
    "authorization",
    "auth",
}

NORMALIZED_SENSITIVE_KEYS = frozenset(
    re.sub(r"[^a-z0-9]", "", item.casefold()) for item in SENSITIVE_KEYS
)

SENSITIVE_KEY_SUFFIXES = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "session",
    "token",
    "verifier",
)


def _is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold().replace("ł", "l"))
    if not normalized:
        return False
    return normalized in NORMALIZED_SENSITIVE_KEYS or normalized.endswith(SENSITIVE_KEY_SUFFIXES)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, str):
        redacted = value
        for pattern, replacement in SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if _is_sensitive_key(key) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return type(value)(redact_secrets(item) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_secrets(str(value))


def append_redacted_jsonl(
    path: Path,
    payload: Mapping[str, Any],
    *,
    default: Callable[[object], Any] | None = str,
) -> None:
    """Append one JSONL record after applying the central secret redaction policy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = redact_secrets(payload)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_payload, ensure_ascii=False, default=default) + "\n")


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.msg)
        if record.args:
            record.args = redact_secrets(record.args)
        if record.exc_info:
            exception_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact_secrets(exception_text)
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


def configure_logging(settings: Settings) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "secret_redaction": {
                    "()": "atnos.core.logging.SecretRedactionFilter",
                },
            },
            "formatters": {
                "standard": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "filters": ["secret_redaction"],
                    "level": settings.log_level,
                }
            },
            "root": {
                "handlers": ["console"],
                "level": settings.log_level,
            },
        }
    )
