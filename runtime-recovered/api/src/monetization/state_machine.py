from __future__ import annotations

from collections.abc import Callable
from typing import Any

from atnos.core.config import Settings

from .approval_bridge import MonetizationApprovalBridge
from .models import ExecutionPhase, MonetizationRun, RiskState


ExecutionCallable = Callable[[], dict[str, Any] | None]
VerifyCallable = Callable[[dict[str, Any] | None], bool]
RecoverCallable = Callable[[Exception], dict[str, Any] | None]


HIGH_RISK_TOKENS = (
    "public upload",
    "publish public",
    "opublikuj publicznie",
    "payment",
    "billing",
    "stripe",
    "secret",
    "api key",
    "token",
    "password",
    "haslo",
    "oauth",
    "delete",
    "remove",
    "rm -rf",
    "main pc",
    "system32",
)


class MonetizationStateMachine:
    """Strict PLAN -> EXECUTE -> VERIFY -> RECOVER -> COMMIT coordinator for Shorts monetization work."""

    def __init__(
        self,
        settings: Settings,
        *,
        approval_bridge: MonetizationApprovalBridge | None = None,
        provider_fallback_order: tuple[str, ...] = ("anthropic-cloud", "gemini-cloud", "ollama-local"),
    ) -> None:
        self.settings = settings
        self.approval_bridge = approval_bridge or MonetizationApprovalBridge(settings)
        self.provider_fallback_order = provider_fallback_order

    def run(
        self,
        *,
        task: str,
        execute: ExecutionCallable,
        verify: VerifyCallable | None = None,
        recover: RecoverCallable | None = None,
        target: str = "local_workspace",
        risk_level: str = "low",
    ) -> MonetizationRun:
        run = MonetizationRun(task=task)
        run.record(
            ExecutionPhase.PLAN,
            status="planned",
            message="Monetization state machine planned the requested batch.",
            payload={"target": target, "risk_level": risk_level},
        )

        high_risk_reason = self._high_risk_reason(task=task, target=target, risk_level=risk_level)
        if high_risk_reason is not None:
            run.risk_state = RiskState.APPROVAL_REQUIRED
            approval = self.approval_bridge.create_approval(
                action=task,
                risk_reason=high_risk_reason,
                payload={"target": target, "risk_level": risk_level, "revenue_vector": run.revenue_vector.value},
            )
            run.approval_id = approval.approval_id
            run.status = "awaiting_approval"
            run.record(
                ExecutionPhase.EXECUTE,
                status="blocked_for_approval",
                message="High-risk action was intercepted before execution.",
                payload=approval.to_dict(),
            )
            self._log(run)
            return run

        run.record(ExecutionPhase.EXECUTE, status="started", message="Executing safe local monetization batch.")
        try:
            result = execute() or {}
        except Exception as exc:
            run.errors.append(f"{type(exc).__name__}: {exc}")
            run.record(
                ExecutionPhase.RECOVER,
                status="recovering",
                message="Execution failed; recovery path selected.",
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
            try:
                recovery_payload = recover(exc) if recover is not None else None
            except Exception as recovery_exc:
                run.errors.append(f"recovery_failed: {type(recovery_exc).__name__}: {recovery_exc}")
                recovery_payload = None
            run.fallback_provider = self.select_fallback_provider(task=task)
            if recovery_payload:
                run.artifacts.update({str(key): str(value) for key, value in recovery_payload.items()})
            run.status = "recovered"
            run.record(
                ExecutionPhase.COMMIT,
                status=run.status,
                message="Recovery completed without throwing an unhandled exception.",
                payload={"fallback_provider": run.fallback_provider},
            )
            self._log(run)
            return run

        run.artifacts.update({str(key): str(value) for key, value in result.items()})
        verification_ok = True if verify is None else bool(verify(result))
        run.record(
            ExecutionPhase.VERIFY,
            status="passed" if verification_ok else "failed",
            message="Verification finished.",
            payload={"ok": verification_ok},
        )
        run.status = "committed" if verification_ok else "verification_failed"
        run.record(
            ExecutionPhase.COMMIT,
            status=run.status,
            message="Safe local monetization batch committed." if verification_ok else "Batch stopped after failed verification.",
        )
        self._log(run)
        return run

    def select_fallback_provider(self, *, task: str) -> str:
        text = task.casefold()
        if "strict_local" in text or "strict-local" in text:
            return "ollama-local"
        for provider in self.provider_fallback_order:
            if provider:
                return provider
        return "ollama-local"

    def _high_risk_reason(self, *, task: str, target: str, risk_level: str) -> str | None:
        if risk_level.casefold() == "high":
            return "Action declared high risk."
        combined = f"{task} {target}".casefold()
        if "ubuntu" in combined and "sandbox" in combined and self.settings.atnos_sandbox_allow_high_risk:
            return None
        for token in HIGH_RISK_TOKENS:
            if token in combined:
                return f"Action contains high-risk token: {token}"
        return None

    def _log(self, run: MonetizationRun) -> None:
        path = self.settings.data_dir / "logs" / "monetization_state_machine.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        import json

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run.to_dict(), ensure_ascii=False, default=str) + "\n")
