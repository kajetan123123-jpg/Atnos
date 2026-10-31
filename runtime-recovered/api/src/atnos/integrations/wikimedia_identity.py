"""Wikimedia client identification.

Official User-Agent policy (foundation.wikimedia.org, checked 2026-09-26):
``client/version (contact) library/version``.
Contact is an email, a public URL, or a wiki user page.
``Api-User-Agent`` is the documented header for callers that cannot set
``User-Agent``. Atnos calls Wikimedia from the backend, so both headers
carry the same string from this helper. No browser user-agent is copied.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from atnos import __version__ as APP_VERSION

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLACEHOLDER_EMAILS = {
    "example@example.com",
    "test@test.com",
    "admin@example.com",
    "user@example.com",
    "account-0c7e6a40@example.invalid",
}
_PLACEHOLDER_HOSTS = {
    "example.com",
    "example.org",
    "example.net",
    "example.edu",
    "test.com",
    "localhost",
    "127.0.0.1",
    "invalid",
}


def app_version() -> str:
    return APP_VERSION or "0.1.0"


def library_version() -> str:
    return getattr(httpx, "__version__", "0")


def classify_contact(value: str) -> str | None:
    text = " ".join(value.split())
    if not text or len(text) > 300:
        return None
    lowered = text.lower()
    if lowered in _PLACEHOLDER_EMAILS:
        return None
    if _EMAIL.fullmatch(text):
        host = text.split("@", 1)[1].lower()
        if host in _PLACEHOLDER_HOSTS or host.endswith(".example") or host == "localhost":
            return None
        return "email"
    if lowered.startswith("user:") and len(text) > 5 and " " not in text:
        return "wiki_user"
    if lowered.startswith("http://") or lowered.startswith("https://"):
        host = (urlparse(text).hostname or "").lower()
        if host in _PLACEHOLDER_HOSTS or host.endswith(".example"):
            return None
        return "url"
    return None


def user_agent(contact: str, app_name: str = "Atnos") -> str:
    """Build the single Wikimedia identification string.

    The trailing ``bot`` token follows the policy request that automated
    clients include that word. The application name itself stays the one
    configured in Settings.
    """
    name = " ".join(app_name.split()) or "Atnos"
    if any(ch in name for ch in "()/"):
        name = "Atnos"
    return f"{name}/{app_version()} ({contact.strip()}) httpx/{library_version()} bot"


def request_headers(contact: str, app_name: str = "Atnos") -> dict[str, str]:
    identification = user_agent(contact, app_name)
    return {
        "User-Agent": identification,
        "Api-User-Agent": identification,
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }


def read_identity(state: dict[str, Any]) -> dict[str, str]:
    raw = state.get("wikimedia") if isinstance(state, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    app_name = str(raw.get("app_name") or "Atnos").strip() or "Atnos"
    contact = str(raw.get("contact") or "").strip()
    if classify_contact(contact) is None:
        contact = ""
    return {"app_name": app_name, "contact": contact}


def identity_payload(app_name: str, contact: str) -> dict[str, Any]:
    kind = classify_contact(contact)
    if kind is None:
        return {
            "status": "ERROR",
            "detail": "Podaj prawdziwy e-mail, publiczny URL albo stronę użytkownika Wikimedia. Placeholdery nie przechodzą.",
        }
    name = " ".join(app_name.split()) or "Atnos"
    return {"status": "CONFIGURED", "app_name": name, "contact": contact.strip(), "contact_kind": kind}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def dump_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
