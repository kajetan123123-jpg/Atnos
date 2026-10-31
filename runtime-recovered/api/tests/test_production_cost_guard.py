from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, upsert_intelligence_account_config, utc_now_iso
from atnos.production.autopilot import ProductionAutopilotService
from atnos.production.cost_guard import CostGuard, CostGuardBlockedError
from atnos.production.money_machine import MoneyMachineStatusService
from atnos.production.orchestrator import ProductionOrchestrator
from test_support import build_test_settings


def test_cost_guard_allows_and_records_budgeted_production_reservation(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_monthly_spend_limit_pln = 200.0
    settings.atnos_daily_spend_limit_pln = 25.0
    settings.atnos_estimated_cost_per_premium_short_pln = 4.0
    settings.shorts_allow_cloud_visuals = True

    guard = CostGuard(settings)
    decision = guard.reserve_production_job(account_id="acct-1", topic_id="topic-1", job_id="prod-1")

    assert decision["allowed"] is True
    snapshot = guard.snapshot()
    assert snapshot["monthly_spent_pln"] == 4.0
    assert snapshot["monthly_remaining_pln"] == 196.0
    with connect(settings) as db:
        notification = db.execute("SELECT * FROM expense_notifications ORDER BY created_at DESC LIMIT 1").fetchone()
    assert notification is not None
    assert notification["event_type"] == "cost_recorded"
    assert notification["amount_pln"] == 4.0
    assert notification["delivery_channel"] == "gmail"
    assert notification["delivery_status"] == "pending_delivery"


def test_cost_guard_treats_local_f5_motion_graphic_render_as_zero_external_cost(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_tts_backend = "f5_tts_http"
    settings.shorts_video_backend = "motion_graphic"
    settings.shorts_allow_cloud_tts = False
    settings.shorts_allow_cloud_visuals = False
    settings.atnos_estimated_cost_per_premium_short_pln = 4.0

    guard = CostGuard(settings)
    estimate = guard.estimate_production_job()
    decision = guard.reserve_production_job(account_id="acct-1", topic_id="topic-1", job_id="prod-local")

    assert estimate.estimated_cost_pln == 0.0
    assert estimate.notes == ("local_tts_and_local_visual_backend",)
    assert decision["allowed"] is True
    assert decision["estimated_action_cost_pln"] == 0.0
    assert guard.snapshot()["monthly_spent_pln"] == 0.0


def test_cost_guard_releases_reserved_estimates_for_terminal_jobs(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_monthly_spend_limit_pln = 200.0
    settings.atnos_daily_spend_limit_pln = 25.0
    settings.atnos_estimated_cost_per_premium_short_pln = 4.0
    settings.shorts_allow_cloud_visuals = True
    now = utc_now_iso()

    guard = CostGuard(settings)
    guard.reserve_production_job(account_id="acct-1", topic_id="topic-1", job_id="prod-done")
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (id, account_id, topic_id, status, current_step, created_at, updated_at)
            VALUES ('prod-done', 'acct-1', 'topic-1', 'qa_passed', 'upload_package', ?, ?)
            """,
            (now, now),
        )
        db.commit()

    snapshot = guard.snapshot()

    assert snapshot["released_stale_reservations"] == {"released_count": 1, "released_pln": 4.0}
    assert snapshot["monthly_spent_pln"] == 0.0
    with connect(settings) as db:
        ledger_status = db.execute("SELECT status FROM production_cost_ledger WHERE job_id = 'prod-done'").fetchone()["status"]
    assert ledger_status == "released"
    smart = snapshot["smart_spending"]
    assert smart["available_balance_pln"] == 50.0
    assert smart["reserved_expenses_pln"] == 4.0
    assert smart["released_expenses_pln"] == 4.0


def test_cost_guard_blocks_when_next_render_exceeds_monthly_budget(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_monthly_spend_limit_pln = 1.0
    settings.atnos_daily_spend_limit_pln = 25.0
    settings.atnos_estimated_cost_per_premium_short_pln = 4.0
    settings.shorts_allow_cloud_visuals = True

    decision = CostGuard(settings).check_can_spend(4.0, account_id="acct-1", operation="production_job")

    assert decision["allowed"] is False
    assert "monthly_budget_exceeded" in decision["blockers"]


def test_cost_guard_treats_zero_limit_as_block_all_spend(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_monthly_spend_limit_pln = 0.0
    settings.atnos_daily_spend_limit_pln = 0.0

    decision = CostGuard(settings).check_can_spend(0.01, account_id="acct-1", operation="production_job")

    assert decision["allowed"] is False
    assert "monthly_budget_exceeded" in decision["blockers"]
    assert "daily_budget_exceeded" in decision["blockers"]


def test_smart_spending_blocks_even_when_classic_cost_guard_is_disabled(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_cost_guard_enabled = False

    decision = CostGuard(settings).check_can_spend(80.0, account_id="acct-1", operation="production_job")

    assert decision["allowed"] is False
    assert "smart_spending_balance_exceeded" in decision["blockers"]
    assert decision["smart_spending"]["available_balance_pln"] == 50


def test_orchestrator_prepare_does_not_queue_job_when_cost_guard_blocks(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_monthly_spend_limit_pln = 1.0
    settings.atnos_daily_spend_limit_pln = 25.0
    settings.atnos_estimated_cost_per_premium_short_pln = 4.0
    settings.shorts_allow_cloud_visuals = True
    _seed_approved_topic(settings)

    with pytest.raises(CostGuardBlockedError):
        asyncio.run(ProductionOrchestrator(settings).prepare(account_id="acct-1", topic_id="topic-1"))

    with connect(settings) as db:
        jobs = db.execute("SELECT COUNT(*) AS count FROM pipeline_jobs").fetchone()["count"]
        topic_status = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-1'").fetchone()["status"]
    assert jobs == 0
    assert topic_status == "approved"


def test_autopilot_returns_blocked_before_starting_when_cost_guard_blocks(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_monthly_spend_limit_pln = 1.0
    settings.atnos_daily_spend_limit_pln = 25.0
    settings.atnos_estimated_cost_per_premium_short_pln = 4.0
    settings.shorts_allow_cloud_visuals = True

    result = asyncio.run(ProductionAutopilotService(settings).run_once(max_jobs=1))

    assert result["status"] == "blocked"
    assert result["errors"][0]["blocker"] == "cost_guard_budget_blocked"
    assert result["cost_guard"]["allowed"] is False


def test_money_machine_surfaces_budget_blocker_for_next_estimated_render(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.atnos_monthly_spend_limit_pln = 1.0
    settings.atnos_daily_spend_limit_pln = 25.0
    settings.atnos_estimated_cost_per_premium_short_pln = 4.0
    settings.shorts_allow_cloud_visuals = True
    _seed_approved_topic(settings)

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    assert status["status"] == "budget_blocked"
    assert status["cost_guard"]["allowed"] is False
    assert "cost_guard_budget_blocked" in status["global_blockers"]
    assert {"scope": "global", "action": "review_or_raise_monthly_api_budget"} in status["next_actions"]


def _seed_approved_topic(settings) -> None:
    now = utc_now_iso()
    upsert_intelligence_account_config(
        settings,
        account_id="acct-1",
        niches=["personal finance"],
        region="US",
        channel_title="Wealth Blueprint",
        content_profile="wealth_blueprint",
    )
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-1', 'acct-1', 'The Money Rule Beginners Skip', 'Test topic', 'approved', 88, ?, ?)
            """,
            (now, now),
        )
        db.commit()
