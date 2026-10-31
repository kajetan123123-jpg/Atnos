from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.user_repository import UserRepository
from atnos.services.gmail_connector_service import GmailConnectorService


def dispatch_investment_autopilot_alert_email(
    settings: Settings,
    session: Session,
    run_result: dict[str, Any],
) -> dict[str, Any]:
    """Send an owner Gmail alert for paper-autopilot anomalies.

    Alert dispatch is best-effort and never changes the paper-trading result.
    """
    alerts = run_result.get("alerts") if isinstance(run_result.get("alerts"), dict) else {}
    if not bool(settings.investment_paper_autopilot_alerts_enabled):
        return {"status": "skipped", "reason": "investment_alerts_disabled"}
    if not bool(alerts.get("required")):
        return {"status": "skipped", "reason": "no_alert_required"}

    owner = UserRepository(session).get_owner()
    owner_email = str(getattr(owner, "email", "") or "").strip()
    if not owner_email:
        return {"status": "blocked", "reason": "owner_email_missing"}

    gmail = GmailConnectorService(session, settings)
    status = gmail.get_status()
    if not status.live_write_enabled:
        return {
            "status": "blocked",
            "reason": "gmail_write_not_ready",
            "connected": bool(status.connected),
            "oauth_status": status.oauth_status,
        }

    subject = f"[Atnos] Investment paper autopilot alert: {run_result.get('strategy_id') or 'portfolio-alpha'}"
    body = _build_alert_body(run_result)
    try:
        draft = gmail.draft_message(to=owner_email, subject=subject, body=body)
        if not bool(settings.shorts_review_email_send_immediately):
            return {
                "status": "drafted",
                "delivery_type": "investment_paper_autopilot_alert",
                "to": owner_email,
                "draft_id": draft.get("draft_id"),
                "message_id": draft.get("message_id"),
            }
        sent = gmail.send_message(draft_id=str(draft["draft_id"]))
        return {
            "status": "sent",
            "delivery_type": "investment_paper_autopilot_alert",
            "to": owner_email,
            "draft_id": draft.get("draft_id"),
            "message_id": sent.get("message_id"),
        }
    except Exception as exc:  # pragma: no cover - defensive integration boundary
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}


def _build_alert_body(run_result: dict[str, Any]) -> str:
    alerts = run_result.get("alerts") if isinstance(run_result.get("alerts"), dict) else {}
    decisions = run_result.get("decisions") if isinstance(run_result.get("decisions"), list) else []
    lines = [
        "Atnos completed an investment paper-autopilot run that needs review.",
        "",
        f"Run ID: {run_result.get('id') or '-'}",
        f"Strategy: {run_result.get('strategy_id') or '-'}",
        f"Status: {run_result.get('status') or '-'}",
        f"Executed paper trades: {run_result.get('executed_trade_count') or 0}",
        f"Rejected paper decisions: {run_result.get('rejected_trade_count') or 0}",
        f"Alert reasons: {', '.join(alerts.get('reasons') or []) or '-'}",
        f"Fallback symbols: {', '.join(alerts.get('fallback_symbols') or []) or '-'}",
        f"Rejected symbols: {', '.join(alerts.get('rejected_symbols') or []) or '-'}",
        "",
        "Top decisions:",
    ]
    for decision in decisions[:8]:
        if not isinstance(decision, dict):
            continue
        lines.append(
            "- "
            f"{decision.get('symbol') or '-'} | "
            f"{decision.get('action') or '-'} | "
            f"price_source={decision.get('price_source') or '-'} | "
            f"market_price={decision.get('market_price') or decision.get('synthetic_price_pln') or '-'}"
        )
    lines.extend(
        [
            "",
            "This is paper trading only. Real broker orders remain blocked.",
        ]
    )
    return "\n".join(lines)
