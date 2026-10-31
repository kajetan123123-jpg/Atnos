from __future__ import annotations

import json
import re
from typing import Any, Literal
from uuid import uuid4

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, utc_now_iso

LedgerEntryType = Literal[
    "seed_capital",
    "revenue",
    "expense_reserved",
    "expense_actual",
    "expense_released",
]

DEFAULT_OPERATING_BALANCE_PLN = 50.0


class SmartSpendingService:
    """Kasa operacyjna Atnosa: wydawaj tylko z salda, najpierw zarabiaj potem skaluj."""

    ACCOUNT_TABLE = "smart_spending_accounts"
    LEDGER_TABLE = "smart_spending_ledger"
    DEFAULT_ACCOUNT_ID = "default"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        """Zwroc aktualny stan salda, polityki i ostatnich wpisow."""
        self._ensure_schema()
        snapshot = self.snapshot()
        snapshot["recent_entries"] = self.list_entries(limit=25)
        return snapshot

    def snapshot(self) -> dict[str, Any]:
        """Zwroc biezace saldo operacyjne bez efektow ubocznych poza inicjalizacja seed."""
        self._ensure_schema()
        with connect(self.settings) as db:
            self._ensure_default_account(db)
            rows = db.execute(
                f"""
                SELECT entry_type, COALESCE(SUM(amount_pln), 0) AS total
                FROM {self.LEDGER_TABLE}
                WHERE spending_account_id = ?
                GROUP BY entry_type
                """,
                (self.DEFAULT_ACCOUNT_ID,),
            ).fetchall()
        totals = {str(row["entry_type"]): float(row["total"] or 0.0) for row in rows}
        seed = totals.get("seed_capital", 0.0)
        revenue = totals.get("revenue", 0.0)
        reserved = totals.get("expense_reserved", 0.0)
        actual = totals.get("expense_actual", 0.0)
        released = totals.get("expense_released", 0.0)
        committed = max(0.0, reserved + actual - released)
        available = max(0.0, seed + revenue - committed)
        net_profit = revenue - actual
        return {
            "spending_account_id": self.DEFAULT_ACCOUNT_ID,
            "currency": "PLN",
            "starting_balance_pln": round(seed, 4),
            "revenue_pln": round(revenue, 4),
            "earned_total_pln": round(revenue, 4),
            "spent_total_pln": round(actual, 4),
            "reserved_expenses_pln": round(reserved, 4),
            "released_expenses_pln": round(released, 4),
            "committed_expenses_pln": round(committed, 4),
            "available_balance_pln": round(available, 4),
            "max_autonomous_spend_pln": round(available, 4),
            "net_profit_pln": round(net_profit, 4),
            "money_awareness": self._money_awareness(
                available=available,
                revenue=revenue,
                actual=actual,
                reserved=reserved,
            ),
            "card_access": self.card_access_status(),
            "policy": self.policy(),
            "card_data_storage": "forbidden",
        }

    def policy(self) -> dict[str, Any]:
        return {
            "starting_balance_pln": DEFAULT_OPERATING_BALANCE_PLN,
            "spend_rule": "Atnos can spend only from available operating balance.",
            "earn_before_scale": True,
            "business_reason_required": True,
            "autonomous_card_charges": False,
            "card_data_storage": "forbidden",
            "manual_checkout_required": True,
        }

    def awareness(self) -> dict[str, Any]:
        """Zwroc skondensowana pamiec finansowa dla agentow decyzyjnych."""
        snapshot = self.snapshot()
        return {
            "generated_at": utc_now_iso(),
            "source": "smart_spending",
            "currency": snapshot["currency"],
            "current_cash_pln": snapshot["available_balance_pln"],
            "earned_total_pln": snapshot["earned_total_pln"],
            "spent_total_pln": snapshot["spent_total_pln"],
            "reserved_expenses_pln": snapshot["reserved_expenses_pln"],
            "committed_expenses_pln": snapshot["committed_expenses_pln"],
            "net_profit_pln": snapshot["net_profit_pln"],
            "max_autonomous_spend_pln": snapshot["max_autonomous_spend_pln"],
            "rule": "Atnos must earn before it scales spending. It cannot spend above available cash.",
            "card_access": snapshot["card_access"],
            "recent_entries": self.list_entries(limit=10),
        }

    def card_access_status(self) -> dict[str, Any]:
        """Prawda operacyjna o karcie: Atnos nie ma bezposredniego dostepu do platnosci."""
        return {
            "can_use_card": False,
            "can_verify_card": False,
            "card_connected": False,
            "card_data_storage": "forbidden",
            "payment_execution": "manual_checkout_only",
            "reason": "Atnos must not store or use card number, expiry date, CVV, PIN, SMS codes or bank credentials.",
            "safe_path": "Atnos creates procurement requests and budget checks; the owner completes checkout directly with the provider.",
        }

    def can_spend(
        self,
        amount_pln: float,
        *,
        provider: str,
        operation: str,
        reason: str,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Sprawdz, czy wydatek ma sens i miesci sie w saldzie."""
        self._reject_payment_secret_text(reason)
        amount = round(max(float(amount_pln or 0.0), 0.0), 4)
        snapshot = self.snapshot()
        blockers: list[str] = []
        if amount > float(snapshot["available_balance_pln"]):
            blockers.append("smart_spending_balance_exceeded")
        if amount > 0 and len(reason.strip()) < 10:
            blockers.append("business_reason_required")
        allowed = not blockers
        return {
            **snapshot,
            "allowed": allowed,
            "account_id": account_id,
            "provider": provider,
            "operation": operation,
            "estimated_action_cost_pln": amount,
            "balance_after_action_pln": round(max(0.0, float(snapshot["available_balance_pln"]) - amount), 4),
            "blockers": blockers,
            "message": (
                "Spend is allowed by smart spending policy."
                if allowed
                else f"Spend blocked by smart spending: {', '.join(blockers)}."
            ),
        }

    def reserve_expense(
        self,
        *,
        provider: str,
        operation: str,
        amount_pln: float,
        reason: str,
        account_id: str | None = None,
        job_id: str | None = None,
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Zarezerwuj srodki na planowany koszt."""
        decision = self.can_spend(
            amount_pln,
            provider=provider,
            operation=operation,
            reason=reason,
            account_id=account_id,
        )
        if not decision["allowed"]:
            return {"status": "blocked", "decision": decision, "entry": None}
        entry = self._add_entry(
            entry_type="expense_reserved",
            amount_pln=amount_pln,
            provider=provider,
            operation=operation,
            reason=reason,
            account_id=account_id,
            job_id=job_id,
            source_id=source_id,
            metadata=metadata,
        )
        return {"status": "reserved", "decision": decision, "entry": entry, "snapshot": self.snapshot()}

    def release_reserved_expenses_for_job(
        self,
        *,
        job_id: str,
        reason: str,
        provider: str = "cost_guard",
        operation: str = "release_reserved_expense",
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Zwolnij zablokowane saldo dla joba, gdy rezerwacja nie stała się realnym kosztem."""
        self._ensure_schema()
        clean_job_id = str(job_id or "").strip()
        if not clean_job_id:
            return {"status": "blocked", "message": "job_id is required.", "released_pln": 0.0, "entry": None}
        self._reject_payment_secret_text(reason)
        with connect(self.settings) as db:
            self._ensure_default_account(db)
            rows = db.execute(
                f"""
                SELECT entry_type, COALESCE(SUM(amount_pln), 0) AS total
                FROM {self.LEDGER_TABLE}
                WHERE spending_account_id = ? AND job_id = ?
                GROUP BY entry_type
                """,
                (self.DEFAULT_ACCOUNT_ID, clean_job_id),
            ).fetchall()
        totals = {str(row["entry_type"]): float(row["total"] or 0.0) for row in rows}
        releasable = round(max(0.0, totals.get("expense_reserved", 0.0) - totals.get("expense_released", 0.0)), 4)
        if releasable <= 0:
            return {"status": "noop", "message": "No reserved expense to release.", "released_pln": 0.0, "entry": None}
        entry = self._add_entry(
            entry_type="expense_released",
            amount_pln=releasable,
            provider=provider,
            operation=operation,
            reason=reason,
            job_id=clean_job_id,
            source_id=source_id or f"release:{clean_job_id}",
            metadata=metadata,
        )
        return {"status": "released", "released_pln": releasable, "entry": entry, "snapshot": self.snapshot()}

    def record_revenue(
        self,
        *,
        amount_pln: float,
        reason: str,
        account_id: str | None = None,
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Zapisz zarobek, ktory zwieksza saldo operacyjne Atnosa."""
        self._reject_payment_secret_text(reason)
        amount = round(max(float(amount_pln or 0.0), 0.0), 4)
        if amount <= 0:
            return {"status": "blocked", "message": "Revenue amount must be positive.", "entry": None}
        entry = self._add_entry(
            entry_type="revenue",
            amount_pln=amount,
            provider="revenue",
            operation="revenue_recorded",
            reason=reason,
            account_id=account_id,
            source_id=source_id,
            metadata=metadata,
        )
        return {"status": "recorded", "entry": entry, "snapshot": self.snapshot()}

    def list_entries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_schema()
        limit = max(1, min(int(limit), 200))
        with connect(self.settings) as db:
            rows = db.execute(
                f"""
                SELECT * FROM {self.LEDGER_TABLE}
                WHERE spending_account_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (self.DEFAULT_ACCOUNT_ID, limit),
            ).fetchall()
        return [self._row_payload(row) for row in rows]

    def _add_entry(
        self,
        *,
        entry_type: LedgerEntryType,
        amount_pln: float,
        provider: str,
        operation: str,
        reason: str,
        account_id: str | None = None,
        job_id: str | None = None,
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_schema()
        self._reject_payment_secret_text(reason)
        now = utc_now_iso()
        entry_id = f"spend-{uuid4().hex[:12]}"
        amount = round(max(float(amount_pln or 0.0), 0.0), 4)
        with connect(self.settings) as db:
            self._ensure_default_account(db)
            db.execute(
                f"""
                INSERT INTO {self.LEDGER_TABLE} (
                    id, created_at, spending_account_id, entry_type, amount_pln,
                    provider, operation, reason, account_id, job_id, source_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    now,
                    self.DEFAULT_ACCOUNT_ID,
                    entry_type,
                    amount,
                    provider,
                    operation,
                    reason.strip(),
                    account_id,
                    job_id,
                    source_id,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            db.commit()
            row = db.execute(f"SELECT * FROM {self.LEDGER_TABLE} WHERE id = ?", (entry_id,)).fetchone()
        return self._row_payload(row)

    def _ensure_schema(self) -> None:
        with connect(self.settings) as db:
            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.ACCOUNT_TABLE} (
                    id TEXT PRIMARY KEY,
                    currency TEXT NOT NULL DEFAULT 'PLN',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    policy_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.LEDGER_TABLE} (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    spending_account_id TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    amount_pln REAL NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    account_id TEXT,
                    job_id TEXT,
                    source_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            db.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.LEDGER_TABLE}_account_created
                ON {self.LEDGER_TABLE} (spending_account_id, created_at)
                """
            )
            db.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.LEDGER_TABLE}_source
                ON {self.LEDGER_TABLE} (source_id)
                """
            )
            self._ensure_default_account(db)
            db.commit()

    def _ensure_default_account(self, db) -> None:
        row = db.execute(f"SELECT id FROM {self.ACCOUNT_TABLE} WHERE id = ?", (self.DEFAULT_ACCOUNT_ID,)).fetchone()
        now = utc_now_iso()
        if row is None:
            db.execute(
                f"""
                INSERT INTO {self.ACCOUNT_TABLE} (id, currency, created_at, updated_at, policy_json)
                VALUES (?, 'PLN', ?, ?, ?)
                """,
                (self.DEFAULT_ACCOUNT_ID, now, now, json.dumps(self.policy(), ensure_ascii=False, separators=(",", ":"))),
            )
        seed = db.execute(
            f"""
            SELECT id FROM {self.LEDGER_TABLE}
            WHERE spending_account_id = ? AND entry_type = 'seed_capital'
            LIMIT 1
            """,
            (self.DEFAULT_ACCOUNT_ID,),
        ).fetchone()
        if seed is None:
            db.execute(
                f"""
                INSERT INTO {self.LEDGER_TABLE} (
                    id, created_at, spending_account_id, entry_type, amount_pln,
                    provider, operation, reason, source_id, metadata_json
                )
                VALUES (?, ?, ?, 'seed_capital', ?, 'owner_seed', 'initial_operating_balance', ?, ?, ?)
                """,
                (
                    f"spend-seed-{uuid4().hex[:8]}",
                    now,
                    self.DEFAULT_ACCOUNT_ID,
                    DEFAULT_OPERATING_BALANCE_PLN,
                    "Initial operating balance approved by owner.",
                    "initial_50_pln_operating_balance",
                    json.dumps({"source": "owner_approved_starting_balance"}, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    @staticmethod
    def _money_awareness(*, available: float, revenue: float, actual: float, reserved: float) -> dict[str, Any]:
        if available <= 0:
            state = "no_cash"
            instruction = "Do not request paid tools. Generate revenue or ask owner to add operating balance."
        elif revenue <= 0 and reserved > 0:
            state = "seed_being_spent"
            instruction = "Be conservative: spending is still coming from seed money, not earned revenue."
        elif revenue > actual:
            state = "profitable"
            instruction = "Spending can scale only within available balance and with business justification."
        else:
            state = "bootstrap"
            instruction = "Use the 50 PLN seed carefully and prioritize actions likely to create revenue."
        return {
            "state": state,
            "instruction": instruction,
            "must_earn_before_spending": True,
            "can_spend_now_pln": round(max(available, 0.0), 4),
        }

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

    @staticmethod
    def _reject_payment_secret_text(value: str) -> None:
        text = str(value or "")
        normalized = " ".join(text.split())
        card_like = re.search(r"\b(?:\d[ -]?){13,19}\b", normalized)
        cvv_like = re.search(r"\b(?:cvv|cvc|kod\s*cvv|security\s*code)\b", normalized, re.IGNORECASE)
        if card_like or cvv_like:
            raise ValueError("Do not paste card numbers, CVV/CVC, PINs, SMS codes or bank credentials into Atnos.")
