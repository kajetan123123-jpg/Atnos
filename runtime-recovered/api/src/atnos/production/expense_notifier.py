from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, utc_now_iso


class ExpenseNotifier:
    """Lokalny outbox powiadomien o kosztach i wnioskach zakupowych."""

    TABLE = "expense_notifications"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def enqueue(
        self,
        *,
        event_type: str,
        provider: str,
        operation: str,
        amount_pln: float,
        reason: str,
        account_id: str | None = None,
        job_id: str | None = None,
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Zapisz powiadomienie do outboxa. Nie wysyla maila bez skonfigurowanego konektora."""
        self._ensure_schema()
        now = utc_now_iso()
        notification_id = f"exp-{uuid4().hex[:12]}"
        amount = round(max(float(amount_pln or 0.0), 0.0), 4)
        subject = self._subject(event_type=event_type, provider=provider, amount_pln=amount)
        body = self._body(
            event_type=event_type,
            provider=provider,
            operation=operation,
            amount_pln=amount,
            reason=reason,
            account_id=account_id,
            job_id=job_id,
            source_id=source_id,
        )
        payload = {
            "id": notification_id,
            "created_at": now,
            "updated_at": now,
            "event_type": event_type,
            "provider": provider,
            "operation": operation,
            "amount_pln": amount,
            "account_id": account_id,
            "job_id": job_id,
            "source_id": source_id,
            "reason": reason.strip(),
            "delivery_channel": "gmail",
            "delivery_status": "pending_delivery",
            "subject": subject,
            "body": body,
            "metadata": metadata or {},
            "card_data_storage": "forbidden",
        }
        with connect(self.settings) as db:
            db.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    id, created_at, updated_at, event_type, provider, operation,
                    amount_pln, account_id, job_id, source_id, reason,
                    delivery_channel, delivery_status, subject, body, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification_id,
                    now,
                    now,
                    event_type,
                    provider,
                    operation,
                    amount,
                    account_id,
                    job_id,
                    source_id,
                    reason.strip(),
                    "gmail",
                    "pending_delivery",
                    subject,
                    body,
                    json.dumps(payload["metadata"], ensure_ascii=False, separators=(",", ":")),
                ),
            )
            db.commit()
        return payload

    def list_notifications(self, *, delivery_status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Zwroc ostatnie powiadomienia kosztowe."""
        self._ensure_schema()
        limit = max(1, min(int(limit), 200))
        with connect(self.settings) as db:
            if delivery_status:
                rows = db.execute(
                    f"""
                    SELECT * FROM {self.TABLE}
                    WHERE delivery_status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (delivery_status, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT * FROM {self.TABLE} ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_payload(row) for row in rows]

    def mark_delivery(self, notification_id: str, *, status: str, message: str = "") -> dict[str, Any]:
        """Oznacz wynik wysylki maila dla danego powiadomienia."""
        self._ensure_schema()
        allowed = {"pending_delivery", "sent", "failed", "skipped"}
        normalized = status.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"Unsupported delivery status: {status}")
        now = utc_now_iso()
        with connect(self.settings) as db:
            db.execute(
                f"""
                UPDATE {self.TABLE}
                SET delivery_status = ?, delivery_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized, message.strip(), now, notification_id),
            )
            db.commit()
            row = db.execute(f"SELECT * FROM {self.TABLE} WHERE id = ?", (notification_id,)).fetchone()
        return self._row_payload(row)

    def dispatch_pending_gmail(self, session: Session, *, limit: int = 20) -> dict[str, Any]:
        """Wyslij oczekujace powiadomienia przez Gmail, jesli konektor jest gotowy."""
        from atnos.repositories.user_repository import UserRepository
        from atnos.services.gmail_connector_service import GmailConnectorService

        pending = self.list_notifications(delivery_status="pending_delivery", limit=limit)
        if not pending:
            return {"status": "idle", "attempted": 0, "sent": 0, "failed": 0, "items": []}

        owner = UserRepository(session).get_owner()
        owner_email = str(getattr(owner, "email", "") or "").strip()
        if not owner_email:
            items = [
                self.mark_delivery(
                    str(item["id"]),
                    status="failed",
                    message="Owner email is not configured.",
                )
                for item in pending
            ]
            return {"status": "blocked", "attempted": len(items), "sent": 0, "failed": len(items), "items": items}

        gmail = GmailConnectorService(session, self.settings)
        status = gmail.get_status()
        if not status.live_write_enabled:
            items = [
                self.mark_delivery(
                    str(item["id"]),
                    status="failed",
                    message="Gmail OAuth write access is not configured.",
                )
                for item in pending
            ]
            return {"status": "blocked", "attempted": len(items), "sent": 0, "failed": len(items), "items": items}

        sent = 0
        failed = 0
        items: list[dict[str, Any]] = []
        for item in pending:
            try:
                draft = gmail.draft_message(
                    to=owner_email,
                    subject=str(item["subject"]),
                    body=str(item["body"]),
                )
                gmail.send_message(draft_id=str(draft["draft_id"]))
                items.append(self.mark_delivery(str(item["id"]), status="sent", message="Sent through Gmail OAuth."))
                sent += 1
            except Exception as exc:
                items.append(
                    self.mark_delivery(
                        str(item["id"]),
                        status="failed",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
                failed += 1
        return {
            "status": "sent" if sent and not failed else "partial" if sent else "failed",
            "attempted": len(pending),
            "sent": sent,
            "failed": failed,
            "items": items,
        }

    def _ensure_schema(self) -> None:
        with connect(self.settings) as db:
            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    amount_pln REAL NOT NULL DEFAULT 0,
                    account_id TEXT,
                    job_id TEXT,
                    source_id TEXT,
                    reason TEXT NOT NULL,
                    delivery_channel TEXT NOT NULL DEFAULT 'gmail',
                    delivery_status TEXT NOT NULL DEFAULT 'pending_delivery',
                    delivery_message TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            db.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_delivery_status_created
                ON {self.TABLE} (delivery_status, created_at)
                """
            )
            db.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_source
                ON {self.TABLE} (source_id)
                """
            )
            db.commit()

    @staticmethod
    def _subject(*, event_type: str, provider: str, amount_pln: float) -> str:
        return f"[Atnos] Wydatek/API: {provider} - {amount_pln:.2f} PLN ({event_type})"

    @staticmethod
    def _body(
        *,
        event_type: str,
        provider: str,
        operation: str,
        amount_pln: float,
        reason: str,
        account_id: str | None,
        job_id: str | None,
        source_id: str | None,
    ) -> str:
        return "\n".join(
            [
                "Atnos zarejestrowal koszt lub wniosek zakupowy.",
                "",
                f"Typ: {event_type}",
                f"Dostawca: {provider}",
                f"Operacja: {operation}",
                f"Kwota szacowana: {amount_pln:.2f} PLN",
                f"Powod: {reason.strip()}",
                f"Konto: {account_id or '-'}",
                f"Job: {job_id or '-'}",
                f"Zrodlo: {source_id or '-'}",
                "",
                "Dane karty nie sa i nie beda przechowywane w Atnosie.",
            ]
        )

    @staticmethod
    def _row_payload(row) -> dict[str, Any]:
        if row is None:
            return {}
        payload = dict(row)
        try:
            metadata = json.loads(str(payload.pop("metadata_json", "") or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        payload["metadata"] = metadata if isinstance(metadata, dict) else {}
        payload["card_data_storage"] = "forbidden"
        return payload
