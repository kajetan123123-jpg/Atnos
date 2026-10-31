from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads, read_intelligence_account_configs, rows_to_dicts, utc_now_iso
from atnos.production.money_machine import MoneyMachineStatusService


QUALITY_REVIEW_BLOCKED = "quality_review_blocked"


@dataclass(frozen=True)
class CanonicalProject:
    project_id: str
    project_type: str
    name: str
    description: str
    expected_niches: tuple[str, ...] = ()


CANONICAL_LOCAL_PROJECTS: tuple[CanonicalProject, ...] = (
    CanonicalProject(
        project_id="creator:synthetic-model-lab",
        project_type="synthetic_creator",
        name="Synthetic Model Lab",
        description="Synthetic creator project. Drafts only until platform accounts, consent policy and payment rules are configured.",
    ),
    CanonicalProject(
        project_id="investment:portfolio-alpha",
        project_type="investment",
        name="Portfolio Alpha",
        description="Investment research and portfolio monitoring project. Trading stays blocked until read-only broker access and risk policy exist.",
    ),
)

SOCIAL_DISTRIBUTION_TARGETS: tuple[tuple[str, str], ...] = (
    ("youtube", "YouTube Shorts"),
    ("tiktok", "TikTok"),
    ("instagram", "Instagram Reels"),
)

DEFAULT_TOOLING_REPORT_REF = "data/learning/tooling-research/20260608T003659Z/atnos_tooling_research_report.md"


class ProjectReadinessService:
    """Builds one operator-facing readiness snapshot for every money-machine project."""

    def __init__(self, settings: Settings, *, session: Session | None = None) -> None:
        self.settings = settings
        self.session = session

    def build(self) -> dict[str, Any]:
        accounts = self._account_configs_by_id()
        money_machine = self._money_machine_status()
        financials = self._financial_snapshot(money_machine)
        money_by_account = {
            str(item.get("account_id") or ""): item
            for item in money_machine.get("accounts", [])
            if isinstance(item, dict)
        }
        projects: list[dict[str, Any]] = []
        for account_id, config in accounts.items():
            canonical = CanonicalProject(
                project_id=account_id,
                project_type="youtube",
                name=str(config.get("channel_title") or account_id),
                description="Configured YouTube content account.",
                expected_niches=tuple(str(item) for item in config.get("niches") or []),
            )
            projects.append(self._build_youtube_project(canonical, config, money_by_account.get(account_id), financials))

        projects.extend(self._build_synthetic_projects(financials))
        projects.append(self._build_investment_project(financials))

        return {
            "generated_at": utc_now_iso(),
            "source": "project_readiness_service",
            "summary": self._summary(projects, money_machine),
            "financials": financials,
            "money_machine": {
                "status": money_machine.get("status"),
                "autopilot_enabled": bool(money_machine.get("autopilot_enabled")),
                "human_approval_before_upload": bool(money_machine.get("human_approval_before_upload", True)),
                "public_upload_enabled": bool(money_machine.get("public_upload_enabled")),
                "global_blockers": list(money_machine.get("global_blockers") or []),
                "next_actions": list(money_machine.get("next_actions") or []),
            },
            "projects": projects,
        }

    def _build_youtube_project(
        self,
        canonical: CanonicalProject,
        config: dict[str, Any] | None,
        money: dict[str, Any] | None,
        financials: dict[str, Any],
    ) -> dict[str, Any]:
        niches = list(config.get("niches") or []) if config else []
        oauth = dict(money.get("youtube_oauth") or {}) if money else {}
        topic_counts = dict(money.get("topic_counts") or {}) if money else {}
        job_counts = dict(money.get("job_counts") or {}) if money else {}
        signals = dict(money.get("signals") or {}) if money else {}
        ready_private_count = int(money.get("ready_private_upload_count") or 0) if money else 0
        public_count = int(money.get("public_promotion_count") or 0) if money else 0
        quality_review = self._quality_review_snapshot(canonical.project_id)
        quality_review_blocked_count = int(quality_review.get("blocked_count") or 0)
        blockers = list(money.get("blockers") or []) if money else []
        if quality_review_blocked_count:
            blockers.append(f"quality_review_blocked:{quality_review_blocked_count}")
            for blocker in quality_review.get("top_blockers") or []:
                blockers.append(f"quality_review:{blocker}")
        distribution_targets = self._youtube_distribution_targets(
            canonical=canonical,
            config=config,
            oauth=oauth,
            can_upload_private=bool(oauth.get("ready")) and ready_private_count > 0,
            can_publish_public=bool(getattr(self.settings, "allow_public_upload", False)) and public_count > 0,
        )

        requirements = [
            self._requirement(
                "account_config",
                "Account exists in intelligence registry",
                bool(config),
                missing_detail="Create or sync this YouTube account in Settings > Accounts.",
            ),
            self._requirement(
                "niches",
                "Niche keywords configured",
                bool(niches),
                detail=", ".join(niches) if niches else "",
                missing_detail="Add at least one niche keyword.",
            ),
            self._requirement(
                "youtube_oauth",
                "YouTube OAuth is connected",
                bool(oauth.get("ready")),
                detail=f"OAuth account {oauth.get('account_id')}" if oauth.get("ready") else str(oauth.get("reason") or ""),
                missing_detail="Connect YouTube Studio OAuth for this channel.",
            ),
            self._requirement(
                "signals",
                "Trend and competitor signals available",
                int(signals.get("youtube_trend_videos") or 0) > 0 or int(signals.get("google_trends_keywords") or 0) > 0,
                detail=f"yt={int(signals.get('youtube_trend_videos') or 0)}, gtrends={int(signals.get('google_trends_keywords') or 0)}",
                missing_detail="Run intelligence collection for this account.",
                limited_if_missing=True,
            ),
            self._requirement(
                "topic_buffer",
                "Topic queue has candidates",
                int(topic_counts.get("buffer_count") or 0) > 0,
                detail=f"buffer={int(topic_counts.get('buffer_count') or 0)}",
                missing_detail="Generate and score topics.",
                limited_if_missing=True,
            ),
            self._requirement(
                "private_upload_buffer",
                "QA-passed private upload package exists",
                ready_private_count > 0,
                detail=f"ready_private={ready_private_count}",
                missing_detail="Produce at least one QA-passed private render.",
                limited_if_missing=True,
            ),
            self._requirement(
                "quality_review_clear",
                "Quality review backlog is clear",
                quality_review_blocked_count == 0,
                detail="No blocked quality-review jobs.",
                missing_detail=(
                    f"{quality_review_blocked_count} production job(s) are blocked by quality review. "
                    "Run quality remediation and regenerate before owner review/upload."
                ),
            ),
        ]

        can_research = bool(config and niches)
        can_generate_private = bool(config and niches)
        can_upload_private = bool(oauth.get("ready")) and ready_private_count > 0
        can_publish_public = bool(getattr(self.settings, "allow_public_upload", False)) and public_count > 0

        status_value = "ready" if can_upload_private else "limited" if can_generate_private else "blocked"
        if blockers and not can_upload_private:
            status_value = "blocked"
        next_action = self._youtube_next_action(
            can_research=can_research,
            can_generate_private=can_generate_private,
            can_upload_private=can_upload_private,
            ready_private_count=ready_private_count,
            money_next_action=str(money.get("next_action") or "") if money else "",
        )
        if quality_review_blocked_count:
            next_action = "Run quality auto-remediation and regenerate blocked Shorts before review/upload."

        return {
            "project_id": canonical.project_id,
            "project_type": canonical.project_type,
            "name": str(config.get("channel_title") or canonical.name) if config else canonical.name,
            "description": canonical.description,
            "status": status_value,
            "safe_mode": bool(getattr(self.settings, "shorts_require_human_approval_before_upload", True)),
            "capabilities": {
                "can_research": can_research,
                "can_generate_private": can_generate_private,
                "can_upload_private": can_upload_private,
                "can_publish_public": can_publish_public,
                "can_trade_or_purchase": False,
            },
            "requirements": requirements,
            "access_playbook": self._youtube_access_playbook(
                config=config,
                oauth=oauth,
                can_upload_private=can_upload_private,
                can_publish_public=can_publish_public,
            ),
            "distribution_targets": distribution_targets,
            "tooling_plan": self._youtube_tooling_plan(
                oauth=oauth,
                config=config,
                distribution_targets=distribution_targets,
            ),
            "blockers": blockers,
            "next_action": next_action,
            "operator_decision": self._youtube_operator_decision(
                can_research=can_research,
                can_generate_private=can_generate_private,
                can_upload_private=can_upload_private,
                can_publish_public=can_publish_public,
                ready_private_count=ready_private_count,
                public_count=public_count,
                blockers=blockers,
                next_action=next_action,
                financials=financials,
            ),
            "financials": financials,
            "evidence": {
                "account_configured": bool(config),
                "niches": niches,
                "expected_niches": list(canonical.expected_niches),
                "region": config.get("region") if config else None,
                "youtube_oauth": oauth,
                "topic_counts": topic_counts,
                "job_counts": job_counts,
                "signals": signals,
                "ready_private_upload_count": ready_private_count,
                "public_promotion_count": public_count,
                "quality_review": quality_review,
                "cross_platform_ready": all(item["status"] == "ready" for item in distribution_targets),
                "distribution_targets": distribution_targets,
                "human_approval_before_upload": bool(getattr(self.settings, "shorts_require_human_approval_before_upload", True)),
                "public_upload_enabled": bool(getattr(self.settings, "allow_public_upload", False)),
            },
        }

    def _build_synthetic_projects(self, financials: dict[str, Any]) -> list[dict[str, Any]]:
        projects = self._load_creator_projects()
        synthetic = [
            item
            for item in projects
            if str(getattr(item, "project_type", "")) in {"synthetic_influencer", "synthetic_model", "creator_monetization"}
        ]
        if not synthetic:
            canonical = CANONICAL_LOCAL_PROJECTS[0]
            return [
                self._blocked_local_project(
                    canonical,
                    "Configure creator account, asset vault, content policy and payment rules.",
                    financials=financials,
                )
            ]

        payloads: list[dict[str, Any]] = []
        for item in synthetic:
            linked_accounts = list(getattr(item, "linked_accounts_json", []) or [])
            platforms = list(getattr(item, "platforms_json", []) or [])
            has_policy = bool(str(getattr(item, "content_boundaries", "") or "").strip())
            has_disclosure = str(getattr(item, "disclosure_policy", "") or "") != "not_required"
            requirements = [
                self._requirement("creator_account", "Creator platform account linked", bool(linked_accounts), missing_detail="Link at least one creator platform account."),
                self._requirement("platforms", "Target platforms selected", bool(platforms), detail=", ".join(platforms), missing_detail="Select target platforms."),
                self._requirement("content_policy", "Content boundaries configured", has_policy, missing_detail="Define content boundaries and brand safety policy."),
                self._requirement("synthetic_disclosure", "Synthetic disclosure policy configured", has_disclosure, missing_detail="Set disclosure policy for synthetic persona."),
            ]
            core_safety_ready = bool(platforms) and has_policy and has_disclosure
            fully_connected = core_safety_ready and bool(linked_accounts)
            next_action = (
                "Generate supervised creator drafts; connect platform account only for manual release packages."
                if core_safety_ready and not fully_connected
                else "Generate supervised creator drafts."
                if fully_connected
                else "Complete creator safety policy and disclosure before draft generation."
            )
            payloads.append(
                {
                    "project_id": f"creator:{getattr(item, 'id')}",
                    "project_type": "synthetic_creator",
                    "name": getattr(item, "name", "Synthetic Creator"),
                    "description": "Synthetic creator project tracked in creator_ops.",
                    "status": "limited" if core_safety_ready else "blocked",
                    "safe_mode": True,
                    "capabilities": {
                        "can_research": core_safety_ready,
                        "can_generate_private": core_safety_ready,
                        "can_upload_private": False,
                        "can_publish_public": False,
                        "can_trade_or_purchase": False,
                    },
                    "requirements": requirements,
                    "access_playbook": self._synthetic_access_playbook(
                        linked_accounts=linked_accounts,
                        platforms=platforms,
                        has_policy=has_policy,
                        has_disclosure=has_disclosure,
                    ),
                    "distribution_targets": self._creator_distribution_targets(platforms=platforms, linked_accounts=linked_accounts),
                    "tooling_plan": self._synthetic_tooling_plan(
                        linked_accounts=linked_accounts,
                        platforms=platforms,
                        has_policy=has_policy,
                        has_disclosure=has_disclosure,
                    ),
                    "blockers": [req["detail"] for req in requirements if req["status"] != "ready"],
                    "next_action": next_action,
                    "operator_decision": self._synthetic_operator_decision(
                        core_safety_ready=core_safety_ready,
                        fully_connected=fully_connected,
                        next_action=next_action,
                        financials=financials,
                    ),
                    "financials": financials,
                    "evidence": {
                        "creator_project_id": getattr(item, "id", None),
                        "canonical_project_id": getattr(item, "project_id", None),
                        "project_type": getattr(item, "project_type", None),
                        "platforms": platforms,
                        "linked_account_count": len(linked_accounts),
                        "core_safety_ready": core_safety_ready,
                        "fully_connected": fully_connected,
                        "operating_mode": getattr(item, "operating_mode", None),
                        "approval_policy": getattr(item, "approval_policy", None),
                    },
                }
            )
        return payloads

    def _build_investment_project(self, financials: dict[str, Any]) -> dict[str, Any]:
        canonical = CANONICAL_LOCAL_PROJECTS[1]
        try:
            from atnos.investment.cockpit import InvestmentCockpitService

            cockpit = InvestmentCockpitService(self.settings).status()
        except Exception as exc:  # pragma: no cover - defensive operator endpoint
            return self._blocked_local_project(
                canonical,
                "Fix investment cockpit storage before portfolio research.",
                capabilities={
                    "can_research": False,
                    "can_generate_private": False,
                    "can_upload_private": False,
                    "can_publish_public": False,
                    "can_trade_or_purchase": False,
                },
                requirements=[
                    self._requirement("investment_cockpit", "Investment cockpit storage", False, missing_detail=f"{type(exc).__name__}: {exc}"),
                    self._requirement("trade_approval", "Real trading remains blocked", True, detail="Trading disabled by default."),
                ],
                financials=financials,
            )

        portfolio_settings = dict(cockpit.get("portfolio_settings") or {})
        broker_access = str(cockpit.get("broker_access") or "")
        market_data_sources = list(cockpit.get("market_data_sources") or [])
        learning_resources = list(cockpit.get("learning_resources") or [])
        monthly_budget = float(portfolio_settings.get("monthly_spend_budget_pln") or 0.0)
        payment_ref = str(portfolio_settings.get("payment_method_ref") or "")
        broker_metadata_ready = broker_access != "not_registered"
        broker_read_only_ready = broker_access == "read_only_verified"
        market_data_ready = str(cockpit.get("market_data_status") or "") == "configured"
        learning_ready = bool(learning_resources)
        budget_ready = monthly_budget > 0
        payment_ready = bool(portfolio_settings.get("payment_method_configured") and payment_ref)
        requirements = [
            self._requirement("investment_cockpit", "Watchlist and paper-trading cockpit", True, detail="Local SQLite cockpit is available."),
            self._requirement("paper_trading", "Paper trading sandbox", True, detail="Paper ledger is active; no real orders are sent."),
            self._requirement("risk_policy", "Baseline risk and approval policy", True, detail="Baseline safe policy blocks real trading."),
            self._requirement(
                "broker_metadata",
                "Broker account metadata configured",
                broker_metadata_ready,
                detail=f"Broker access state: {broker_access}.",
                missing_detail="Configure XTB demo metadata first.",
            ),
            self._requirement(
                "broker_read_only",
                "Broker read-only access connected",
                broker_read_only_ready,
                detail="Read-only broker access is marked as verified.",
                missing_detail="Connect broker in read-only mode first.",
            ),
            self._requirement(
                "market_data",
                "Market data source configured",
                market_data_ready,
                detail=f"Sources: {', '.join(market_data_sources)}.",
                missing_detail="Configure a market data provider.",
            ),
            self._requirement(
                "learning_resources",
                "Investment learning resources configured",
                learning_ready,
                detail=f"{len(learning_resources)} learning resources configured.",
                missing_detail="Add courses/resources for Atnos before autonomous research.",
            ),
            self._requirement(
                "spend_budget",
                "Monthly spending budget configured",
                budget_ready,
                detail=f"Monthly budget: {monthly_budget:.2f} PLN.",
                missing_detail="Set a monthly budget limit.",
            ),
            self._requirement(
                "payment_reference",
                "Payment method reference configured",
                payment_ready,
                detail="Payment method is configured as a vault reference.",
                missing_detail="Use a vault/payment reference, not card data in settings.",
            ),
            self._requirement("trade_approval", "Real trading requires explicit approval", True, detail="Trading disabled by default."),
        ]
        blockers = [req["detail"] for req in requirements if req["status"] != "ready"]
        return {
            "project_id": canonical.project_id,
            "project_type": canonical.project_type,
            "name": canonical.name,
            "description": canonical.description,
            "status": "limited",
            "safe_mode": True,
            "capabilities": {
                "can_research": True,
                "can_generate_private": False,
                "can_upload_private": False,
                "can_publish_public": False,
                "can_trade_or_purchase": False,
            },
            "requirements": requirements,
            "access_playbook": self._investment_access_playbook(cockpit=cockpit),
            "distribution_targets": [],
            "tooling_plan": self._investment_tooling_plan(cockpit=cockpit),
            "blockers": blockers,
            "next_action": "Use watchlist and paper trades now; connect read-only broker and market data later.",
            "operator_decision": self._investment_operator_decision(financials=financials),
            "financials": financials,
            "evidence": {
                "configured_backend": True,
                "mode": cockpit.get("mode"),
                "watchlist_count": cockpit.get("watchlist_count"),
                "paper_trade_count": cockpit.get("paper_trade_count"),
                "paper_position_count": cockpit.get("paper_position_count"),
                "real_trading_enabled": cockpit.get("real_trading_enabled"),
                "broker_access": cockpit.get("broker_access"),
                "market_data_status": cockpit.get("market_data_status"),
                "market_data_sources": market_data_sources,
                "learning_resource_count": len(learning_resources),
                "monthly_spend_budget_pln": monthly_budget,
                "payment_method_configured": payment_ready,
            },
        }

    def _blocked_local_project(
        self,
        canonical: CanonicalProject,
        next_action: str,
        *,
        capabilities: dict[str, bool] | None = None,
        requirements: list[dict[str, Any]] | None = None,
        financials: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requirements = requirements or [
            self._requirement("access", "External access configured", False, missing_detail=next_action),
            self._requirement("policy", "Safety policy configured", False, missing_detail="Define policy before automation."),
            self._requirement("human_approval", "Human approval required", True, detail="Safe mode is active."),
        ]
        return {
            "project_id": canonical.project_id,
            "project_type": canonical.project_type,
            "name": canonical.name,
            "description": canonical.description,
            "status": "blocked",
            "safe_mode": True,
            "capabilities": capabilities
            or {
                "can_research": False,
                "can_generate_private": False,
                "can_upload_private": False,
                "can_publish_public": False,
                "can_trade_or_purchase": False,
            },
            "requirements": requirements,
            "access_playbook": self._local_access_playbook(canonical.project_type, requirements),
            "distribution_targets": [],
            "tooling_plan": self._tooling_plan_for_project_type(canonical.project_type, requirements),
            "blockers": [req["detail"] for req in requirements if req["status"] != "ready"],
            "next_action": next_action,
            "operator_decision": self._local_operator_decision(
                project_type=canonical.project_type,
                next_action=next_action,
                financials=financials or self._financial_snapshot({}),
            ),
            "financials": financials or self._financial_snapshot({}),
            "evidence": {"configured_backend": False},
        }

    def _financial_snapshot(self, money_machine: dict[str, Any]) -> dict[str, Any]:
        """Return the safe spending snapshot exposed to operator UI.

        This intentionally never exposes or stores payment-card data. Atnos can
        reason about budget and prepare procurement requests, but checkout stays
        manual.
        """
        cost_guard = money_machine.get("cost_guard") if isinstance(money_machine.get("cost_guard"), dict) else {}
        if not cost_guard:
            try:
                from atnos.production.cost_guard import CostGuard

                guard = CostGuard(self.settings)
                estimate = guard.estimate_production_job()
                cost_guard = guard.check_can_spend(
                    estimate.estimated_cost_pln,
                    operation="project_readiness_next_production_job",
                )
                cost_guard["next_job_estimate_notes"] = list(estimate.notes)
            except Exception as exc:  # pragma: no cover - defensive operator endpoint
                cost_guard = {
                    "enabled": True,
                    "blocked": True,
                    "warning": True,
                    "allowed": False,
                    "currency": "PLN",
                    "estimated_action_cost_pln": 0.0,
                    "monthly_spent_pln": 0.0,
                    "daily_spent_pln": 0.0,
                    "blockers": [f"{type(exc).__name__}: {exc}"],
                    "smart_spending": {},
                }

        smart = cost_guard.get("smart_spending") if isinstance(cost_guard.get("smart_spending"), dict) else {}
        if not smart:
            try:
                from atnos.procurement.smart_spending import SmartSpendingService

                smart = SmartSpendingService(self.settings).snapshot()
            except Exception:
                smart = {}

        card_access = smart.get("card_access") if isinstance(smart.get("card_access"), dict) else {}
        policy = smart.get("policy") if isinstance(smart.get("policy"), dict) else {}
        budget_allowed = bool(cost_guard.get("allowed", not bool(cost_guard.get("blocked"))))
        return {
            "currency": str(cost_guard.get("currency") or smart.get("currency") or "PLN"),
            "available_balance_pln": float(smart.get("available_balance_pln") or 0.0),
            "current_cash_pln": float(smart.get("available_balance_pln") or 0.0),
            "starting_balance_pln": float(smart.get("starting_balance_pln") or 0.0),
            "earned_total_pln": float(smart.get("earned_total_pln") or 0.0),
            "spent_total_pln": float(smart.get("spent_total_pln") or 0.0),
            "committed_expenses_pln": float(smart.get("committed_expenses_pln") or 0.0),
            "net_profit_pln": float(smart.get("net_profit_pln") or 0.0),
            "next_private_render_estimated_cost_pln": float(cost_guard.get("estimated_action_cost_pln") or 0.0),
            "budget_allowed": budget_allowed,
            "budget_blocked": bool(cost_guard.get("blocked")) or not budget_allowed,
            "budget_warning": bool(cost_guard.get("warning")),
            "budget_blockers": list(cost_guard.get("blockers") or []),
            "monthly_remaining_pln": cost_guard.get("monthly_remaining_pln"),
            "daily_remaining_pln": cost_guard.get("daily_remaining_pln"),
            "monthly_spent_pln": float(cost_guard.get("monthly_spent_pln") or 0.0),
            "daily_spent_pln": float(cost_guard.get("daily_spent_pln") or 0.0),
            "manual_checkout_only": bool(policy.get("manual_checkout_required", True)),
            "autonomous_card_charges": bool(policy.get("autonomous_card_charges", False)),
            "card_connected": bool(card_access.get("card_connected", False)),
            "can_use_card": bool(card_access.get("can_use_card", False)),
            "card_data_storage": "forbidden",
            "payment_execution": str(card_access.get("payment_execution") or "manual_checkout_only"),
            "rule": str(
                (smart.get("money_awareness") if isinstance(smart.get("money_awareness"), str) else "")
                or policy.get("spend_rule")
                or "Atnos can spend only from available operating balance; owner completes checkout manually."
            ),
        }

    def _operator_decision(
        self,
        *,
        state: str,
        label: str,
        primary_action: str,
        primary_endpoint: str | None,
        requires_owner_approval: bool,
        safe_to_run_now: bool,
        risk_level: str,
        reason: str,
        financials: dict[str, Any],
        public_quality_ready: bool = False,
        public_upload_enabled: bool = False,
        forbidden_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "label": label,
            "primary_action": primary_action,
            "primary_endpoint": primary_endpoint,
            "requires_owner_approval": requires_owner_approval,
            "safe_to_run_now": safe_to_run_now,
            "risk_level": risk_level,
            "reason": reason,
            "estimated_cost_pln": financials.get("next_private_render_estimated_cost_pln", 0.0),
            "available_balance_pln": financials.get("available_balance_pln", 0.0),
            "budget_allowed": bool(financials.get("budget_allowed")),
            "budget_blockers": list(financials.get("budget_blockers") or []),
            "manual_checkout_only": bool(financials.get("manual_checkout_only", True)),
            "card_data_storage": "forbidden",
            "public_quality_ready": public_quality_ready,
            "public_upload_enabled": public_upload_enabled,
            "forbidden_actions": forbidden_actions or [],
        }

    def _youtube_operator_decision(
        self,
        *,
        can_research: bool,
        can_generate_private: bool,
        can_upload_private: bool,
        can_publish_public: bool,
        ready_private_count: int,
        public_count: int,
        blockers: list[str],
        next_action: str,
        financials: dict[str, Any],
    ) -> dict[str, Any]:
        budget_allowed = bool(financials.get("budget_allowed"))
        public_quality_blocked = any(
            token in str(blocker)
            for blocker in blockers
            for token in (
                "public_quality",
                "premium_voice",
                "visual_backend",
                "private_upload_public_quality",
                "quality_review",
                "thumbnail_quality",
                "semantic_visual",
                "semantic_first_frame",
                "scene_concreteness",
                "visual_relevance",
            )
        )
        if can_publish_public:
            return self._operator_decision(
                state="ready_public_review",
                label="Gotowe do publicznej decyzji",
                primary_action=f"Sprawdź {public_count} prywatny film i zatwierdź harmonogram publiczny.",
                primary_endpoint="/api/production/money-machine/run-publication",
                requires_owner_approval=True,
                safe_to_run_now=False,
                risk_level="high",
                reason="Publiczny upload jest włączony, ale publikacja nadal wymaga świadomej decyzji operatora.",
                financials=financials,
                public_quality_ready=True,
                public_upload_enabled=True,
                forbidden_actions=["autonomous_public_publish_without_review", "autonomous_card_charge"],
            )
        if can_upload_private:
            return self._operator_decision(
                state="ready_private_upload_review",
                label="Gotowe do prywatnego testu",
                primary_action=f"Zatwierdź prywatny upload pakietu QA ({ready_private_count} gotowe).",
                primary_endpoint="/api/production/money-machine/run-next",
                requires_owner_approval=True,
                safe_to_run_now=False,
                risk_level="medium",
                reason="Film przeszedł lokalną bramkę QA; upload prywatny jest bezpiecznym etapem review.",
                financials=financials,
                public_quality_ready=False,
                public_upload_enabled=False,
                forbidden_actions=["public_publish", "autonomous_card_charge"],
            )
        if public_quality_blocked:
            return self._operator_decision(
                state="blocked_public_quality",
                label="Blokada jakości publicznej",
                primary_action="Napraw premium voice/visual backend i wygeneruj nowy render przed kolejnym uploadem.",
                primary_endpoint="/api/production/money-machine/run-next",
                requires_owner_approval=not budget_allowed,
                safe_to_run_now=can_generate_private and budget_allowed,
                risk_level="medium",
                reason="Aktualny materiał nie spełnia progu publicznej jakości, więc automatyczna publikacja jest zablokowana.",
                financials=financials,
                public_quality_ready=False,
                public_upload_enabled=False,
                forbidden_actions=["public_publish", "reuse_failed_public_quality_render", "autonomous_card_charge"],
            )
        if can_generate_private:
            return self._operator_decision(
                state="can_generate_private",
                label="Może tworzyć prywatne próbki",
                primary_action=next_action,
                primary_endpoint="/api/production/content-loop/run-once",
                requires_owner_approval=not budget_allowed,
                safe_to_run_now=budget_allowed,
                risk_level="low",
                reason="Konto ma niszę i konfigurację do researchu/produkcji, ale nie ma jeszcze gotowego prywatnego uploadu QA.",
                financials=financials,
                public_quality_ready=False,
                public_upload_enabled=False,
                forbidden_actions=["public_publish", "autonomous_card_charge"],
            )
        if can_research:
            return self._operator_decision(
                state="research_only",
                label="Tylko research i tematy",
                primary_action="Zbierz sygnały i wygeneruj kolejkę tematów przed produkcją.",
                primary_endpoint="/api/intelligence/collect-all",
                requires_owner_approval=False,
                safe_to_run_now=True,
                risk_level="low",
                reason="Research nie wymaga uploadu ani płatności kartą.",
                financials=financials,
                public_quality_ready=False,
                public_upload_enabled=False,
                forbidden_actions=["upload", "public_publish", "autonomous_card_charge"],
            )
        return self._operator_decision(
            state="needs_account_access",
            label="Wymaga dostępu",
            primary_action="Uzupełnij konfigurację konta, niszę i OAuth YouTube.",
            primary_endpoint=None,
            requires_owner_approval=True,
            safe_to_run_now=False,
            risk_level="medium",
            reason="Bez konfiguracji konta Atnos nie może legalnie ani technicznie prowadzić kanału.",
            financials=financials,
            forbidden_actions=["upload", "public_publish", "autonomous_card_charge"],
        )

    def _synthetic_operator_decision(
        self,
        *,
        core_safety_ready: bool,
        fully_connected: bool,
        next_action: str,
        financials: dict[str, Any],
    ) -> dict[str, Any]:
        if core_safety_ready:
            return self._operator_decision(
                state="drafts_allowed",
                label="Dozwolone prywatne drafty",
                primary_action=next_action,
                primary_endpoint="/api/creator-cockpit/seed-work-items",
                requires_owner_approval=True,
                safe_to_run_now=True,
                risk_level="medium",
                reason="Projekt może generować wewnętrzne paczki draftów; publikacja i płatności są nadal zablokowane.",
                financials=financials,
                forbidden_actions=["external_publish", "paid_offer", "real_person_likeness", "autonomous_card_charge"]
                + ([] if fully_connected else ["creator_platform_publish"]),
            )
        return self._operator_decision(
            state="needs_creator_policy",
            label="Wymaga polityki twórcy",
            primary_action="Uzupełnij granice treści, disclosure i konto/platformę przed generowaniem draftów.",
            primary_endpoint=None,
            requires_owner_approval=True,
            safe_to_run_now=False,
            risk_level="high",
            reason="Synthetic creator bez polityki i disclosure może łatwo wyprodukować ryzykowny lub niezgodny content.",
            financials=financials,
            forbidden_actions=["external_publish", "paid_offer", "real_person_likeness", "autonomous_card_charge"],
        )

    def _investment_operator_decision(self, *, financials: dict[str, Any]) -> dict[str, Any]:
        return self._operator_decision(
            state="research_paper_only",
            label="Research + paper trading",
            primary_action="Prowadź watchlistę, notatki ryzyka i paper-trades; nie wysyłaj realnych zleceń.",
            primary_endpoint=None,
            requires_owner_approval=False,
            safe_to_run_now=True,
            risk_level="high",
            reason="Projekt inwestycyjny jest celowo ograniczony do edukacji, researchu i symulacji bez brokera.",
            financials=financials,
            forbidden_actions=["real_broker_order", "portfolio_rebalance", "autonomous_card_charge", "leverage_or_options"],
        )

    def _local_operator_decision(
        self,
        *,
        project_type: str,
        next_action: str,
        financials: dict[str, Any],
    ) -> dict[str, Any]:
        if project_type == "investment":
            return self._investment_operator_decision(financials=financials)
        if project_type == "synthetic_creator":
            return self._synthetic_operator_decision(
                core_safety_ready=False,
                fully_connected=False,
                next_action=next_action,
                financials=financials,
            )
        return self._operator_decision(
            state="needs_configuration",
            label="Wymaga konfiguracji",
            primary_action=next_action,
            primary_endpoint=None,
            requires_owner_approval=True,
            safe_to_run_now=False,
            risk_level="medium",
            reason="Projekt nie ma jeszcze minimalnego zestawu dostępu i zasad działania.",
            financials=financials,
            forbidden_actions=["external_action", "autonomous_card_charge"],
        )

    def _money_machine_status(self) -> dict[str, Any]:
        try:
            return MoneyMachineStatusService(self.settings, session=self.session).build(include_oauth=True)
        except Exception as exc:  # pragma: no cover - defensive operator endpoint
            return {
                "status": "unavailable",
                "accounts": [],
                "global_blockers": [f"{type(exc).__name__}: {exc}"],
                "next_actions": ["Fix money-machine status endpoint."],
                "human_approval_before_upload": True,
                "public_upload_enabled": False,
                "autopilot_enabled": False,
            }

    def _account_configs_by_id(self) -> dict[str, dict[str, Any]]:
        try:
            return {str(item.get("account_id") or ""): item for item in read_intelligence_account_configs(self.settings)}
        except Exception:
            return {}

    def _quality_review_snapshot(self, account_id: str) -> dict[str, Any]:
        """Return quality-review backlog for a content account."""
        try:
            with connect(self.settings) as db:
                row = db.execute(
                    """
                    SELECT
                        SUM(CASE WHEN quality_review_status = ? THEN 1 ELSE 0 END) AS blocked_count,
                        SUM(CASE WHEN quality_review_status = 'quality_review_passed' THEN 1 ELSE 0 END) AS passed_count,
                        COUNT(*) AS total_count
                    FROM pipeline_jobs
                    WHERE account_id = ?
                    """,
                    (QUALITY_REVIEW_BLOCKED, account_id),
                ).fetchone()
                blocked_rows = rows_to_dicts(
                    db.execute(
                        """
                        SELECT id, topic_id, status, current_step, quality_review_result, quality_review_checked_at
                        FROM pipeline_jobs
                        WHERE account_id = ?
                          AND quality_review_status = ?
                        ORDER BY COALESCE(quality_review_checked_at, updated_at, created_at, '') DESC
                        LIMIT 5
                        """,
                        (account_id, QUALITY_REVIEW_BLOCKED),
                    ).fetchall()
                )
        except Exception as exc:
            return {
                "status": "unavailable",
                "account_id": account_id,
                "blocked_count": 0,
                "passed_count": 0,
                "total_count": 0,
                "latest_blocked": [],
                "top_blockers": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

        latest_blocked: list[dict[str, Any]] = []
        top_blockers: list[str] = []
        for item in blocked_rows:
            result = json_loads(str(item.get("quality_review_result") or ""), {})
            blockers = [str(blocker) for blocker in result.get("blockers") or [] if str(blocker).strip()]
            for blocker in blockers:
                if blocker not in top_blockers:
                    top_blockers.append(blocker)
            latest_blocked.append(
                {
                    "job_id": str(item.get("id") or ""),
                    "topic_id": str(item.get("topic_id") or ""),
                    "status": str(item.get("status") or ""),
                    "current_step": str(item.get("current_step") or ""),
                    "quality_review_checked_at": item.get("quality_review_checked_at"),
                    "blockers": blockers[:8],
                    "next_action": str(result.get("next_action") or ""),
                }
            )

        counts = dict(row) if row is not None else {}
        return {
            "status": "blocked" if blocked_rows else "clear",
            "account_id": account_id,
            "blocked_count": int(counts.get("blocked_count") or 0),
            "passed_count": int(counts.get("passed_count") or 0),
            "total_count": int(counts.get("total_count") or 0),
            "latest_blocked": latest_blocked,
            "top_blockers": top_blockers[:12],
            "remediation_endpoint": "POST /api/production/quality-remediation/run-once",
            "scheduler_endpoint": "POST /api/production/quality-review/run-once",
        }

    def _load_creator_projects(self) -> list[Any]:
        if self.session is None:
            return []
        try:
            from atnos.repositories.creator_project_repository import CreatorProjectRepository

            return CreatorProjectRepository(self.session).list_projects()
        except Exception:
            return []

    def _configured_social_accounts(self) -> dict[str, list[dict[str, Any]]]:
        if self.session is None:
            return {}
        try:
            from atnos.repositories.account_credential_repository import AccountCredentialRepository

            accounts = AccountCredentialRepository(self.session).list_all()
        except Exception:
            return {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for account in accounts:
            provider_key = str(getattr(account, "provider_key", "") or "").strip().lower()
            platform = self._provider_platform(provider_key)
            if platform is None:
                continue
            grouped.setdefault(platform, []).append(
                {
                    "label": getattr(account, "label", ""),
                    "provider_key": provider_key,
                    "account_identifier": getattr(account, "account_identifier", ""),
                    "status": getattr(account, "status", ""),
                    "has_secret": bool(getattr(account, "has_secret", False)),
                    "allowed_capabilities": list(getattr(account, "allowed_capabilities", []) or []),
                    "linked_integration_key": getattr(account, "linked_integration_key", None),
                }
            )
        return grouped

    @staticmethod
    def _provider_platform(provider_key: str) -> str | None:
        normalized = provider_key.replace("-", "_").replace(".", "_")
        if normalized in {"youtube", "youtube_studio", "google_youtube"}:
            return "youtube"
        if normalized in {"tiktok", "tiktok_browser", "tiktok_creator"}:
            return "tiktok"
        if normalized in {"instagram", "instagram_reels", "instagram_browser", "meta_instagram"}:
            return "instagram"
        return None

    @staticmethod
    def _social_account_ready(account: dict[str, Any]) -> bool:
        capabilities = {str(item) for item in account.get("allowed_capabilities") or []}
        write_capable = bool(
            capabilities
            & {
                "draft_write",
                "confirmed_write_later",
                "browser_login_later",
                "publish_later",
                "browser_workflow",
                "connector_write",
                "oauth_session",
            }
        )
        return str(account.get("status") or "") == "configured" and bool(account.get("has_secret")) and write_capable

    def _youtube_distribution_targets(
        self,
        *,
        canonical: CanonicalProject,
        config: dict[str, Any] | None,
        oauth: dict[str, Any],
        can_upload_private: bool,
        can_publish_public: bool,
    ) -> list[dict[str, Any]]:
        social_accounts = self._configured_social_accounts()
        result: list[dict[str, Any]] = []
        for platform, label in SOCIAL_DISTRIBUTION_TARGETS:
            if platform == "youtube":
                oauth_ready = bool(oauth.get("ready"))
                result.append(
                    {
                        "platform": platform,
                        "label": label,
                        "status": "ready" if oauth_ready else "missing",
                        "connected": oauth_ready,
                        "account_identifier": canonical.project_id,
                        "ready_for_private": can_upload_private,
                        "ready_for_public": can_publish_public,
                        "required_connection": "YouTube Studio OAuth",
                        "next_action": "Review private package." if can_upload_private else "Connect YouTube OAuth and produce QA-passed private render.",
                    }
                )
                continue
            accounts = social_accounts.get(platform, [])
            ready_accounts = [account for account in accounts if self._social_account_ready(account)]
            if ready_accounts:
                first = ready_accounts[0]
                status = "ready"
                connected = True
                next_action = "Use supervised browser workflow for cross-posting."
                account_identifier = str(first.get("account_identifier") or first.get("label") or platform)
            elif accounts:
                first = accounts[0]
                status = "limited"
                connected = False
                next_action = "Finish account secret/session and publish permission setup."
                account_identifier = str(first.get("account_identifier") or first.get("label") or platform)
            else:
                status = "missing"
                connected = False
                next_action = f"Add {label} trusted account in Settings > Konta."
                account_identifier = None
            result.append(
                {
                    "platform": platform,
                    "label": label,
                    "status": status,
                    "connected": connected,
                    "account_identifier": account_identifier,
                    "ready_for_private": bool(config) and connected,
                    "ready_for_public": False,
                    "required_connection": f"{label} trusted account/session",
                    "next_action": next_action,
                }
            )
        return result

    @staticmethod
    def _creator_distribution_targets(*, platforms: list[str], linked_accounts: list[Any]) -> list[dict[str, Any]]:
        linked = bool(linked_accounts)
        targets: list[dict[str, Any]] = []
        for platform in platforms:
            normalized = str(platform).strip()
            if not normalized:
                continue
            targets.append(
                {
                    "platform": normalized,
                    "label": normalized.replace("_", " ").title(),
                    "status": "ready" if linked else "missing",
                    "connected": linked,
                    "account_identifier": None,
                    "ready_for_private": linked,
                    "ready_for_public": False,
                    "required_connection": "Creator platform account/session",
                    "next_action": "Generate supervised draft pack." if linked else "Link creator platform account/session.",
                }
            )
        return targets

    def _youtube_tooling_plan(
        self,
        *,
        oauth: dict[str, Any],
        config: dict[str, Any] | None,
        distribution_targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        research_status, research_detail = self._research_provider_status()
        voice_status, voice_detail = self._premium_voice_status()
        video_status, video_detail = self._video_backend_status()
        render_status = "ready" if shutil.which("ffmpeg") else "limited"
        render_detail = "Local FFmpeg available." if render_status == "ready" else "FFmpeg not detected in PATH; use Rendi or install FFmpeg."
        cross_platform_ready = all(str(item.get("status") or "") == "ready" for item in distribution_targets)
        report = self._latest_tooling_report()
        return {
            **report,
            "must_have": [
                self._tooling_item(
                    "youtube_oauth",
                    "YouTube Studio OAuth",
                    "Official upload, scheduling and analytics access.",
                    "ready" if oauth.get("ready") else "missing",
                    "Connect YouTube Studio OAuth for this channel.",
                    detail=f"OAuth account {oauth.get('account_id')}" if oauth.get("ready") else "No ready OAuth connection.",
                    link="https://studio.youtube.com",
                ),
                self._tooling_item(
                    "research_signals",
                    "Trends + search signals",
                    "Use YouTube/Google/search demand instead of LLM guesses.",
                    research_status,
                    "Configure SERPAPI_KEY or BRAVE_SEARCH_KEY, then run intelligence collection.",
                    detail=research_detail,
                    link="https://trends.google.com",
                ),
                self._tooling_item(
                    "niche_config",
                    "Niche keywords",
                    "Drive collectors, topic scoring and production prompts.",
                    "ready" if config and config.get("niches") else "missing",
                    "Add account niches in Settings > Konta.",
                    detail=", ".join(config.get("niches") or []) if config else "No account config.",
                ),
                self._tooling_item(
                    "premium_voice",
                    "Premium voice",
                    "Public quality gate should reject fallback TTS.",
                    voice_status,
                    "Use OpenAI TTS with billing or a working paid ElevenLabs key/voice.",
                    detail=voice_detail,
                    link="https://openai.com/api/pricing/",
                ),
                self._tooling_item(
                    "visual_backend",
                    "ComfyUI LTX/Wan or image-motion",
                    "Generate visuals tied to each script segment.",
                    video_status,
                    "Use auto/wan/ltx backend and verify ComfyUI or cloud visual budget.",
                    detail=video_detail,
                ),
                self._tooling_item(
                    "render_stack",
                    "FFmpeg / Rendi render stack",
                    "Assemble 1080x1920 video, ASS captions and 44.1 kHz stereo audio.",
                    render_status,
                    "Install FFmpeg or configure Rendi as server-side fallback.",
                    detail=render_detail,
                    link="https://rendi.dev/docs/api-reference/endpoint/run-ffmpeg-command",
                ),
            ],
            "nice_to_have": [
                self._tooling_item("vidiq", "vidIQ", "Competitor tracking and topic feedback after baseline analytics.", "planned", "Use only after 30-50 Shorts."),
                self._tooling_item("submagic", "Submagic", "Caption styling benchmark if ASS captions stay below quality bar.", "planned", "Do not buy before our subtitle renderer is re-tested."),
                self._tooling_item("runway_kling", "Runway/Kling/Pika", "Hero image-to-video shots, not full automated films.", "planned", "Test cost per accepted 9:16 shot first."),
            ],
            "not_now": [
                self._tooling_item("faceless_factory", "End-to-end AI faceless factories", "High risk of repetitive AI slop.", "blocked", "Avoid until channel format is validated."),
                self._tooling_item("public_autopost", "Public autopost without review", "Too risky before retention and QA are proven.", "blocked", "Keep private/supervised flow."),
            ],
            "cross_platform_ready": cross_platform_ready,
        }

    def _synthetic_tooling_plan(
        self,
        *,
        linked_accounts: list[Any],
        platforms: list[str],
        has_policy: bool,
        has_disclosure: bool,
    ) -> dict[str, Any]:
        image_status = "ready" if bool(getattr(self.settings, "openai_api_key", "")) else "limited"
        core_safety_ready = bool(platforms) and has_policy and has_disclosure
        report = self._latest_tooling_report()
        return {
            **report,
            "must_have": [
                self._tooling_item("character_bible", "Character Bible", "Fictional adult persona, style, limits and forbidden lookalikes.", "ready" if has_policy else "missing", "Define content boundaries and persona rules."),
                self._tooling_item(
                    "asset_ledger",
                    "Asset ledger",
                    "Track prompt, model, date, cost, license and disclosure.",
                    "limited" if core_safety_ready else "missing",
                    "Use Creator Cockpit draft ledger before every release package.",
                    detail="Draft ledger available from creator work items." if core_safety_ready else "Create project boundaries and disclosure first.",
                ),
                self._tooling_item("creator_account", "Creator platform account", "Target platform account/session for supervised drafts.", "ready" if linked_accounts else "missing", "Link creator platform account/session."),
                self._tooling_item("synthetic_disclosure", "AI disclosure policy", "Required for realistic synthetic content.", "ready" if has_disclosure else "missing", "Configure disclosure text and platform rules."),
                self._tooling_item("image_video_tools", "Image/video generation tools", "Generate reference images and short clips.", image_status, "Use OpenAI/Picsart/Runway/Kling only with cost limits.", detail="OpenAI key configured." if image_status == "ready" else "No image provider verified in readiness."),
            ],
            "nice_to_have": [
                self._tooling_item("heygen", "HeyGen/Synthesia", "Talking avatar clips for supervised tests.", "planned", "Use only if the creator format needs talking-head video."),
                self._tooling_item("midjourney", "Midjourney/Firefly", "Moodboards and consistent visual references.", "planned", "Manual design support, not core automation."),
            ],
            "not_now": [
                self._tooling_item("faceswap", "Face-swap/deepfake tools", "Risky and not needed for safe fictional personas.", "blocked", "Do not use for real-person likeness."),
                self._tooling_item("paid_offers", "Paid offers/subscriptions", "Blocked until policy, accounts and payment rules exist.", "blocked", "Keep drafts internal."),
            ],
            "platform_count": len(platforms),
        }

    def _investment_tooling_plan(self, cockpit: dict[str, Any] | None = None) -> dict[str, Any]:
        report = self._latest_tooling_report()
        paper_ready = bool((cockpit or {}).get("capabilities", {}).get("can_paper_trade", True))
        watchlist_count = int((cockpit or {}).get("watchlist_count") or 0)
        paper_trade_count = int((cockpit or {}).get("paper_trade_count") or 0)
        broker_access = str((cockpit or {}).get("broker_access") or "not_registered")
        market_data_sources = list((cockpit or {}).get("market_data_sources") or [])
        learning_resources = list((cockpit or {}).get("learning_resources") or [])
        market_data_ready = bool(market_data_sources)
        broker_read_only_ready = broker_access == "read_only_verified"
        return {
            **report,
            "must_have": [
                self._tooling_item("regulator_education", "KNF/SEC/FINRA education sources", "Education and scam-safety baseline.", "ready", "Use as source material for safe investment education."),
                self._tooling_item(
                    "market_data",
                    "Market data provider",
                    "Quotes, filings, macro data and watchlists.",
                    "ready" if market_data_ready else "missing",
                    "Configure NBP/FRED/OpenBB and at least one market data provider.",
                    detail=f"Sources: {', '.join(market_data_sources)}." if market_data_ready else None,
                ),
                self._tooling_item(
                    "watchlist",
                    "Local watchlist",
                    "Store theses, risks and monitored ideas before any trade.",
                    "ready",
                    "Add thesis and risk notes for every symbol.",
                    detail=f"{watchlist_count} watchlist items.",
                ),
                self._tooling_item(
                    "paper_trading",
                    "Paper trading sandbox",
                    "Test automation without real capital.",
                    "ready" if paper_ready else "missing",
                    "Record paper trades only; no broker orders are sent.",
                    detail=f"{paper_trade_count} paper trades recorded.",
                ),
                self._tooling_item(
                    "broker_read_only",
                    "Broker read-only access",
                    "Read holdings/history without trade permissions.",
                    "ready" if broker_read_only_ready else ("limited" if broker_access != "not_registered" else "missing"),
                    "Connect only read-only broker access first.",
                    detail=f"Broker access: {broker_access}.",
                ),
                self._tooling_item(
                    "learning_resources",
                    "Investment courses/resources",
                    "Learning material for the agent before strategy changes.",
                    "ready" if learning_resources else "missing",
                    "Add course URLs, playbooks or source documents for Portfolio Alpha.",
                    detail=f"{len(learning_resources)} resources configured." if learning_resources else None,
                ),
                self._tooling_item("risk_policy", "Risk policy and kill switch", "Limits, forbidden instruments and approval thresholds.", "ready", "Baseline policy blocks real trading and forbids leverage/options for MVP."),
            ],
            "nice_to_have": [
                self._tooling_item("tradingview", "TradingView", "Charts and alert workflow benchmark.", "planned", "Use after watchlist UX is clear."),
                self._tooling_item("koyfin", "Koyfin/OpenBB terminal workflows", "Research dashboard inspiration.", "planned", "Useful after MVP data sources work."),
            ],
            "not_now": [
                self._tooling_item("live_trading", "Live broker auto-trading", "Real money execution.", "blocked", "Blocked until explicit approvals, broker API and risk policy exist."),
                self._tooling_item("leverage_options", "Leverage/options/crypto bots", "High product and compliance risk.", "blocked", "Do not implement for MVP."),
            ],
        }

    def _tooling_plan_for_project_type(self, project_type: str, requirements: list[dict[str, Any]]) -> dict[str, Any]:
        if project_type == "investment":
            return self._investment_tooling_plan()
        if project_type == "synthetic_creator":
            return self._synthetic_tooling_plan(
                linked_accounts=[],
                platforms=[],
                has_policy=any(item.get("id") == "policy" and item.get("status") == "ready" for item in requirements),
                has_disclosure=False,
            )
        return {"must_have": [], "nice_to_have": [], "not_now": []}

    def _latest_tooling_report(self) -> dict[str, str]:
        data_dir = Path(self.settings.data_dir).resolve()
        root = data_dir.parent.resolve()
        candidates: list[Path] = []
        for pattern in (
            "learning/side_research/*.md",
            "learning/tooling-research/*/*.md",
        ):
            candidates.extend(path for path in data_dir.glob(pattern) if path.is_file())
        if not candidates:
            return {
                "report_ref": DEFAULT_TOOLING_REPORT_REF,
                "report_kind": "tooling_research",
                "report_updated_at": "",
            }

        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        try:
            report_ref = latest.relative_to(root).as_posix()
        except ValueError:
            report_ref = str(latest)
        report_kind = "side_research" if "side_research" in latest.parts else "tooling_research"
        updated_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat()
        return {
            "report_ref": report_ref,
            "report_kind": report_kind,
            "report_updated_at": updated_at,
        }

    @staticmethod
    def _tooling_item(
        key: str,
        label: str,
        purpose: str,
        status: str,
        next_action: str,
        *,
        detail: str = "",
        link: str | None = None,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "purpose": purpose,
            "status": status,
            "detail": detail,
            "next_action": next_action,
            "link": link,
        }

    def _research_provider_status(self) -> tuple[str, str]:
        try:
            from atnos.research.config import load_research_config

            config = load_research_config()
        except Exception:
            config = {}
        providers = [
            name
            for name, value in (
                ("SerpAPI", config.get("serpapi_key")),
                ("Brave Search", config.get("brave_search_key")),
            )
            if value
        ]
        if providers:
            return "ready", "Configured: " + ", ".join(providers)
        return "limited", "Google Trends/Wikipedia can run, but paid web/news search keys are not configured."

    def _premium_voice_status(self) -> tuple[str, str]:
        try:
            from atnos.production.voice_provider_status import build_voice_provider_status

            report = build_voice_provider_status(self.settings)
            if bool(report.get("public_voice_ready")):
                return "ready", f"Live premium voice verified via {report.get('selected_tts_backend') or 'provider doctor'}."
            providers = report.get("providers") if isinstance(report.get("providers"), dict) else {}
            configured = [
                name
                for name, provider in providers.items()
                if isinstance(provider, dict) and bool(provider.get("configured"))
            ]
            if configured:
                next_action = str(report.get("next_action") or "run_voice_provider_doctor")
                return "limited", f"Configured providers: {', '.join(configured)}. Live public voice not verified; next action: {next_action}."
        except Exception:
            pass
        backend = str(getattr(self.settings, "shorts_tts_backend", "") or "").strip().lower()
        cloud_allowed = bool(getattr(self.settings, "shorts_allow_cloud_tts", False))
        if backend == "openai_tts" and getattr(self.settings, "openai_api_key", ""):
            return (
                "limited",
                "OpenAI TTS key configured; run voice provider doctor to prove live generation." if cloud_allowed else "OpenAI key exists, but SHORTS_ALLOW_CLOUD_TTS is false.",
            )
        if backend == "elevenlabs" and getattr(self.settings, "elevenlabs_api_key", None):
            return "limited", "ElevenLabs key exists, but live generation must pass before public QA."
        return "missing", f"Current TTS backend is '{backend or 'unknown'}', not a verified premium voice."

    def _video_backend_status(self) -> tuple[str, str]:
        try:
            from atnos.production.visual_backend_status import build_visual_backend_status

            report = build_visual_backend_status(self.settings)
            if bool(report.get("public_visual_ready")):
                return "ready", f"Live visual backend verified via {report.get('effective_video_backend') or 'visual doctor'}."
            providers = report.get("providers") if isinstance(report.get("providers"), dict) else {}
            configured = [
                name
                for name, provider in providers.items()
                if isinstance(provider, dict) and bool(provider.get("configured"))
            ]
            if report.get("checked") or configured:
                next_action = str(report.get("next_action") or "run_visual_backend_doctor")
                effective = str(report.get("effective_video_backend") or report.get("selected_video_backend") or "unknown")
                configured_text = f" Configured providers: {', '.join(configured)}." if configured else ""
                return "limited", f"Effective backend '{effective}' is not verified for public-quality visuals.{configured_text} Next action: {next_action}."
        except Exception:
            pass
        backend = str(getattr(self.settings, "shorts_video_backend", "") or "").strip().lower()
        if backend in {"auto", "wan", "ltx"}:
            return "limited", f"Selected backend '{backend}'. Verify live ComfyUI/Wan/LTX render before public upload."
        if backend == "openai_image_motion":
            return (
                "ready" if getattr(self.settings, "openai_api_key", "") else "missing",
                "Cloud image-motion selected." if getattr(self.settings, "openai_api_key", "") else "OpenAI key missing for image-motion.",
            )
        return "missing", f"Selected backend '{backend or 'unknown'}' is below public visual quality target."

    @staticmethod
    def _connection(
        key: str,
        label: str,
        status: str,
        *,
        where: str,
        notes: str,
    ) -> dict[str, str]:
        return {
            "key": key,
            "label": label,
            "status": status,
            "where": where,
            "notes": notes,
        }

    def _youtube_access_playbook(
        self,
        *,
        config: dict[str, Any] | None,
        oauth: dict[str, Any],
        can_upload_private: bool,
        can_publish_public: bool,
    ) -> dict[str, Any]:
        oauth_ready = bool(oauth.get("ready"))
        config_ready = bool(config)
        niches_ready = bool(config and config.get("niches"))
        return {
            "mode": "supervised_private_upload",
            "required_connections": [
                self._connection(
                    "intelligence_config",
                    "Intelligence account config",
                    "ready" if config_ready else "missing",
                    where="Settings > Konta > Intelligence",
                    notes="Channel account id, niches, region, reference and competitor channels.",
                ),
                self._connection(
                    "youtube_oauth",
                    "YouTube Studio OAuth",
                    "ready" if oauth_ready else "missing",
                    where="Settings > Dostepy > YouTube OAuth",
                    notes="Scopes required for upload, readonly channel checks and analytics.",
                ),
                self._connection(
                    "niche_keywords",
                    "Niche keywords",
                    "ready" if niches_ready else "missing",
                    where="Settings > Konta > Nisze",
                    notes="Used by collectors, topic scoring and production prompts.",
                ),
            ],
            "allowed_now": [
                "Collect market signals and trends.",
                "Generate and score topic candidates.",
                "Produce private QA packages.",
            ]
            + (
                ["Upload QA-passed videos as private after explicit operator approval."]
                if can_upload_private
                else []
            ),
            "blocked_until_configured": [
                *([] if can_publish_public else ["Public publication remains disabled until public upload is explicitly enabled and quality gates pass."]),
                *([] if oauth_ready else ["Private upload requires YouTube OAuth connection."]),
            ],
            "operator_warning": "No public publication runs automatically. Private upload still requires an explicit approved action.",
        }

    @staticmethod
    def _synthetic_access_playbook(
        *,
        linked_accounts: list[Any],
        platforms: list[str],
        has_policy: bool,
        has_disclosure: bool,
    ) -> dict[str, Any]:
        return {
            "mode": "draft_only_until_creator_access",
            "required_connections": [
                ProjectReadinessService._connection(
                    "creator_platform_account",
                    "Creator platform account/session",
                    "ready" if linked_accounts else "missing",
                    where="Settings > Konta > Synthetic creator",
                    notes="Add the platform account or supervised browser session. Do not paste card data into chat.",
                ),
                ProjectReadinessService._connection(
                    "asset_vault",
                    "Asset vault",
                    "ready" if platforms else "missing",
                    where="Settings > Konta > Vault assetow",
                    notes="Folder or storage for reference photos, style guides, generated images and release packs.",
                ),
                ProjectReadinessService._connection(
                    "content_policy",
                    "Content and safety policy",
                    "ready" if has_policy else "missing",
                    where="Settings > Konta > Polityka tresci",
                    notes="Defines allowed content, disclosure, forbidden categories and manual approval rules.",
                ),
                ProjectReadinessService._connection(
                    "synthetic_disclosure",
                    "Synthetic disclosure",
                    "ready" if has_disclosure else "missing",
                    where="Settings > Konta > Disclosure",
                    notes="Required before any external publication for a synthetic persona.",
                ),
            ],
            "allowed_now": [
                "Prepare moodboards, prompts and draft packs.",
                "Generate internal images/videos for manual review.",
                "Build publishing calendars in supervised mode.",
            ],
            "blocked_until_configured": [
                "Public posting to creator platforms.",
                "Paid offers, payment automation and subscriptions.",
                "Any content that violates the configured safety policy.",
            ],
            "operator_warning": "Synthetic creator work stays supervised. Payments and publication are blocked until explicit account links and policy are configured.",
        }

    @staticmethod
    def _investment_access_playbook(cockpit: dict[str, Any] | None = None) -> dict[str, Any]:
        watchlist_count = int((cockpit or {}).get("watchlist_count") or 0)
        paper_trade_count = int((cockpit or {}).get("paper_trade_count") or 0)
        broker_access = str((cockpit or {}).get("broker_access") or "not_registered")
        market_data_sources = list((cockpit or {}).get("market_data_sources") or [])
        learning_resources = list((cockpit or {}).get("learning_resources") or [])
        portfolio_settings = dict((cockpit or {}).get("portfolio_settings") or {})
        payment_ready = bool(portfolio_settings.get("payment_method_configured") and portfolio_settings.get("payment_method_ref"))
        return {
            "mode": "paper_trading_only",
            "required_connections": [
                ProjectReadinessService._connection(
                    "investment_cockpit",
                    "Local investment cockpit",
                    "ready",
                    where="Project Control > Portfolio Alpha",
                    notes=f"Watchlist: {watchlist_count}. Paper trades: {paper_trade_count}. Real orders stay blocked.",
                ),
                ProjectReadinessService._connection(
                    "broker_read_only",
                    "Broker read-only connection",
                    "ready" if broker_access == "read_only_verified" else ("limited" if broker_access != "not_registered" else "missing"),
                    where="Settings > Konta > Portfolio Alpha",
                    notes=f"State: {broker_access}. Read-only holdings and history. Never provide trading credentials in chat.",
                ),
                ProjectReadinessService._connection(
                    "market_data",
                    "Market data provider",
                    "ready" if market_data_sources else "missing",
                    where="Settings > API i providerzy",
                    notes=f"Sources: {', '.join(market_data_sources)}." if market_data_sources else "Connect market data/news provider for quotes, calendars and research.",
                ),
                ProjectReadinessService._connection(
                    "learning_resources",
                    "Investment learning resources",
                    "ready" if learning_resources else "missing",
                    where="Settings > Konta > Portfolio Alpha",
                    notes=f"{len(learning_resources)} resources configured." if learning_resources else "Add course URLs or source documents for the investment agent.",
                ),
                ProjectReadinessService._connection(
                    "payment_reference",
                    "Payment/budget reference",
                    "ready" if payment_ready else "missing",
                    where="Settings > Konta > Portfolio Alpha",
                    notes="Use a vault or provider reference. Never paste card number/CVV into settings or chat.",
                ),
                ProjectReadinessService._connection(
                    "risk_policy",
                    "Risk policy",
                    "ready",
                    where="Settings > Portfolio Alpha > Ryzyko",
                    notes="Baseline policy blocks real orders and keeps Portfolio Alpha in paper/read-only mode.",
                ),
                ProjectReadinessService._connection(
                    "trade_approval",
                    "Trade approval gate",
                    "ready",
                    where="Settings > Portfolio Alpha > Zatwierdzanie",
                    notes="Trading execution is disabled unless you later configure a separate approval flow.",
                ),
            ],
            "allowed_now": [
                "Research public companies and ETFs.",
                "Prepare watchlists and risk notes.",
                "Record paper-only trades for strategy tests.",
                "Generate alerts and summaries.",
            ],
            "blocked_until_configured": [
                "Any buy/sell order.",
                "Any autonomous broker action.",
                "Any portfolio rebalance without explicit approval rules.",
            ],
            "operator_warning": "Atnos can analyze investments and run paper trades. Real trading requires separate explicit approvals and broker integration.",
        }

    @staticmethod
    def _local_access_playbook(project_type: str, requirements: list[dict[str, Any]]) -> dict[str, Any]:
        if project_type == "investment":
            return ProjectReadinessService._investment_access_playbook()
        if project_type == "synthetic_creator":
            return ProjectReadinessService._synthetic_access_playbook(
                linked_accounts=[],
                platforms=[],
                has_policy=False,
                has_disclosure=False,
            )
        return {
            "mode": "manual_setup",
            "required_connections": [
                ProjectReadinessService._connection(
                    str(item.get("id") or "requirement"),
                    str(item.get("label") or "Requirement"),
                    str(item.get("status") or "missing"),
                    where="Settings",
                    notes=str(item.get("detail") or ""),
                )
                for item in requirements
            ],
            "allowed_now": ["Manual planning."],
            "blocked_until_configured": ["Automation until required access is configured."],
            "operator_warning": "Finish configuration before automation.",
        }

    @staticmethod
    def _requirement(
        requirement_id: str,
        label: str,
        ready: bool,
        *,
        detail: str = "",
        missing_detail: str = "",
        limited_if_missing: bool = False,
    ) -> dict[str, Any]:
        if ready:
            return {"id": requirement_id, "label": label, "status": "ready", "detail": detail or "Ready."}
        return {
            "id": requirement_id,
            "label": label,
            "status": "limited" if limited_if_missing else "missing",
            "detail": missing_detail or detail or "Missing.",
        }

    @staticmethod
    def _youtube_next_action(
        *,
        can_research: bool,
        can_generate_private: bool,
        can_upload_private: bool,
        ready_private_count: int,
        money_next_action: str,
    ) -> str:
        if can_upload_private:
            return f"Review and approve private upload package ({ready_private_count} ready)."
        if money_next_action:
            return money_next_action
        if can_generate_private:
            return "Generate or repair QA-passed private Shorts."
        if can_research:
            return "Generate scored topics and start supervised production."
        return "Complete account config, niches and YouTube OAuth."

    @staticmethod
    def _summary(projects: list[dict[str, Any]], money_machine: dict[str, Any]) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for project in projects:
            status = str(project.get("status") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
        return {
            "total": len(projects),
            "ready": by_status.get("ready", 0),
            "limited": by_status.get("limited", 0),
            "blocked": by_status.get("blocked", 0),
            "safe_mode": sum(1 for project in projects if bool(project.get("safe_mode"))),
            "youtube_private_upload_ready": sum(
                1 for project in projects if bool((project.get("capabilities") or {}).get("can_upload_private"))
            ),
            "cross_platform_ready": sum(
                1
                for project in projects
                if project.get("project_type") == "youtube"
                and project.get("distribution_targets")
                and all(str(item.get("status") or "") == "ready" for item in project.get("distribution_targets") or [])
            ),
            "youtube_quality_review_blocked": sum(
                int(((project.get("evidence") or {}).get("quality_review") or {}).get("blocked_count") or 0)
                for project in projects
                if project.get("project_type") == "youtube"
            ),
            "public_publish_enabled": bool(money_machine.get("public_upload_enabled")),
        }
