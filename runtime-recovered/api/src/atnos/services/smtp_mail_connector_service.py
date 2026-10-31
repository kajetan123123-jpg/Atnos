from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
import smtplib
import ssl
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.account_credentials_service import AccountCredentialsService


INT_MAIL_PROVIDER_KEYS = ("int-mail", "int_mail", "bootstrap-mailbox")
INT_MAIL_SMTP_HOST = "poczta.int.pl"
INT_MAIL_SSL_PORT = 465
INT_MAIL_STARTTLS_PORT = 587


@dataclass(frozen=True, slots=True)
class SmtpMailStatusView:
    connector_key: str
    display_name: str
    provider_key: str
    account_id: int | None
    account_identifier: str | None
    configured: bool
    has_secret: bool
    warnings: tuple[str, ...]
    checked_at: datetime
    live_login_enabled: bool
    live_send_enabled: bool
    probe_status: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SmtpMailConnectorService:
    """SMTP connector for non-Google mailboxes stored in the trusted account vault."""

    CONNECTOR_KEY = "int-mail-smtp"
    DISPLAY_NAME = "INT Mail SMTP"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.accounts = AccountCredentialsService(session, settings)

    def get_status(self, *, account_id: int | None = None) -> SmtpMailStatusView:
        account = self._resolve_account(account_id=account_id)
        warnings: list[str] = []
        if account is None:
            warnings.append("No configured INT mail account with a stored secret was found.")
            return self._status(
                account=None,
                configured=False,
                warnings=warnings,
                probe_status="not_configured",
                message="Configure an INT mail account before SMTP actions.",
            )
        if not account.has_secret:
            warnings.append("INT mail account exists but has no retrievable stored secret.")
        return self._status(
            account=account,
            configured=account.has_secret and account.status == "configured",
            warnings=warnings,
            probe_status="not_probed",
            message="SMTP account is configured locally. Run probe to verify provider login.",
        )

    def probe(self, *, account_id: int | None = None) -> SmtpMailStatusView:
        account = self._resolve_account(account_id=account_id)
        if account is None:
            return self._status(
                account=None,
                configured=False,
                warnings=("No configured INT mail account with a stored secret was found.",),
                probe_status="not_configured",
                message="Configure an INT mail account before SMTP actions.",
            )
        if not account.has_secret:
            return self._status(
                account=account,
                configured=False,
                warnings=("INT mail account exists but has no retrievable stored secret.",),
                probe_status="missing_secret",
                message="Stored password/app-password is missing.",
            )

        password = self.accounts.resolve_secret_for_adapter(account.id)
        if not password:
            return self._status(
                account=account,
                configured=False,
                warnings=("Stored INT mail secret could not be resolved from the vault.",),
                probe_status="missing_secret",
                message="Stored password/app-password could not be opened.",
            )

        result = self._try_login(account.account_identifier, password)
        return self._status(
            account=account,
            configured=True,
            warnings=result["warnings"],
            probe_status=result["status"],
            message=result["message"],
            live_login_enabled=result["status"] == "ready",
            live_send_enabled=result["status"] == "ready",
        )

    def send_test_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        account = self._resolve_account(account_id=account_id)
        if account is None or not account.has_secret:
            return {
                "status": "blocked",
                "message": "Configured INT mail account with stored secret was not found.",
                "sent": False,
            }
        password = self.accounts.resolve_secret_for_adapter(account.id)
        if not password:
            return {"status": "blocked", "message": "Stored INT mail secret could not be opened.", "sent": False}

        message = EmailMessage()
        message["From"] = account.account_identifier
        message["To"] = to.strip()
        message["Subject"] = subject.strip()
        message.set_content(body.strip(), charset="utf-8")

        attempts: list[dict[str, Any]] = []
        for mode in ("ssl", "starttls"):
            try:
                self._send(account.account_identifier, password, message, mode=mode)
                return {
                    "status": "sent",
                    "mode": mode,
                    "from": account.account_identifier,
                    "to": to.strip(),
                    "attempts": attempts + [{"mode": mode, "status": "sent"}],
                    "sent": True,
                }
            except smtplib.SMTPAuthenticationError as exc:
                attempts.append(
                    {
                        "mode": mode,
                        "status": "auth_failed",
                        "smtp_code": exc.smtp_code,
                    }
                )
                last_error = {
                    "status": "auth_failed",
                    "mode": mode,
                    "smtp_code": exc.smtp_code,
                    "message": self._smtp_error_message(exc),
                    "attempts": attempts,
                    "sent": False,
                }
            except (OSError, smtplib.SMTPException) as exc:
                attempts.append(
                    {
                        "mode": mode,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
                )
                last_error = {
                    "status": "failed",
                    "mode": mode,
                    "message": f"{type(exc).__name__}: {exc}",
                    "attempts": attempts,
                    "sent": False,
                }
        return last_error

    def _try_login(self, username: str, password: str) -> dict[str, Any]:
        last_error: dict[str, Any] | None = None
        auth_failures: list[str] = []
        for mode in ("ssl", "starttls"):
            try:
                self._login(username, password, mode=mode)
                return {
                    "status": "ready",
                    "warnings": (),
                    "message": f"SMTP login succeeded via {mode}.",
                }
            except smtplib.SMTPAuthenticationError as exc:
                auth_failures.append(f"{mode}:{exc.smtp_code}")
                last_error = {
                    "status": "auth_failed",
                    "warnings": (f"SMTP authentication failed on {mode}: code {exc.smtp_code}.",),
                    "message": self._smtp_error_message(exc),
                }
            except (OSError, smtplib.SMTPException) as exc:
                last_error = {
                    "status": "connection_failed",
                    "warnings": (f"SMTP {mode} probe failed: {type(exc).__name__}.",),
                    "message": str(exc),
                }
        if auth_failures:
            return {
                "status": "auth_failed",
                "warnings": (f"SMTP authentication failed on modes: {', '.join(auth_failures)}.",),
                "message": last_error["message"] if last_error else "SMTP authentication failed.",
            }
        return last_error or {
            "status": "failed",
            "warnings": ("SMTP probe failed before attempting login.",),
            "message": "SMTP probe failed.",
        }

    def _login(self, username: str, password: str, *, mode: str) -> None:
        context = ssl.create_default_context()
        if mode == "ssl":
            with smtplib.SMTP_SSL(INT_MAIL_SMTP_HOST, INT_MAIL_SSL_PORT, timeout=30, context=context) as smtp:
                smtp.login(username, password)
            return
        with smtplib.SMTP(INT_MAIL_SMTP_HOST, INT_MAIL_STARTTLS_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(username, password)

    def _send(self, username: str, password: str, message: EmailMessage, *, mode: str) -> None:
        context = ssl.create_default_context()
        if mode == "ssl":
            with smtplib.SMTP_SSL(INT_MAIL_SMTP_HOST, INT_MAIL_SSL_PORT, timeout=30, context=context) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(INT_MAIL_SMTP_HOST, INT_MAIL_STARTTLS_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(message)

    def _resolve_account(self, *, account_id: int | None):
        accounts = self.accounts.list_accounts()
        if account_id is not None:
            return next((item for item in accounts if item.id == account_id), None)
        return next(
            (
                item
                for item in accounts
                if item.provider_key in INT_MAIL_PROVIDER_KEYS
                and item.status == "configured"
                and item.has_secret
            ),
            None,
        )

    def _status(
        self,
        *,
        account,
        configured: bool,
        warnings: list[str] | tuple[str, ...],
        probe_status: str,
        message: str,
        live_login_enabled: bool = False,
        live_send_enabled: bool = False,
    ) -> SmtpMailStatusView:
        return SmtpMailStatusView(
            connector_key=self.CONNECTOR_KEY,
            display_name=self.DISPLAY_NAME,
            provider_key=account.provider_key if account is not None else "int-mail",
            account_id=account.id if account is not None else None,
            account_identifier=account.account_identifier if account is not None else None,
            configured=configured,
            has_secret=bool(account and account.has_secret),
            warnings=tuple(warnings),
            checked_at=datetime.now(timezone.utc),
            live_login_enabled=live_login_enabled,
            live_send_enabled=live_send_enabled,
            probe_status=probe_status,
            message=message,
        )

    @staticmethod
    def _smtp_error_message(exc: smtplib.SMTPAuthenticationError) -> str:
        error = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        return f"SMTP authentication failed: {error}"
