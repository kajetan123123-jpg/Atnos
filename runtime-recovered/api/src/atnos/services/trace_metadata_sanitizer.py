from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


FORBIDDEN_KEY_TOKENS = (
    "password",
    "token",
    "api_key",
    "secret",
    "secret_ref",
    "credential",
    "authorization",
    "bearer",
    "client_secret",
    "private_key",
    "account_secret",
    "secret_value",
    "raw_payload",
    "preview_payload",
    "action_payload",
    "account_payload",
    "headers",
    "cookies",
)

ALLOWED_TRACE_METADATA_KEYS = frozenset(
    {
        "active_document_action_skip_reason",
        "active_document_preview_chars",
        "active_document_preview_limit",
        "assistant_output_chars",
        "attachment_count",
        "conversation_id",
        "conversation_summary_chars",
        "document_action",
        "error_kind",
        "eval_count",
        "external_confirmation_required",
        "failure_type",
        "failure_kind",
        "finish_reason",
        "generation_max_tokens",
        "load_duration",
        "local_first",
        "memory_enabled",
        "memory_enabled_source",
        "memory_layer",
        "model_key",
        "object",
        "persona_profile",
        "persona_profile_source",
        "project_context_applied",
        "project_id",
        "project_memory_updated",
        "project_summary_updated",
        "prompt_eval_count",
        "prompt_eval_duration",
        "provider_duration_ms",
        "provider_key",
        "provider_message_id",
        "provider_prompt_chars",
        "quality_profile",
        "quality_profile_source",
        "response_style",
        "response_style_source",
        "routing_reason",
        "runtime_model",
        "selected_model_key",
        "selected_provider_key",
        "selected_review_note_decision",
        "selected_review_note_id",
        "selected_review_note_view",
        "status",
        "task",
        "total_duration",
        "usage",
        "work_mode",
        "work_mode_source",
    }
)

ALLOWED_USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "total_tokens"})
MAX_STRING_LENGTH = 240
MAX_PROVIDER_MESSAGE_ID_LENGTH = 160
DROP = object()


def sanitize_trace_metadata(metadata: Mapping[str, object] | None) -> dict[str, object] | None:
    if not isinstance(metadata, Mapping):
        return None

    sanitized: dict[str, object] = {}
    for raw_key, raw_value in metadata.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not key or _is_forbidden_key(key):
            continue
        if key not in ALLOWED_TRACE_METADATA_KEYS:
            continue

        value = _sanitize_value(key, raw_value)
        if value is DROP:
            continue
        sanitized[key] = value

    return sanitized or None


def _sanitize_value(key: str, value: object) -> object:
    if key == "usage":
        return _sanitize_usage(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else DROP
    if isinstance(value, str):
        limit = MAX_PROVIDER_MESSAGE_ID_LENGTH if key == "provider_message_id" else MAX_STRING_LENGTH
        return _sanitize_string(value, limit=limit)
    return DROP


def _sanitize_usage(value: object) -> object:
    if not isinstance(value, Mapping):
        return DROP

    sanitized: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not key or key not in ALLOWED_USAGE_KEYS:
            continue
        if isinstance(raw_value, int):
            sanitized[key] = raw_value

    return sanitized or DROP


def _sanitize_string(value: str, *, limit: int) -> object:
    normalized = " ".join(value.split())
    if not normalized:
        return DROP
    if _looks_like_secret_value(normalized):
        return DROP
    if len(normalized) > limit:
        return normalized[: limit - 3].rstrip() + "..."
    return normalized


def _looks_like_secret_value(value: str) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in FORBIDDEN_KEY_TOKENS)


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in FORBIDDEN_KEY_TOKENS)
