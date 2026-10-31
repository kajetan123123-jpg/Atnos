from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, rows_to_dicts, utc_now_iso
from atnos.production.cost_guard import CostGuard
from atnos.production.expense_notifier import ExpenseNotifier
from atnos.procurement.smart_spending import SmartSpendingService

ProcurementStatus = Literal[
    "pending_approval",
    "blocked_by_budget",
    "blocked_by_smart_spending",
    "manual_checkout_required",
    "access_ready",
    "cancelled",
]


class ProcurementBlockedError(RuntimeError):
    """Twarda blokada bezpiecznego systemu zakupowego."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(str(payload.get("message") or "Procurement request blocked."))


@dataclass(frozen=True, slots=True)
class ToolSubscriptionSpec:
    provider_id: str
    display_name: str
    category: str
    purpose: str
    estimated_monthly_cost_pln: float
    checkout_url: str
    config_hint: str
    risk_level: Literal["low", "medium", "high"]
    autopurchase_allowed: bool = False


TOOL_CATALOG: dict[str, ToolSubscriptionSpec] = {
    "openai_api": ToolSubscriptionSpec(
        provider_id="openai_api",
        display_name="OpenAI API",
        category="llm_tts_image",
        purpose="Premium TTS, image generation, script/QA fallback.",
        estimated_monthly_cost_pln=80.0,
        checkout_url="https://platform.openai.com/settings/organization/billing/overview",
        config_hint="After billing is active, store OPENAI_API_KEY in the local Atnos runtime secret store.",
        risk_level="medium",
    ),
    "elevenlabs": ToolSubscriptionSpec(
        provider_id="elevenlabs",
        display_name="ElevenLabs",
        category="voice",
        purpose="Premium English narration voices for Shorts.",
        estimated_monthly_cost_pln=50.0,
        checkout_url="https://elevenlabs.io/app/subscription",
        config_hint="After subscription/API access is active, store ELEVENLABS_API_KEY and voice IDs.",
        risk_level="medium",
    ),
    "serpapi": ToolSubscriptionSpec(
        provider_id="serpapi",
        display_name="SerpAPI",
        category="research",
        purpose="Google Search/Trends-like signal collection for topic research.",
        estimated_monthly_cost_pln=60.0,
        checkout_url="https://serpapi.com/dashboard",
        config_hint="Store SERPAPI_KEY after plan activation.",
        risk_level="low",
    ),
    "brave_search": ToolSubscriptionSpec(
        provider_id="brave_search",
        display_name="Brave Search API",
        category="research",
        purpose="Fallback search provider for intelligence collectors.",
        estimated_monthly_cost_pln=25.0,
        checkout_url="https://api.search.brave.com/app/subscriptions",
        config_hint="Store BRAVE_SEARCH_KEY after subscription activation.",
        risk_level="low",
    ),
    "rendi": ToolSubscriptionSpec(
        provider_id="rendi",
        display_name="Rendi",
        category="video_generation",
        purpose="Cloud video generation/rendering experiments when local ComfyUI is insufficient.",
        estimated_monthly_cost_pln=80.0,
        checkout_url="https://www.rendi.dev/",
        config_hint="Store RENDI_API_KEY or MCP config after account activation.",
        risk_level="medium",
    ),
    "google_cloud": ToolSubscriptionSpec(
        provider_id="google_cloud",
        display_name="Google Cloud / YouTube APIs",
        category="youtube_ops",
        purpose="YouTube upload, analytics, OAuth, channel operations.",
        estimated_monthly_cost_pln=0.0,
        checkout_url="https://console.cloud.google.com/apis/dashboard",
        config_hint="Enable APIs and OAuth client; do not enter card data into Atnos.",
        risk_level="medium",
    ),
}


class ProcurementService:
    """Zarządza zakupami narzędzi bez przechowywania danych karty w Atnos."""

    TABLE = "procurement_requests"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def catalog(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in TOOL_CATALOG.values()]

    def status(self) -> dict[str, Any]:
        self._ensure_schema()
        rows = self._list_rows(limit=50)
        return {
            "policy": self.policy(),
            "catalog": self.catalog(),
            "requests": rows,
        }

    def policy(self) -> dict[str, Any]:
        cost = CostGuard(self.settings).snapshot()
        return {
            "card_data_storage": "forbidden",
            "card_data_instruction": "Never paste card number, CVV, PIN, SMS codes or bank credentials into Atnos.",
            "purchase_execution": "manual_checkout_required",
            "autopurchase_enabled": False,
            "approval_required": bool(getattr(self.settings, "atnos_api_billing_approval", True)),
            "monthly_limit_pln": cost.get("monthly_limit_pln"),
            "monthly_remaining_pln": cost.get("monthly_remaining_pln"),
            "daily_limit_pln": cost.get("daily_limit_pln"),
            "daily_remaining_pln": cost.get("daily_remaining_pln"),
            "ledger_path": cost.get("ledger_path"),
        }

    def propose(
        self,
        *,
        provider_id: str,
        reason: str,
        account_id: str | None = None,
        estimated_monthly_cost_pln: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_schema()
        self._reject_payment_secret_text(reason)
        spec = TOOL_CATALOG.get(provider_id)
        if spec is None:
            raise ProcurementBlockedError(
                {
                    "status": "unknown_provider",
                    "message": f"Unknown procurement provider: {provider_id}",
                    "known_provider_ids": sorted(TOOL_CATALOG),
                }
            )
        estimate = round(float(estimated_monthly_cost_pln if estimated_monthly_cost_pln is not None else spec.estimated_monthly_cost_pln), 4)
        budget = CostGuard(self.settings).check_can_spend(
            estimate,
            account_id=account_id,
            operation=f"procurement:{provider_id}",
        )
        smart = dict(budget.get("smart_spending") or {})
        now = utc_now_iso()
        request_id = f"proc-{uuid4().hex[:12]}"
        blockers = {str(item) for item in budget.get("blockers") or []}
        if budget["allowed"]:
            status: ProcurementStatus = "pending_approval"
        elif "smart_spending_balance_exceeded" in blockers and not any("budget" in item for item in blockers):
            status = "blocked_by_smart_spending"
        else:
            status = "blocked_by_budget"
        payload = {
            "id": request_id,
            "created_at": now,
            "updated_at": now,
            "provider_id": provider_id,
            "provider_name": spec.display_name,
            "category": spec.category,
            "account_id": account_id,
            "reason": reason.strip(),
            "estimated_monthly_cost_pln": estimate,
            "status": status,
            "approval_required": bool(getattr(self.settings, "atnos_api_billing_approval", True)),
            "checkout_url": spec.checkout_url,
            "config_hint": spec.config_hint,
            "card_data_storage": "forbidden",
            "autopurchase_allowed": False,
            "budget_decision": budget,
            "smart_spending_decision": smart,
            "metadata": metadata or {},
        }
        with connect(self.settings) as db:
            db.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    id, created_at, updated_at, provider_id, provider_name, category,
                    account_id, reason, estimated_monthly_cost_pln, status,
                    approval_required, checkout_url, config_hint, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    now,
                    now,
                    provider_id,
                    spec.display_name,
                    spec.category,
                    account_id,
                    reason.strip(),
                    estimate,
                    status,
                    1 if payload["approval_required"] else 0,
                    spec.checkout_url,
                    spec.config_hint,
                    json.dumps(payload["metadata"], ensure_ascii=False, separators=(",", ":")),
                ),
            )
            db.commit()
        if status == "pending_approval" and estimate > 0:
            SmartSpendingService(self.settings).reserve_expense(
                provider=provider_id,
                operation=f"procurement:{provider_id}",
                amount_pln=estimate,
                reason=reason.strip(),
                account_id=account_id,
                source_id=request_id,
                metadata={
                    "source": "procurement_request",
                    "provider_name": spec.display_name,
                    "request_status": status,
                },
            )
        ExpenseNotifier(self.settings).enqueue(
            event_type="procurement_request",
            provider=provider_id,
            operation=f"procurement:{provider_id}",
            amount_pln=estimate,
            reason=reason.strip(),
            account_id=account_id,
            source_id=request_id,
            metadata={
                "request_status": status,
                "provider_name": spec.display_name,
                "checkout_url": spec.checkout_url,
                "budget_allowed": bool(budget.get("allowed")),
            },
        )
        return payload

    def approve_for_manual_checkout(self, request_id: str) -> dict[str, Any]:
        self._ensure_schema()
        row = self._get_row(request_id)
        if row is None:
            raise ProcurementBlockedError({"status": "not_found", "message": f"Procurement request {request_id} not found."})
        if row["status"] in {"blocked_by_budget", "blocked_by_smart_spending"}:
            raise ProcurementBlockedError(
                {
                    "status": row["status"],
                    "message": "This request is blocked and cannot be approved.",
                    "request": dict(row),
                }
            )
        now = utc_now_iso()
        with connect(self.settings) as db:
            db.execute(
                f"UPDATE {self.TABLE} SET status = ?, updated_at = ? WHERE id = ?",
                ("manual_checkout_required", now, request_id),
            )
            db.commit()
        payload = self._row_payload(self._get_row(request_id))
        payload["message"] = "Manual checkout required. Atnos must not receive or store card data."
        return payload

    def mark_access_ready(self, request_id: str, *, access_note: str = "") -> dict[str, Any]:
        self._ensure_schema()
        row = self._get_row(request_id)
        if row is None:
            raise ProcurementBlockedError({"status": "not_found", "message": f"Procurement request {request_id} not found."})
        self._reject_payment_secret_text(access_note)
        metadata = self._metadata(row)
        metadata["access_note"] = access_note.strip()
        metadata["access_ready_at"] = utc_now_iso()
        with connect(self.settings) as db:
            db.execute(
                f"UPDATE {self.TABLE} SET status = ?, updated_at = ?, metadata_json = ? WHERE id = ?",
                ("access_ready", utc_now_iso(), json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), request_id),
            )
            db.commit()
        return self._row_payload(self._get_row(request_id))

    def list_requests(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_schema()
        return self._list_rows(status=status, limit=limit)

    def _list_rows(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with connect(self.settings) as db:
            if status:
                rows = db.execute(
                    f"SELECT * FROM {self.TABLE} WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = db.execute(f"SELECT * FROM {self.TABLE} ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_payload(row) for row in rows]

    def _get_row(self, request_id: str):
        with connect(self.settings) as db:
            return db.execute(f"SELECT * FROM {self.TABLE} WHERE id = ?", (request_id,)).fetchone()

    def _row_payload(self, row) -> dict[str, Any]:
        if row is None:
            return {}
        payload = dict(row)
        payload["approval_required"] = bool(payload.get("approval_required"))
        payload["metadata"] = self._metadata(row)
        payload.pop("metadata_json", None)
        payload["card_data_storage"] = "forbidden"
        return payload

    @staticmethod
    def _metadata(row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["metadata_json"] or "{}"))
        except (json.JSONDecodeError, KeyError, TypeError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _ensure_schema(self) -> None:
        with connect(self.settings) as db:
            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    account_id TEXT,
                    reason TEXT NOT NULL,
                    estimated_monthly_cost_pln REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    approval_required INTEGER NOT NULL DEFAULT 1,
                    checkout_url TEXT NOT NULL,
                    config_hint TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_status_created ON {self.TABLE} (status, created_at)"
            )
            db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_provider ON {self.TABLE} (provider_id)"
            )
            db.commit()

    @staticmethod
    def _reject_payment_secret_text(value: str) -> None:
        text = str(value or "")
        normalized = " ".join(text.split())
        card_like = re.search(r"\b(?:\d[ -]?){13,19}\b", normalized)
        cvv_like = re.search(r"\b(?:cvv|cvc|kod\s*cvv|security\s*code)\b", normalized, re.IGNORECASE)
        if card_like or cvv_like:
            raise ProcurementBlockedError(
                {
                    "status": "payment_secret_rejected",
                    "message": "Do not paste card numbers, CVV/CVC, PINs, SMS codes or bank credentials into Atnos.",
                }
            )
