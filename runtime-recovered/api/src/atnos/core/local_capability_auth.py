from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
import time
from typing import Any

from atnos.core.config import Settings


LOCAL_CAPABILITY_HEADER = "X-Atnos-Local-Capability"

# In-process override so Launch-Atnos can hot-bind a new session without API restart.
_runtime_capability_override: dict[str, Any] | None = None


class LocalCapabilityUnavailableError(RuntimeError):
    """Sesja lokalnych operacji uprzywilejowanych nie jest dostepna."""


class LocalCapabilityDeniedError(PermissionError):
    """Token sesji lokalnych operacji uprzywilejowanych jest nieprawidlowy."""


@dataclass(frozen=True, slots=True)
class LocalCapabilitySession:
    expires_at: int


def bind_local_capability_session(*, token: str, expires_at: int) -> LocalCapabilitySession:
    """Hot-reload capability token for a healthy API process."""
    global _runtime_capability_override
    cleaned = str(token or "").strip()
    expiry = int(expires_at)
    if not cleaned:
        raise ValueError("Capability token is required.")
    if expiry <= int(time.time()):
        raise ValueError("Capability expiry must be in the future.")
    _runtime_capability_override = {"token": cleaned, "expires_at": expiry}
    return LocalCapabilitySession(expires_at=expiry)


def clear_local_capability_override() -> None:
    global _runtime_capability_override
    _runtime_capability_override = None


def validate_local_capability(
    settings: Settings,
    provided_token: str | None,
    *,
    now: int | None = None,
) -> LocalCapabilitySession:
    """Waliduje krotkotrwaly token bez zapisywania go w logach lub odpowiedzi."""

    expected_token = ""
    expires_at: int | None = None
    if _runtime_capability_override is not None:
        expected_token = str(_runtime_capability_override.get("token") or "")
        raw_expiry = _runtime_capability_override.get("expires_at")
        expires_at = int(raw_expiry) if raw_expiry is not None else None
    if not expected_token or expires_at is None:
        expected_token = _secret_value(settings.local_host_capability_token)
        expires_at = settings.local_host_capability_expires_at
    if not expected_token or expires_at is None:
        raise LocalCapabilityUnavailableError(
            "Local capability session is disabled. Start Atnos with the official launcher."
        )

    current_time = int(time.time()) if now is None else int(now)
    if int(expires_at) <= current_time:
        raise LocalCapabilityUnavailableError(
            "Local capability session expired. Restart Atnos with the official launcher."
        )

    candidate = str(provided_token or "")
    if not candidate or not compare_digest(candidate, expected_token):
        raise LocalCapabilityDeniedError("Local capability authorization failed.")

    return LocalCapabilitySession(expires_at=int(expires_at))


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value)
