from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import utc_now_iso
from atnos.production.money_machine_strategy import MoneyMachineStrategyService
from atnos.production.voice_provider_doctor import write_voice_provider_doctor_report


class MoneyMachineRemediationService:
    """Mapuje blokery money-machine na konkretne akcje operacyjne."""

    def __init__(self, settings: Settings, *, session: Session | None = None) -> None:
        self.settings = settings
        self.session = session

    async def build_plan(
        self,
        *,
        account_id: str | None = None,
        include_live_checks: bool = True,
    ) -> dict[str, Any]:
        """Zwroc plan naprawczy bez wykonywania zewnetrznych akcji."""
        strategy = await MoneyMachineStrategyService(self.settings, session=self.session).build(
            account_id=account_id,
            include_live_checks=include_live_checks,
            send_gmail=False,
        )
        critical = [str(item) for item in strategy.get("blockers", {}).get("critical", [])]
        warnings = [str(item) for item in strategy.get("blockers", {}).get("warnings", [])]
        actions = self._actions_for_blockers(critical=critical, warnings=warnings, strategy=strategy)
        payload: dict[str, Any] = {
            "status": "ready" if not critical else "needs_remediation",
            "generated_at": utc_now_iso(),
            "account_id": account_id,
            "strategy_report": strategy.get("artifacts", {}),
            "critical_blockers": critical,
            "warnings": warnings,
            "summary": self._summary(actions),
            "actions": actions,
            "safe_auto_actions": [item for item in actions if item["execution"] == "safe_auto"],
            "manual_owner_actions": [item for item in actions if item["execution"] == "owner_action_required"],
            "configuration_actions": [item for item in actions if item["execution"] == "configuration_required"],
            "quality_recovery": self._quality_recovery(strategy),
            "public_action_guard": {
                "public_upload_auto_execution": False,
                "reason": "Public upload and public scheduling remain blocked until owner review and explicit approved=true.",
            },
        }
        payload["artifacts"] = self.write_report(payload, report_type="plan")
        return payload

    async def execute_safe_repairs(
        self,
        *,
        account_id: str | None = None,
        run_voice_doctor: bool = True,
        include_live_checks: bool = True,
    ) -> dict[str, Any]:
        """Wykonaj tylko naprawy bezpieczne: diagnostyki i raporty, bez publikacji i OAuth consent."""
        before = await self.build_plan(account_id=account_id, include_live_checks=include_live_checks)
        results: list[dict[str, Any]] = []
        if run_voice_doctor:
            try:
                report = await write_voice_provider_doctor_report(self.settings)
                results.append(
                    {
                        "action_id": "run_voice_provider_doctor",
                        "status": "completed",
                        "public_voice_ready": bool(report.get("public_voice_ready")),
                        "next_action": report.get("next_action"),
                        "report_path": report.get("report_path"),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "action_id": "run_voice_provider_doctor",
                        "status": "error",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
        after = await self.build_plan(account_id=account_id, include_live_checks=include_live_checks)
        payload: dict[str, Any] = {
            "status": "completed",
            "generated_at": utc_now_iso(),
            "executed": results,
            "before": self._compact_plan(before),
            "after": self._compact_plan(after),
            "not_executed_policy": [
                "No OAuth consent was started automatically.",
                "No public upload was enabled automatically.",
                "No public promotion was executed automatically.",
                "No payment method or card operation was attempted.",
            ],
        }
        payload["artifacts"] = self.write_report(payload, report_type="execution")
        return payload

    def write_report(self, payload: dict[str, Any], *, report_type: str) -> dict[str, str]:
        """Zapisz raport naprawczy JSON i Markdown do data/reports."""
        reports_dir = Path(self.settings.data_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(payload.get("generated_at") or utc_now_iso()).replace(":", "").replace("-", "")
        safe_stamp = stamp.replace("+0000", "Z").replace(".", "_")
        prefix = "money_machine_remediation" if report_type == "plan" else "money_machine_remediation_execution"
        json_path = reports_dir / f"{prefix}_{safe_stamp}.json"
        markdown_path = reports_dir / f"{prefix}_{safe_stamp}.md"
        markdown = self.render_markdown(payload, report_type=report_type)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(markdown, encoding="utf-8")
        return {
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
        }

    @staticmethod
    def render_markdown(payload: dict[str, Any], *, report_type: str) -> str:
        """Zamien plan naprawczy na czytelny raport operatorski."""
        lines = [
            "# Atnos Money Machine - remediation report",
            "",
            f"Type: {report_type}",
            f"Generated at: {payload.get('generated_at')}",
            f"Status: {payload.get('status')}",
            "",
        ]
        if report_type == "execution":
            lines.append("## Safe actions executed")
            executed = payload.get("executed") or []
            if executed:
                for item in executed:
                    parts = [
                        f"- {item.get('action_id')}: {item.get('status')}",
                        f"next_action={item.get('next_action')}" if item.get("next_action") else "",
                        f"report={item.get('report_path')}" if item.get("report_path") else "",
                    ]
                    lines.append(" | ".join(part for part in parts if part))
            else:
                lines.append("- None")
            lines.extend(["", "## Policy actions not executed"])
            for item in payload.get("not_executed_policy") or []:
                lines.append(f"- {item}")
            lines.extend(["", "## Before", MoneyMachineRemediationService._render_compact(payload.get("before") or {})])
            lines.extend(["", "## After", MoneyMachineRemediationService._render_compact(payload.get("after") or {})])
            return "\n".join(lines) + "\n"

        lines.extend(["## Critical blockers"])
        critical = payload.get("critical_blockers") or []
        if critical:
            lines.extend(f"- {item}" for item in critical)
        else:
            lines.append("- None")
        lines.extend(["", "## Warnings"])
        warnings = payload.get("warnings") or []
        if warnings:
            lines.extend(f"- {item}" for item in warnings)
        else:
            lines.append("- None")
        lines.extend(["", "## Required actions"])
        for action in payload.get("actions") or []:
            lines.append(f"### {action.get('label') or action.get('id')}")
            lines.append(f"- ID: {action.get('id')}")
            lines.append(f"- Execution: {action.get('execution')}")
            lines.append(f"- Reason: {action.get('reason')}")
            if action.get("endpoint"):
                lines.append(f"- Endpoint: {action.get('endpoint')}")
            if action.get("endpoints"):
                lines.append(f"- Endpoints: {', '.join(str(item) for item in action.get('endpoints') or [])}")
            if action.get("owner_instruction"):
                lines.append(f"- Owner instruction: {action.get('owner_instruction')}")
            if action.get("payload"):
                lines.append(f"- Payload: `{json.dumps(action.get('payload'), ensure_ascii=False)}`")
            blocks = action.get("blocks") or []
            if blocks:
                lines.append(f"- Unblocks: {', '.join(str(item) for item in blocks)}")
            lines.append("")
        recovery = payload.get("quality_recovery") if isinstance(payload.get("quality_recovery"), dict) else {}
        if recovery:
            lines.extend(["## Quality recovery queue", ""])
            lines.append(f"- Total recovery candidates: {recovery.get('total_candidates')}")
            counts = recovery.get("blocker_counts") if isinstance(recovery.get("blocker_counts"), dict) else {}
            if counts:
                lines.append(f"- Blocker counts: `{json.dumps(counts, ensure_ascii=False)}`")
            for item in recovery.get("targets") or []:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "- "
                    f"{item.get('job_id')} | {item.get('account_id')} | "
                    f"{item.get('recovery_action')} | blockers={', '.join(str(x) for x in item.get('public_quality_blockers') or [])}"
                )
            lines.append("")
        guard = payload.get("public_action_guard") or {}
        lines.extend(
            [
                "## Public action guard",
                f"- Public upload auto execution: {guard.get('public_upload_auto_execution')}",
                f"- Reason: {guard.get('reason')}",
            ]
        )
        artifacts = payload.get("strategy_report") or {}
        if artifacts:
            lines.extend(
                [
                    "",
                    "## Linked strategy report",
                    f"- JSON: {artifacts.get('json_path')}",
                    f"- Markdown: {artifacts.get('markdown_path')}",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_compact(payload: dict[str, Any]) -> str:
        lines = [
            f"- Status: {payload.get('status')}",
            f"- Critical blockers: {', '.join(str(item) for item in payload.get('critical_blockers') or []) or 'None'}",
            f"- Warnings: {', '.join(str(item) for item in payload.get('warnings') or []) or 'None'}",
        ]
        summary = payload.get("summary") or {}
        if summary:
            lines.append(f"- Summary: {json.dumps(summary, ensure_ascii=False)}")
        return "\n".join(lines)

    @staticmethod
    def _quality_recovery(strategy: dict[str, Any]) -> dict[str, Any]:
        money_machine = strategy.get("money_machine") if isinstance(strategy.get("money_machine"), dict) else {}
        operator_summary = (
            money_machine.get("operator_summary")
            if isinstance(money_machine.get("operator_summary"), dict)
            else {}
        )
        raw_targets = operator_summary.get("recovery_ready") if isinstance(operator_summary.get("recovery_ready"), list) else []
        cost_guard = operator_summary.get("cost_guard") if isinstance(operator_summary.get("cost_guard"), dict) else {}
        targets: list[dict[str, Any]] = []
        blocker_counts: dict[str, int] = {}
        for raw in raw_targets:
            if not isinstance(raw, dict):
                continue
            blockers = [str(item) for item in (raw.get("public_quality_blockers") or []) if str(item).strip()]
            for blocker in blockers:
                blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
            targets.append(
                {
                    "account_id": str(raw.get("account_id") or ""),
                    "job_id": str(raw.get("job_id") or ""),
                    "topic_id": str(raw.get("topic_id") or ""),
                    "topic_title": str(raw.get("topic_title") or ""),
                    "recovery_action": str(raw.get("recovery_action") or ""),
                    "recovery_endpoint": str(raw.get("recovery_endpoint") or ""),
                    "public_quality_blockers": blockers,
                    "quality": raw.get("quality") if isinstance(raw.get("quality"), dict) else {},
                }
            )
        priority = [
            "premium_visual_backend_required_for_public",
            "actual_visual_variety_missing_for_public",
            "actual_visual_variety_failed_for_public",
            "scene_concreteness_risk_for_public",
            "visual_relevance_risk_for_public",
            "premium_voice_required_for_public",
            "qa_not_passed_for_public",
        ]
        sorted_counts = {
            key: blocker_counts[key]
            for key in sorted(
                blocker_counts,
                key=lambda item: (
                    priority.index(item) if item in priority else len(priority),
                    item,
                ),
            )
        }
        return {
            "total_candidates": len(targets),
            "blocker_counts": sorted_counts,
            "targets": targets[:10],
            "cost_guard": {
                "enabled": bool(cost_guard.get("enabled")),
                "allowed": bool(cost_guard.get("allowed")),
                "blocked": bool(cost_guard.get("blocked")),
                "warning": bool(cost_guard.get("warning")),
                "currency": str(cost_guard.get("currency") or "PLN"),
                "estimated_action_cost_pln": cost_guard.get("estimated_action_cost_pln"),
                "monthly_remaining_pln": cost_guard.get("monthly_remaining_pln"),
                "daily_remaining_pln": cost_guard.get("daily_remaining_pln"),
            },
            "safe_to_execute_next_batch": bool(cost_guard.get("allowed")) and bool(targets),
            "next_batch_endpoint": "POST /api/production/money-machine/run-next",
            "next_batch_payload": {
                "execute": True,
                "execute_recovery": True,
                "approved_private_upload": False,
                "max_uploads": 3,
            },
        }

    @staticmethod
    def _actions_for_blockers(
        *,
        critical: list[str],
        warnings: list[str],
        strategy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        blocker_set = set(critical)
        warning_set = set(warnings)
        if "youtube_oauth" in blocker_set:
            actions.append(
                {
                    "id": "reconnect_youtube_oauth",
                    "label": "Reconnect YouTube OAuth",
                    "execution": "owner_action_required",
                    "reason": "Google token refresh is failing or OAuth scopes are missing.",
                    "endpoints": [
                        "GET /oauth/youtube-studio/status",
                        "POST /oauth/youtube-studio/connect",
                        "POST /oauth/youtube-studio/start",
                    ],
                    "owner_instruction": "Open the returned authorization_url and approve YouTube upload/read/analytics scopes for each channel account.",
                    "blocks": ["private_upload", "public_upload", "own_channel_analytics"],
                }
            )
        if "public_upload_disabled" in blocker_set:
            actions.append(
                {
                    "id": "enable_public_upload_policy",
                    "label": "Enable public upload only after private QA review",
                    "execution": "owner_action_required",
                    "reason": "ALLOW_PUBLIC_UPLOAD is disabled by design.",
                    "endpoint": "PATCH /settings/control-center/runtime",
                    "payload": {"public_upload_enabled": True},
                    "owner_instruction": "Enable only after reviewing a private upload and QA report.",
                    "blocks": ["public_scheduling"],
                }
            )
        if "premium_voice_provider_not_ready" in blocker_set:
            actions.append(
                {
                    "id": "run_voice_provider_doctor",
                    "label": "Run live voice provider doctor",
                    "execution": "safe_auto",
                    "reason": "Voice provider readiness is stale or failing.",
                    "endpoint": "POST /api/production/voice-provider/check",
                    "safe_default": True,
                    "blocks": ["public_quality_gate"],
                }
            )
        if "private_upload_public_quality_failed" in blocker_set or "public_quality_gate_failed" in blocker_set:
            actions.append(
                {
                    "id": "regenerate_failed_public_quality_jobs",
                    "label": "Regenerate jobs that failed public quality",
                    "execution": "configuration_required",
                    "reason": "Existing private/QA jobs are not good enough for public upload.",
                    "endpoint": "POST /api/production/money-machine/run-next",
                    "payload": {
                        "execute": True,
                        "execute_recovery": True,
                        "approved_private_upload": False,
                        "max_uploads": 3,
                    },
                    "owner_instruction": "Run this only when local ComfyUI/voice runtime is available; it queues regeneration, not public upload.",
                    "blocks": ["public_quality_gate", "private_upload_ready"],
                }
            )
        if "own_channel_analytics_missing" in blocker_set:
            actions.append(
                {
                    "id": "refresh_own_channel_analytics",
                    "label": "Refresh own-channel analytics after OAuth is fixed",
                    "execution": "configuration_required",
                    "reason": "Revenue loop needs retention/watch-time feedback from YouTube.",
                    "endpoint": "GET /api/intelligence/own?account_id={account_id}",
                    "owner_instruction": "Requires YouTube Analytics API enabled and valid OAuth token.",
                    "blocks": ["feedback_loop", "topic_scoring_feedback"],
                }
            )
        if "direct_card_use_forbidden_manual_checkout_only" in warning_set:
            actions.append(
                {
                    "id": "use_procurement_manual_checkout",
                    "label": "Use procurement requests, not raw card data",
                    "execution": "policy",
                    "reason": "Atnos cannot store or use card/CVV/bank data.",
                    "endpoint": "POST /api/procurement/requests",
                    "owner_instruction": "Atnos can prepare provider purchase requests; owner completes checkout manually.",
                    "blocks": [],
                }
            )
        if not actions:
            actions.append(
                {
                    "id": "monitor_and_generate_private_samples",
                    "label": "Monitor and continue private samples",
                    "execution": "safe_auto",
                    "reason": "No critical blocker was detected in the current strategy report.",
                    "endpoint": "POST /api/production/autopilot/run-once",
                    "payload": {"max_jobs": 1},
                    "blocks": [],
                }
            )
        for action in actions:
            action["strategy_status"] = strategy.get("status")
        return actions

    @staticmethod
    def _summary(actions: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(actions),
            "safe_auto": sum(1 for item in actions if item["execution"] == "safe_auto"),
            "owner_action_required": sum(1 for item in actions if item["execution"] == "owner_action_required"),
            "configuration_required": sum(1 for item in actions if item["execution"] == "configuration_required"),
            "policy": sum(1 for item in actions if item["execution"] == "policy"),
        }

    @staticmethod
    def _compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": plan.get("status"),
            "critical_blockers": plan.get("critical_blockers") or [],
            "warnings": plan.get("warnings") or [],
            "summary": plan.get("summary") or {},
        }
