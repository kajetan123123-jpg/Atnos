from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, utc_now, utc_now_iso
from atnos.production.expense_notifier import ExpenseNotifier
from atnos.procurement.smart_spending import SmartSpendingService


class CostGuardBlockedError(RuntimeError):
    """Błąd twardej blokady kosztów przed uruchomieniem płatnej produkcji."""

    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        super().__init__(str(decision.get("message") or "Cost guard blocked this operation."))


@dataclass(frozen=True, slots=True)
class ProductionCostEstimate:
    """Lokalny szacunek kosztu jednej operacji produkcyjnej."""

    operation: str
    estimated_cost_pln: float
    notes: tuple[str, ...]


class CostGuard:
    """Pilnuje miesięcznego i dziennego budżetu płatnych API dla produkcji Shorts."""

    LEDGER_TABLE = "production_cost_ledger"
    TERMINAL_JOB_STATUSES = {
        "qa_passed",
        "qa_failed",
        "error",
        "uploaded_private",
        "uploaded",
        "scheduled_public",
        "published_public",
        "public_publish_failed",
        "public_publish_connection_required",
        "public_publish_quota_exceeded",
        "cancelled",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def snapshot(self) -> dict[str, Any]:
        """Zwróć aktualny stan budżetu bez wykonywania zewnętrznych wywołań."""
        self._ensure_schema()
        released = self.release_completed_job_reservations()
        day_start, day_end = _current_day_bounds_iso()
        month_start, month_end = _current_month_bounds_iso()
        daily_spent = self._sum_between(day_start, day_end)
        monthly_spent = self._sum_between(month_start, month_end)
        daily_limit = _limit_float(getattr(self.settings, "atnos_daily_spend_limit_pln", 0.0))
        monthly_limit = _limit_float(getattr(self.settings, "atnos_monthly_spend_limit_pln", 0.0))
        enabled = bool(getattr(self.settings, "atnos_cost_guard_enabled", True))
        warning_ratio = _clamp_float(getattr(self.settings, "atnos_cost_warning_threshold_ratio", 0.8), 0.0, 1.0)
        monthly_remaining = _remaining(monthly_limit, monthly_spent)
        daily_remaining = _remaining(daily_limit, daily_spent)
        blocked = enabled and (
            (monthly_limit is not None and monthly_remaining <= 0)
            or (daily_limit is not None and daily_remaining <= 0)
        )
        warning = enabled and not blocked and (
            _limit_near_exhausted(daily_limit, daily_spent, warning_ratio)
            or _limit_near_exhausted(monthly_limit, monthly_spent, warning_ratio)
        )
        return {
            "enabled": enabled,
            "blocked": blocked,
            "warning": warning,
            "currency": "PLN",
            "daily_spent_pln": round(daily_spent, 4),
            "monthly_spent_pln": round(monthly_spent, 4),
            "daily_limit_pln": daily_limit,
            "monthly_limit_pln": monthly_limit,
            "daily_remaining_pln": None if daily_limit is None else round(daily_remaining, 4),
            "monthly_remaining_pln": None if monthly_limit is None else round(monthly_remaining, 4),
            "warning_threshold_ratio": warning_ratio,
            "ledger_table": self.LEDGER_TABLE,
            "ledger_path": str((self.settings.data_dir / "intelligence.db").resolve()),
            "released_stale_reservations": released,
            "smart_spending": SmartSpendingService(self.settings).snapshot(),
        }

    def estimate_production_job(self) -> ProductionCostEstimate:
        """Oszacuj koszt jednego pełnego renderu przed uruchomieniem joba."""
        if _production_uses_only_local_backends(self.settings):
            return ProductionCostEstimate(
                operation="production_job",
                estimated_cost_pln=0.0,
                notes=("local_tts_and_local_visual_backend",),
            )
        configured = _limit_float(getattr(self.settings, "atnos_estimated_cost_per_premium_short_pln", 0.0))
        if configured is not None:
            return ProductionCostEstimate(
                operation="production_job",
                estimated_cost_pln=round(configured, 4),
                notes=("configured_estimate",),
            )
        notes: list[str] = []
        total = 0.0
        if bool(getattr(self.settings, "shorts_allow_cloud_tts", False)):
            total += float(getattr(self.settings, "atnos_estimated_tts_cost_per_short_pln", 0.0) or 0.0)
            notes.append("cloud_tts_enabled")
        if bool(getattr(self.settings, "shorts_allow_cloud_visuals", False)):
            total += float(getattr(self.settings, "atnos_estimated_visual_cost_per_short_pln", 0.0) or 0.0)
            notes.append("cloud_visuals_enabled")
        return ProductionCostEstimate(
            operation="production_job",
            estimated_cost_pln=round(max(total, 0.0), 4),
            notes=tuple(notes or ["local_or_zero_cost_path"]),
        )

    def check_can_spend(
        self,
        estimated_cost_pln: float,
        *,
        account_id: str | None = None,
        operation: str = "operation",
    ) -> dict[str, Any]:
        """Sprawdź, czy operacja mieści się w budżecie."""
        snapshot = self.snapshot()
        estimate = round(max(float(estimated_cost_pln or 0.0), 0.0), 4)
        if estimate <= 0:
            smart_spending = SmartSpendingService(self.settings).snapshot()
            return {
                **snapshot,
                "smart_spending": smart_spending,
                "allowed": True,
                "account_id": account_id,
                "operation": operation,
                "estimated_action_cost_pln": estimate,
                "message": "Cost guard is disabled or this operation has zero estimated external cost.",
            }

        blockers: list[str] = []
        monthly_remaining = snapshot.get("monthly_remaining_pln")
        daily_remaining = snapshot.get("daily_remaining_pln")
        if snapshot["enabled"]:
            if isinstance(monthly_remaining, (int, float)) and estimate > float(monthly_remaining):
                blockers.append("monthly_budget_exceeded")
            if isinstance(daily_remaining, (int, float)) and estimate > float(daily_remaining):
                blockers.append("daily_budget_exceeded")
            if snapshot.get("blocked") and not blockers:
                blockers.append("budget_exhausted")
        smart = SmartSpendingService(self.settings).can_spend(
            estimate,
            provider="cost_guard",
            operation=operation,
            reason=f"Estimated external cost for {operation}.",
            account_id=account_id,
        )
        if not smart["allowed"]:
            blockers.extend(str(item) for item in smart.get("blockers") or [])
        blockers = list(dict.fromkeys(blockers))
        allowed = not blockers and not (bool(snapshot.get("blocked")) and snapshot["enabled"])
        return {
            **snapshot,
            "smart_spending": smart,
            "allowed": allowed,
            "account_id": account_id,
            "operation": operation,
            "estimated_action_cost_pln": estimate,
            "blockers": blockers,
            "message": (
                "Operation fits within the configured API budget."
                if allowed
                else f"Operation blocked by cost guard: {', '.join(blockers)}."
            ),
        }

    def reserve_production_job(self, *, account_id: str, topic_id: str, job_id: str) -> dict[str, Any]:
        """Zarezerwuj koszt renderu w lokalnym ledgerze przed startem joba."""
        estimate = self.estimate_production_job()
        decision = self.check_can_spend(
            estimate.estimated_cost_pln,
            account_id=account_id,
            operation=estimate.operation,
        )
        if not decision["allowed"]:
            raise CostGuardBlockedError(decision)
        if estimate.estimated_cost_pln > 0:
            self.record_event(
                account_id=account_id,
                job_id=job_id,
                provider="estimated_pipeline",
                operation=estimate.operation,
                estimated_cost_pln=estimate.estimated_cost_pln,
                status="reserved",
                metadata={
                    "topic_id": topic_id,
                    "notes": list(estimate.notes),
                    "source": "cost_guard.reserve_production_job",
                },
            )
        return decision

    def record_event(
        self,
        *,
        account_id: str | None,
        job_id: str | None,
        provider: str,
        operation: str,
        estimated_cost_pln: float,
        status: str = "estimated",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Zapisz lokalny szacunek kosztu do SQLite ledger."""
        self._ensure_schema()
        with connect(self.settings) as db:
            db.execute(
                f"""
                INSERT INTO {self.LEDGER_TABLE} (
                    created_at, account_id, job_id, provider, operation,
                    estimated_cost_pln, status, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    account_id,
                    job_id,
                    provider,
                    operation,
                    round(max(float(estimated_cost_pln or 0.0), 0.0), 4),
                    status,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            db.commit()
        if round(max(float(estimated_cost_pln or 0.0), 0.0), 4) > 0:
            SmartSpendingService(self.settings).reserve_expense(
                provider=provider,
                operation=operation,
                amount_pln=estimated_cost_pln,
                reason=str((metadata or {}).get("reason") or f"External cost for {operation}."),
                account_id=account_id,
                job_id=job_id,
                source_id=f"{provider}:{operation}:{utc_now_iso()}",
                metadata={**(metadata or {}), "ledger_status": status, "source": "cost_guard.record_event"},
            )
            ExpenseNotifier(self.settings).enqueue(
                event_type="cost_recorded",
                provider=provider,
                operation=operation,
                amount_pln=estimated_cost_pln,
                reason=str((metadata or {}).get("reason") or operation),
                account_id=account_id,
                job_id=job_id,
                source_id=f"{provider}:{operation}:{utc_now_iso()}",
                metadata={**(metadata or {}), "ledger_status": status},
            )

    def release_completed_job_reservations(self) -> dict[str, Any]:
        """Zwolnij estymowane rezerwacje kosztu dla jobow, ktore zakonczyly pipeline."""
        self._ensure_schema()
        terminal_statuses = tuple(sorted(self.TERMINAL_JOB_STATUSES))
        placeholders = ",".join("?" for _ in terminal_statuses)
        with connect(self.settings) as db:
            rows = db.execute(
                f"""
                SELECT l.id, l.account_id, l.job_id, l.provider, l.operation, l.estimated_cost_pln
                FROM {self.LEDGER_TABLE} l
                JOIN pipeline_jobs j ON j.id = l.job_id
                WHERE l.status = 'reserved'
                  AND l.provider = 'estimated_pipeline'
                  AND j.status IN ({placeholders})
                """,
                terminal_statuses,
            ).fetchall()
            if not rows:
                return {"released_count": 0, "released_pln": 0.0}
            release_items: list[dict[str, Any]] = []
            for row in rows:
                ledger_id = int(row["id"])
                job_id = str(row["job_id"] or "")
                amount = round(max(float(row["estimated_cost_pln"] or 0.0), 0.0), 4)
                db.execute(
                    f"UPDATE {self.LEDGER_TABLE} SET status = 'released' WHERE id = ? AND status = 'reserved'",
                    (ledger_id,),
                )
                release_items.append(
                    {
                        "ledger_id": ledger_id,
                        "job_id": job_id,
                        "amount": amount,
                        "account_id": row["account_id"],
                    }
                )
            db.commit()
        for item in release_items:
            SmartSpendingService(self.settings).release_reserved_expenses_for_job(
                job_id=str(item["job_id"]),
                provider="estimated_pipeline",
                operation="release_production_job_reservation",
                reason="Production job completed before the estimated reservation became an actual external cost.",
                source_id=f"release:production_cost_ledger:{item['ledger_id']}",
                metadata={
                    "source": "cost_guard.release_completed_job_reservations",
                    "production_cost_ledger_id": item["ledger_id"],
                    "account_id": item["account_id"],
                },
            )
        return {
            "released_count": len(release_items),
            "released_pln": round(sum(float(item["amount"]) for item in release_items), 4),
        }

    def _sum_between(self, start_iso: str, end_iso: str) -> float:
        with connect(self.settings) as db:
            row = db.execute(
                f"""
                SELECT COALESCE(SUM(estimated_cost_pln), 0) AS total
                FROM {self.LEDGER_TABLE}
                WHERE created_at >= ? AND created_at < ?
                    AND status NOT IN ('void', 'cancelled', 'released')
                """,
                (start_iso, end_iso),
            ).fetchone()
        return float(row["total"] if row else 0.0)

    def _ensure_schema(self) -> None:
        with connect(self.settings) as db:
            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.LEDGER_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    account_id TEXT,
                    job_id TEXT,
                    provider TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    estimated_cost_pln REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'estimated',
                    metadata_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            db.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.LEDGER_TABLE}_created_at
                ON {self.LEDGER_TABLE} (created_at)
                """
            )
            db.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.LEDGER_TABLE}_account_job
                ON {self.LEDGER_TABLE} (account_id, job_id)
                """
            )
            db.commit()


def _limit_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(number, 0.0), 4)


def _production_uses_only_local_backends(settings: Settings) -> bool:
    """Czy aktualna sciezka produkcji nie powinna uruchamiac platnych API."""
    video_backend = str(getattr(settings, "shorts_video_backend", "") or "").strip().lower()
    tts_backend = str(getattr(settings, "shorts_tts_backend", "") or "").strip().lower()
    cloud_tts_enabled = bool(getattr(settings, "shorts_allow_cloud_tts", False))
    cloud_visuals_enabled = bool(getattr(settings, "shorts_allow_cloud_visuals", False))
    local_video = video_backend in {"motion_graphic", "ltx", "wan"}
    local_tts = tts_backend in {"f5_tts_http", "kokoro_local", "coqui_tts", "pyttsx3", "piper"}
    return bool(local_video and local_tts and not cloud_tts_enabled and not cloud_visuals_enabled)


def _clamp_float(value: object, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = lower
    return max(lower, min(number, upper))


def _remaining(limit: float | None, spent: float) -> float:
    if limit is None:
        return float("inf")
    return max(0.0, float(limit) - float(spent))


def _limit_near_exhausted(limit: float | None, spent: float, ratio: float) -> bool:
    return limit is not None and limit > 0 and spent >= limit * ratio


def _current_day_bounds_iso() -> tuple[str, str]:
    now = utc_now()
    start = datetime.combine(now.date(), time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _current_month_bounds_iso() -> tuple[str, str]:
    now = utc_now()
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return start.isoformat(), end.isoformat()
