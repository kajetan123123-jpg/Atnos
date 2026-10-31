from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import read_intelligence_account_config
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.oauth_token_store import OAuthTokenStore
from atnos.services.youtube_connector_service import YouTubeConnectorService


@dataclass(frozen=True, slots=True)
class ResolvedYouTubeOAuthAccount:
    """Wynik mapowania konta produkcyjnego Atnosa na konto OAuth YouTube."""

    account_id: int
    source: str
    email: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedGmailOAuthAccount:
    """Wynik mapowania konta produkcyjnego Atnosa na konto OAuth Gmail."""

    account_id: int
    source: str
    email: str | None = None


def resolve_youtube_oauth_account(
    settings: Settings,
    content_account_id: str,
    *,
    session: Session | None = None,
) -> ResolvedYouTubeOAuthAccount | None:
    """Zwróć konto OAuth YouTube dla konta contentowego.

    Priorytet:
    1. jawne `youtube_oauth_account_id` w `intelligence_accounts`;
    2. fallback po emailu z `account_id` w formacie `youtube:email`.
    """

    configured = _configured_youtube_oauth_account_id(settings, content_account_id)
    if configured is not None:
        return ResolvedYouTubeOAuthAccount(account_id=configured, source="intelligence_account_mapping")

    email = _email_from_content_account_id(content_account_id)
    if not email or session is None:
        return None

    matched = _connected_youtube_oauth_account_for_email(settings, session=session, email=email)
    if matched is None:
        return None
    return ResolvedYouTubeOAuthAccount(account_id=matched, source="oauth_id_token_email", email=email)


def resolve_youtube_oauth_account_id(
    settings: Settings,
    content_account_id: str,
    *,
    session: Session | None = None,
) -> int | None:
    resolved = resolve_youtube_oauth_account(settings, content_account_id, session=session)
    return resolved.account_id if resolved is not None else None


def resolve_gmail_oauth_account(
    settings: Settings,
    content_account_id: str,
    *,
    session: Session | None = None,
) -> ResolvedGmailOAuthAccount | None:
    """Zwroc konto OAuth Gmail dla konta contentowego.

    Priorytet:
    1. jawne `gmail_oauth_account_id` w `intelligence_accounts`;
    2. fallback po emailu z `account_id`, np. `youtube:atnos@example.com`.
    """

    configured = _configured_oauth_account_id(settings, content_account_id, field="gmail_oauth_account_id")
    if configured is not None:
        return ResolvedGmailOAuthAccount(account_id=configured, source="intelligence_account_mapping")

    email = _email_from_content_account_id(content_account_id)
    if not email or session is None:
        return None

    matched = _connected_google_oauth_account_for_email(
        settings,
        session=session,
        email=email,
        connector_keys=("gmail", "gmail-readonly"),
    )
    if matched is None:
        return None
    return ResolvedGmailOAuthAccount(account_id=matched, source="oauth_id_token_email", email=email)


def resolve_gmail_oauth_account_id(
    settings: Settings,
    content_account_id: str,
    *,
    session: Session | None = None,
) -> int | None:
    resolved = resolve_gmail_oauth_account(settings, content_account_id, session=session)
    return resolved.account_id if resolved is not None else None


def _configured_youtube_oauth_account_id(settings: Settings, content_account_id: str) -> int | None:
    return _configured_oauth_account_id(settings, content_account_id, field="youtube_oauth_account_id")


def _configured_oauth_account_id(settings: Settings, content_account_id: str, *, field: str) -> int | None:
    config = read_intelligence_account_config(settings, account_id=content_account_id) or {}
    raw = config.get(field)
    try:
        return int(raw) if raw is not None and str(raw).strip() else None
    except (TypeError, ValueError):
        return None


def _email_from_content_account_id(content_account_id: str) -> str | None:
    prefix = "youtube:"
    value = str(content_account_id or "").strip()
    if not value.lower().startswith(prefix):
        return None
    email = value[len(prefix) :].strip().casefold()
    return email if "@" in email else None


def _connected_youtube_oauth_account_for_email(settings: Settings, *, session: Session, email: str) -> int | None:
    return _connected_google_oauth_account_for_email(
        settings,
        session=session,
        email=email,
        connector_keys=(YouTubeConnectorService.CONNECTOR_KEY,),
    )


def _connected_google_oauth_account_for_email(
    settings: Settings,
    *,
    session: Session,
    email: str,
    connector_keys: tuple[str, ...],
) -> int | None:
    normalized_email = email.strip().casefold()
    token_store = OAuthTokenStore(settings)
    allowed_connectors = {item.strip().casefold() for item in connector_keys if item.strip()}
    for connection in OAuthConnectionRepository(session).list_all():
        if connection.connector_key.strip().casefold() not in allowed_connectors:
            continue
        if connection.status != "connected" or connection.account_id is None:
            continue
        token = token_store.load_token_bundle(connection.token_ref)
        token_email = _email_from_id_token(token.id_token if token else None)
        if token_email and token_email.strip().casefold() == normalized_email:
            return int(connection.account_id)
    return None


def _email_from_id_token(id_token: str | None) -> str | None:
    payload = _decode_jwt_payload(id_token)
    email = payload.get("email")
    return str(email).strip() if isinstance(email, str) and email.strip() else None


def _decode_jwt_payload(token: str | None) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        parsed = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
