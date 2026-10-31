from __future__ import annotations

from atnos.browser_operator.models import BrowserAction
from atnos.core.config import Settings


HIGH_RISK_TOKENS = (
    "submit",
    "send",
    "publish",
    "delete",
    "buy",
    "payment",
    "login",
    "password",
    "api key",
    "konto",
    "account settings",
    "ustawienia konta",
    "upload",
)


def classify_browser_risk(action: BrowserAction) -> str:
    haystack = " ".join(
        filter(
            None,
            (
                action.action_type,
                action.target,
                action.value,
                action.reason,
                action.expected_result,
            ),
        )
    ).casefold()
    if any(token in haystack for token in HIGH_RISK_TOKENS):
        return "high"
    if action.action_type in {"upload_file"}:
        return "high"
    if action.action_type in {"click", "press"}:
        return "medium"
    return action.risk_level


def action_requires_human_approval(action: BrowserAction, settings: Settings) -> bool:
    if not settings.browser_require_approval_for_high_risk:
        return False
    return classify_browser_risk(action) == "high"
