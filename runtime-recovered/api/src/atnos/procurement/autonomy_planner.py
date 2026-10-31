from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import read_intelligence_account_configs, utc_now_iso
from atnos.production.oauth_resolver import resolve_gmail_oauth_account_id, resolve_youtube_oauth_account_id
from atnos.procurement.service import ProcurementService, TOOL_CATALOG
from atnos.procurement.smart_spending import SmartSpendingService
from atnos.research.config import load_research_config

NeedStatus = Literal["ready", "missing", "manual_required", "blocked"]
NeedAction = Literal["procurement_request", "oauth_grant", "secret_config", "local_runtime"]


@dataclass(frozen=True, slots=True)
class AutonomyNeed:
    """Jedna konkretna potrzeba Atnosa, wykryta z konfiguracji runtime."""

    id: str
    capability: str
    title: str
    reason: str
    priority: int
    status: NeedStatus
    action_type: NeedAction
    provider_id: str | None = None
    estimated_monthly_cost_pln: float = 0.0
    checkout_url: str | None = None
    config_hint: str | None = None
    account_id: str | None = None
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["card_data_storage"] = "forbidden"
        payload["autopurchase_allowed"] = False
        payload["requires_human_checkout"] = self.action_type == "procurement_request" and self.status != "ready"
        return payload


class AutonomyPlanner:
    """Buduje bezpieczny plan: czego Atnos potrzebuje, co kupic i co podpiac."""

    def __init__(
        self,
        settings: Settings,
        *,
        research_config: dict[str, Any] | None = None,
        session: Session | None = None,
    ) -> None:
        self.settings = settings
        self.procurement = ProcurementService(settings)
        self.research_config = research_config if research_config is not None else load_research_config()
        self.session = session

    def build_plan(self, *, account_id: str | None = None, include_optional: bool = True) -> dict[str, Any]:
        accounts = read_intelligence_account_configs(self.settings)
        account = _find_account(accounts, account_id)
        needs = self._needs(account_id=account_id, account=account, include_optional=include_optional)
        purchase_candidates = [need for need in needs if need.action_type == "procurement_request" and need.status != "ready"]
        access_tasks = [need for need in needs if need.action_type != "procurement_request" and need.status != "ready"]
        total_estimate = round(sum(need.estimated_monthly_cost_pln for need in purchase_candidates), 4)
        policy = self.procurement.policy()
        financial_awareness = SmartSpendingService(self.settings).awareness()
        existing_requests = self.procurement.list_requests(limit=200)
        existing_by_provider = _active_requests_by_provider(existing_requests)
        return {
            "generated_at": utc_now_iso(),
            "mode": "operator_approved_autonomy",
            "summary": {
                "ready": not purchase_candidates and not access_tasks,
                "missing_count": len(purchase_candidates) + len(access_tasks),
                "purchase_candidate_count": len(purchase_candidates),
                "access_task_count": len(access_tasks),
                "estimated_new_monthly_cost_pln": total_estimate,
                "autopurchase_enabled": False,
                "card_data_storage": "forbidden",
            },
            "policy": {
                **policy,
                "autonomy_rule": "Atnos may diagnose, prepare requests and verify access, but payment/OAuth consent stays manual.",
            },
            "financial_awareness": financial_awareness,
            "account": account,
            "needs": [need.to_dict() for need in needs],
            "purchase_candidates": [
                {
                    **need.to_dict(),
                    "existing_request": existing_by_provider.get(str(need.provider_id or "")),
                }
                for need in purchase_candidates
            ],
            "access_tasks": [need.to_dict() for need in access_tasks],
            "blocked_actions": [
                {
                    "action": "store_card_or_bank_credentials",
                    "reason": "Card number, CVV, PIN, SMS codes and bank credentials must never be stored in Atnos.",
                },
                {
                    "action": "fully_autonomous_purchase",
                    "reason": "External billing changes require manual checkout and audit trail.",
                },
                {
                    "action": "real_money_investment_trade",
                    "reason": "Trading needs broker integration, risk policy and explicit per-action approval.",
                },
            ],
            "next_actions": self._next_actions(purchase_candidates, access_tasks),
        }

    def propose_missing_purchases(
        self,
        *,
        account_id: str | None = None,
        include_optional: bool = True,
        max_requests: int = 5,
    ) -> dict[str, Any]:
        plan = self.build_plan(account_id=account_id, include_optional=include_optional)
        existing_by_provider = _active_requests_by_provider(self.procurement.list_requests(limit=200))
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        limit = max(1, min(int(max_requests), 20))
        for item in plan["purchase_candidates"]:
            provider_id = str(item.get("provider_id") or "")
            if not provider_id:
                continue
            existing = existing_by_provider.get(provider_id)
            if existing:
                skipped.append(
                    {
                        "provider_id": provider_id,
                        "reason": "active_request_exists",
                        "existing_request": existing,
                    }
                )
                continue
            if len(created) >= limit:
                skipped.append({"provider_id": provider_id, "reason": "max_requests_reached"})
                continue
            created.append(
                self.procurement.propose(
                    provider_id=provider_id,
                    reason=str(item.get("reason") or item.get("title") or "Autonomy planner request."),
                    account_id=account_id,
                    estimated_monthly_cost_pln=float(item.get("estimated_monthly_cost_pln") or 0.0),
                    metadata={
                        "source": "autonomy_planner",
                        "need_id": item.get("id"),
                        "capability": item.get("capability"),
                        "priority": item.get("priority"),
                    },
                )
            )
        return {
            "generated_at": utc_now_iso(),
            "mode": "operator_approved_autonomy",
            "created": created,
            "skipped": skipped,
            "policy": plan["policy"],
            "next_actions": plan["next_actions"],
        }

    def _needs(
        self,
        *,
        account_id: str | None,
        account: dict[str, Any] | None,
        include_optional: bool,
    ) -> list[AutonomyNeed]:
        needs: list[AutonomyNeed] = []
        needs.extend(self._voice_needs(account_id=account_id))
        needs.extend(self._research_needs(account_id=account_id, include_optional=include_optional))
        needs.extend(self._youtube_needs(account_id=account_id, account=account))
        needs.extend(self._visual_needs(account_id=account_id, include_optional=include_optional))
        needs.extend(self._notification_needs(account_id=account_id, account=account))
        return sorted(needs, key=lambda item: (item.status == "ready", item.priority, item.id))

    def _voice_needs(self, *, account_id: str | None) -> list[AutonomyNeed]:
        openai_key = bool(getattr(self.settings, "openai_api_key", ""))
        eleven_key = bool(getattr(self.settings, "elevenlabs_api_key", None))
        allow_cloud_tts = bool(getattr(self.settings, "shorts_allow_cloud_tts", False))
        if openai_key and allow_cloud_tts:
            return [self._ready("voice.openai", "premium_voice", "Premium voice provider configured", {"openai_api_key": True})]

        spec = TOOL_CATALOG["openai_api"]
        needs = [
            AutonomyNeed(
                id="voice.openai_api",
                capability="premium_voice",
                title="OpenAI API billing/key for public-quality English narration",
                reason="Public Shorts should not pass with fallback robotic voice. OpenAI API can cover premium TTS and emergency LLM/image fallback.",
                priority=10,
                status="missing" if not openai_key else "manual_required",
                action_type="procurement_request" if not openai_key else "secret_config",
                provider_id="openai_api" if not openai_key else None,
                estimated_monthly_cost_pln=spec.estimated_monthly_cost_pln if not openai_key else 0.0,
                checkout_url=spec.checkout_url if not openai_key else None,
                config_hint=spec.config_hint if not openai_key else "Set SHORTS_ALLOW_CLOUD_TTS=true after verifying billing.",
                account_id=account_id,
                evidence={"openai_api_key": openai_key, "shorts_allow_cloud_tts": allow_cloud_tts},
            )
        ]
        if not eleven_key:
            spec = TOOL_CATALOG["elevenlabs"]
            needs.append(
                AutonomyNeed(
                    id="voice.elevenlabs",
                    capability="premium_voice_backup",
                    title="ElevenLabs as backup narration provider",
                    reason="A second premium voice provider reduces production failures when one provider is quota-blocked.",
                    priority=30,
                    status="missing",
                    action_type="procurement_request",
                    provider_id="elevenlabs",
                    estimated_monthly_cost_pln=spec.estimated_monthly_cost_pln,
                    checkout_url=spec.checkout_url,
                    config_hint=spec.config_hint,
                    account_id=account_id,
                    evidence={"elevenlabs_api_key": False},
                )
            )
        return needs

    def _research_needs(self, *, account_id: str | None, include_optional: bool) -> list[AutonomyNeed]:
        serp = bool(self.research_config.get("serpapi_key"))
        brave = bool(self.research_config.get("brave_search_key"))
        if serp or brave:
            return [self._ready("research.search_api", "trend_research", "External search API configured", {"serpapi_key": serp, "brave_search_key": brave})]
        providers = ["brave_search", "serpapi"] if include_optional else ["brave_search"]
        return [
            AutonomyNeed(
                id=f"research.{provider_id}",
                capability="trend_research",
                title=f"{TOOL_CATALOG[provider_id].display_name} for external trend/search signals",
                reason="Topic generation should be based on live search demand and competitor signals, not only local guesses.",
                priority=20 if provider_id == "brave_search" else 35,
                status="missing",
                action_type="procurement_request",
                provider_id=provider_id,
                estimated_monthly_cost_pln=TOOL_CATALOG[provider_id].estimated_monthly_cost_pln,
                checkout_url=TOOL_CATALOG[provider_id].checkout_url,
                config_hint=TOOL_CATALOG[provider_id].config_hint,
                account_id=account_id,
                evidence={"serpapi_key": serp, "brave_search_key": brave},
            )
            for provider_id in providers
        ]

    def _youtube_needs(self, *, account_id: str | None, account: dict[str, Any] | None) -> list[AutonomyNeed]:
        has_data_key = bool(getattr(self.settings, "youtube_data_api_key", ""))
        resolved_oauth_account_id = (
            resolve_youtube_oauth_account_id(self.settings, str(account_id or ""), session=self.session)
            if account_id and self.session is not None
            else None
        )
        configured_oauth_account_id = account.get("youtube_oauth_account_id") if account else None
        has_oauth_account = bool(configured_oauth_account_id or resolved_oauth_account_id)
        if has_data_key or has_oauth_account:
            return [
                self._ready(
                    "youtube.api_access",
                    "youtube_ops",
                    "YouTube API/OAuth access configured",
                    {
                        "youtube_data_api_key": has_data_key,
                        "youtube_oauth_account_id": configured_oauth_account_id,
                        "resolved_youtube_oauth_account_id": resolved_oauth_account_id,
                    },
                )
            ]
        spec = TOOL_CATALOG["google_cloud"]
        return [
            AutonomyNeed(
                id="youtube.google_cloud_oauth",
                capability="youtube_ops",
                title="Google Cloud YouTube APIs + OAuth consent",
                reason="Atnos needs YouTube Data API, upload scope and analytics scope for private uploads and performance feedback.",
                priority=15,
                status="manual_required",
                action_type="oauth_grant",
                provider_id="google_cloud",
                estimated_monthly_cost_pln=0.0,
                checkout_url=spec.checkout_url,
                config_hint=spec.config_hint,
                account_id=account_id,
                evidence={
                    "youtube_data_api_key": has_data_key,
                    "youtube_oauth_account_id": configured_oauth_account_id,
                    "resolved_youtube_oauth_account_id": resolved_oauth_account_id,
                },
            )
        ]

    def _visual_needs(self, *, account_id: str | None, include_optional: bool) -> list[AutonomyNeed]:
        backend = str(getattr(self.settings, "shorts_video_backend", "") or "").lower()
        if backend in {"auto", "wan", "ltx", "openai_image_motion"}:
            return [self._ready("visual.backend_selected", "video_generation", "Public-quality visual backend selected", {"shorts_video_backend": backend})]
        needs = [
            AutonomyNeed(
                id="visual.local_comfyui",
                capability="video_generation",
                title="Switch Shorts video backend to auto/WAN/LTX and verify ComfyUI",
                reason="Motion-graphic fallback is not enough for public-quality Shorts.",
                priority=12,
                status="manual_required",
                action_type="local_runtime",
                account_id=account_id,
                evidence={"shorts_video_backend": backend or "unset"},
                config_hint="Set SHORTS_VIDEO_BACKEND=auto and run visual backend doctor/smoke render.",
            )
        ]
        if include_optional:
            spec = TOOL_CATALOG["rendi"]
            needs.append(
                AutonomyNeed(
                    id="visual.rendi_cloud",
                    capability="video_generation_backup",
                    title="Rendi cloud rendering/video experiments",
                    reason="Optional fallback when local ComfyUI generation is too slow or unstable.",
                    priority=45,
                    status="missing",
                    action_type="procurement_request",
                    provider_id="rendi",
                    estimated_monthly_cost_pln=spec.estimated_monthly_cost_pln,
                    checkout_url=spec.checkout_url,
                    config_hint=spec.config_hint,
                    account_id=account_id,
                    evidence={"optional": True},
                )
            )
        return needs

    def _notification_needs(self, *, account_id: str | None, account: dict[str, Any] | None) -> list[AutonomyNeed]:
        resolved_gmail_account_id = (
            resolve_gmail_oauth_account_id(self.settings, str(account_id or ""), session=self.session)
            if account_id and self.session is not None
            else None
        )
        configured_gmail_account_id = account.get("gmail_oauth_account_id") if account else None
        has_gmail = bool(configured_gmail_account_id or resolved_gmail_account_id)
        if has_gmail:
            return [
                self._ready(
                    "notifications.gmail",
                    "owner_notifications",
                    "Gmail notification access configured",
                    {
                        "gmail_oauth_account_id": configured_gmail_account_id,
                        "resolved_gmail_oauth_account_id": resolved_gmail_account_id,
                    },
                )
            ]
        return [
            AutonomyNeed(
                id="notifications.gmail_oauth",
                capability="owner_notifications",
                title="Gmail OAuth for reports and approval questions",
                reason="Atnos should send learning reports, QA failures and access tasks to the owner without blocking the UI.",
                priority=25,
                status="manual_required",
                action_type="oauth_grant",
                account_id=account_id,
                config_hint="Grant Gmail send/modify scope through OAuth; do not share mailbox password.",
                evidence={
                    "gmail_oauth_account_id": configured_gmail_account_id,
                    "resolved_gmail_oauth_account_id": resolved_gmail_account_id,
                },
            )
        ]

    @staticmethod
    def _ready(need_id: str, capability: str, title: str, evidence: dict[str, Any]) -> AutonomyNeed:
        return AutonomyNeed(
            id=need_id,
            capability=capability,
            title=title,
            reason="Already configured.",
            priority=100,
            status="ready",
            action_type="secret_config",
            evidence=evidence,
        )

    @staticmethod
    def _next_actions(purchase_candidates: list[AutonomyNeed], access_tasks: list[AutonomyNeed]) -> list[str]:
        actions: list[str] = []
        if purchase_candidates:
            first = purchase_candidates[0]
            actions.append(f"Create procurement request for {first.provider_id}: {first.title}.")
        if access_tasks:
            first = access_tasks[0]
            actions.append(f"Complete access task: {first.title}.")
        if not actions:
            actions.append("All required autonomy inputs are configured; run a private QA render.")
        actions.append("Never paste card number, CVV, PIN, SMS code or bank credentials into Atnos.")
        return actions


def _find_account(accounts: list[dict[str, Any]], account_id: str | None) -> dict[str, Any] | None:
    if not account_id:
        return accounts[0] if accounts else None
    for account in accounts:
        if str(account.get("account_id") or "") == account_id:
            return account
    return None


def _active_requests_by_provider(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    active = {"pending_approval", "manual_checkout_required", "access_ready", "blocked_by_budget", "blocked_by_smart_spending"}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider_id = str(row.get("provider_id") or "")
        if provider_id and str(row.get("status") or "") in active and provider_id not in result:
            result[provider_id] = row
    return result
