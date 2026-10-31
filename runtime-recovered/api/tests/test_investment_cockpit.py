from __future__ import annotations

from pathlib import Path
import asyncio
import sys
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import httpx
import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from test_support import create_test_app  # noqa: E402
from atnos.api.routes import investment as investment_routes  # noqa: E402
from atnos.investment.alpaca_market_data import AlpacaPaperReadOnlyAdapter  # noqa: E402
from atnos.investment.xtb_market_data import (  # noqa: E402
    XtbApiRetiredError,
    XtbForbiddenCommandError,
    XtbReadOnlyMarketDataAdapter,
)
from atnos.investment.education_sources import InvestmentEducationSourceRegistry  # noqa: E402
from atnos.investment.education_research import InvestmentEducationResearchService  # noqa: E402


def test_investment_cockpit_starts_in_safe_paper_mode(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/investment/cockpit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "paper_trading_only"
    assert payload["status"] == "limited"
    assert payload["real_trading_enabled"] is False
    assert payload["broker_connected"] is False
    assert payload["capabilities"]["can_research"] is True
    assert payload["capabilities"]["can_paper_trade"] is True
    assert payload["capabilities"]["can_seed_paper_instruments"] is True
    assert payload["capabilities"]["can_trade_real_money"] is False
    assert "xtb_demo_not_registered" in payload["blockers"]
    assert "real_trading_disabled" in payload["blockers"]


def test_portfolio_settings_preserve_linked_xtb_vault_credential(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        credential = client.post(
            "/accounts",
            json={
                "label": "XTB Demo",
                "provider_key": "xtb-demo",
                "account_identifier": "account-63eb9cd4@example.invalid",
                "secret_type": "password",
                "secret_value": "test-vault-secret",
                "risk_level": "high",
                "allowed_capabilities": ["financial_read_later"],
            },
        )
        assert credential.status_code == 201
        credential_id = credential.json()["id"]

        broker = client.post(
            "/api/investment/brokers/xtb-demo",
            json={"account_identifier": "account-63eb9cd4@example.invalid", "credential_account_id": credential_id},
        )
        settings = client.put(
            "/api/investment/settings/portfolio-alpha",
            json={
                "broker_account_identifier": "account-63eb9cd4@example.invalid",
                "credential_vault_ref": f"account-vault:{credential_id}",
                "broker_read_access": "read_only_candidate",
            },
        )
        market_status = client.get("/api/investment/market-data/xtb-demo/status")

    assert broker.status_code == 201
    assert settings.status_code == 200
    assert market_status.status_code == 200
    assert market_status.json()["credential_account_id"] == credential_id
    assert market_status.json()["has_credential_ref"] is True
    assert market_status.json()["real_trading_enabled"] is False


def test_alpaca_adapter_verifies_only_read_operations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["APCA-API-KEY-ID"] == "paper-key-id"
        assert request.headers["APCA-API-SECRET-KEY"] == "paper-secret"
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "currency": "USD"})
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=[])
        if request.url.path == "/v2/stocks/quotes/latest":
            return httpx.Response(
                200,
                json={"quotes": {"SPY": {"bp": 500.1, "ap": 500.2, "bs": 2, "as": 3, "t": "2026-07-13T12:00:00Z"}}},
            )
        return httpx.Response(404)

    adapter = AlpacaPaperReadOnlyAdapter(
        api_key_id="paper-key-id",
        api_secret_key="paper-secret",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(adapter.verify_read_only_access(symbols=["SPY"]))

    assert result["status"] == "read_only_verified"
    assert result["quote_count"] == 1
    assert result["real_orders_enabled"] is False
    assert "place_order" in result["forbidden_operations"]
    assert not hasattr(adapter, "place_order")


def test_alpaca_paper_credentials_are_linked_through_local_vault(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        credential = client.post(
            "/accounts",
            json={
                "label": "Alpaca Paper",
                "provider_key": "alpaca-paper",
                "account_identifier": "alpaca-paper-primary",
                "secret_type": "api_key",
                "secret_value": "{\"api_key_id\":\"paper-key-id\",\"api_secret_key\":\"paper-secret\"}",
                "risk_level": "high",
                "allowed_capabilities": ["financial_read_later"],
            },
        )
        assert credential.status_code == 201
        credential_id = credential.json()["id"]
        broker = client.post(
            "/api/investment/brokers/alpaca-paper",
            json={"credential_account_id": credential_id},
        )
        status_response = client.get("/api/investment/market-data/alpaca-paper/status")

    assert broker.status_code == 201
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["registered"] is True
    assert payload["has_credential_ref"] is True
    assert payload["read_only_verified"] is False
    assert payload["real_trading_enabled"] is False


def test_education_source_bootstrap_registers_public_urls_without_claiming_learning(tmp_path: Path, monkeypatch) -> None:
    async def fake_check(self, _client, source, *, checked_at):
        result = {**source, "checked_at": checked_at, "http_status": 200, "last_error": None}
        result["verification_status"] = (
            "external_copilot_only"
            if source["access_scope"] == "external_copilot_only"
            else "available"
        )
        return result

    monkeypatch.setattr(InvestmentEducationSourceRegistry, "_check_source", fake_check)
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        bootstrap = client.post("/api/investment/education/bootstrap")
        sources = client.get("/api/investment/education/sources")
        cockpit = client.get("/api/investment/cockpit")

    assert bootstrap.status_code == 200
    assert bootstrap.json()["learning_state"] == "sources_registered_not_synthesized"
    assert bootstrap.json()["verified_count"] >= 4
    assert bootstrap.json()["external_copilot_only_count"] == 1
    assert sources.status_code == 200
    source_by_id = {item["source_id"]: item for item in sources.json()["sources"]}
    assert source_by_id["binance_public_market_data"]["access_scope"] == "public_reference_only"
    assert source_by_id["codex_investment_banking_methodology"]["verification_status"] == "external_copilot_only"
    assert cockpit.json()["verified_education_source_count"] >= 4
    assert "https://www.xtb.com/pl/edukacja/konto-demo" in cockpit.json()["learning_resources"]
    assert cockpit.json()["real_trading_enabled"] is False


def test_cited_education_research_persists_excerpts_without_creating_a_strategy(tmp_path: Path, monkeypatch) -> None:
    async def fake_check(self, _client, source, *, checked_at):
        result = {**source, "checked_at": checked_at, "http_status": 200, "last_error": None}
        result["verification_status"] = (
            "external_copilot_only"
            if source["access_scope"] == "external_copilot_only"
            else "available"
        )
        return result

    async def fake_fetch(self, _client, source):
        return {
            "title": f"Verified {source['source_id']}",
            "excerpt": "This is a source-backed educational excerpt about risk and paper trading. It contains no trading recommendation.",
        }

    monkeypatch.setattr(InvestmentEducationSourceRegistry, "_check_source", fake_check)
    monkeypatch.setattr(InvestmentEducationResearchService, "_fetch_source", fake_fetch)
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        assert client.post("/api/investment/education/bootstrap").status_code == 200
        created = client.post("/api/investment/education/research", json={"max_sources": 2})
        reports = client.get("/api/investment/education/reports")
        cockpit = client.get("/api/investment/cockpit")

    assert created.status_code == 200
    assert created.json()["status"] == "completed"
    assert created.json()["source_count"] == 2
    assert created.json()["citation_count"] == 2
    assert created.json()["llm_synthesis_status"] == "not_requested"
    assert created.json()["safe_for_paper_strategy"] is False
    assert created.json()["real_trading_enabled"] is False
    assert reports.status_code == 200
    assert reports.json()["reports"][0]["citation_count"] == 2
    assert cockpit.json()["education_reports"][0]["id"] == created.json()["id"]
    assert cockpit.json()["capabilities"]["can_run_autonomous_paper_strategy"] is False


def test_cited_education_research_formats_public_json_as_a_non_signal_snapshot(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = InvestmentEducationResearchService(app.state.settings)

    result = service._extract_json_snapshot(
        '{"symbol":"BTCUSDT","lastPrice":"64127.56","highPrice":"64692.83","lowPrice":"63656.00"}',
        fallback_title="Binance public data",
    )

    assert result["title"] == "Binance public data (BTCUSDT)"
    assert "lastPrice=64127.56" in result["excerpt"]
    assert "No trading signal is inferred." in result["excerpt"]


def test_xtb_demo_registration_keeps_real_trading_blocked(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/api/investment/brokers/xtb-demo",
            json={"account_identifier": "account-c5034bf4@example.invalid"},
        )
        brokers = client.get("/api/investment/brokers")
        cockpit = client.get("/api/investment/cockpit")
        market_status = client.get("/api/investment/market-data/xtb-demo/status").json()

    assert created.status_code == 201
    assert created.json()["provider_key"] == "xtb"
    assert created.json()["environment"] == "demo"
    assert created.json()["status"] == "metadata_registered"
    assert brokers.status_code == 200
    assert brokers.json()[0]["account_identifier"] == "account-c5034bf4@example.invalid"
    assert cockpit.json()["real_trading_enabled"] is False
    assert cockpit.json()["broker_access"] == "metadata_registered"
    assert cockpit.json()["broker_connected"] is False
    assert "xtb_demo_not_verified_read_only" in cockpit.json()["blockers"]

    assert market_status["api_available"] is False
    assert market_status["provider_status"] == "blocked_provider_api_retired"
    assert market_status["api_retired_at"] == "2025-03-14"


def test_portfolio_alpha_settings_store_investment_connections_without_secrets(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    payload = {
        "broker_name": "XTB Demo",
        "broker_portal_url": "https://xstation5.xtb.com/",
        "broker_account_identifier": "account-c5034bf4@example.invalid",
        "credential_vault_ref": "vault:xtb-demo:atnos-int",
        "broker_read_access": "read_only_candidate",
        "account_mode": "demo_paper",
        "market_data_sources": ["serpapi", "brave_search", "xtb_demo_quotes"],
        "learning_resources": [
            "https://www.xtb.com/pl/edukacja",
            "https://www.investopedia.com/trading/",
        ],
        "monthly_spend_budget_pln": 50,
        "payment_method_ref": "vault:card:atnos-monthly-budget",
        "payment_method_configured": True,
        "real_trading_blocked": True,
        "trade_approval_rules_enabled": False,
    }

    with TestClient(app) as client:
        updated = client.put("/api/investment/settings/portfolio-alpha", json=payload)
        settings = client.get("/api/investment/settings/portfolio-alpha")
        cockpit = client.get("/api/investment/cockpit")
        readiness = client.get("/api/projects/readiness")

    assert updated.status_code == 200
    assert updated.json()["stores_plaintext_secret"] is False
    assert updated.json()["credential_vault_ref"] == "vault:xtb-demo:atnos-int"
    assert updated.json()["payment_method_ref"] == "vault:card:atnos-monthly-budget"
    assert settings.status_code == 200
    assert settings.json()["market_data_sources"] == ["serpapi", "brave_search", "xtb_demo_quotes"]
    assert settings.json()["learning_resources"]

    cockpit_payload = cockpit.json()
    assert cockpit_payload["market_data_status"] == "configured"
    assert cockpit_payload["broker_access"] == "read_only_candidate"
    assert cockpit_payload["broker_connected"] is False
    assert cockpit_payload["real_trading_enabled"] is False
    assert cockpit_payload["capabilities"]["can_use_card_or_bank"] is False

    investment = next(
        project for project in readiness.json()["projects"] if project["project_id"] == "investment:portfolio-alpha"
    )
    requirement_ids = {item["id"]: item["status"] for item in investment["requirements"]}
    assert requirement_ids["broker_metadata"] == "ready"
    assert requirement_ids["broker_read_only"] == "missing"
    assert requirement_ids["market_data"] == "ready"
    assert requirement_ids["learning_resources"] == "ready"
    assert requirement_ids["spend_budget"] == "ready"
    assert requirement_ids["payment_reference"] == "ready"
    assert investment["capabilities"]["can_trade_or_purchase"] is False


def test_portfolio_alpha_settings_reject_plaintext_password_or_card_data(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    base_payload = {
        "broker_name": "XTB Demo",
        "broker_portal_url": "https://xstation5.xtb.com/",
        "broker_account_identifier": "account-c5034bf4@example.invalid",
        "credential_vault_ref": "vault:xtb-demo:atnos-int",
        "broker_read_access": "read_only_candidate",
        "account_mode": "demo_paper",
        "market_data_sources": [],
        "learning_resources": [],
        "monthly_spend_budget_pln": 50,
        "payment_method_ref": "vault:card:atnos-monthly-budget",
        "payment_method_configured": True,
        "real_trading_blocked": True,
        "trade_approval_rules_enabled": False,
    }

    with TestClient(app) as client:
        test_card = " ".join(["4242"] * 4)
        plaintext_password = client.put(
            "/api/investment/settings/portfolio-alpha",
            json={**base_payload, "credential_vault_ref": "password:secret-value"},
        )
        plaintext_card = client.put(
            "/api/investment/settings/portfolio-alpha",
            json={**base_payload, "payment_method_ref": test_card},
        )

    assert plaintext_password.status_code == 400
    assert "must not contain secret material" in plaintext_password.json()["detail"]
    assert plaintext_card.status_code == 400
    assert "vault reference" in plaintext_card.json()["detail"]


def test_portfolio_alpha_settings_cannot_fake_read_only_verification(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        updated = client.put(
            "/api/investment/settings/portfolio-alpha",
            json={
                "broker_name": "XTB Demo",
                "broker_portal_url": "https://xstation5.xtb.com/",
                "broker_account_identifier": "account-c5034bf4@example.invalid",
                "credential_vault_ref": "vault:xtb-demo:atnos-int",
                "broker_read_access": "read_only_verified",
                "account_mode": "demo_paper",
                "market_data_sources": ["xtb_demo_quotes"],
                "learning_resources": ["https://www.xtb.com/pl/edukacja"],
                "monthly_spend_budget_pln": 50,
                "payment_method_ref": "vault:card:atnos-monthly-budget",
                "payment_method_configured": True,
                "real_trading_blocked": True,
                "trade_approval_rules_enabled": False,
            },
        )
        cockpit = client.get("/api/investment/cockpit")

    assert updated.status_code == 200
    assert updated.json()["broker_read_access"] == "read_only_candidate"
    assert cockpit.json()["broker_access"] == "read_only_candidate"
    assert cockpit.json()["broker_connected"] is False
    assert "xtb_demo_not_verified_read_only" in cockpit.json()["blockers"]


def test_seed_xtb_demo_instruments_is_paper_only_and_updates_watchlist(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        seeded = client.post("/api/investment/instruments/seed-xtb-demo", json={"also_add_to_watchlist": True})
        instruments = client.get("/api/investment/instruments", params={"provider_key": "xtb"})
        cockpit = client.get("/api/investment/cockpit")

    assert seeded.status_code == 201
    assert seeded.json()["mode"] == "paper_only"
    assert seeded.json()["real_trading_enabled"] is False
    assert seeded.json()["instrument_count"] >= 8
    assert instruments.status_code == 200
    assert instruments.json()
    assert {item["trade_mode"] for item in instruments.json()} == {"paper_only"}
    assert cockpit.json()["paper_trading_universe_count"] == seeded.json()["instrument_count"]
    assert cockpit.json()["watchlist_count"] == seeded.json()["instrument_count"]


def test_investment_watchlist_upsert(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/api/investment/watchlist",
            json={
                "symbol": "spy",
                "name": "SPDR S&P 500 ETF",
                "asset_type": "etf",
                "thesis": "Broad-market benchmark for paper portfolio tests.",
                "risk_notes": "Equity market drawdown risk.",
            },
        )
        status = client.get("/api/investment/cockpit")

    assert created.status_code == 201
    assert created.json()["symbol"] == "SPY"
    assert created.json()["asset_type"] == "etf"
    assert status.json()["watchlist_count"] == 1
    assert status.json()["watchlist"][0]["symbol"] == "SPY"


def test_investment_paper_trades_aggregate_positions(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        buy = client.post(
            "/api/investment/paper-trades",
            json={
                "symbol": "spy",
                "side": "buy",
                "quantity": 2,
                "price_pln": 100,
                "reason": "Paper test of long benchmark exposure.",
            },
        )
        sell = client.post(
            "/api/investment/paper-trades",
            json={
                "symbol": "SPY",
                "side": "sell",
                "quantity": 0.5,
                "price_pln": 120,
                "reason": "Paper test of partial exit discipline.",
            },
        )
        positions = client.get("/api/investment/paper-positions")

    assert buy.status_code == 201
    assert sell.status_code == 201
    assert positions.status_code == 200
    assert positions.json() == [
        {
            "symbol": "SPY",
            "net_quantity": 1.5,
            "average_buy_price_pln": 100,
            "gross_buy_cost_pln": 200,
            "gross_sell_value_pln": 60,
            "real_market_value_pln": None,
            "mode": "paper",
        }
    ]


def test_configured_risk_limits_block_oversized_paper_trade_and_position(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        configured = client.put(
            "/api/investment/risk-policy",
            json={
                "max_single_paper_trade_pln": 50,
                "max_paper_position_pln": 50,
                "max_daily_realized_loss_pln": 10,
                "max_weekly_realized_loss_pln": 20,
                "paper_only": True,
                "emergency_stop": False,
            },
        )
        oversized = client.post(
            "/api/investment/paper-trades",
            json={"symbol": "SPY", "side": "buy", "quantity": 1, "price_pln": 60, "reason": "Paper risk-limit oversized trade."},
        )
        first = client.post(
            "/api/investment/paper-trades",
            json={"symbol": "SPY", "side": "buy", "quantity": 1, "price_pln": 40, "reason": "Paper risk-limit allowed trade."},
        )
        position_exceeded = client.post(
            "/api/investment/paper-trades",
            json={"symbol": "SPY", "side": "buy", "quantity": 0.5, "price_pln": 40, "reason": "Paper risk-limit position test."},
        )
        status = client.get("/api/investment/cockpit")

    assert configured.status_code == 200
    assert configured.json()["policy"]["configured"] is True
    assert configured.json()["policy"]["paper_only"] is True
    assert oversized.status_code == 400
    assert "max_single_paper_trade_pln" in oversized.json()["detail"]
    assert first.status_code == 201
    assert position_exceeded.status_code == 400
    assert "max_paper_position_pln" in position_exceeded.json()["detail"]
    assert status.json()["risk_policy"]["max_single_paper_trade_pln"] == 50


def test_risk_policy_blocks_loss_limit_and_emergency_stop(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        configured = client.put(
            "/api/investment/risk-policy",
            json={
                "max_single_paper_trade_pln": 200,
                "max_paper_position_pln": 200,
                "max_daily_realized_loss_pln": 10,
                "max_weekly_realized_loss_pln": 20,
                "paper_only": True,
                "emergency_stop": False,
            },
        )
        buy = client.post(
            "/api/investment/paper-trades",
            json={"symbol": "SPY", "side": "buy", "quantity": 1, "price_pln": 100, "reason": "Paper loss-limit position entry."},
        )
        loss_sell = client.post(
            "/api/investment/paper-trades",
            json={"symbol": "SPY", "side": "sell", "quantity": 1, "price_pln": 80, "reason": "Paper loss-limit exit candidate."},
        )
        stopped = client.put(
            "/api/investment/risk-policy",
            json={
                "max_single_paper_trade_pln": 200,
                "max_paper_position_pln": 200,
                "max_daily_realized_loss_pln": 10,
                "max_weekly_realized_loss_pln": 20,
                "paper_only": True,
                "emergency_stop": True,
            },
        )
        blocked_trade = client.post(
            "/api/investment/paper-trades",
            json={"symbol": "SPY", "side": "buy", "quantity": 0.1, "price_pln": 100, "reason": "Paper emergency-stop test trade."},
        )
        autopilot = client.post("/api/investment/autopilot/run-once", json={"max_trades": 1})

    assert configured.status_code == 200
    assert buy.status_code == 201
    assert loss_sell.status_code == 400
    assert "max_daily_realized_loss_pln" in loss_sell.json()["detail"]
    assert stopped.status_code == 200
    assert stopped.json()["policy"]["emergency_stop"] is True
    assert blocked_trade.status_code == 400
    assert "emergency stop" in blocked_trade.json()["detail"]
    assert autopilot.status_code == 201
    assert autopilot.json()["reason"] == "paper_trading_emergency_stop_enabled"


def test_investment_autopilot_run_is_paper_only_and_records_decision_journal(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        run = client.post(
            "/api/investment/autopilot/run-once",
            json={"strategy_id": "test_strategy_v1", "max_trades": 2},
        )
        strategy_runs = client.get("/api/investment/strategy-runs")
        trades = client.get("/api/investment/paper-trades")
        cockpit = client.get("/api/investment/cockpit")
        real_trade = client.post(
            "/api/investment/real-trade",
            json={"symbol": "US500", "side": "buy", "quantity": 1},
        )

    assert run.status_code == 201
    payload = run.json()
    assert payload["mode"] == "paper_only"
    assert payload["status"] == "blocked"
    assert payload["reason"] == "fresh_xtb_demo_quotes_required"
    assert payload["real_trading_enabled"] is False
    assert payload["executed_trade_count"] == 0
    assert payload["safety"]["real_broker_orders_sent"] == 0
    assert payload["safety"]["uses_supplied_chat_password"] is False
    assert payload["decisions"]
    assert payload["alerts"]["required"] is True
    assert "fresh_xtb_demo_quotes_required_for_paper_autopilot" in payload["alerts"]["reasons"]
    assert strategy_runs.status_code == 200
    assert strategy_runs.json()[0]["id"] == payload["id"]
    assert trades.status_code == 200
    assert trades.json() == []
    assert cockpit.json()["capabilities"]["can_run_autonomous_paper_strategy"] is False
    assert cockpit.json()["strategy_run_count"] == 1
    assert real_trade.status_code == 403


def test_paper_ledger_records_costs_closes_position_and_stays_insufficient(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    quote_time = datetime.now(UTC).isoformat()

    with TestClient(app) as client:
        opened = client.post(
            "/api/investment/paper-trades",
            json={
                "symbol": "US500",
                "side": "buy",
                "quantity": 1,
                "price_pln": 100,
                "reason": "Verified paper entry for ledger validation.",
                "spread_pln": 0.5,
                "commission_pln": 0.1,
                "slippage_pln": 0.1,
                "price_source": "xtb_demo_quotes",
                "quote_timestamp": quote_time,
                "dataset_split": "out_of_sample",
            },
        )
        assert opened.status_code == 201
        closed = client.post(
            f"/api/investment/paper-trades/{opened.json()['id']}/close",
            json={
                "exit_price_pln": 110,
                "exit_reason": "Paper exit after the predefined validation condition.",
                "spread_pln": 0.5,
                "commission_pln": 0.1,
                "slippage_pln": 0.1,
            },
        )
        analysis = client.get("/api/investment/paper-analysis/status")

    assert closed.status_code == 200
    assert closed.json()["status"] == "paper_closed"
    assert closed.json()["gross_pnl_pln"] == 10.0
    assert closed.json()["net_pnl_pln"] == pytest.approx(8.6)
    assert closed.json()["validation_status"] == "complete"
    assert analysis.status_code == 200
    assert analysis.json()["status"] == "insufficient_data"
    assert analysis.json()["score"] is None
    assert analysis.json()["complete_closed_trade_count"] == 1
    assert analysis.json()["out_of_sample_trade_count"] == 1
    assert analysis.json()["metrics"]["net_pnl_pln"] == pytest.approx(8.6)


def test_paper_ledger_marks_unverified_manual_trade_incomplete(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/investment/paper-trades",
            json={
                "symbol": "US500",
                "side": "buy",
                "quantity": 1,
                "price_pln": 100,
                "reason": "Manual paper entry without verified market evidence.",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["validation_status"] == "incomplete"
    assert "unverified_price_source" in payload["validation_errors"]
    assert "cost_model_incomplete" in payload["validation_errors"]
    assert "dataset_split_unassigned" in payload["validation_errors"]


def test_paper_ledger_rejects_future_quote_timestamp(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    with TestClient(app) as client:
        response = client.post(
            "/api/investment/paper-trades",
            json={
                "symbol": "US500",
                "side": "buy",
                "quantity": 1,
                "price_pln": 100,
                "reason": "Future quote must be rejected by the ledger.",
                "price_source": "xtb_demo_quotes",
                "quote_timestamp": future,
                "dataset_split": "training",
            },
        )

    assert response.status_code == 400
    assert "quote_timestamp" in response.json()["detail"]


def test_scheduled_investment_autopilot_runs_due_paper_only_cycle(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        before = client.get("/api/investment/autopilot/schedule")
        run_due = client.post("/api/investment/autopilot/run-due", json={"force": False})
        after = client.get("/api/investment/autopilot/schedule")
        skipped = client.post("/api/investment/autopilot/run-due", json={"force": False})

    assert before.status_code == 200
    assert before.json()["enabled"] is True
    assert before.json()["due"] is True
    assert run_due.status_code == 201
    payload = run_due.json()
    assert payload["status"] == "blocked"
    assert payload["reason"] == "fresh_xtb_demo_quotes_required"
    assert payload["mode"] == "paper_only"
    assert payload["triggered_by"] == "scheduler"
    assert payload["real_trading_enabled"] is False
    assert payload["safety"]["real_broker_orders_sent"] == 0
    assert payload["alerts"]["required"] is True
    assert after.status_code == 200
    assert after.json()["due"] is False
    assert after.json()["last_run"]["id"] == payload["id"]
    assert skipped.status_code == 201
    assert skipped.json()["status"] == "skipped"
    assert skipped.json()["reason"] == "not_due"


def test_investment_real_trade_is_blocked(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/investment/real-trade",
            json={"symbol": "SPY", "side": "buy", "quantity": 1},
        )
        trades = client.get("/api/investment/paper-trades")

    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "blocked"
    assert response.json()["detail"]["real_trading_enabled"] is False
    assert trades.json() == []


def test_project_readiness_reports_investment_as_limited_not_placeholder(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        payload = client.get("/api/projects/readiness").json()

    investment = next(project for project in payload["projects"] if project["project_id"] == "investment:portfolio-alpha")
    assert investment["status"] == "limited"
    assert investment["capabilities"]["can_research"] is True
    assert investment["capabilities"]["can_trade_or_purchase"] is False
    assert investment["evidence"]["configured_backend"] is True
    assert investment["evidence"]["real_trading_enabled"] is False
    requirement_ids = {item["id"]: item["status"] for item in investment["requirements"]}
    assert requirement_ids["investment_cockpit"] == "ready"
    assert requirement_ids["paper_trading"] == "ready"
    assert requirement_ids["broker_read_only"] == "missing"


def test_xtb_adapter_blocks_trading_commands_before_network() -> None:
    with pytest.raises(XtbForbiddenCommandError):
        XtbReadOnlyMarketDataAdapter.assert_read_only_command("tradeTransaction")

    with pytest.raises(XtbForbiddenCommandError):
        XtbReadOnlyMarketDataAdapter.assert_read_only_command("getTrades")


def test_xtb_live_adapter_reports_retired_provider_before_network() -> None:
    adapter = XtbReadOnlyMarketDataAdapter(user_id="demo-login", password="demo-password")

    with pytest.raises(XtbApiRetiredError, match="retired on 2025-03-14"):
        __import__("asyncio").run(adapter.verify_read_only_access(symbols=["US500"]))


def test_xtb_adapter_verifies_read_only_access_with_fake_websocket() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent_commands: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def send(self, payload: str) -> None:
            self.sent_commands.append(json.loads(payload)["command"])

        async def recv(self) -> str:
            command = self.sent_commands[-1]
            if command == "login":
                return json.dumps({"status": True, "streamSessionId": "test-stream"})
            if command == "getServerTime":
                return json.dumps({"status": True, "returnData": {"time": 1710000000000}})
            if command == "getTickPrices":
                return json.dumps(
                    {
                        "status": True,
                        "returnData": [
                            {
                                "symbol": "US500",
                                "bid": 5100.1,
                                "ask": 5100.9,
                                "high": 5120.0,
                                "low": 5088.0,
                                "timestamp": 1710000001000,
                            }
                        ],
                    }
                )
            if command == "logout":
                return json.dumps({"status": True})
            raise AssertionError(f"Unexpected command: {command}")

    fake_ws = FakeWebSocket()
    adapter = XtbReadOnlyMarketDataAdapter(
        user_id="demo-login",
        password="demo-password",
        websocket_factory=lambda _url: fake_ws,
    )

    evidence = __import__("asyncio").run(adapter.verify_read_only_access(symbols=["US500"]))

    assert evidence["status"] == "read_only_verified"
    assert evidence["real_orders_enabled"] is False
    assert evidence["quote_count"] == 1
    assert evidence["quotes"][0]["symbol"] == "US500"
    assert fake_ws.sent_commands == ["login", "getServerTime", "getTickPrices", "logout"]


def test_xtb_demo_read_only_endpoint_uses_vault_and_updates_status(tmp_path: Path, monkeypatch) -> None:
    class FakeAdapter:
        def __init__(self, *, user_id: str, password: str, environment: str, timeout_s: float) -> None:
            assert user_id == "account-c5034bf4@example.invalid"
            assert password == "fake-vault-password"
            assert environment == "demo"
            self.timeout_s = timeout_s

        async def verify_read_only_access(self, *, symbols: list[str] | None = None):
            return {
                "status": "read_only_verified",
                "environment": "demo",
                "symbols": symbols or [],
                "quote_count": 1,
                "quotes": [{"symbol": "US500", "bid": 5100.1, "ask": 5100.9, "password": "should-redact"}],
                "real_orders_enabled": False,
            }

        async def get_quotes(self, symbols: list[str]):
            return [{"symbol": symbols[0], "bid": 5100.1, "ask": 5100.9}]

    monkeypatch.setattr(investment_routes, "XtbReadOnlyMarketDataAdapter", FakeAdapter)
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        account = client.post(
            "/accounts",
            json={
                "label": "XTB Demo",
                "provider_key": "xtb",
                "account_identifier": "account-c5034bf4@example.invalid",
                "secret_type": "password",
                "secret_value": "fake-vault-password",
                "risk_level": "high",
                "allowed_capabilities": ["financial_read_later"],
            },
        )
        assert account.status_code == 201
        broker = client.post(
            "/api/investment/brokers/xtb-demo",
            json={"account_identifier": "account-c5034bf4@example.invalid", "credential_account_id": account.json()["id"]},
        )
        verify = client.post(
            "/api/investment/market-data/xtb-demo/verify-read-only",
            json={"symbols": ["US500"], "timeout_s": 2.0},
        )
        status_response = client.get("/api/investment/market-data/xtb-demo/status")
        quotes = client.get("/api/investment/market-data/xtb-demo/quotes", params={"symbols": "US500"})
        cockpit = client.get("/api/investment/cockpit")

    assert broker.status_code == 201
    assert verify.status_code == 200
    assert verify.json()["status"] == "read_only_verified"
    assert verify.json()["real_trading_enabled"] is False
    assert status_response.json()["read_only_verified"] is True
    assert status_response.json()["last_check"]["status"] == "read_only_verified"
    assert status_response.json()["last_check"]["result"]["quotes"][0]["password"] == "[redacted]"
    assert quotes.status_code == 200
    assert quotes.json()["quotes"][0]["symbol"] == "US500"
    assert quotes.json()["real_trading_enabled"] is False
    assert cockpit.json()["broker_connected"] is True
    assert cockpit.json()["broker_access"] == "read_only_verified"
    assert "xtb_demo_quotes" in cockpit.json()["market_data_sources"]


def test_autopilot_uses_latest_xtb_demo_quotes_after_read_only_verify(tmp_path: Path, monkeypatch) -> None:
    class FakeAdapter:
        def __init__(self, *, user_id: str, password: str, environment: str, timeout_s: float) -> None:
            assert user_id == "account-c5034bf4@example.invalid"
            assert password == "fake-vault-password"
            assert environment == "demo"

        async def verify_read_only_access(self, *, symbols: list[str] | None = None):
            return {
                "status": "read_only_verified",
                "environment": "demo",
                "symbols": symbols or [],
                "quote_count": 1,
                "quotes": [
                    {
                        "symbol": "US500",
                        "bid": 5100.0,
                        "ask": 5102.0,
                        "high": 5120.0,
                        "low": 5080.0,
                        "timestamp": 1710000001000,
                    }
                ],
                "real_orders_enabled": False,
            }

        async def get_quotes(self, symbols: list[str]):
            return [{"symbol": symbols[0], "bid": 5100.0, "ask": 5102.0}]

    monkeypatch.setattr(investment_routes, "XtbReadOnlyMarketDataAdapter", FakeAdapter)
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        account = client.post(
            "/accounts",
            json={
                "label": "XTB Demo",
                "provider_key": "xtb",
                "account_identifier": "account-c5034bf4@example.invalid",
                "secret_type": "password",
                "secret_value": "fake-vault-password",
                "risk_level": "high",
                "allowed_capabilities": ["financial_read_later"],
            },
        )
        assert account.status_code == 201
        broker = client.post(
            "/api/investment/brokers/xtb-demo",
            json={"account_identifier": "account-c5034bf4@example.invalid", "credential_account_id": account.json()["id"]},
        )
        verify = client.post(
            "/api/investment/market-data/xtb-demo/verify-read-only",
            json={"symbols": ["US500"], "timeout_s": 2.0},
        )
        run = client.post(
            "/api/investment/autopilot/run-once",
            json={"strategy_id": "quote_driven_paper_v1", "max_trades": 2},
        )

    assert broker.status_code == 201
    assert verify.status_code == 200
    assert run.status_code == 201
    payload = run.json()
    decision_by_symbol = {item["symbol"]: item for item in payload["decisions"]}
    assert decision_by_symbol["US500"]["price_source"] == "xtb_demo_quotes"
    assert decision_by_symbol["US500"]["market_price"] == 5101.0
    assert decision_by_symbol["US500"]["quote_bid"] == 5100.0
    assert decision_by_symbol["US500"]["quote_ask"] == 5102.0
    assert decision_by_symbol["US500"]["signal_source"] == "xtb_demo_quote_range_v1_unvalidated"
    assert payload["real_trading_enabled"] is False
    assert payload["safety"]["real_broker_orders_sent"] == 0
