from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from tools.ubuntu_control import UbuntuControlError, stop_remote_atnos_processes

from agent.autonomy_modes import AutonomyMode, resolve_autonomy_mode
from agent.models import RiskLevel


class ActionTarget(str):
    UBUNTU_SANDBOX = "ubuntu_sandbox"
    MAIN_PC = "main_pc"
    PUBLICATION = "publication"
    API_BILLING = "api_billing"
    SECRET = "secret"
    PRIVATE_ACCOUNT = "private_account"
    CLOUD = "cloud"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    log_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PUBLICATION_TOKENS = (
    "publish",
    "publication",
    "opublikuj",
    "public upload",
    "youtube public",
    "tiktok",
    "instagram",
    "social media",
)
API_BILLING_TOKENS = ("billing", "payment", "platnosc", "płatność", "invoice", "quota", "paid api")
SECRET_TOKENS = ("api_key", "api key", "token", "secret", "haslo", "hasło", "password", "private key", "ssh key")
MAIN_PC_TOKENS = ("main_pc", "main pc", "windows", "host pc", "glowny pc", "główny pc", "c:\\", "f:\\")
SANDBOX_TOKENS = ("ubuntu", "sandbox", "ssh", "/home/atnos/atnos", "atnos-latitude")
HIGH_RISK_TOKENS = (
    "sudo",
    "rm -rf",
    "remove-item",
    "del /",
    "format",
    "mkfs",
    "shutdown",
    "reboot",
    "systemctl",
    "install",
    "apt-get",
    "upload",
    "publish",
    "delete",
    "kasuj",
    "usun",
    "usuń",
)


def is_action_allowed(
    action: str,
    target: str,
    risk_level: str | RiskLevel,
    *,
    settings: Settings | None = None,
    log: bool = True,
) -> PolicyDecision:
    resolved_settings = settings or Settings()
    mode = resolve_autonomy_mode(resolved_settings)
    normalized_action = _normalize(action)
    normalized_target = _normalize(target)
    normalized_risk = _normalize_risk(risk_level, normalized_action)
    target_type = classify_target(normalized_action, normalized_target, resolved_settings)

    decision = _decide(
        mode=mode,
        action=normalized_action,
        target=normalized_target,
        target_type=target_type,
        risk_level=normalized_risk,
        settings=resolved_settings,
    )
    if log and _should_log(resolved_settings):
        log_policy_decision(
            action=action,
            target=target,
            risk_level=normalized_risk,
            target_type=target_type,
            mode=mode,
            decision=decision,
            settings=resolved_settings,
        )
    return decision


def classify_target(action: str, target: str, settings: Settings) -> str:
    haystack = f"{action} {target}"
    if _contains_any(haystack, SECRET_TOKENS):
        return ActionTarget.SECRET
    if _contains_any(haystack, API_BILLING_TOKENS):
        return ActionTarget.API_BILLING
    if _contains_any(haystack, PUBLICATION_TOKENS):
        return ActionTarget.PUBLICATION
    sandbox_host = _normalize(settings.atnos_sandbox_host or settings.atnos_ubuntu_host or "")
    sandbox_user = _normalize(settings.atnos_sandbox_user or settings.atnos_ubuntu_user or "")
    if _contains_any(haystack, SANDBOX_TOKENS) or (sandbox_host and sandbox_host in haystack):
        return ActionTarget.UBUNTU_SANDBOX
    if target.strip() == ActionTarget.UBUNTU_SANDBOX or (sandbox_user and f"{sandbox_user}@" in haystack):
        return ActionTarget.UBUNTU_SANDBOX
    if _contains_any(haystack, MAIN_PC_TOKENS):
        return ActionTarget.MAIN_PC
    if "account" in haystack or "konto" in haystack:
        return ActionTarget.PRIVATE_ACCOUNT
    if "cloud" in haystack or "api" in haystack:
        return ActionTarget.CLOUD
    return ActionTarget.UNKNOWN


def log_policy_decision(
    *,
    action: str,
    target: str,
    risk_level: RiskLevel,
    target_type: str,
    mode: AutonomyMode,
    decision: PolicyDecision,
    settings: Settings,
) -> Path:
    path = settings.agent_decisions_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "sandbox_policy_decision",
        "autonomy_mode": mode.value,
        "action": redact_sensitive(action),
        "target": redact_sensitive(target),
        "target_type": target_type,
        "risk_level": risk_level.value,
        "decision": decision.to_dict(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def emergency_stop_sandbox(*, settings: Settings | None = None) -> dict[str, Any]:
    resolved_settings = settings or Settings()
    decision = is_action_allowed(
        "emergency stop Ubuntu sandbox atnos processes",
        ActionTarget.UBUNTU_SANDBOX,
        RiskLevel.HIGH,
        settings=resolved_settings,
    )
    if decision.requires_approval or not decision.allowed:
        return {
            "ok": False,
            "status": "approval_required",
            "decision": decision.to_dict(),
        }
    try:
        result = stop_remote_atnos_processes(settings=resolved_settings)
    except UbuntuControlError as exc:
        return {
            "ok": False,
            "status": "ubuntu_error",
            "error": str(exc),
            "decision": decision.to_dict(),
        }
    return {
        "ok": bool(result.get("exit_code") == 0),
        "status": "stopped" if result.get("exit_code") == 0 else "stop_failed",
        "result": result,
        "decision": decision.to_dict(),
    }


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if _is_sensitive_key(str(key)) else redact_sensitive(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if not isinstance(value, str):
        return value
    redacted = value
    redacted = _redact_assignment(redacted, "api_key")
    redacted = _redact_assignment(redacted, "token")
    redacted = _redact_assignment(redacted, "secret")
    redacted = _redact_assignment(redacted, "password")
    redacted = _redact_assignment(redacted, "haslo")
    redacted = _redact_assignment(redacted, "hasło")
    return redacted


def _decide(
    *,
    mode: AutonomyMode,
    action: str,
    target: str,
    target_type: str,
    risk_level: RiskLevel,
    settings: Settings,
) -> PolicyDecision:
    if target_type == ActionTarget.SECRET:
        if settings.atnos_secret_access_approval:
            return PolicyDecision(False, True, "Secret access always requires explicit approval.", "approval_required")
        return PolicyDecision(True, False, "Secret access approval disabled by configuration; values are still redacted from logs.", "warning")

    if target_type == ActionTarget.API_BILLING:
        if settings.atnos_api_billing_approval:
            return PolicyDecision(False, True, "API billing or paid API changes always require approval.", "approval_required")
        return PolicyDecision(True, False, "API billing approval disabled by configuration.", "warning")

    if target_type == ActionTarget.PUBLICATION:
        if settings.atnos_publication_approval:
            return PolicyDecision(False, True, "Publication and social media actions always require approval.", "approval_required")
        return PolicyDecision(True, False, "Publication approval disabled by configuration.", "warning")

    if target_type == ActionTarget.PRIVATE_ACCOUNT:
        return PolicyDecision(False, True, "Private account changes are outside the Ubuntu sandbox and require approval.", "approval_required")

    if target_type == ActionTarget.UBUNTU_SANDBOX:
        if mode == AutonomyMode.FULL_AUTO_SANDBOX:
            if "sudo" in action and not settings.atnos_sandbox_allow_sudo:
                return PolicyDecision(False, True, "Ubuntu sandbox sudo is disabled by ATNOS_SANDBOX_ALLOW_SUDO.", "approval_required")
            return PolicyDecision(True, False, "Full auto sandbox mode allows this Ubuntu sandbox action and logs it.", "info")
        if risk_level == RiskLevel.HIGH:
            return PolicyDecision(False, True, "High-risk Ubuntu sandbox action requires approval outside full_auto_sandbox mode.", "approval_required")
        return PolicyDecision(True, False, "Low/medium Ubuntu sandbox action is allowed.", "info")

    if target_type == ActionTarget.MAIN_PC and risk_level == RiskLevel.HIGH and settings.atnos_main_pc_high_risk_approval:
        return PolicyDecision(False, True, "High-risk main PC actions require approval.", "approval_required")

    if risk_level == RiskLevel.HIGH:
        return PolicyDecision(False, True, "High-risk action outside the Ubuntu sandbox requires approval.", "approval_required")

    return PolicyDecision(True, False, "Action is low/medium risk and not a protected publication, billing, secret or main-PC operation.", "info")


def _normalize(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _normalize_risk(value: str | RiskLevel, action: str) -> RiskLevel:
    if isinstance(value, RiskLevel):
        risk = value
    else:
        try:
            risk = RiskLevel(str(value).casefold())
        except ValueError:
            risk = RiskLevel.LOW
    if risk != RiskLevel.HIGH and _contains_any(action, HIGH_RISK_TOKENS):
        return RiskLevel.HIGH
    return risk


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token in value for token in tokens)


def _should_log(settings: Settings) -> bool:
    return bool(settings.atnos_sandbox_log_everything or settings.atnos_log_all_actions)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(token.replace(" ", "_") in normalized for token in SECRET_TOKENS)


def _redact_assignment(value: str, key: str) -> str:
    import re

    pattern = re.compile(rf"({re.escape(key)}\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE)
    return pattern.sub(r"\1[REDACTED]", value)
