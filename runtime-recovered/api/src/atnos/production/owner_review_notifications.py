from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.production.oauth_resolver import resolve_gmail_oauth_account_id
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.gmail_connector_service import GmailConnectorService

MAX_REVIEW_ATTACHMENT_BYTES = 18 * 1024 * 1024


def dispatch_review_packet_email(
    settings: Settings,
    session: Session,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Wyslij wlascicielowi paczke review: tytul, pliki, QA i decyzje."""
    if not bool(settings.shorts_review_email_enabled):
        return {"status": "skipped", "reason": "review_email_disabled"}
    owner_email = _owner_email(session)
    if not owner_email:
        return {"status": "blocked", "reason": "owner_email_missing"}

    account_id = str(payload.get("account_id") or "")
    subject = f"[Atnos] Review ready: {_compact_subject(payload.get('topic_title') or payload.get('metadata', {}).get('title'))}"
    body = _review_packet_body(payload)
    attachments, skipped_attachments = _review_attachments(payload)
    if attachments:
        body += "\n\nAttached files:\n" + "\n".join(f"- {path.name}" for path in attachments)
    if skipped_attachments:
        body += "\n\nFiles not attached because of Gmail size limit:\n" + "\n".join(f"- {item}" for item in skipped_attachments)
    return _deliver_with_fallback(
        settings,
        session,
        account_id=account_id,
        to=owner_email,
        subject=subject,
        body=body,
        attachments=attachments,
        delivery_type="review_packet",
    )


def dispatch_private_upload_link_email(
    settings: Settings,
    session: Session,
    result: dict[str, Any],
    *,
    job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wyslij link YouTube po udanym prywatnym uploadzie."""
    if not bool(settings.shorts_review_email_enabled):
        return {"status": "skipped", "reason": "review_email_disabled"}
    if str(result.get("status") or "") != "uploaded_private":
        return {"status": "skipped", "reason": "not_uploaded_private"}
    owner_email = _owner_email(session)
    if not owner_email:
        return {"status": "blocked", "reason": "owner_email_missing"}

    account_id = str((job or {}).get("account_id") or result.get("account_id") or "")
    title = str((job or {}).get("topic_title") or (job or {}).get("topic_id") or result.get("job_id") or "private short")
    subject = f"[Atnos] Private YouTube link: {_compact_subject(title)}"
    body = "\n".join(
        [
            "Atnos uploaded a private review video.",
            "",
            f"Channel/account: {account_id or '-'}",
            f"Job: {result.get('job_id') or '-'}",
            f"Title/topic: {title}",
            f"YouTube link: {result.get('youtube_url') or '-'}",
            "",
            "Review it on mobile before any public scheduling decision.",
            "Public publishing remains blocked until explicit owner approval.",
        ]
    )
    return _deliver_with_fallback(
        settings=settings,
        session=session,
        account_id=account_id,
        to=owner_email,
        subject=subject,
        body=body,
        delivery_type="private_upload_link",
    )


def _deliver_with_fallback(
    settings: Settings,
    session: Session,
    *,
    account_id: str,
    to: str,
    subject: str,
    body: str,
    delivery_type: str,
    attachments: list[Path] | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for gmail, source in _gmail_delivery_candidates(settings, session, account_id=account_id):
        status = gmail.get_status()
        attempt = {
            "source": source,
            "gmail_account_id": getattr(status, "account_id", None),
            "connected": bool(getattr(status, "connected", False)),
            "live_write_enabled": bool(getattr(status, "live_write_enabled", False)),
        }
        if not status.live_write_enabled:
            attempt["status"] = "blocked"
            attempt["reason"] = "gmail_write_not_ready"
            attempts.append(attempt)
            continue

        probe = _probe_gmail_mailbox(gmail)
        if probe.get("probe_status") != "ok":
            attempt.update(probe)
            attempts.append(attempt)
            continue

        result = _send_or_draft(
            gmail,
            settings=settings,
            to=to,
            subject=subject,
            body=body,
            attachments=attachments,
            delivery_type=delivery_type,
        )
        result["gmail_account_id"] = getattr(status, "account_id", None)
        result["gmail_delivery_source"] = source
        if attempts:
            result["delivery_attempts"] = attempts
        if str(result.get("status") or "") not in {"failed", "blocked"}:
            return result
        attempts.append({**attempt, **result})

    return {
        "status": "failed",
        "delivery_type": delivery_type,
        "reason": "no_usable_gmail_mailbox",
        "delivery_attempts": attempts,
    }


def _gmail_delivery_candidates(
    settings: Settings,
    session: Session,
    *,
    account_id: str,
) -> list[tuple[GmailConnectorService, str]]:
    seen: set[int] = set()
    candidates: list[tuple[GmailConnectorService, str]] = []

    preferred = resolve_gmail_oauth_account_id(settings, account_id, session=session) if account_id else None
    if preferred is not None:
        seen.add(preferred)
        candidates.append((GmailConnectorService(session, settings, account_id=preferred), "content_account_mapping"))

    for connection in OAuthConnectionRepository(session).list_all():
        if connection.connector_key not in {"gmail", "gmail-readonly"}:
            continue
        if connection.status != "connected" or connection.account_id is None:
            continue
        if connection.account_id in seen:
            continue
        seen.add(connection.account_id)
        candidates.append((GmailConnectorService(session, settings, account_id=connection.account_id), "connected_gmail_fallback"))

    if not candidates:
        candidates.append((GmailConnectorService(session, settings), "latest_gmail_connection"))
    return candidates


def _probe_gmail_mailbox(gmail: GmailConnectorService) -> dict[str, Any]:
    probe = getattr(gmail, "probe", None)
    if not callable(probe):
        return {"probe_status": "ok"}
    item = probe()
    return {
        "probe_status": getattr(item, "probe_status", "error"),
        "probe_message": getattr(item, "message", None),
        "provider_reachable": bool(getattr(item, "provider_reachable", False)),
    }


def _owner_email(session: Session) -> str:
    owner = UserRepository(session).get_owner()
    return str(getattr(owner, "email", "") or "").strip()


def _send_or_draft(
    gmail: GmailConnectorService,
    *,
    settings: Settings,
    to: str,
    subject: str,
    body: str,
    delivery_type: str,
    attachments: list[Path] | None = None,
) -> dict[str, Any]:
    try:
        draft = gmail.draft_message(to=to, subject=subject, body=body, attachments=attachments)
        if not bool(settings.shorts_review_email_send_immediately):
            return {
                "status": "drafted",
                "delivery_type": delivery_type,
                "to": to,
                "draft_id": draft.get("draft_id"),
                "message_id": draft.get("message_id"),
                "attachments": [path.name for path in attachments or []],
            }
        sent = gmail.send_message(draft_id=str(draft["draft_id"]))
        return {
            "status": "sent",
            "delivery_type": delivery_type,
            "to": to,
            "draft_id": draft.get("draft_id"),
            "message_id": sent.get("message_id") or draft.get("message_id"),
            "thread_id": sent.get("thread_id") or draft.get("thread_id"),
            "attachments": [path.name for path in attachments or []],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "delivery_type": delivery_type,
            "error": _format_delivery_error(exc),
        }


def _format_delivery_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.text.strip()[:1000] if exc.response is not None else ""
        return f"{type(exc).__name__}: {exc}; response={detail}"
    return f"{type(exc).__name__}: {exc}"


def _review_packet_body(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    qa = payload.get("qa") if isinstance(payload.get("qa"), dict) else {}
    lines = [
        "Atnos prepared a new Short for review.",
        "",
        f"Channel/account: {payload.get('account_id') or '-'}",
        f"Job: {payload.get('job_id') or '-'}",
        f"Topic: {payload.get('topic_title') or '-'}",
        "",
        "Review assets:",
        "- Primary review files are attached when they fit Gmail limits.",
        f"- Local video path: {_path(payload.get('video_file_path'))}",
        f"- Local thumbnail path: {_path(payload.get('thumbnail_file_path'))}",
        f"- Local subtitles path: {_path(payload.get('subtitle_file_path'))}",
        f"- Local QA report path: {_path(payload.get('qa_report_path'))}",
        "",
        "Suggested metadata:",
        f"- Title: {metadata.get('title') or '-'}",
        f"- Visibility: {metadata.get('visibility') or 'private'}",
        f"- Tags: {', '.join(str(tag) for tag in (metadata.get('tags') or [])) or '-'}",
        "",
        "QA:",
        f"- Overall: {qa.get('overall') or '-'}",
        f"- Blocking failures: {', '.join(str(item) for item in (qa.get('blocking_failures') or [])) or 'none'}",
        f"- Warnings: {', '.join(str(item) for item in (qa.get('advisory_failures') or [])) or 'none'}",
        "",
        "Decision:",
        f"- Private upload endpoint: {payload.get('upload_endpoint') or '-'}",
        "- Public publishing remains blocked until explicit owner approval.",
    ]
    if payload.get("review_markdown_path"):
        lines.insert(9, f"- Review markdown: {_path(payload.get('review_markdown_path'))}")
    return "\n".join(lines)


def _review_attachments(payload: dict[str, Any]) -> tuple[list[Path], list[str]]:
    keys = [
        "video_file_path",
        "thumbnail_file_path",
        "qa_report_path",
        "subtitle_file_path",
    ]
    attachments: list[Path] = []
    skipped: list[str] = []
    total = 0
    for key in keys:
        raw = str(payload.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.exists() or not path.is_file():
            skipped.append(f"{path.name or key} (missing)")
            continue
        size = path.stat().st_size
        if total + size > MAX_REVIEW_ATTACHMENT_BYTES:
            skipped.append(f"{path.name} ({size / (1024 * 1024):.1f} MB)")
            continue
        attachments.append(path)
        total += size
    return attachments, skipped


def _path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    try:
        return _prevent_email_linkification(str(Path(raw)))
    except OSError:
        return _prevent_email_linkification(raw)


def _prevent_email_linkification(value: str) -> str:
    return (
        value.replace("youtube-atnos-int.pl", "youtube-atnos-int[dot]pl")
        .replace("youtube-atnos2909-gmail.com", "youtube-atnos2909-gmail[dot]com")
        .replace("youtube-atomek3133-gmail.com", "youtube-atomek3133-gmail[dot]com")
    )


def _compact_subject(value: object) -> str:
    text = " ".join(str(value or "short").split())
    return text[:90] if len(text) > 90 else text
