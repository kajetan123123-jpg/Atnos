from __future__ import annotations

import logging
import sys

from agent.agent_logging import AgentJsonlLogger
from atnos.core.config import Settings
from atnos.core.logging import SecretRedactionFilter, redact_secrets


def test_redacts_secret_query_parameters() -> None:
    message = (
        "HTTP Request: GET "
        "https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSySecretValue1234567890abcdef"
    )

    redacted = redact_secrets(message)

    assert "AIzaSySecretValue" not in redacted
    assert "key=[REDACTED]" in redacted


def test_redacts_extended_secret_labels_in_freeform_text() -> None:
    message = (
        "auth_token=opaque-auth-value "
        "clientSecret:opaque-client-value "
        "smtp_password=opaque-mail-value "
        "code-verifier:opaque-verifier-value"
    )

    redacted = redact_secrets(message)

    assert "opaque-" not in redacted
    assert redacted.count("[REDACTED]") == 4


def test_redacts_structured_log_args() -> None:
    payload = {
        "api_key": "sk-test-very-secret-value",
        "nested": {"Authorization": "Bearer secret-token-value"},
        "safe": "visible",
    }

    redacted = redact_secrets(payload)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["Authorization"] == "[REDACTED]"
    assert redacted["safe"] == "visible"


def test_redacts_recursive_secret_field_name_variants() -> None:
    payload = {
        "clientSecret": "client-secret-value",
        "nested": {
            "auth_token": "auth-token-value",
            "webhook-token": "webhook-token-value",
            "smtp_password": "smtp-password-value",
            "cookie": "cookie-value",
            "session": "session-value",
            "code_verifier": "verifier-value",
        },
    }

    redacted = redact_secrets(payload)

    assert redacted["clientSecret"] == "[REDACTED]"
    assert all(value == "[REDACTED]" for value in redacted["nested"].values())


def test_redacts_payment_card_and_polish_password_labels() -> None:
    payload = {
        "card_number": "4111 1111 1111 1111",
        "cvv": "123",
        "hasło": "nie-zapisuj-tego",
        "message": "karta 4111-1111-1111-1111, hasło: tajne",
    }

    redacted = redact_secrets(payload)

    assert redacted["card_number"] == "[REDACTED]"
    assert redacted["cvv"] == "[REDACTED]"
    assert redacted["hasło"] == "[REDACTED]"
    assert "4111" not in redacted["message"]
    assert "tajne" not in redacted["message"]


def test_redaction_preserves_nonsecret_metadata_and_values() -> None:
    payload = {
        "token_count": 17,
        "token_type": "Bearer",
        "auth_method": "oauth2",
        "auth_status": "connected",
        "secret_ref": "vault-item-reference",
        "secret_stored": True,
        "session_id": "trace-session-identifier",
        "safe": {"message": "operation completed", "attempts": 2},
    }

    assert redact_secrets(payload) == payload


def test_log_record_filter_redacts_message_args_before_formatting() -> None:
    class UrlLike:
        def __str__(self) -> str:
            return "https://example.com/models?key=AIzaSySecretValue1234567890abcdef"

    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="HTTP Request: GET %s",
        args=(UrlLike(),),
        exc_info=None,
    )

    assert SecretRedactionFilter().filter(record)

    assert "AIzaSySecretValue" not in record.getMessage()
    assert "key=[REDACTED]" in record.getMessage()


def test_agent_jsonl_log_redacts_secrets_before_persisting(tmp_path) -> None:
    synthetic_token = "sk-test-redaction-boundary-1234567890"
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    logger = AgentJsonlLogger(settings)

    logger.log_decision(
        "security_regression",
        {
            "authorization": f"Bearer {synthetic_token}",
            "detail": f"token={synthetic_token}",
            "safe": "visible",
        },
    )

    persisted = settings.agent_decisions_log_path.read_text(encoding="utf-8")
    assert synthetic_token not in persisted
    assert "[REDACTED]" in persisted
    assert "visible" in persisted


def test_log_record_filter_redacts_exception_trace_before_formatting() -> None:
    synthetic_token = "sk-test-traceback-boundary-1234567890"
    try:
        raise RuntimeError(f"provider failed with token={synthetic_token}")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="atnos.security.regression",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Provider request failed",
        args=(),
        exc_info=exc_info,
    )

    assert SecretRedactionFilter().filter(record)
    formatted = logging.Formatter("%(message)s").format(record)

    assert synthetic_token not in formatted
    assert "token=[REDACTED" in formatted
