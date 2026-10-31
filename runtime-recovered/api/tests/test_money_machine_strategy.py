from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import init_intelligence_db  # noqa: E402
from atnos.production.money_machine_strategy import MoneyMachineStrategyService  # noqa: E402
from test_support import bootstrap_test_app, create_test_app  # noqa: E402


def _boot(tmp_path: Path):
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    asyncio.run(init_intelligence_db(app.state.settings))
    return app


def test_strategy_report_writes_artifacts_and_financial_awareness(tmp_path: Path) -> None:
    app = _boot(tmp_path)

    with app.state.database.session_scope() as session:
        payload = asyncio.run(
            MoneyMachineStrategyService(app.state.settings, session=session).build(
                include_live_checks=False,
                send_gmail=False,
            )
        )

    assert payload["financial_awareness"]["current_cash_pln"] == 50.0
    assert payload["financial_awareness"]["earned_total_pln"] == 0.0
    assert payload["card_access"]["can_use_card"] is False
    assert payload["card_access"]["can_verify_card"] is False
    assert payload["card_access"]["payment_execution"] == "manual_checkout_only"
    assert "direct_card_use_forbidden_manual_checkout_only" in payload["blockers"]["warnings"]

    samples = payload["samples"]
    assert len(samples["shorts"]) == 3
    assert {item["channel_key"] for item in samples["shorts"]} == {
        "personal_finance",
        "dark_history",
        "ai_absurd",
    }
    assert all(item["language"] == "EN" for item in samples["shorts"])
    assert all(item["visual_rule"] for item in samples["shorts"])
    assert all("non-explicit" in item["boundaries"] for item in samples["synthetic_creators"])
    assert len(samples["investment"]) >= 2
    assert all("No automatic orders" in item["boundary"] or "Education only" in item["boundary"] for item in samples["investment"])

    json_path = Path(payload["artifacts"]["json_path"])
    markdown_path = Path(payload["artifacts"]["markdown_path"])
    assert json_path.exists()
    assert markdown_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Atnos nie zapisuje danych karty" in markdown
    assert "Budżet i zasady wydatków" in markdown
    assert "Próbki shortów" in markdown
    assert "Personal Finance Shorts EN" in markdown
    assert "Konto twórcy / synthetic creator" in markdown
    assert "Portfel inwestycyjny" in markdown
    assert "Probki shortow" not in markdown
    assert "Budzet i zasady" not in markdown
    assert "Nastepne kroki" not in markdown


def test_strategy_report_gmail_dispatch_is_blocked_until_money_machine_ready(tmp_path: Path) -> None:
    app = _boot(tmp_path)

    with app.state.database.session_scope() as session:
        payload = asyncio.run(
            MoneyMachineStrategyService(app.state.settings, session=session).build(
                include_live_checks=False,
                send_gmail=True,
            )
        )

    assert payload["status"] == "needs_work"
    assert payload["gmail"]["status"] == "blocked_not_ready"
    assert "critical money-machine blockers" in payload["gmail"]["message"]
    assert Path(payload["gmail"]["markdown_path"]).exists()


def test_strategy_report_api_endpoint(tmp_path: Path) -> None:
    app = _boot(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/production/money-machine/strategy-report",
        json={"include_live_checks": False, "send_gmail": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["financial_awareness"]["current_cash_pln"] == 50.0
    assert payload["card_access"]["can_use_card"] is False
    assert Path(payload["artifacts"]["markdown_path"]).exists()
    assert payload["gmail"]["status"] == "skipped"


def test_strategy_next_best_actions_uses_operator_next_action() -> None:
    actions = MoneyMachineStrategyService._next_best_actions(
        blockers={
            "critical": ["private_upload_public_quality_failed"],
            "warnings": ["direct_card_use_forbidden_manual_checkout_only"],
        },
        operator_summary={"next_operator_action": "execute_recovery_regeneration"},
    )

    assert actions[0] == "execute_recovery_regeneration"
    assert "run_visual_backend_check_and_regenerate_weak_visuals" in actions
    assert "use_procurement_requests_for_paid_tools_instead_of_card_storage" in actions
