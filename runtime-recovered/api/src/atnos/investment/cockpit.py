from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, rows_to_dicts, utc_now_iso

PaperTradeSide = Literal["buy", "sell"]

PROJECT_ID = "investment:portfolio-alpha"
DEFAULT_BROKER_URL = "https://xstation5.xtb.com/"
ALPACA_PAPER_URL = "https://app.alpaca.markets/paper/dashboard/overview"
XTB_QUOTE_MAX_AGE = timedelta(minutes=15)


XTB_DEMO_INSTRUMENTS: tuple[dict[str, str], ...] = (
    {
        "symbol": "US500",
        "name": "S&P 500 index CFD benchmark",
        "asset_type": "index_cfd_demo",
        "market": "US",
        "currency": "USD",
        "risk_bucket": "high",
        "notes": "Demo-only market index exposure. Real CFD/leverage trading stays forbidden.",
    },
    {
        "symbol": "US100",
        "name": "Nasdaq 100 index CFD benchmark",
        "asset_type": "index_cfd_demo",
        "market": "US",
        "currency": "USD",
        "risk_bucket": "high",
        "notes": "Demo-only growth/tech index exposure. Real CFD/leverage trading stays forbidden.",
    },
    {
        "symbol": "DE40",
        "name": "DAX 40 index CFD benchmark",
        "asset_type": "index_cfd_demo",
        "market": "DE",
        "currency": "EUR",
        "risk_bucket": "high",
        "notes": "Demo-only European index monitoring.",
    },
    {
        "symbol": "EURUSD",
        "name": "EUR/USD forex pair",
        "asset_type": "forex_demo",
        "market": "FX",
        "currency": "USD",
        "risk_bucket": "high",
        "notes": "Demo-only FX signal testing. No real leveraged FX orders.",
    },
    {
        "symbol": "USDPLN",
        "name": "USD/PLN forex pair",
        "asset_type": "forex_demo",
        "market": "FX",
        "currency": "PLN",
        "risk_bucket": "high",
        "notes": "Demo-only PLN macro sensitivity monitor.",
    },
    {
        "symbol": "GOLD",
        "name": "Gold spot CFD benchmark",
        "asset_type": "commodity_cfd_demo",
        "market": "COMMODITY",
        "currency": "USD",
        "risk_bucket": "high",
        "notes": "Demo-only commodity hedge signal testing.",
    },
    {
        "symbol": "OIL.WTI",
        "name": "WTI crude oil CFD benchmark",
        "asset_type": "commodity_cfd_demo",
        "market": "COMMODITY",
        "currency": "USD",
        "risk_bucket": "high",
        "notes": "Demo-only macro/news sensitivity monitor.",
    },
    {
        "symbol": "BTCUSD",
        "name": "Bitcoin / USD demo market",
        "asset_type": "crypto_demo",
        "market": "CRYPTO",
        "currency": "USD",
        "risk_bucket": "very_high",
        "notes": "Demo-only volatility test. No real crypto leverage.",
    },
    {
        "symbol": "AAPL.US",
        "name": "Apple Inc.",
        "asset_type": "stock_demo",
        "market": "US",
        "currency": "USD",
        "risk_bucket": "medium",
        "notes": "Demo-only large-cap stock observation.",
    },
    {
        "symbol": "MSFT.US",
        "name": "Microsoft Corp.",
        "asset_type": "stock_demo",
        "market": "US",
        "currency": "USD",
        "risk_bucket": "medium",
        "notes": "Demo-only large-cap stock observation.",
    },
)


class InvestmentCockpitService:
    """Safe investment cockpit: research, watchlist and paper trades only."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        """Return current safe investment status for Portfolio Alpha."""
        self._ensure_schema()
        from atnos.investment.education_sources import InvestmentEducationSourceRegistry
        from atnos.investment.education_research import InvestmentEducationResearchService

        portfolio_settings = self.get_portfolio_settings()
        watchlist = self.list_watchlist()
        paper_trades = self.list_paper_trades(limit=50)
        positions = self.paper_positions()
        risk_policy = self.risk_policy()
        risk_metrics = self.paper_loss_metrics()
        brokers = self.list_broker_accounts()
        instruments = self.list_instruments(enabled_only=True)
        broker_status = self._broker_status_from_accounts(brokers, portfolio_settings=portfolio_settings)
        market_data_sources = list(portfolio_settings.get("market_data_sources") or [])
        learning_resources = list(portfolio_settings.get("learning_resources") or [])
        education_sources = InvestmentEducationSourceRegistry(self.settings).list_sources()
        education_reports = InvestmentEducationResearchService(self.settings).list_reports(limit=3)
        verified_education_source_count = sum(
            1 for item in education_sources if item.get("verification_status") == "available"
        )
        fresh_quote_prices = self._latest_xtb_demo_quote_prices()
        market_data_status = "configured" if market_data_sources else "manual_or_unconfigured"
        blockers = [
            "real_trading_disabled",
        ]
        if not market_data_sources:
            blockers.insert(0, "market_data_provider_not_configured")
        if broker_status["access"] == "not_registered":
            blockers.insert(0, "xtb_demo_not_registered")
        elif broker_status["access"] != "read_only_verified":
            blockers.insert(0, "xtb_demo_not_verified_read_only")
        if not fresh_quote_prices:
            blockers.insert(0, "fresh_xtb_demo_quotes_required_for_paper_autopilot")
        if bool(risk_policy.get("emergency_stop")):
            blockers.insert(0, "paper_trading_emergency_stop_enabled")
        return {
            "project_id": PROJECT_ID,
            "mode": "paper_trading_only",
            "status": "limited",
            "generated_at": utc_now_iso(),
            "real_trading_enabled": False,
            "broker_connected": broker_status["access"] == "read_only_verified",
            "broker_access": broker_status["access"],
            "brokers": brokers,
            "portfolio_settings": portfolio_settings,
            "market_data_status": market_data_status,
            "market_data_sources": market_data_sources,
            "learning_resources": learning_resources,
            "education_sources": education_sources,
            "verified_education_source_count": verified_education_source_count,
            "education_reports": education_reports,
            "paper_trading_universe_count": len(instruments),
            "watchlist": watchlist,
            "watchlist_count": len(watchlist),
            "paper_positions": positions,
            "paper_position_count": len(positions),
            "paper_trades": paper_trades,
            "paper_trade_count": len(paper_trades),
            "strategy_runs": self.list_strategy_runs(limit=5),
            "strategy_run_count": self.count_strategy_runs(),
            "autopilot_schedule": self.autopilot_schedule_status(),
            "risk_policy": risk_policy,
            "risk_metrics": risk_metrics,
            "blockers": blockers,
            "capabilities": {
                "can_research": True,
                "can_manage_watchlist": True,
                "can_paper_trade": True,
                "can_run_autonomous_paper_strategy": bool(fresh_quote_prices) and not bool(risk_policy.get("emergency_stop")),
                "can_manage_demo_broker_metadata": True,
                "can_manage_portfolio_settings": True,
                "can_seed_paper_instruments": True,
                "can_configure_market_data_sources": True,
                "can_trade_real_money": False,
                "can_use_card_or_bank": False,
            },
            "next_actions": [
                "Register XTB demo metadata and rotate the password through the encrypted account vault.",
                "Save Portfolio Alpha settings with a credential vault reference, not a password.",
                "Add market-data sources and learning resources for the investment agent.",
                "Register and verify public education sources before asking Atnos for a cited research summary.",
                "Configure hard paper-risk limits and keep the emergency stop available.",
                "Seed XTB demo instruments into the paper-only universe.",
                "Add real watchlist ideas with thesis and risk notes.",
                "Record paper trades only after a written reason.",
                "Refresh verified XTB demo quotes before running an autonomous paper strategy session.",
                "Run autonomous paper strategy sessions and review the decision journal.",
                "Verify broker read-only access before any automatic market polling.",
                "Keep every real-money order disabled until a separate risk policy and approval flow exists.",
            ],
            "safety": {
                "card_data_storage": "forbidden",
                "real_orders": "blocked",
                "leverage": "forbidden",
                "derivatives": "forbidden_for_mvp",
                "autonomous_strategy_mode": "paper_only",
                "owner_approval_required": True,
            },
        }

    def list_watchlist(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT symbol, name, asset_type, thesis, risk_notes, created_at, updated_at
                FROM investment_watchlist
                ORDER BY updated_at DESC, symbol ASC
                """
            ).fetchall()
        return rows_to_dicts(rows)

    def list_broker_accounts(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT provider_key, label, account_identifier, environment, access_mode,
                       status, credential_account_id, login_url, notes, created_at, updated_at
                FROM investment_broker_accounts
                ORDER BY provider_key ASC
                """
            ).fetchall()
        return rows_to_dicts(rows)

    def get_portfolio_settings(self) -> dict[str, Any]:
        """Return Portfolio Alpha settings without secrets."""
        self._ensure_schema()
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT project_id, broker_name, broker_portal_url, broker_account_identifier,
                       credential_vault_ref, broker_read_access, account_mode,
                       market_data_sources_json, learning_resources_json, monthly_spend_budget_pln,
                       payment_method_ref, payment_method_configured, real_trading_blocked,
                       trade_approval_rules_enabled, created_at, updated_at
                FROM investment_project_settings
                WHERE project_id = ?
                """,
                (PROJECT_ID,),
            ).fetchone()
        if row is None:
            return self._default_portfolio_settings()
        item = dict(row)
        item["market_data_sources"] = self._json_list(item.pop("market_data_sources_json"))
        item["learning_resources"] = self._json_list(item.pop("learning_resources_json"))
        item["payment_method_configured"] = bool(item.get("payment_method_configured"))
        item["real_trading_blocked"] = bool(item.get("real_trading_blocked"))
        item["trade_approval_rules_enabled"] = bool(item.get("trade_approval_rules_enabled"))
        item["monthly_spend_budget_pln"] = float(item.get("monthly_spend_budget_pln") or 0.0)
        item["stores_plaintext_secret"] = False
        return item

    def configure_portfolio_settings(
        self,
        *,
        broker_name: str = "XTB Demo",
        broker_portal_url: str = DEFAULT_BROKER_URL,
        broker_account_identifier: str = "",
        credential_vault_ref: str = "",
        broker_read_access: str = "not_configured",
        account_mode: str = "demo_paper",
        market_data_sources: list[str] | None = None,
        learning_resources: list[str] | None = None,
        monthly_spend_budget_pln: float = 0.0,
        payment_method_ref: str = "",
        payment_method_configured: bool = False,
        real_trading_blocked: bool = True,
        trade_approval_rules_enabled: bool = False,
    ) -> dict[str, Any]:
        """Save Portfolio Alpha settings without storing passwords or card numbers."""
        self._ensure_schema()
        safe_broker_name = self._safe_text(broker_name, max_len=120, default="XTB Demo")
        safe_url = self._safe_https_url(broker_portal_url)
        safe_account_identifier = self._safe_text(broker_account_identifier, max_len=240, default="")
        safe_vault_ref = self._safe_reference(credential_vault_ref, "credential_vault_ref", allow_empty=True)
        safe_payment_ref = self._safe_reference(payment_method_ref, "payment_method_ref", allow_empty=True)
        safe_read_access = self._normalize_broker_read_access(broker_read_access)
        if safe_read_access == "read_only_verified":
            safe_read_access = "read_only_candidate"
        safe_account_mode = self._normalize_account_mode(account_mode)
        safe_market_sources = self._safe_list(market_data_sources or [], max_items=20, max_item_len=180)
        safe_learning = self._safe_list(learning_resources or [], max_items=50, max_item_len=500)
        safe_budget = max(0.0, min(float(monthly_spend_budget_pln or 0.0), 100_000.0))
        safe_payment_configured = bool(payment_method_configured and safe_payment_ref)
        safe_real_blocked = True if not real_trading_blocked else bool(real_trading_blocked)
        safe_trade_rules = bool(trade_approval_rules_enabled)

        if safe_read_access == "read_only_verified" and not safe_vault_ref:
            raise ValueError("Read-only verification requires credential_vault_ref, not a plaintext password.")

        now = utc_now_iso()
        with connect(self.settings) as db:
            existing = db.execute(
                "SELECT created_at FROM investment_project_settings WHERE project_id = ?",
                (PROJECT_ID,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """
                INSERT INTO investment_project_settings (
                    project_id, broker_name, broker_portal_url, broker_account_identifier,
                    credential_vault_ref, broker_read_access, account_mode,
                    market_data_sources_json, learning_resources_json, monthly_spend_budget_pln,
                    payment_method_ref, payment_method_configured, real_trading_blocked,
                    trade_approval_rules_enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    broker_name = excluded.broker_name,
                    broker_portal_url = excluded.broker_portal_url,
                    broker_account_identifier = excluded.broker_account_identifier,
                    credential_vault_ref = excluded.credential_vault_ref,
                    broker_read_access = excluded.broker_read_access,
                    account_mode = excluded.account_mode,
                    market_data_sources_json = excluded.market_data_sources_json,
                    learning_resources_json = excluded.learning_resources_json,
                    monthly_spend_budget_pln = excluded.monthly_spend_budget_pln,
                    payment_method_ref = excluded.payment_method_ref,
                    payment_method_configured = excluded.payment_method_configured,
                    real_trading_blocked = excluded.real_trading_blocked,
                    trade_approval_rules_enabled = excluded.trade_approval_rules_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    PROJECT_ID,
                    safe_broker_name,
                    safe_url,
                    safe_account_identifier,
                    safe_vault_ref,
                    safe_read_access,
                    safe_account_mode,
                    json.dumps(safe_market_sources, ensure_ascii=False),
                    json.dumps(safe_learning, ensure_ascii=False),
                    safe_budget,
                    safe_payment_ref,
                    1 if safe_payment_configured else 0,
                    1 if safe_real_blocked else 0,
                    1 if safe_trade_rules else 0,
                    created_at,
                    now,
                ),
            )
            db.commit()

        if safe_account_identifier:
            self.configure_xtb_demo_account(
                account_identifier=safe_account_identifier,
                credential_account_id=None,
            )
        self._sync_xtb_read_access_status(safe_read_access)
        return self.get_portfolio_settings()

    def add_learning_resources(self, resources: list[str]) -> dict[str, Any]:
        """Dołącz zweryfikowane adresy bez nadpisywania ustawień Portfolio Alpha."""
        current = self.get_portfolio_settings()
        merged = list(dict.fromkeys([*(current.get("learning_resources") or []), *resources]))
        if current.get("updated_at") is None:
            return self.configure_portfolio_settings(learning_resources=merged)
        with connect(self.settings) as db:
            db.execute(
                """
                UPDATE investment_project_settings
                SET learning_resources_json = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (json.dumps(self._safe_list(merged, max_items=50, max_item_len=500), ensure_ascii=False), utc_now_iso(), PROJECT_ID),
            )
            db.commit()
        return self.get_portfolio_settings()

    def configure_xtb_demo_account(
        self,
        *,
        account_identifier: str,
        credential_account_id: int | None = None,
    ) -> dict[str, Any]:
        """Register XTB demo metadata without storing a plaintext password."""
        self._ensure_schema()
        normalized_identifier = account_identifier.strip().lower()
        if "@" not in normalized_identifier or len(normalized_identifier) > 240:
            raise ValueError("XTB demo account identifier must be a valid email-like login.")
        now = utc_now_iso()
        with connect(self.settings) as db:
            existing = db.execute(
                "SELECT created_at FROM investment_broker_accounts WHERE provider_key = 'xtb'",
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """
                INSERT INTO investment_broker_accounts (
                    provider_key, label, account_identifier, environment, access_mode, status,
                    credential_account_id, login_url, notes, created_at, updated_at
                )
                VALUES ('xtb', 'XTB Demo', ?, 'demo', 'read_only_candidate', 'metadata_registered',
                        ?, ?,
                        'Demo account with artificial funds. Password must be stored only through the encrypted account vault.',
                        ?, ?)
                ON CONFLICT(provider_key) DO UPDATE SET
                    account_identifier = excluded.account_identifier,
                    credential_account_id = COALESCE(
                        excluded.credential_account_id,
                        investment_broker_accounts.credential_account_id
                    ),
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (normalized_identifier, credential_account_id, DEFAULT_BROKER_URL, created_at, now),
            )
            db.commit()
        return self.get_broker_account("xtb")

    def configure_alpaca_paper_account(
        self,
        *,
        credential_account_id: int,
    ) -> dict[str, Any]:
        """Zarejestruj klucz Alpaca Paper; sekret pozostaje w lokalnym vaulcie."""
        self._ensure_schema()
        if int(credential_account_id or 0) <= 0:
            raise ValueError("Alpaca Paper credential_account_id is required.")
        account_label = "alpaca-paper-primary"
        now = utc_now_iso()
        with connect(self.settings) as db:
            existing = db.execute(
                "SELECT created_at FROM investment_broker_accounts WHERE provider_key = 'alpaca-paper'"
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """
                INSERT INTO investment_broker_accounts (
                    provider_key, label, account_identifier, environment, access_mode, status,
                    credential_account_id, login_url, notes, created_at, updated_at
                )
                VALUES ('alpaca-paper', 'Alpaca Paper', ?, 'paper', 'read_only_candidate',
                        'metadata_registered', ?, ?,
                        'Paper API only. Secret is stored in the local vault. Live orders are disabled.',
                        ?, ?)
                ON CONFLICT(provider_key) DO UPDATE SET
                    account_identifier = excluded.account_identifier,
                    credential_account_id = excluded.credential_account_id,
                    access_mode = 'read_only_candidate',
                    status = 'metadata_registered',
                    login_url = excluded.login_url,
                    updated_at = excluded.updated_at
                """,
                (account_label, int(credential_account_id), ALPACA_PAPER_URL, created_at, now),
            )
            db.execute(
                """
                INSERT INTO investment_project_settings (
                    project_id, broker_name, broker_portal_url, broker_account_identifier,
                    credential_vault_ref, broker_read_access, account_mode,
                    market_data_sources_json, learning_resources_json, monthly_spend_budget_pln,
                    payment_method_ref, payment_method_configured, real_trading_blocked,
                    trade_approval_rules_enabled, created_at, updated_at
                )
                VALUES (?, 'Alpaca Paper', ?, ?, ?, 'read_only_candidate', 'demo_paper',
                        '[]', '[]', 0, '', 0, 1, 0, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    broker_name = 'Alpaca Paper',
                    broker_portal_url = excluded.broker_portal_url,
                    broker_account_identifier = excluded.broker_account_identifier,
                    credential_vault_ref = excluded.credential_vault_ref,
                    broker_read_access = 'read_only_candidate',
                    account_mode = 'demo_paper',
                    real_trading_blocked = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    PROJECT_ID,
                    ALPACA_PAPER_URL,
                    account_label,
                    f"account-vault:{int(credential_account_id)}",
                    now,
                    now,
                ),
            )
            db.commit()
        return self.get_broker_account("alpaca-paper")

    def get_broker_account(self, provider_key: str) -> dict[str, Any]:
        self._ensure_schema()
        normalized = str(provider_key or "").strip().lower()
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT provider_key, label, account_identifier, environment, access_mode,
                       status, credential_account_id, login_url, notes, created_at, updated_at
                FROM investment_broker_accounts
                WHERE provider_key = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown investment broker account: {normalized}")
        return dict(row)

    def xtb_market_data_status(self) -> dict[str, Any]:
        """Return XTB read-only market-data wiring status without secrets."""
        from atnos.investment.xtb_market_data import (
            XTB_XAPI_RETIRED_AT,
            XTB_XAPI_RETIREMENT_SOURCE,
        )

        self._ensure_schema()
        try:
            broker = self.get_broker_account("xtb")
        except ValueError:
            broker = {}
        portfolio = self.get_portfolio_settings()
        last_check = self._last_market_data_check("xtb")
        credential_account_id = broker.get("credential_account_id")
        return {
            "provider_key": "xtb",
            "environment": str(broker.get("environment") or "demo"),
            "api_available": False,
            "provider_status": "blocked_provider_api_retired",
            "api_retired_at": XTB_XAPI_RETIRED_AT,
            "retirement_source": XTB_XAPI_RETIREMENT_SOURCE,
            "registered": bool(broker),
            "account_identifier": str(broker.get("account_identifier") or ""),
            "credential_account_id": credential_account_id,
            "has_credential_ref": bool(credential_account_id or portfolio.get("credential_vault_ref")),
            "broker_read_access": str(portfolio.get("broker_read_access") or "not_configured"),
            "broker_status": str(broker.get("status") or "not_registered"),
            "read_only_verified": str(portfolio.get("broker_read_access") or "") == "read_only_verified",
            "last_check": last_check,
            "allowed_commands": [
                "login",
                "logout",
                "getServerTime",
                "getAllSymbols",
                "getSymbol",
                "getTickPrices",
                "getChartRangeRequest",
            ],
            "forbidden_trading_commands": [
                "tradeTransaction",
                "tradeTransactionStatus",
                "getTrades",
                "getTradesHistory",
                "getProfitCalculation",
                "getMarginTrade",
                "getMarginLevel",
            ],
            "real_trading_enabled": False,
            "next_action": "Keep XTB Demo manual; configure a supported read-only market-data provider for paper trading.",
        }

    def alpaca_paper_status(self) -> dict[str, Any]:
        """Zwracaj stan Alpaca Paper bez ujawniania klucza i sekretu."""
        self._ensure_schema()
        try:
            broker = self.get_broker_account("alpaca-paper")
        except ValueError:
            broker = {}
        last_check = self._last_market_data_check("alpaca-paper")
        credential_account_id = broker.get("credential_account_id")
        return {
            "provider_key": "alpaca-paper",
            "environment": "paper",
            "api_available": True,
            "provider_status": "ready" if last_check and last_check.get("status") == "read_only_verified" else "configuration_required",
            "registered": bool(broker),
            "credential_account_id": credential_account_id,
            "has_credential_ref": bool(credential_account_id),
            "broker_status": str(broker.get("status") or "not_registered"),
            "read_only_verified": bool(last_check and last_check.get("status") == "read_only_verified"),
            "last_check": last_check,
            "allowed_operations": ["get_account", "get_positions", "get_latest_quotes"],
            "forbidden_operations": ["place_order", "replace_order", "cancel_order", "close_position"],
            "real_trading_enabled": False,
            "next_action": (
                "Alpaca Paper read-only is verified."
                if last_check and last_check.get("status") == "read_only_verified"
                else "Store a Paper API key and secret in the local vault, then run verification."
            ),
        }

    def record_alpaca_read_only_check(
        self,
        *,
        status: str,
        symbols: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Zapisz dowod testu Alpaca Paper bez sekretow i bez mozliwosci live trade."""
        self._ensure_schema()
        check_id = f"alpaca_check_{uuid4().hex}"
        now = utc_now_iso()
        safe_status = "read_only_verified" if status == "read_only_verified" else "failed"
        safe_symbols = [self._normalize_symbol(symbol) for symbol in symbols[:50]]
        sanitized_result = self._sanitize_market_data_result(result)
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO investment_market_data_checks (
                    id, created_at, provider_key, environment, status, symbols_json,
                    result_json, real_orders_enabled
                )
                VALUES (?, ?, 'alpaca-paper', 'paper', ?, ?, ?, 0)
                """,
                (
                    check_id,
                    now,
                    safe_status,
                    json.dumps(safe_symbols, ensure_ascii=False),
                    json.dumps(sanitized_result, ensure_ascii=False),
                ),
            )
            if safe_status == "read_only_verified":
                db.execute(
                    """
                    UPDATE investment_broker_accounts
                    SET access_mode = 'read_only', status = 'read_only_verified', updated_at = ?
                    WHERE provider_key = 'alpaca-paper'
                    """,
                    (now,),
                )
                settings_row = db.execute(
                    "SELECT market_data_sources_json FROM investment_project_settings WHERE project_id = ?",
                    (PROJECT_ID,),
                ).fetchone()
                sources = self._json_list(settings_row["market_data_sources_json"] if settings_row else "[]")
                if "alpaca_iex_quotes" not in sources:
                    sources.append("alpaca_iex_quotes")
                db.execute(
                    """
                    UPDATE investment_project_settings
                    SET broker_read_access = 'read_only_verified',
                        account_mode = 'demo_read_only',
                        market_data_sources_json = ?,
                        real_trading_blocked = 1,
                        updated_at = ?
                    WHERE project_id = ?
                    """,
                    (json.dumps(sources, ensure_ascii=False), now, PROJECT_ID),
                )
            db.commit()
        return self._last_market_data_check("alpaca-paper") or {
            "id": check_id,
            "provider_key": "alpaca-paper",
            "status": safe_status,
            "symbols": safe_symbols,
        }

    def record_xtb_read_only_check(
        self,
        *,
        status: str,
        symbols: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Persistuj wynik testu adaptera XTB bez sekretów."""
        self._ensure_schema()
        check_id = f"xtb_check_{uuid4().hex}"
        now = utc_now_iso()
        safe_status = "read_only_verified" if status == "read_only_verified" else "failed"
        safe_symbols = [self._normalize_symbol(symbol) for symbol in symbols[:20]]
        sanitized_result = self._sanitize_market_data_result(result)
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO investment_market_data_checks (
                    id, created_at, provider_key, environment, status, symbols_json,
                    result_json, real_orders_enabled
                )
                VALUES (?, ?, 'xtb', 'demo', ?, ?, ?, 0)
                """,
                (
                    check_id,
                    now,
                    safe_status,
                    json.dumps(safe_symbols, ensure_ascii=False),
                    json.dumps(sanitized_result, ensure_ascii=False),
                ),
            )
            if safe_status == "read_only_verified":
                db.execute(
                    """
                    UPDATE investment_broker_accounts
                    SET access_mode = 'read_only', status = 'read_only_verified', updated_at = ?
                    WHERE provider_key = 'xtb'
                    """,
                    (now,),
                )
                settings_row = db.execute(
                    """
                    SELECT market_data_sources_json FROM investment_project_settings
                    WHERE project_id = ?
                    """,
                    (PROJECT_ID,),
                ).fetchone()
                sources = self._json_list(settings_row["market_data_sources_json"] if settings_row else "[]")
                if "xtb_demo_quotes" not in sources:
                    sources.append("xtb_demo_quotes")
                db.execute(
                    """
                    INSERT INTO investment_project_settings (
                        project_id, broker_name, broker_portal_url, broker_account_identifier,
                        credential_vault_ref, broker_read_access, account_mode,
                        market_data_sources_json, learning_resources_json, monthly_spend_budget_pln,
                        payment_method_ref, payment_method_configured, real_trading_blocked,
                        trade_approval_rules_enabled, created_at, updated_at
                    )
                    VALUES (?, 'XTB Demo', ?, '', '', 'read_only_verified', 'demo_read_only',
                            ?, '[]', 0, '', 0, 1, 0, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        broker_read_access = 'read_only_verified',
                        account_mode = CASE
                            WHEN account_mode = 'live_read_only' THEN account_mode
                            ELSE 'demo_read_only'
                        END,
                        market_data_sources_json = excluded.market_data_sources_json,
                        real_trading_blocked = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        PROJECT_ID,
                        DEFAULT_BROKER_URL,
                        json.dumps(sources, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            db.commit()
        return self._last_market_data_check("xtb") or {
            "id": check_id,
            "provider_key": "xtb",
            "status": safe_status,
            "symbols": safe_symbols,
        }

    def _last_market_data_check(self, provider_key: str) -> dict[str, Any] | None:
        self._ensure_schema()
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT id, created_at, provider_key, environment, status, symbols_json,
                       result_json, real_orders_enabled
                FROM investment_market_data_checks
                WHERE provider_key = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (str(provider_key or "").strip().lower(),),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["symbols"] = self._json_list(item.pop("symbols_json"))
        parsed = json.loads(str(item.pop("result_json") or "{}"))
        item["result"] = parsed if isinstance(parsed, dict) else {}
        item["real_orders_enabled"] = bool(item.get("real_orders_enabled"))
        return item

    def _latest_xtb_demo_quote_prices(self) -> dict[str, dict[str, Any]]:
        """Return only fresh prices observed through a verified XTB demo read-only check."""
        last_check = self._last_market_data_check("xtb")
        if not last_check or last_check.get("status") != "read_only_verified":
            return {}
        checked_at = self._parse_datetime(last_check.get("created_at"))
        if checked_at is None or self._utcnow() - checked_at > XTB_QUOTE_MAX_AGE:
            return {}
        result = last_check.get("result")
        if not isinstance(result, dict) or result.get("real_orders_enabled") is True:
            return {}
        raw_quotes = result.get("quotes")
        if not isinstance(raw_quotes, list):
            return {}

        prices: dict[str, dict[str, Any]] = {}
        for raw_quote in raw_quotes:
            if not isinstance(raw_quote, dict):
                continue
            try:
                symbol = self._normalize_symbol(str(raw_quote.get("symbol") or ""))
            except ValueError:
                continue
            market_price = self._paper_market_price_from_quote(raw_quote)
            if market_price is None:
                continue
            prices[symbol] = {
                "market_price": market_price,
                "bid": self._quote_number(raw_quote.get("bid")),
                "ask": self._quote_number(raw_quote.get("ask")),
                "high": self._quote_number(raw_quote.get("high")),
                "low": self._quote_number(raw_quote.get("low")),
                "timestamp": raw_quote.get("timestamp"),
                "checked_at": last_check.get("created_at"),
            }
        return prices

    def seed_xtb_demo_instruments(self, *, also_add_to_watchlist: bool = True) -> dict[str, Any]:
        """Seed a safe paper-only XTB demo universe."""
        self._ensure_schema()
        now = utc_now_iso()
        upserted_symbols: list[str] = []
        with connect(self.settings) as db:
            for item in XTB_DEMO_INSTRUMENTS:
                symbol = self._normalize_symbol(item["symbol"])
                existing = db.execute(
                    "SELECT created_at FROM investment_instruments WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                created_at = str(existing["created_at"]) if existing else now
                db.execute(
                    """
                    INSERT INTO investment_instruments (
                        symbol, name, asset_type, market, currency, provider_key, trade_mode,
                        risk_bucket, enabled, notes, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'xtb', 'paper_only', ?, 1, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        name = excluded.name,
                        asset_type = excluded.asset_type,
                        market = excluded.market,
                        currency = excluded.currency,
                        provider_key = excluded.provider_key,
                        trade_mode = excluded.trade_mode,
                        risk_bucket = excluded.risk_bucket,
                        enabled = excluded.enabled,
                        notes = excluded.notes,
                        updated_at = excluded.updated_at
                    """,
                    (
                        symbol,
                        item["name"],
                        item["asset_type"],
                        item["market"],
                        item["currency"],
                        item["risk_bucket"],
                        item["notes"],
                        created_at,
                        now,
                    ),
                )
                upserted_symbols.append(symbol)
            db.commit()

        if also_add_to_watchlist:
            for item in XTB_DEMO_INSTRUMENTS:
                self.add_watchlist_item(
                    symbol=item["symbol"],
                    name=item["name"],
                    asset_type="paper_test",
                    thesis=f"Demo-only signal test for {item['name']}.",
                    risk_notes=f"{item['notes']} Real execution is blocked.",
                )
        return {
            "status": "seeded",
            "provider_key": "xtb",
            "mode": "paper_only",
            "instrument_count": len(upserted_symbols),
            "symbols": upserted_symbols,
            "watchlist_updated": bool(also_add_to_watchlist),
            "real_trading_enabled": False,
        }

    def list_instruments(
        self,
        *,
        provider_key: str | None = None,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses: list[str] = []
        params: list[Any] = []
        if provider_key:
            clauses.append("provider_key = ?")
            params.append(provider_key.strip().lower())
        if enabled_only:
            clauses.append("enabled = 1")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with connect(self.settings) as db:
            rows = db.execute(
                f"""
                SELECT symbol, name, asset_type, market, currency, provider_key, trade_mode,
                       risk_bucket, enabled, notes, created_at, updated_at
                FROM investment_instruments
                {where}
                ORDER BY provider_key ASC, market ASC, symbol ASC
                """,
                tuple(params),
            ).fetchall()
        return rows_to_dicts(rows)

    def add_watchlist_item(
        self,
        *,
        symbol: str,
        name: str | None = None,
        asset_type: str = "stock",
        thesis: str = "",
        risk_notes: str = "",
    ) -> dict[str, Any]:
        self._ensure_schema()
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_asset_type = self._normalize_asset_type(asset_type)
        now = utc_now_iso()
        with connect(self.settings) as db:
            existing = db.execute(
                "SELECT created_at FROM investment_watchlist WHERE symbol = ?",
                (normalized_symbol,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """
                INSERT INTO investment_watchlist (
                    symbol, name, asset_type, thesis, risk_notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    asset_type = excluded.asset_type,
                    thesis = excluded.thesis,
                    risk_notes = excluded.risk_notes,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_symbol,
                    (name or "").strip(),
                    normalized_asset_type,
                    thesis.strip(),
                    risk_notes.strip(),
                    created_at,
                    now,
                ),
            )
            db.commit()
        return self.get_watchlist_item(normalized_symbol)

    def get_watchlist_item(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT symbol, name, asset_type, thesis, risk_notes, created_at, updated_at
                FROM investment_watchlist
                WHERE symbol = ?
                """,
                (normalized_symbol,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown watchlist symbol: {normalized_symbol}")
        return dict(row)

    def list_paper_trades(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        safe_limit = max(1, min(int(limit or 100), 500))
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT id, created_at, updated_at, symbol, side, quantity, price_pln, reason, status,
                       entry_time, entry_price_pln, position_size, spread_pln, commission_pln,
                       slippage_pln, exit_time, exit_price_pln, exit_reason, gross_pnl_pln,
                       net_pnl_pln, price_source, quote_timestamp, dataset_split,
                       validation_status, validation_errors_json
                FROM investment_paper_trades
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        items = rows_to_dicts(rows)
        for item in items:
            item["validation_errors"] = json.loads(str(item.pop("validation_errors_json") or "[]"))
        return items

    def list_strategy_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_schema()
        safe_limit = max(1, min(int(limit or 50), 200))
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT id, created_at, strategy_id, mode, status, thesis, decisions_json,
                       executed_trade_count, rejected_trade_count, real_trading_enabled
                FROM investment_strategy_runs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        items = rows_to_dicts(rows)
        for item in items:
            item["decisions"] = json.loads(str(item.pop("decisions_json") or "[]"))
            item["real_trading_enabled"] = bool(item.get("real_trading_enabled"))
        return items

    def count_strategy_runs(self) -> int:
        self._ensure_schema()
        with connect(self.settings) as db:
            row = db.execute("SELECT COUNT(*) AS count FROM investment_strategy_runs").fetchone()
        return int(row["count"] if row else 0)

    def _last_strategy_run(self, strategy_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        normalized_strategy_id = self._normalize_strategy_id(strategy_id)
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT id, created_at, strategy_id, mode, status, thesis, decisions_json,
                       executed_trade_count, rejected_trade_count, real_trading_enabled
                FROM investment_strategy_runs
                WHERE strategy_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (normalized_strategy_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["decisions"] = json.loads(str(item.pop("decisions_json") or "[]"))
        item["real_trading_enabled"] = bool(item.get("real_trading_enabled"))
        return item

    def autopilot_schedule_status(self) -> dict[str, Any]:
        """Return schedule status for safe paper-only investment autopilot."""
        self._ensure_schema()
        interval_minutes = int(self.settings.investment_paper_autopilot_interval_minutes)
        strategy_id = "scheduled_portfolio_alpha_v1"
        last_run = self._last_strategy_run(strategy_id)
        last_run_at = self._parse_datetime(last_run.get("created_at") if last_run else None)
        next_due_at = (last_run_at + timedelta(minutes=interval_minutes)) if last_run_at else self._utcnow()
        due = self._utcnow() >= next_due_at
        enabled = bool(self.settings.investment_paper_autopilot_enabled)
        return {
            "enabled": enabled,
            "strategy_id": strategy_id,
            "interval_minutes": interval_minutes,
            "max_trades_per_tick": int(self.settings.investment_paper_autopilot_max_trades_per_tick),
            "alerts_enabled": bool(self.settings.investment_paper_autopilot_alerts_enabled),
            "alert_on_fallback": bool(self.settings.investment_paper_autopilot_alert_on_fallback),
            "alert_on_unusual_decision": bool(self.settings.investment_paper_autopilot_alert_on_unusual_decision),
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "next_due_at": next_due_at.isoformat(),
            "due": bool(enabled and due),
            "last_run": last_run,
            "real_trading_enabled": False,
        }

    def run_scheduled_paper_autopilot(self, *, force: bool = False) -> dict[str, Any]:
        """Run scheduled paper autopilot if due. Never sends real broker orders."""
        schedule = self.autopilot_schedule_status()
        if not bool(schedule["enabled"]) and not force:
            return {
                "status": "skipped",
                "reason": "investment_paper_autopilot_disabled",
                "schedule": schedule,
                "real_trading_enabled": False,
            }
        if not bool(schedule["due"]) and not force:
            return {
                "status": "skipped",
                "reason": "not_due",
                "schedule": schedule,
                "real_trading_enabled": False,
            }
        run = self.run_autonomous_paper_session(
            strategy_id=str(schedule["strategy_id"]),
            max_trades=int(schedule["max_trades_per_tick"]),
        )
        run["triggered_by"] = "scheduler_force" if force else "scheduler"
        run["schedule"] = self.autopilot_schedule_status()
        return run

    def run_autonomous_paper_session(
        self,
        *,
        strategy_id: str = "baseline_signal_journal_v1",
        max_trades: int = 3,
    ) -> dict[str, Any]:
        """Run one autonomous trading-training session in paper-only mode."""
        self._ensure_schema()
        instruments = self.list_instruments(provider_key="xtb", enabled_only=True)
        if not instruments:
            self.seed_xtb_demo_instruments(also_add_to_watchlist=True)
            instruments = self.list_instruments(provider_key="xtb", enabled_only=True)

        normalized_strategy_id = self._normalize_strategy_id(strategy_id)
        safe_max_trades = max(1, min(int(max_trades or 3), 10))
        risk_policy = self.risk_policy()
        if bool(risk_policy.get("emergency_stop")):
            return self._record_blocked_paper_run(
                strategy_id=normalized_strategy_id,
                reason="Paper trading emergency stop is enabled. No paper decision or trade was generated.",
                reason_code="paper_trading_emergency_stop_enabled",
                alert_code="paper_trading_emergency_stop_enabled",
            )
        positions = {item["symbol"]: item for item in self.paper_positions()}
        xtb_quote_prices = self._latest_xtb_demo_quote_prices()
        if not xtb_quote_prices:
            return self._record_blocked_paper_run(
                strategy_id=normalized_strategy_id,
                reason=(
                    "Autonomous paper strategy requires a fresh XTB demo quote snapshot from a verified "
                    "read-only check. No synthetic market data or decisions were generated."
                ),
            )
        decisions: list[dict[str, Any]] = []
        executed_trade_ids: list[str] = []
        now = utc_now_iso()

        for instrument in instruments:
            symbol = str(instrument["symbol"])
            quote_price = xtb_quote_prices.get(symbol)
            if quote_price is None:
                decisions.append(
                    {
                        "symbol": symbol,
                        "name": instrument.get("name"),
                        "risk_bucket": instrument.get("risk_bucket"),
                        "quote_range_signal": None,
                        "signal_source": "xtb_demo_quotes_missing_symbol",
                        "market_price": None,
                        "price_source": "xtb_demo_quotes_missing_symbol",
                        "price_currency": str(instrument.get("currency") or "PLN"),
                        "action": "skipped_missing_fresh_quote",
                        "reason": "No fresh verified XTB demo quote is available for this symbol.",
                        "trade_id": None,
                    }
                )
                continue

            price_pln = float(quote_price["market_price"])
            signal = self._quote_range_signal(quote_price)
            if signal is None:
                decisions.append(
                    {
                        "symbol": symbol,
                        "name": instrument.get("name"),
                        "risk_bucket": instrument.get("risk_bucket"),
                        "quote_range_signal": None,
                        "signal_source": "xtb_demo_quotes_insufficient_range",
                        "market_price": round(price_pln, 6),
                        "price_source": "xtb_demo_quotes",
                        "price_currency": str(instrument.get("currency") or "PLN"),
                        "action": "skipped_insufficient_quote_range",
                        "reason": "The verified XTB quote did not include a valid high/low range for this paper rule.",
                        "trade_id": None,
                        "quote_bid": quote_price.get("bid"),
                        "quote_ask": quote_price.get("ask"),
                        "quote_timestamp": quote_price.get("timestamp"),
                        "quote_checked_at": quote_price.get("checked_at"),
                    }
                )
                continue

            action = "hold"
            side: PaperTradeSide | None = None
            quantity = 0.0
            reason = (
                f"Autonomous paper quote-range run {normalized_strategy_id}: range signal={signal:.3f}; "
                "price_source=xtb_demo_quotes; "
                "no broker order is sent."
            )

            if signal >= 0.72 and len(executed_trade_ids) < safe_max_trades:
                action = "paper_buy"
                side = "buy"
                budget_pln = self._paper_trade_budget_for_risk(str(instrument.get("risk_bucket") or "medium"))
                quantity = round(budget_pln / price_pln, 6)
            elif signal <= 0.08 and len(executed_trade_ids) < safe_max_trades:
                current = float((positions.get(symbol) or {}).get("net_quantity") or 0.0)
                if current > 0:
                    action = "paper_sell"
                    side = "sell"
                    quantity = round(min(current, current * 0.5), 6)
                else:
                    action = "rejected_sell_no_position"

            decision = {
                "symbol": symbol,
                "name": instrument.get("name"),
                "risk_bucket": instrument.get("risk_bucket"),
                "quote_range_signal": round(signal, 3),
                "signal_source": "xtb_demo_quote_range_v1_unvalidated",
                "market_price": round(price_pln, 6),
                "price_source": "xtb_demo_quotes",
                "price_currency": str(instrument.get("currency") or "PLN"),
                "action": action,
                "reason": reason,
                "trade_id": None,
            }
            if quote_price:
                decision.update(
                    {
                        "quote_bid": quote_price.get("bid"),
                        "quote_ask": quote_price.get("ask"),
                        "quote_high": quote_price.get("high"),
                        "quote_low": quote_price.get("low"),
                        "quote_timestamp": quote_price.get("timestamp"),
                        "quote_checked_at": quote_price.get("checked_at"),
                    }
                )
            if side is not None and quantity > 0:
                try:
                    quote_spread_cost = None
                    if quote_price and quote_price.get("bid") is not None and quote_price.get("ask") is not None:
                        quote_spread_cost = abs(float(quote_price["ask"]) - float(quote_price["bid"])) * quantity
                    trade = self.record_paper_trade(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        price_pln=price_pln,
                        reason=reason,
                        spread_pln=quote_spread_cost,
                        commission_pln=None,
                        slippage_pln=None,
                        price_source="xtb_demo_quotes",
                        quote_timestamp=str(quote_price.get("timestamp") or "") if quote_price else None,
                        dataset_split="unassigned",
                    )
                except ValueError as exc:
                    decision["action"] = "rejected_risk_policy"
                    decision["reason"] = str(exc)
                else:
                    decision["trade_id"] = trade["id"]
                    decision["quantity"] = quantity
                    executed_trade_ids.append(str(trade["id"]))
            decisions.append(decision)

        rejected_count = sum(1 for item in decisions if str(item["action"]).startswith("rejected"))
        run_id = f"strategy_{uuid4().hex}"
        thesis = (
            "Paper-only quote-range experiment using fresh verified XTB demo read-only quotes. The rule is "
            "unvalidated and no real XTB execution is allowed. Symbols without a current quote are skipped."
        )
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO investment_strategy_runs (
                    id, created_at, strategy_id, mode, status, thesis, decisions_json,
                    executed_trade_count, rejected_trade_count, real_trading_enabled
                )
                VALUES (?, ?, ?, 'paper_only', 'completed', ?, ?, ?, ?, 0)
                """,
                (
                    run_id,
                    now,
                    normalized_strategy_id,
                    thesis,
                    json.dumps(decisions, ensure_ascii=False),
                    len(executed_trade_ids),
                    rejected_count,
                ),
            )
            db.commit()

        alerts = self._paper_autopilot_alerts(decisions, rejected_count=rejected_count)
        return {
            "id": run_id,
            "created_at": now,
            "strategy_id": normalized_strategy_id,
            "mode": "paper_only",
            "status": "completed",
            "real_trading_enabled": False,
            "executed_trade_count": len(executed_trade_ids),
            "rejected_trade_count": rejected_count,
            "decisions": decisions,
            "alerts": alerts,
            "safety": {
                "real_broker_orders_sent": 0,
                "uses_supplied_chat_password": False,
                "requires_read_only_market_data_before_live_execution": True,
                "price_sources_allowed": ["xtb_demo_quotes"],
            },
        }

    def _record_blocked_paper_run(
        self,
        *,
        strategy_id: str,
        reason: str,
        reason_code: str = "fresh_xtb_demo_quotes_required",
        alert_code: str = "fresh_xtb_demo_quotes_required_for_paper_autopilot",
    ) -> dict[str, Any]:
        """Persist a transparent no-data result so the scheduler does not invent a trade."""
        run_id = f"strategy_{uuid4().hex}"
        now = utc_now_iso()
        decisions = [
            {
                "symbol": None,
                "signal_source": "none",
                "market_price": None,
                "price_source": "xtb_demo_quotes_unavailable",
                "action": "blocked_missing_fresh_market_data",
                "reason": reason,
                "trade_id": None,
            }
        ]
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO investment_strategy_runs (
                    id, created_at, strategy_id, mode, status, thesis, decisions_json,
                    executed_trade_count, rejected_trade_count, real_trading_enabled
                )
                VALUES (?, ?, ?, 'paper_only', 'blocked', ?, ?, 0, 0, 0)
                """,
                (run_id, now, strategy_id, reason, json.dumps(decisions, ensure_ascii=False)),
            )
            db.commit()
        return {
            "id": run_id,
            "created_at": now,
            "strategy_id": strategy_id,
            "mode": "paper_only",
            "status": "blocked",
            "reason": reason_code,
            "blockers": [alert_code],
            "real_trading_enabled": False,
            "executed_trade_count": 0,
            "rejected_trade_count": 0,
            "decisions": decisions,
            "alerts": {
                "required": True,
                "reasons": [alert_code],
                "fallback_symbols": [],
                "missing_quote_symbols": [],
                "unknown_price_symbols": [],
                "rejected_symbols": [],
                "invalid_price_symbols": [],
                "gmail_enabled": bool(self.settings.investment_paper_autopilot_alerts_enabled),
                "real_trading_enabled": False,
            },
            "safety": {
                "real_broker_orders_sent": 0,
                "uses_supplied_chat_password": False,
                "requires_read_only_market_data_before_live_execution": True,
                "price_sources_allowed": ["xtb_demo_quotes"],
            },
        }

    @staticmethod
    def _quote_range_signal(quote: dict[str, Any]) -> float | None:
        """Compute an explicitly unvalidated paper-only range position from observed quote fields."""
        market_price = InvestmentCockpitService._quote_number(quote.get("market_price"))
        high = InvestmentCockpitService._quote_number(quote.get("high"))
        low = InvestmentCockpitService._quote_number(quote.get("low"))
        if market_price is None or high is None or low is None or high <= low:
            return None
        return max(0.0, min(1.0, (market_price - low) / (high - low)))

    def _paper_autopilot_alerts(self, decisions: list[dict[str, Any]], *, rejected_count: int) -> dict[str, Any]:
        missing_quote_symbols = [
            str(item.get("symbol") or "")
            for item in decisions
            if str(item.get("price_source") or "") != "xtb_demo_quotes"
        ]
        rejected_symbols = [
            str(item.get("symbol") or "")
            for item in decisions
            if str(item.get("action") or "").startswith("rejected")
        ]
        invalid_price_symbols = [
            str(item.get("symbol") or "")
            for item in decisions
            if item.get("market_price") is not None and float(item.get("market_price") or 0.0) <= 0.0
        ]
        reasons: list[str] = []
        if missing_quote_symbols:
            reasons.append("fresh_xtb_demo_quotes_missing_for_some_symbols")
        if (
            (missing_quote_symbols or rejected_symbols or invalid_price_symbols or rejected_count > 0)
            and bool(self.settings.investment_paper_autopilot_alert_on_unusual_decision)
        ):
            reasons.append("unusual_paper_decision")
        return {
            "required": bool(reasons),
            "reasons": reasons,
            "fallback_symbols": [],
            "missing_quote_symbols": missing_quote_symbols,
            "unknown_price_symbols": missing_quote_symbols,
            "rejected_symbols": rejected_symbols,
            "invalid_price_symbols": invalid_price_symbols,
            "gmail_enabled": bool(self.settings.investment_paper_autopilot_alerts_enabled),
            "real_trading_enabled": False,
        }

    def record_paper_trade(
        self,
        *,
        symbol: str,
        side: PaperTradeSide,
        quantity: float,
        price_pln: float,
        reason: str,
        spread_pln: float | None = None,
        commission_pln: float | None = None,
        slippage_pln: float | None = None,
        price_source: str = "manual_unverified",
        quote_timestamp: str | None = None,
        dataset_split: str = "unassigned",
    ) -> dict[str, Any]:
        self._ensure_schema()
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_side = self._normalize_side(side)
        safe_quantity = self._positive_float(quantity, "quantity")
        safe_price = self._positive_float(price_pln, "price_pln")
        safe_reason = reason.strip()
        if len(safe_reason) < 10:
            raise ValueError("Paper trade requires a written reason with at least 10 characters.")
        self._validate_paper_trade_risk(
            symbol=normalized_symbol,
            side=normalized_side,
            quantity=safe_quantity,
            price_pln=safe_price,
        )
        trade_id = f"paper_{uuid4().hex}"
        now = utc_now_iso()
        normalized_split = self._normalize_dataset_split(dataset_split)
        normalized_source = str(price_source or "manual_unverified").strip()[:80]
        normalized_quote_timestamp = self._normalize_optional_timestamp(quote_timestamp, "quote_timestamp")
        now_dt = datetime.fromisoformat(now)
        validation_errors: list[str] = []
        if normalized_source != "xtb_demo_quotes":
            validation_errors.append("unverified_price_source")
        if normalized_quote_timestamp is None:
            validation_errors.append("quote_timestamp_missing")
        else:
            quote_dt = datetime.fromisoformat(normalized_quote_timestamp)
            if quote_dt > now_dt + timedelta(seconds=2):
                raise ValueError("quote_timestamp cannot be later than the paper decision time.")
            if now_dt - quote_dt > XTB_QUOTE_MAX_AGE:
                validation_errors.append("stale_quote_timestamp")
        costs = {
            "spread_pln": self._optional_non_negative_float(spread_pln, "spread_pln"),
            "commission_pln": self._optional_non_negative_float(commission_pln, "commission_pln"),
            "slippage_pln": self._optional_non_negative_float(slippage_pln, "slippage_pln"),
        }
        if any(value is None for value in costs.values()):
            validation_errors.append("cost_model_incomplete")
        if normalized_split == "unassigned":
            validation_errors.append("dataset_split_unassigned")
        validation_status = "complete" if not validation_errors else "incomplete"
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO investment_paper_trades (
                    id, created_at, updated_at, symbol, side, quantity, price_pln, reason, status,
                    entry_time, entry_price_pln, position_size, spread_pln, commission_pln,
                    slippage_pln, price_source, quote_timestamp, dataset_split,
                    validation_status, validation_errors_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'paper_executed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    now,
                    now,
                    normalized_symbol,
                    normalized_side,
                    safe_quantity,
                    safe_price,
                    safe_reason,
                    now,
                    safe_price,
                    safe_quantity,
                    costs["spread_pln"],
                    costs["commission_pln"],
                    costs["slippage_pln"],
                    normalized_source,
                    normalized_quote_timestamp,
                    normalized_split,
                    validation_status,
                    json.dumps(validation_errors),
                ),
            )
            db.commit()
            row = db.execute(
                """
                SELECT id, created_at, updated_at, symbol, side, quantity, price_pln, reason, status,
                       entry_time, entry_price_pln, position_size, spread_pln, commission_pln,
                       slippage_pln, exit_time, exit_price_pln, exit_reason, gross_pnl_pln,
                       net_pnl_pln, price_source, quote_timestamp, dataset_split,
                       validation_status, validation_errors_json
                FROM investment_paper_trades
                WHERE id = ?
                """,
                (trade_id,),
            ).fetchone()
        item = dict(row)
        item["validation_errors"] = json.loads(str(item.pop("validation_errors_json") or "[]"))
        return item

    def close_paper_trade(
        self,
        *,
        trade_id: str,
        exit_price_pln: float,
        exit_reason: str,
        exit_time: str | None = None,
        spread_pln: float | None = None,
        commission_pln: float | None = None,
        slippage_pln: float | None = None,
    ) -> dict[str, Any]:
        self._ensure_schema()
        normalized_id = str(trade_id or "").strip()
        safe_exit_price = self._positive_float(exit_price_pln, "exit_price_pln")
        safe_exit_reason = str(exit_reason or "").strip()
        if len(safe_exit_reason) < 10:
            raise ValueError("Paper trade exit requires a written reason with at least 10 characters.")
        closed_at = self._normalize_optional_timestamp(exit_time, "exit_time") or utc_now_iso()
        exit_costs = {
            "spread_pln": self._optional_non_negative_float(spread_pln, "spread_pln"),
            "commission_pln": self._optional_non_negative_float(commission_pln, "commission_pln"),
            "slippage_pln": self._optional_non_negative_float(slippage_pln, "slippage_pln"),
        }
        with connect(self.settings) as db:
            row = db.execute("SELECT * FROM investment_paper_trades WHERE id = ?", (normalized_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown paper trade: {normalized_id}")
            item = dict(row)
            if str(item.get("status")) != "paper_executed":
                raise ValueError("Paper trade is not open.")
            entry_time = datetime.fromisoformat(str(item.get("entry_time") or item.get("created_at")))
            exit_dt = datetime.fromisoformat(closed_at)
            if exit_dt < entry_time:
                raise ValueError("exit_time cannot be earlier than entry_time.")
            quantity = float(item.get("position_size") or item.get("quantity") or 0.0)
            entry_price = float(item.get("entry_price_pln") or item.get("price_pln") or 0.0)
            direction = 1.0 if str(item.get("side")) == "buy" else -1.0
            gross_pnl = round((safe_exit_price - entry_price) * quantity * direction, 8)
            stored_costs = [item.get("spread_pln"), item.get("commission_pln"), item.get("slippage_pln")]
            supplied_costs = list(exit_costs.values())
            cost_complete = all(value is not None for value in stored_costs + supplied_costs)
            total_cost = sum(float(value or 0.0) for value in stored_costs + supplied_costs)
            net_pnl = round(gross_pnl - total_cost, 8) if cost_complete else None
            errors = json.loads(str(item.get("validation_errors_json") or "[]"))
            errors = [error for error in errors if error != "position_open"]
            if not cost_complete and "cost_model_incomplete" not in errors:
                errors.append("cost_model_incomplete")
            validation_status = "complete" if not errors and net_pnl is not None else "incomplete"
            db.execute(
                """
                UPDATE investment_paper_trades
                SET updated_at = ?, status = 'paper_closed', exit_time = ?, exit_price_pln = ?,
                    exit_reason = ?, exit_spread_pln = ?, exit_commission_pln = ?,
                    exit_slippage_pln = ?, gross_pnl_pln = ?, net_pnl_pln = ?,
                    validation_status = ?, validation_errors_json = ?
                WHERE id = ?
                """,
                (
                    utc_now_iso(), closed_at, safe_exit_price, safe_exit_reason,
                    exit_costs["spread_pln"], exit_costs["commission_pln"], exit_costs["slippage_pln"],
                    gross_pnl, net_pnl, validation_status, json.dumps(errors), normalized_id,
                ),
            )
            db.commit()
        return next(item for item in self.list_paper_trades(limit=500) if item["id"] == normalized_id)

    def paper_analysis_status(self) -> dict[str, Any]:
        self._ensure_schema()
        with connect(self.settings) as db:
            rows = rows_to_dicts(
                db.execute("SELECT * FROM investment_paper_trades ORDER BY created_at, id").fetchall()
            )
        closed = [row for row in rows if str(row.get("status")) == "paper_closed"]
        complete = [row for row in closed if str(row.get("validation_status")) == "complete" and row.get("net_pnl_pln") is not None]
        oos = [row for row in complete if str(row.get("dataset_split")) == "out_of_sample"]
        timestamps = [datetime.fromisoformat(str(row["created_at"])) for row in rows if row.get("created_at")]
        span_days = (max(timestamps) - min(timestamps)).total_seconds() / 86400 if len(timestamps) > 1 else 0.0
        required_closed = 30
        required_oos = 10
        required_days = 28
        missing = []
        if len(complete) < required_closed:
            missing.append(f"complete_closed_trades:{required_closed - len(complete)}")
        if len(oos) < required_oos:
            missing.append(f"out_of_sample_trades:{required_oos - len(oos)}")
        if span_days < required_days:
            missing.append(f"observation_days:{round(required_days - span_days, 3)}")
        if not any(str(row.get("price_source")) == "xtb_demo_quotes" for row in rows):
            missing.append("verified_xtb_demo_quotes")
        missing.append("benchmark_price_series")
        net_values = [float(row["net_pnl_pln"]) for row in complete]
        metrics: dict[str, Any] = {
            "gross_pnl_pln": None,
            "net_pnl_pln": None,
            "win_rate": None,
            "average_profit_pln": None,
            "average_loss_pln": None,
            "profit_factor": None,
            "expectancy_pln": None,
            "max_drawdown_pln": None,
            "benchmark_return_pct": None,
            "out_of_sample_net_pnl_pln": None,
        }
        if complete:
            profits = [value for value in net_values if value > 0]
            losses = [value for value in net_values if value < 0]
            equity = 0.0
            peak = 0.0
            max_drawdown = 0.0
            for value in net_values:
                equity += value
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, peak - equity)
            metrics.update(
                {
                    "gross_pnl_pln": round(sum(float(row["gross_pnl_pln"]) for row in complete), 8),
                    "net_pnl_pln": round(sum(net_values), 8),
                    "win_rate": round(len(profits) / len(complete), 6),
                    "average_profit_pln": round(sum(profits) / len(profits), 8) if profits else 0.0,
                    "average_loss_pln": round(sum(losses) / len(losses), 8) if losses else 0.0,
                    "profit_factor": round(sum(profits) / abs(sum(losses)), 6) if losses else None,
                    "expectancy_pln": round(sum(net_values) / len(complete), 8),
                    "max_drawdown_pln": round(max_drawdown, 8),
                    "out_of_sample_net_pnl_pln": round(sum(float(row["net_pnl_pln"]) for row in oos), 8) if oos else None,
                }
            )
        return {
            "status": "insufficient_data" if missing else "evidence_ready",
            "score": None,
            "trade_count": len(rows),
            "closed_trade_count": len(closed),
            "complete_closed_trade_count": len(complete),
            "out_of_sample_trade_count": len(oos),
            "observation_span_days": round(span_days, 3),
            "minimum_requirements": {
                "complete_closed_trades": required_closed,
                "out_of_sample_trades": required_oos,
                "observation_days": required_days,
            },
            "missing_evidence": missing,
            "metrics": metrics,
            "limitations": [
                "No strategy rating is produced while status is insufficient_data.",
                "Legacy synthetic trades remain visible but do not count as complete evidence.",
                "Real-money recommendations and broker execution remain forbidden.",
            ],
        }

    def paper_positions(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT symbol, side, quantity, price_pln
                FROM investment_paper_trades
                WHERE status = 'paper_executed'
                ORDER BY created_at ASC
                """
            ).fetchall()

        positions: dict[str, dict[str, float | str]] = {}
        for row in rows:
            symbol = str(row["symbol"])
            item = positions.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "net_quantity": 0.0,
                    "buy_quantity": 0.0,
                    "sell_quantity": 0.0,
                    "gross_buy_cost_pln": 0.0,
                    "gross_sell_value_pln": 0.0,
                },
            )
            quantity = float(row["quantity"] or 0.0)
            price = float(row["price_pln"] or 0.0)
            if str(row["side"]) == "buy":
                item["net_quantity"] = float(item["net_quantity"]) + quantity
                item["buy_quantity"] = float(item["buy_quantity"]) + quantity
                item["gross_buy_cost_pln"] = float(item["gross_buy_cost_pln"]) + quantity * price
            else:
                item["net_quantity"] = float(item["net_quantity"]) - quantity
                item["sell_quantity"] = float(item["sell_quantity"]) + quantity
                item["gross_sell_value_pln"] = float(item["gross_sell_value_pln"]) + quantity * price

        result: list[dict[str, Any]] = []
        for item in positions.values():
            buy_quantity = float(item["buy_quantity"])
            gross_buy_cost = float(item["gross_buy_cost_pln"])
            avg_buy_price = gross_buy_cost / buy_quantity if buy_quantity > 0 else 0.0
            result.append(
                {
                    "symbol": item["symbol"],
                    "net_quantity": round(float(item["net_quantity"]), 6),
                    "average_buy_price_pln": round(avg_buy_price, 4),
                    "gross_buy_cost_pln": round(gross_buy_cost, 4),
                    "gross_sell_value_pln": round(float(item["gross_sell_value_pln"]), 4),
                    "real_market_value_pln": None,
                    "mode": "paper",
                }
            )
        return sorted(result, key=lambda item: str(item["symbol"]))

    def reject_real_trade_attempt(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": "Real-money trading is disabled. Use paper trades only until read-only broker access, market data, risk policy and explicit approval flow exist.",
            "real_trading_enabled": False,
            "received_payload_keys": sorted((payload or {}).keys()),
            "next_action": "Record a paper trade or add a watchlist thesis instead.",
        }

    def risk_policy(self) -> dict[str, Any]:
        """Zwróć egzekwowaną politykę ryzyka; live trading pozostaje niemożliwy."""
        self._ensure_schema()
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT max_single_paper_trade_pln, max_paper_position_pln,
                       max_daily_realized_loss_pln, max_weekly_realized_loss_pln,
                       paper_only, emergency_stop, configured_at, updated_at
                FROM investment_risk_policies
                WHERE project_id = ?
                """,
                (PROJECT_ID,),
            ).fetchone()
        policy = self._default_risk_policy()
        if row is not None:
            policy.update(
                {
                    "mode": "configured_safe_policy",
                    "configured": True,
                    "max_single_paper_trade_pln": float(row["max_single_paper_trade_pln"]),
                    "max_paper_position_pln": float(row["max_paper_position_pln"]),
                    "max_daily_realized_loss_pln": float(row["max_daily_realized_loss_pln"]),
                    "max_weekly_realized_loss_pln": float(row["max_weekly_realized_loss_pln"]),
                    "paper_only": bool(row["paper_only"]),
                    "emergency_stop": bool(row["emergency_stop"]),
                    "configured_at": row["configured_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return policy

    def configure_risk_policy(
        self,
        *,
        max_single_paper_trade_pln: float,
        max_paper_position_pln: float,
        max_daily_realized_loss_pln: float,
        max_weekly_realized_loss_pln: float,
        paper_only: bool = True,
        emergency_stop: bool = False,
    ) -> dict[str, Any]:
        """Zapisz ograniczenia paper tradingu i wymuś brak możliwości live trade."""
        self._ensure_schema()
        if not paper_only:
            raise ValueError("Portfolio Alpha must remain paper_only. Live trading cannot be enabled here.")
        single_trade = self._risk_limit(max_single_paper_trade_pln, "max_single_paper_trade_pln", 100_000.0)
        max_position = self._risk_limit(max_paper_position_pln, "max_paper_position_pln", 500_000.0)
        daily_loss = self._risk_limit(max_daily_realized_loss_pln, "max_daily_realized_loss_pln", 100_000.0)
        weekly_loss = self._risk_limit(max_weekly_realized_loss_pln, "max_weekly_realized_loss_pln", 500_000.0)
        if max_position < single_trade:
            raise ValueError("max_paper_position_pln must be at least max_single_paper_trade_pln.")
        if weekly_loss < daily_loss:
            raise ValueError("max_weekly_realized_loss_pln must be at least max_daily_realized_loss_pln.")

        now = utc_now_iso()
        with connect(self.settings) as db:
            existing = db.execute(
                "SELECT configured_at FROM investment_risk_policies WHERE project_id = ?",
                (PROJECT_ID,),
            ).fetchone()
            configured_at = str(existing["configured_at"]) if existing else now
            db.execute(
                """
                INSERT INTO investment_risk_policies (
                    project_id, max_single_paper_trade_pln, max_paper_position_pln,
                    max_daily_realized_loss_pln, max_weekly_realized_loss_pln,
                    paper_only, emergency_stop, configured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    max_single_paper_trade_pln = excluded.max_single_paper_trade_pln,
                    max_paper_position_pln = excluded.max_paper_position_pln,
                    max_daily_realized_loss_pln = excluded.max_daily_realized_loss_pln,
                    max_weekly_realized_loss_pln = excluded.max_weekly_realized_loss_pln,
                    paper_only = 1,
                    emergency_stop = excluded.emergency_stop,
                    updated_at = excluded.updated_at
                """,
                (
                    PROJECT_ID,
                    single_trade,
                    max_position,
                    daily_loss,
                    weekly_loss,
                    1 if emergency_stop else 0,
                    configured_at,
                    now,
                ),
            )
            db.commit()
        return self.risk_policy()

    def paper_loss_metrics(self) -> dict[str, Any]:
        """Oblicz wyłącznie zrealizowany P/L z wpisów paper ledgera w PLN."""
        self._ensure_schema()
        now = datetime.now(UTC)
        daily_start = now - timedelta(days=1)
        weekly_start = now - timedelta(days=7)
        positions: dict[str, dict[str, float]] = {}
        daily_pnl = 0.0
        weekly_pnl = 0.0
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT created_at, symbol, side, quantity, price_pln
                FROM investment_paper_trades
                WHERE status = 'paper_executed'
                ORDER BY created_at ASC
                """
            ).fetchall()
        for row in rows:
            symbol = str(row["symbol"])
            quantity = float(row["quantity"] or 0.0)
            price = float(row["price_pln"] or 0.0)
            item = positions.setdefault(symbol, {"quantity": 0.0, "avg_price": 0.0})
            if str(row["side"]) == "buy":
                prior_cost = item["quantity"] * item["avg_price"]
                item["quantity"] += quantity
                item["avg_price"] = (prior_cost + (quantity * price)) / item["quantity"] if item["quantity"] else 0.0
                continue

            sell_quantity = min(quantity, max(0.0, item["quantity"]))
            realized_pnl = (price - item["avg_price"]) * sell_quantity
            item["quantity"] = max(0.0, item["quantity"] - sell_quantity)
            try:
                created_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if created_at >= daily_start:
                daily_pnl += realized_pnl
            if created_at >= weekly_start:
                weekly_pnl += realized_pnl
        return {
            "basis": "realized_pnl_only",
            "daily_realized_pnl_pln": round(daily_pnl, 4),
            "weekly_realized_pnl_pln": round(weekly_pnl, 4),
            "daily_realized_loss_pln": round(max(0.0, -daily_pnl), 4),
            "weekly_realized_loss_pln": round(max(0.0, -weekly_pnl), 4),
            "unrealized_pnl_available": False,
            "calculated_at": utc_now_iso(),
        }

    def _validate_paper_trade_risk(
        self,
        *,
        symbol: str,
        side: PaperTradeSide,
        quantity: float,
        price_pln: float,
    ) -> None:
        policy = self.risk_policy()
        if bool(policy.get("emergency_stop")):
            raise ValueError("Paper trade is blocked because the emergency stop is enabled.")
        notional = quantity * price_pln
        if notional > float(policy["max_single_paper_trade_pln"]):
            raise ValueError("Paper trade exceeds max_single_paper_trade_pln.")
        positions = {item["symbol"]: item for item in self.paper_positions()}
        current = positions.get(symbol) or {}
        current_quantity = float(current.get("net_quantity") or 0.0)
        if side == "buy":
            projected_position = (current_quantity + quantity) * price_pln
            if projected_position > float(policy["max_paper_position_pln"]):
                raise ValueError("Paper trade exceeds max_paper_position_pln.")
            return
        if quantity > current_quantity:
            raise ValueError("Paper sell exceeds the currently recorded paper position.")
        average_buy_price = float(current.get("average_buy_price_pln") or 0.0)
        projected_loss = max(0.0, (average_buy_price - price_pln) * quantity)
        if not projected_loss:
            return
        metrics = self.paper_loss_metrics()
        if metrics["daily_realized_loss_pln"] + projected_loss > float(policy["max_daily_realized_loss_pln"]):
            raise ValueError("Paper trade exceeds max_daily_realized_loss_pln.")
        if metrics["weekly_realized_loss_pln"] + projected_loss > float(policy["max_weekly_realized_loss_pln"]):
            raise ValueError("Paper trade exceeds max_weekly_realized_loss_pln.")

    @staticmethod
    def _default_risk_policy() -> dict[str, Any]:
        return {
            "mode": "baseline_safe_policy",
            "configured": False,
            "real_trading_enabled": False,
            "paper_trading_enabled": True,
            "paper_only": True,
            "emergency_stop": False,
            "max_single_paper_trade_pln": 5000.0,
            "max_paper_position_pln": 20000.0,
            "max_daily_realized_loss_pln": 1000.0,
            "max_weekly_realized_loss_pln": 2500.0,
            "forbidden_instruments_for_real_money": ["leverage", "options", "futures", "cfds", "margin"],
            "paper_only_instruments_allowed": ["stocks", "etfs", "indices", "forex", "commodities", "crypto"],
            "broker_credentials_in_chat": "forbidden",
            "card_or_bank_credentials_in_chat": "forbidden",
            "owner_approval_required_for_real_orders": True,
            "demo_broker_execution": "blocked_until_verified_read_only_adapter_exists",
            "configured_at": None,
            "updated_at": None,
        }

    @staticmethod
    def _risk_limit(value: float, field_name: str, maximum: float) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a positive number.") from exc
        if normalized <= 0 or normalized > maximum:
            raise ValueError(f"{field_name} must be greater than zero and at most {maximum:.0f}.")
        return round(normalized, 2)

    def _ensure_schema(self) -> None:
        with connect(self.settings) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS investment_watchlist (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    asset_type TEXT NOT NULL DEFAULT 'stock',
                    thesis TEXT NOT NULL DEFAULT '',
                    risk_notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS investment_paper_trades (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                    quantity REAL NOT NULL CHECK (quantity > 0),
                    price_pln REAL NOT NULL CHECK (price_pln > 0),
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'paper_executed',
                    updated_at TEXT,
                    entry_time TEXT,
                    entry_price_pln REAL,
                    position_size REAL,
                    spread_pln REAL,
                    commission_pln REAL,
                    slippage_pln REAL,
                    exit_time TEXT,
                    exit_price_pln REAL,
                    exit_reason TEXT,
                    exit_spread_pln REAL,
                    exit_commission_pln REAL,
                    exit_slippage_pln REAL,
                    gross_pnl_pln REAL,
                    net_pnl_pln REAL,
                    price_source TEXT NOT NULL DEFAULT 'legacy_unknown',
                    quote_timestamp TEXT,
                    dataset_split TEXT NOT NULL DEFAULT 'unassigned',
                    validation_status TEXT NOT NULL DEFAULT 'incomplete',
                    validation_errors_json TEXT NOT NULL DEFAULT '["legacy_record_incomplete"]'
                );

                CREATE INDEX IF NOT EXISTS idx_investment_paper_trades_symbol_created
                    ON investment_paper_trades (symbol, created_at);

                CREATE TABLE IF NOT EXISTS investment_broker_accounts (
                    provider_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    account_identifier TEXT NOT NULL,
                    environment TEXT NOT NULL DEFAULT 'demo',
                    access_mode TEXT NOT NULL DEFAULT 'read_only_candidate',
                    status TEXT NOT NULL DEFAULT 'metadata_registered',
                    credential_account_id INTEGER,
                    login_url TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS investment_instruments (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    market TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    provider_key TEXT NOT NULL,
                    trade_mode TEXT NOT NULL DEFAULT 'paper_only',
                    risk_bucket TEXT NOT NULL DEFAULT 'medium',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_investment_instruments_provider_enabled
                    ON investment_instruments (provider_key, enabled);

                CREATE TABLE IF NOT EXISTS investment_strategy_runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'paper_only',
                    status TEXT NOT NULL DEFAULT 'completed',
                    thesis TEXT NOT NULL DEFAULT '',
                    decisions_json TEXT NOT NULL DEFAULT '[]',
                    executed_trade_count INTEGER NOT NULL DEFAULT 0,
                    rejected_trade_count INTEGER NOT NULL DEFAULT 0,
                    real_trading_enabled INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_investment_strategy_runs_created
                    ON investment_strategy_runs (created_at);

                CREATE TABLE IF NOT EXISTS investment_project_settings (
                    project_id TEXT PRIMARY KEY,
                    broker_name TEXT NOT NULL DEFAULT 'XTB Demo',
                    broker_portal_url TEXT NOT NULL DEFAULT '',
                    broker_account_identifier TEXT NOT NULL DEFAULT '',
                    credential_vault_ref TEXT NOT NULL DEFAULT '',
                    broker_read_access TEXT NOT NULL DEFAULT 'not_configured',
                    account_mode TEXT NOT NULL DEFAULT 'demo_paper',
                    market_data_sources_json TEXT NOT NULL DEFAULT '[]',
                    learning_resources_json TEXT NOT NULL DEFAULT '[]',
                    monthly_spend_budget_pln REAL NOT NULL DEFAULT 0,
                    payment_method_ref TEXT NOT NULL DEFAULT '',
                    payment_method_configured INTEGER NOT NULL DEFAULT 0,
                    real_trading_blocked INTEGER NOT NULL DEFAULT 1,
                    trade_approval_rules_enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS investment_market_data_checks (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    provider_key TEXT NOT NULL,
                    environment TEXT NOT NULL DEFAULT 'demo',
                    status TEXT NOT NULL,
                    symbols_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    real_orders_enabled INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_investment_market_data_checks_provider_created
                    ON investment_market_data_checks (provider_key, created_at);

                CREATE TABLE IF NOT EXISTS investment_risk_policies (
                    project_id TEXT PRIMARY KEY,
                    max_single_paper_trade_pln REAL NOT NULL,
                    max_paper_position_pln REAL NOT NULL,
                    max_daily_realized_loss_pln REAL NOT NULL,
                    max_weekly_realized_loss_pln REAL NOT NULL,
                    paper_only INTEGER NOT NULL DEFAULT 1,
                    emergency_stop INTEGER NOT NULL DEFAULT 0,
                    configured_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_paper_trade_columns(db)
            db.commit()

    @staticmethod
    def _ensure_paper_trade_columns(db: Any) -> None:
        existing = {str(row[1]) for row in db.execute("PRAGMA table_info(investment_paper_trades)").fetchall()}
        columns = {
            "updated_at": "TEXT",
            "entry_time": "TEXT",
            "entry_price_pln": "REAL",
            "position_size": "REAL",
            "spread_pln": "REAL",
            "commission_pln": "REAL",
            "slippage_pln": "REAL",
            "exit_time": "TEXT",
            "exit_price_pln": "REAL",
            "exit_reason": "TEXT",
            "exit_spread_pln": "REAL",
            "exit_commission_pln": "REAL",
            "exit_slippage_pln": "REAL",
            "gross_pnl_pln": "REAL",
            "net_pnl_pln": "REAL",
            "price_source": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
            "quote_timestamp": "TEXT",
            "dataset_split": "TEXT NOT NULL DEFAULT 'unassigned'",
            "validation_status": "TEXT NOT NULL DEFAULT 'incomplete'",
            "validation_errors_json": "TEXT NOT NULL DEFAULT '[\"legacy_record_incomplete\"]'",
        }
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE investment_paper_trades ADD COLUMN {name} {definition}")

    @staticmethod
    def _normalize_dataset_split(value: str) -> str:
        normalized = str(value or "unassigned").strip().lower()
        if normalized not in {"unassigned", "training", "validation", "out_of_sample"}:
            raise ValueError("dataset_split must be unassigned, training, validation or out_of_sample.")
        return normalized

    @staticmethod
    def _normalize_optional_timestamp(value: str | None, field: str) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp.") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()

    @staticmethod
    def _optional_non_negative_float(value: float | None, field: str) -> float | None:
        if value is None:
            return None
        parsed = float(value)
        if parsed < 0:
            raise ValueError(f"{field} must be greater than or equal to zero.")
        return parsed

    @staticmethod
    def _broker_status_from_accounts(
        accounts: list[dict[str, Any]],
        *,
        portfolio_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        xtb = next((item for item in accounts if str(item.get("provider_key")) == "xtb"), None)
        if xtb is None:
            return {"provider_key": "xtb", "access": "not_registered"}
        status = str(xtb.get("status") or "")
        settings_access = str((portfolio_settings or {}).get("broker_read_access") or "")
        if status == "read_only_verified" or settings_access == "read_only_verified":
            access = "read_only_verified"
        elif settings_access == "read_only_candidate":
            access = "read_only_candidate"
        elif status:
            access = "metadata_registered"
        else:
            access = "not_verified"
        return {"provider_key": "xtb", "access": access}

    def _sync_xtb_read_access_status(self, broker_read_access: str) -> None:
        normalized = self._normalize_broker_read_access(broker_read_access)
        if normalized not in {"read_only_candidate", "read_only_verified"}:
            return
        access_mode = "read_only_candidate"
        status_value = "metadata_registered"
        now = utc_now_iso()
        with connect(self.settings) as db:
            db.execute(
                """
                UPDATE investment_broker_accounts
                SET access_mode = ?, status = ?, updated_at = ?
                WHERE provider_key = 'xtb'
                """,
                (access_mode, status_value, now),
            )
            db.commit()

    @staticmethod
    def _default_portfolio_settings() -> dict[str, Any]:
        return {
            "project_id": PROJECT_ID,
            "broker_name": "XTB Demo",
            "broker_portal_url": DEFAULT_BROKER_URL,
            "broker_account_identifier": "",
            "credential_vault_ref": "",
            "broker_read_access": "not_configured",
            "account_mode": "demo_paper",
            "market_data_sources": [],
            "learning_resources": [],
            "monthly_spend_budget_pln": 0.0,
            "payment_method_ref": "",
            "payment_method_configured": False,
            "real_trading_blocked": True,
            "trade_approval_rules_enabled": False,
            "created_at": None,
            "updated_at": None,
            "stores_plaintext_secret": False,
        }

    @staticmethod
    def _json_list(value: Any) -> list[str]:
        try:
            raw = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if str(item).strip()]

    @staticmethod
    def _safe_text(value: str, *, max_len: int, default: str) -> str:
        normalized = " ".join(str(value or "").split())
        return (normalized or default)[:max_len]

    @staticmethod
    def _safe_https_url(value: str) -> str:
        normalized = str(value or DEFAULT_BROKER_URL).strip()
        if not normalized.startswith("https://"):
            raise ValueError("Broker portal URL must start with https://.")
        if len(normalized) > 500:
            raise ValueError("Broker portal URL is too long.")
        return normalized

    @staticmethod
    def _safe_reference(value: str, field_name: str, *, allow_empty: bool = False) -> str:
        normalized = str(value or "").strip()
        if not normalized and allow_empty:
            return ""
        if not normalized:
            raise ValueError(f"{field_name} is required.")
        if len(normalized) > 180:
            raise ValueError(f"{field_name} is too long.")
        if re.search(r"\d[ \-]?(?:\d[ \-]?){12,18}\d", normalized):
            raise ValueError(f"{field_name} must be a vault reference, not a card or account number.")
        if re.search(r"(?i)(password|passwd|haslo|hasło|cvv|cvc|pin)", normalized):
            raise ValueError(f"{field_name} must not contain secret material.")
        if not re.fullmatch(r"[A-Za-z0-9:_./@-]+", normalized):
            raise ValueError(f"{field_name} may contain only safe reference characters.")
        return normalized

    @staticmethod
    def _safe_list(values: list[str], *, max_items: int, max_item_len: int) -> list[str]:
        cleaned: list[str] = []
        for item in values[:max_items]:
            normalized = " ".join(str(item or "").split())
            if normalized:
                cleaned.append(normalized[:max_item_len])
        return cleaned

    @staticmethod
    def _sanitize_market_data_result(result: dict[str, Any]) -> dict[str, Any]:
        forbidden_keys = {"password", "secret", "token", "credential", "api_key", "apikey"}

        def _sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    str(key): ("[redacted]" if str(key).casefold() in forbidden_keys else _sanitize(item))
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [_sanitize(item) for item in value[:50]]
            if isinstance(value, str):
                if re.search(r"(?i)(password|passwd|haslo|hasło|cvv|cvc|bearer|secret)", value):
                    return "[redacted]"
                return value[:500]
            return value

        return _sanitize(result if isinstance(result, dict) else {})

    @staticmethod
    def _normalize_broker_read_access(value: str) -> str:
        normalized = str(value or "not_configured").strip().lower()
        allowed = {"not_configured", "read_only_candidate", "read_only_verified"}
        return normalized if normalized in allowed else "not_configured"

    @staticmethod
    def _normalize_account_mode(value: str) -> str:
        normalized = str(value or "demo_paper").strip().lower()
        allowed = {"demo_paper", "demo_read_only", "live_read_only"}
        return normalized if normalized in allowed else "demo_paper"

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        normalized = "".join(ch for ch in str(symbol or "").strip().upper() if ch.isalnum() or ch in {".", "-", "_"})
        if len(normalized) < 1 or len(normalized) > 24:
            raise ValueError("Symbol must be 1-24 safe characters.")
        return normalized

    @staticmethod
    def _normalize_asset_type(asset_type: str) -> str:
        normalized = str(asset_type or "stock").strip().lower().replace(" ", "_")
        allowed = {"stock", "etf", "bond", "crypto_watch_only", "fund", "paper_test", "custom"}
        return normalized if normalized in allowed else "custom"

    @staticmethod
    def _normalize_side(side: str) -> PaperTradeSide:
        normalized = str(side or "").strip().lower()
        if normalized not in {"buy", "sell"}:
            raise ValueError("Paper trade side must be buy or sell.")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _positive_float(value: float, field_name: str) -> float:
        number = float(value or 0.0)
        if number <= 0:
            raise ValueError(f"{field_name} must be greater than zero.")
        return round(number, 6)

    @staticmethod
    def _normalize_strategy_id(strategy_id: str) -> str:
        normalized = "".join(ch for ch in str(strategy_id or "").strip().lower() if ch.isalnum() or ch in {"_", "-"})
        return normalized[:80] or "baseline_signal_journal_v1"

    @staticmethod
    def _synthetic_signal(symbol: str, *, strategy_id: str) -> float:
        digest = hashlib.sha256(f"{strategy_id}:{symbol}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF

    @staticmethod
    def _synthetic_price_pln(symbol: str) -> float:
        digest = hashlib.sha256(f"price:{symbol}".encode("utf-8")).hexdigest()
        return round(25 + (int(digest[:8], 16) / 0xFFFFFFFF) * 975, 2)

    @classmethod
    def _paper_market_price_from_quote(cls, quote: dict[str, Any]) -> float | None:
        bid = cls._quote_number(quote.get("bid"))
        ask = cls._quote_number(quote.get("ask"))
        if bid is not None and ask is not None:
            return round((bid + ask) / 2, 6)
        if bid is not None:
            return bid
        if ask is not None:
            return ask
        high = cls._quote_number(quote.get("high"))
        low = cls._quote_number(quote.get("low"))
        if high is not None and low is not None:
            return round((high + low) / 2, 6)
        return high or low

    @staticmethod
    def _quote_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        return round(number, 6)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _paper_trade_budget_for_risk(risk_bucket: str) -> float:
        normalized = str(risk_bucket or "medium").strip().lower()
        if normalized == "very_high":
            return 500.0
        if normalized == "high":
            return 1000.0
        return 1500.0
