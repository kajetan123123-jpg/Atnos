from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from atnos.investment.alpaca_market_data import AlpacaMarketDataError, AlpacaPaperReadOnlyAdapter
from atnos.investment.cockpit import InvestmentCockpitService
from atnos.investment.education_research import InvestmentEducationResearchService
from atnos.investment.education_sources import InvestmentEducationSourceRegistry
from atnos.investment.xtb_market_data import (
    XtbApiRetiredError,
    XtbForbiddenCommandError,
    XtbMarketDataError,
    XtbReadOnlyMarketDataAdapter,
)
from atnos.services.account_credentials_service import AccountCredentialsService

router = APIRouter(prefix="/api/investment", tags=["investment"])


class WatchlistItemRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    name: str | None = Field(default=None, max_length=160)
    asset_type: str = Field(default="stock", max_length=40)
    thesis: str = Field(default="", max_length=2000)
    risk_notes: str = Field(default="", max_length=2000)


class PaperTradeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price_pln: float = Field(gt=0)
    reason: str = Field(min_length=10, max_length=2000)
    spread_pln: float | None = Field(default=None, ge=0)
    commission_pln: float | None = Field(default=None, ge=0)
    slippage_pln: float | None = Field(default=None, ge=0)
    price_source: str = Field(default="manual_unverified", min_length=1, max_length=80)
    quote_timestamp: str | None = Field(default=None, max_length=80)
    dataset_split: Literal["unassigned", "training", "validation", "out_of_sample"] = "unassigned"


class PaperTradeCloseRequest(BaseModel):
    exit_price_pln: float = Field(gt=0)
    exit_reason: str = Field(min_length=10, max_length=2000)
    exit_time: str | None = Field(default=None, max_length=80)
    spread_pln: float | None = Field(default=None, ge=0)
    commission_pln: float | None = Field(default=None, ge=0)
    slippage_pln: float | None = Field(default=None, ge=0)


class XtbDemoAccountRequest(BaseModel):
    account_identifier: str = Field(min_length=3, max_length=240)
    credential_account_id: int | None = Field(default=None, gt=0)


class AlpacaPaperAccountRequest(BaseModel):
    credential_account_id: int = Field(gt=0)


class PortfolioAlphaSettingsRequest(BaseModel):
    broker_name: str = Field(default="XTB Demo", min_length=1, max_length=120)
    broker_portal_url: str = Field(default="https://xstation5.xtb.com/", min_length=8, max_length=500)
    broker_account_identifier: str = Field(default="", max_length=240)
    credential_vault_ref: str = Field(default="", max_length=180)
    broker_read_access: Literal["not_configured", "read_only_candidate", "read_only_verified"] = "not_configured"
    account_mode: Literal["demo_paper", "demo_read_only", "live_read_only"] = "demo_paper"
    market_data_sources: list[str] = Field(default_factory=list, max_length=20)
    learning_resources: list[str] = Field(default_factory=list, max_length=50)
    monthly_spend_budget_pln: float = Field(default=0.0, ge=0.0, le=100000.0)
    payment_method_ref: str = Field(default="", max_length=180)
    payment_method_configured: bool = False
    real_trading_blocked: bool = True
    trade_approval_rules_enabled: bool = False


class SeedXtbInstrumentsRequest(BaseModel):
    also_add_to_watchlist: bool = True


class AutonomousPaperRunRequest(BaseModel):
    strategy_id: str = Field(default="baseline_signal_journal_v1", min_length=1, max_length=80)
    max_trades: int = Field(default=3, ge=1, le=10)


class ScheduledAutopilotRunRequest(BaseModel):
    force: bool = False


class XtbReadOnlyVerifyRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["US500"], min_length=1, max_length=20)
    timeout_s: float = Field(default=10.0, ge=2.0, le=30.0)


class AlpacaReadOnlyVerifyRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"], min_length=1, max_length=50)
    timeout_s: float = Field(default=10.0, ge=2.0, le=30.0)


class CitedEducationResearchRequest(BaseModel):
    max_sources: int = Field(default=4, ge=1, le=4)


class InvestmentRiskPolicyRequest(BaseModel):
    max_single_paper_trade_pln: float = Field(gt=0, le=100000.0)
    max_paper_position_pln: float = Field(gt=0, le=500000.0)
    max_daily_realized_loss_pln: float = Field(gt=0, le=100000.0)
    max_weekly_realized_loss_pln: float = Field(gt=0, le=500000.0)
    paper_only: bool = True
    emergency_stop: bool = False


@router.get("/cockpit")
async def investment_cockpit(request: Request) -> dict[str, Any]:
    """Return safe investment cockpit state."""
    return InvestmentCockpitService(request.app.state.settings).status()


@router.get("/education/sources")
async def investment_education_sources(request: Request) -> dict[str, Any]:
    """Zwróć wyłącznie zapisane źródła i dowód ich ostatniej weryfikacji."""
    registry = InvestmentEducationSourceRegistry(request.app.state.settings)
    sources = registry.list_sources()
    return {
        "sources": sources,
        "verified_count": sum(1 for item in sources if item.get("verification_status") == "available"),
        "learning_state": "sources_registered_not_synthesized",
        "real_trading_enabled": False,
    }


@router.post("/education/bootstrap")
async def bootstrap_investment_education_sources(request: Request) -> dict[str, Any]:
    """Zapisz katalog i sprawdź dostępność wyłącznie publicznych materiałów."""
    registry = InvestmentEducationSourceRegistry(request.app.state.settings)
    result = await registry.bootstrap_curated_sources()
    portfolio_settings = InvestmentCockpitService(request.app.state.settings).add_learning_resources(
        list(result.get("verified_runtime_urls") or [])
    )
    return {**result, "portfolio_settings": portfolio_settings}


@router.get("/education/reports")
async def investment_education_reports(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Zwróć zapisane digesty wraz z cytatami i statusem ich pochodzenia."""
    reports = InvestmentEducationResearchService(request.app.state.settings).list_reports(limit=limit)
    return {
        "reports": reports,
        "real_trading_enabled": False,
        "safe_for_paper_strategy": False,
    }


@router.post("/education/research")
async def run_investment_education_research(
    request: Request,
    payload: CitedEducationResearchRequest,
) -> dict[str, Any]:
    """Pobierz cytowany digest; raport nie tworzy sygnału ani nie zmienia strategii."""
    return await InvestmentEducationResearchService(request.app.state.settings).create_cited_digest(
        max_sources=payload.max_sources,
    )


@router.get("/risk-policy")
async def investment_risk_policy(request: Request) -> dict[str, Any]:
    """Zwróć aktywną, egzekwowaną politykę ryzyka Portfolio Alpha."""
    service = InvestmentCockpitService(request.app.state.settings)
    return {"policy": service.risk_policy(), "metrics": service.paper_loss_metrics()}


@router.put("/risk-policy")
async def update_investment_risk_policy(
    request: Request,
    payload: InvestmentRiskPolicyRequest,
) -> dict[str, Any]:
    """Zapisz hard limits wyłącznie dla paper tradingu."""
    try:
        service = InvestmentCockpitService(request.app.state.settings)
        policy = service.configure_risk_policy(
            max_single_paper_trade_pln=payload.max_single_paper_trade_pln,
            max_paper_position_pln=payload.max_paper_position_pln,
            max_daily_realized_loss_pln=payload.max_daily_realized_loss_pln,
            max_weekly_realized_loss_pln=payload.max_weekly_realized_loss_pln,
            paper_only=payload.paper_only,
            emergency_stop=payload.emergency_stop,
        )
        return {"policy": policy, "metrics": service.paper_loss_metrics()}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/watchlist")
async def investment_watchlist(request: Request) -> list[dict[str, Any]]:
    """Return investment watchlist."""
    return InvestmentCockpitService(request.app.state.settings).list_watchlist()


@router.get("/brokers")
async def investment_brokers(request: Request) -> list[dict[str, Any]]:
    """Return registered investment broker metadata. Secrets are never returned."""
    return InvestmentCockpitService(request.app.state.settings).list_broker_accounts()


@router.get("/market-data/xtb-demo/status")
async def xtb_demo_market_data_status(request: Request) -> dict[str, Any]:
    """Return XTB demo read-only market-data status without touching secrets."""
    return InvestmentCockpitService(request.app.state.settings).xtb_market_data_status()


@router.get("/market-data/alpaca-paper/status")
async def alpaca_paper_status(request: Request) -> dict[str, Any]:
    """Return Alpaca Paper read-only status without exposing credentials."""
    return InvestmentCockpitService(request.app.state.settings).alpaca_paper_status()


@router.post("/market-data/alpaca-paper/verify-read-only")
async def verify_alpaca_paper_read_only(
    request: Request,
    payload: AlpacaReadOnlyVerifyRequest,
) -> dict[str, Any]:
    """Verify Alpaca Paper account and IEX quotes without creating orders."""
    service = InvestmentCockpitService(request.app.state.settings)
    try:
        broker, api_key_id, secret = _resolve_alpaca_vault_credentials(request, service)
        adapter = AlpacaPaperReadOnlyAdapter(
            api_key_id=api_key_id,
            api_secret_key=secret,
            timeout_s=payload.timeout_s,
        )
        evidence = await adapter.verify_read_only_access(symbols=payload.symbols)
        check = service.record_alpaca_read_only_check(
            status="read_only_verified",
            symbols=payload.symbols,
            result=evidence,
        )
        return {
            "status": "read_only_verified",
            "provider_key": "alpaca-paper",
            "environment": "paper",
            "real_trading_enabled": False,
            "broker_access": "read_only_verified",
            "market_data_check": check,
            "evidence": evidence,
        }
    except (ValueError, AlpacaMarketDataError) as exc:
        service.record_alpaca_read_only_check(
            status="failed",
            symbols=payload.symbols,
            result={"error": str(exc), "real_orders_enabled": False},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/market-data/alpaca-paper/quotes")
async def alpaca_paper_quotes(
    request: Request,
    symbols: str = Query(default="SPY,QQQ", min_length=1, max_length=500),
    timeout_s: float = Query(default=10.0, ge=2.0, le=30.0),
) -> dict[str, Any]:
    """Fetch latest IEX quotes from Alpaca without exposing an order surface."""
    service = InvestmentCockpitService(request.app.state.settings)
    try:
        broker, api_key_id, secret = _resolve_alpaca_vault_credentials(request, service)
        requested_symbols = [item.strip() for item in symbols.split(",") if item.strip()]
        adapter = AlpacaPaperReadOnlyAdapter(
            api_key_id=api_key_id,
            api_secret_key=secret,
            timeout_s=timeout_s,
        )
        quotes = await adapter.get_latest_quotes(requested_symbols)
        return {
            "provider_key": "alpaca-paper",
            "environment": "paper",
            "symbols": requested_symbols,
            "quotes": quotes,
            "real_trading_enabled": False,
        }
    except (ValueError, AlpacaMarketDataError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/market-data/xtb-demo/verify-read-only")
async def verify_xtb_demo_read_only(
    request: Request,
    payload: XtbReadOnlyVerifyRequest,
) -> dict[str, Any]:
    """Verify XTB demo read-only access using password from the encrypted account vault."""
    service = InvestmentCockpitService(request.app.state.settings)
    try:
        broker, password = _resolve_xtb_vault_credentials(request, service)
        adapter = XtbReadOnlyMarketDataAdapter(
            user_id=str(broker["account_identifier"]),
            password=password,
            environment="demo",
            timeout_s=payload.timeout_s,
        )
        evidence = await adapter.verify_read_only_access(symbols=payload.symbols)
        check = service.record_xtb_read_only_check(
            status=str(evidence.get("status") or "read_only_verified"),
            symbols=payload.symbols,
            result=evidence,
        )
        return {
            "status": "read_only_verified",
            "provider_key": "xtb",
            "environment": "demo",
            "real_trading_enabled": False,
            "broker_access": "read_only_verified",
            "market_data_check": check,
            "evidence": evidence,
        }
    except XtbApiRetiredError as exc:
        service.record_xtb_read_only_check(
            status="failed",
            symbols=payload.symbols,
            result={
                "error": str(exc),
                "provider_status": "blocked_provider_api_retired",
                "real_orders_enabled": False,
            },
        )
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except (ValueError, XtbForbiddenCommandError, XtbMarketDataError) as exc:
        service.record_xtb_read_only_check(
            status="failed",
            symbols=payload.symbols,
            result={"error": str(exc), "real_orders_enabled": False},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/market-data/xtb-demo/quotes")
async def xtb_demo_quotes(
    request: Request,
    symbols: str = Query(default="US500", min_length=1, max_length=500),
    timeout_s: float = Query(default=10.0, ge=2.0, le=30.0),
) -> dict[str, Any]:
    """Fetch XTB demo quotes through the read-only adapter. No orders are possible."""
    service = InvestmentCockpitService(request.app.state.settings)
    try:
        broker, password = _resolve_xtb_vault_credentials(request, service)
        requested_symbols = [item.strip() for item in symbols.split(",") if item.strip()]
        adapter = XtbReadOnlyMarketDataAdapter(
            user_id=str(broker["account_identifier"]),
            password=password,
            environment="demo",
            timeout_s=timeout_s,
        )
        quotes = await adapter.get_quotes(requested_symbols)
        return {
            "provider_key": "xtb",
            "environment": "demo",
            "symbols": requested_symbols,
            "quotes": quotes,
            "real_trading_enabled": False,
        }
    except XtbApiRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except (ValueError, XtbForbiddenCommandError, XtbMarketDataError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/settings/portfolio-alpha")
async def portfolio_alpha_settings(request: Request) -> dict[str, Any]:
    """Return investment-specific settings for Portfolio Alpha. Secrets are never returned."""
    return InvestmentCockpitService(request.app.state.settings).get_portfolio_settings()


@router.put("/settings/portfolio-alpha")
async def update_portfolio_alpha_settings(
    request: Request,
    payload: PortfolioAlphaSettingsRequest,
) -> dict[str, Any]:
    """Save Portfolio Alpha settings without storing passwords or card numbers."""
    try:
        return InvestmentCockpitService(request.app.state.settings).configure_portfolio_settings(
            broker_name=payload.broker_name,
            broker_portal_url=payload.broker_portal_url,
            broker_account_identifier=payload.broker_account_identifier,
            credential_vault_ref=payload.credential_vault_ref,
            broker_read_access=payload.broker_read_access,
            account_mode=payload.account_mode,
            market_data_sources=payload.market_data_sources,
            learning_resources=payload.learning_resources,
            monthly_spend_budget_pln=payload.monthly_spend_budget_pln,
            payment_method_ref=payload.payment_method_ref,
            payment_method_configured=payload.payment_method_configured,
            real_trading_blocked=payload.real_trading_blocked,
            trade_approval_rules_enabled=payload.trade_approval_rules_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/brokers/xtb-demo", status_code=status.HTTP_201_CREATED)
async def configure_xtb_demo_account(request: Request, payload: XtbDemoAccountRequest) -> dict[str, Any]:
    """Register XTB demo metadata. Use /accounts/{id}/rotate-secret for encrypted password storage."""
    try:
        return InvestmentCockpitService(request.app.state.settings).configure_xtb_demo_account(
            account_identifier=payload.account_identifier,
            credential_account_id=payload.credential_account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/brokers/alpaca-paper", status_code=status.HTTP_201_CREATED)
async def configure_alpaca_paper_account(
    request: Request,
    payload: AlpacaPaperAccountRequest,
) -> dict[str, Any]:
    """Register Alpaca Paper key metadata linked to a secret in the local vault."""
    try:
        return InvestmentCockpitService(request.app.state.settings).configure_alpaca_paper_account(
            credential_account_id=payload.credential_account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/instruments")
async def investment_instruments(
    request: Request,
    provider_key: str | None = Query(default=None, max_length=80),
    enabled_only: bool = Query(default=True),
) -> list[dict[str, Any]]:
    """Return paper-only investment instruments available to the strategy sandbox."""
    return InvestmentCockpitService(request.app.state.settings).list_instruments(
        provider_key=provider_key,
        enabled_only=enabled_only,
    )


@router.post("/instruments/seed-xtb-demo", status_code=status.HTTP_201_CREATED)
async def seed_xtb_demo_instruments(request: Request, payload: SeedXtbInstrumentsRequest) -> dict[str, Any]:
    """Seed safe XTB demo instruments into the paper-only universe."""
    return InvestmentCockpitService(request.app.state.settings).seed_xtb_demo_instruments(
        also_add_to_watchlist=payload.also_add_to_watchlist,
    )


@router.post("/watchlist", status_code=status.HTTP_201_CREATED)
async def upsert_investment_watchlist_item(request: Request, payload: WatchlistItemRequest) -> dict[str, Any]:
    """Create or update a watchlist item."""
    try:
        return InvestmentCockpitService(request.app.state.settings).add_watchlist_item(
            symbol=payload.symbol,
            name=payload.name,
            asset_type=payload.asset_type,
            thesis=payload.thesis,
            risk_notes=payload.risk_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/paper-trades")
async def investment_paper_trades(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Return paper trade ledger."""
    return InvestmentCockpitService(request.app.state.settings).list_paper_trades(limit=limit)


@router.post("/paper-trades", status_code=status.HTTP_201_CREATED)
async def record_investment_paper_trade(request: Request, payload: PaperTradeRequest) -> dict[str, Any]:
    """Record a paper-only trade. No real order is sent."""
    try:
        return InvestmentCockpitService(request.app.state.settings).record_paper_trade(
            symbol=payload.symbol,
            side=payload.side,
            quantity=payload.quantity,
            price_pln=payload.price_pln,
            reason=payload.reason,
            spread_pln=payload.spread_pln,
            commission_pln=payload.commission_pln,
            slippage_pln=payload.slippage_pln,
            price_source=payload.price_source,
            quote_timestamp=payload.quote_timestamp,
            dataset_split=payload.dataset_split,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/paper-trades/{trade_id}/close")
async def close_investment_paper_trade(
    trade_id: str,
    request: Request,
    payload: PaperTradeCloseRequest,
) -> dict[str, Any]:
    """Close one paper ledger entry without sending a broker order."""
    try:
        return InvestmentCockpitService(request.app.state.settings).close_paper_trade(
            trade_id=trade_id,
            exit_price_pln=payload.exit_price_pln,
            exit_reason=payload.exit_reason,
            exit_time=payload.exit_time,
            spread_pln=payload.spread_pln,
            commission_pln=payload.commission_pln,
            slippage_pln=payload.slippage_pln,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/paper-analysis/status")
async def investment_paper_analysis_status(request: Request) -> dict[str, Any]:
    """Return evidence sufficiency and metrics without inventing missing results."""
    return InvestmentCockpitService(request.app.state.settings).paper_analysis_status()


@router.get("/paper-positions")
async def investment_paper_positions(request: Request) -> list[dict[str, Any]]:
    """Return aggregate paper positions."""
    return InvestmentCockpitService(request.app.state.settings).paper_positions()


@router.get("/strategy-runs")
async def investment_strategy_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Return autonomous paper strategy runs."""
    return InvestmentCockpitService(request.app.state.settings).list_strategy_runs(limit=limit)


@router.post("/autopilot/run-once", status_code=status.HTTP_201_CREATED)
async def run_investment_paper_autopilot(request: Request, payload: AutonomousPaperRunRequest) -> dict[str, Any]:
    """Run one autonomous paper-only trading session. No broker order is sent."""
    return InvestmentCockpitService(request.app.state.settings).run_autonomous_paper_session(
        strategy_id=payload.strategy_id,
        max_trades=payload.max_trades,
    )


@router.get("/autopilot/schedule")
async def investment_paper_autopilot_schedule(request: Request) -> dict[str, Any]:
    """Return safe paper-autopilot schedule state."""
    return InvestmentCockpitService(request.app.state.settings).autopilot_schedule_status()


@router.post("/autopilot/run-due", status_code=status.HTTP_201_CREATED)
async def run_due_investment_paper_autopilot(
    request: Request,
    payload: ScheduledAutopilotRunRequest,
) -> dict[str, Any]:
    """Run scheduled paper-autopilot if due. Real broker execution remains blocked."""
    return InvestmentCockpitService(request.app.state.settings).run_scheduled_paper_autopilot(force=payload.force)


@router.post("/real-trade")
async def blocked_real_trade(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reject every real-money trading attempt."""
    result = InvestmentCockpitService(request.app.state.settings).reject_real_trade_attempt(payload)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result)


def _resolve_xtb_vault_credentials(
    request: Request,
    service: InvestmentCockpitService,
) -> tuple[dict[str, Any], str, str]:
    broker = service.get_broker_account("xtb")
    credential_account_id = broker.get("credential_account_id")
    if not credential_account_id:
        raise ValueError("XTB credential_account_id is missing. Store the password via /accounts and link its id.")
    with request.app.state.database.session_scope() as session:
        accounts = AccountCredentialsService(session, request.app.state.settings)
        account = accounts.get_account(int(credential_account_id))
        if account is None:
            raise ValueError("Linked XTB credential account was not found.")
        if not account.has_secret:
            raise ValueError("Linked XTB credential has no secret in the account vault.")
        provider_key = account.provider_key.strip().lower()
        capabilities = set(account.allowed_capabilities)
        if provider_key not in {"xtb", "xtb-demo", "xstation", "xstation-demo"} and "financial_read_later" not in capabilities:
            raise ValueError("Linked credential is not approved for financial read access.")
        secret = accounts.resolve_secret_for_adapter(account.id)
    if not secret:
        raise ValueError("XTB password could not be resolved from the account vault.")
    return broker, secret


def _resolve_alpaca_vault_credentials(
    request: Request,
    service: InvestmentCockpitService,
) -> tuple[dict[str, Any], str]:
    broker = service.get_broker_account("alpaca-paper")
    credential_account_id = broker.get("credential_account_id")
    if not credential_account_id:
        raise ValueError("Alpaca Paper credential_account_id is missing.")
    with request.app.state.database.session_scope() as session:
        accounts = AccountCredentialsService(session, request.app.state.settings)
        account = accounts.get_account(int(credential_account_id))
        if account is None:
            raise ValueError("Linked Alpaca Paper credential was not found.")
        if not account.has_secret:
            raise ValueError("Linked Alpaca Paper credential has no secret in the local vault.")
        provider_key = account.provider_key.strip().lower()
        capabilities = set(account.allowed_capabilities)
        if provider_key not in {"alpaca", "alpaca-paper"} or "financial_read_later" not in capabilities:
            raise ValueError("Linked credential is not approved for Alpaca Paper read-only access.")
        secret_bundle = accounts.resolve_secret_for_adapter(account.id)
    if not secret_bundle:
        raise ValueError("Alpaca Paper credentials could not be resolved from the local vault.")
    try:
        parsed = json.loads(secret_bundle)
    except (TypeError, ValueError) as exc:
        raise ValueError("Alpaca Paper vault entry has an invalid format.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Alpaca Paper vault entry has an invalid format.")
    api_key_id = str(parsed.get("api_key_id") or "").strip()
    api_secret_key = str(parsed.get("api_secret_key") or "").strip()
    if not api_key_id or not api_secret_key:
        raise ValueError("Alpaca Paper vault entry is incomplete.")
    return broker, api_key_id, api_secret_key
