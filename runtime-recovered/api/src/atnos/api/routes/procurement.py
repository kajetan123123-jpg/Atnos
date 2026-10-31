from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from atnos.procurement.autonomy_planner import AutonomyPlanner
from atnos.procurement.service import ProcurementBlockedError, ProcurementService
from atnos.procurement.smart_spending import SmartSpendingService
from atnos.production.expense_notifier import ExpenseNotifier

router = APIRouter(prefix="/api/procurement", tags=["procurement"])


class ProcurementRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=2000)
    account_id: str | None = None
    estimated_monthly_cost_pln: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcurementAccessReadyRequest(BaseModel):
    access_note: str = Field(default="", max_length=1000)


class AutonomyPlanProposeRequest(BaseModel):
    account_id: str | None = None
    include_optional: bool = True
    max_requests: int = Field(default=5, ge=1, le=20)


class SmartSpendingRevenueRequest(BaseModel):
    amount_pln: float = Field(gt=0)
    reason: str = Field(min_length=3, max_length=2000)
    account_id: str | None = None
    source_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
async def procurement_status(request: Request) -> dict[str, Any]:
    """Zwróć katalog narzędzi, politykę zakupową i ostatnie wnioski."""
    return ProcurementService(request.app.state.settings).status()


@router.get("/catalog")
async def procurement_catalog(request: Request) -> list[dict[str, Any]]:
    """Zwróć dozwolony katalog narzędzi, które Atnos może rekomendować."""
    return ProcurementService(request.app.state.settings).catalog()


@router.get("/smart-spending/status")
async def smart_spending_status(request: Request) -> dict[str, Any]:
    """Zwroc kase operacyjna Atnosa: saldo, przychody, wydatki i polityke."""
    return SmartSpendingService(request.app.state.settings).status()


@router.get("/smart-spending/awareness")
async def smart_spending_awareness(request: Request) -> dict[str, Any]:
    """Zwroc krotka pamiec finansowa, ktora agent ma brac pod uwage przy decyzjach."""
    return SmartSpendingService(request.app.state.settings).awareness()


@router.get("/card-access/check")
async def card_access_check(request: Request) -> dict[str, Any]:
    """Sprawdz, czy Atnos ma realny dostep do karty. Bezposredni dostep jest zabroniony."""
    return SmartSpendingService(request.app.state.settings).card_access_status()


@router.get("/smart-spending/ledger")
async def smart_spending_ledger(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Zwroc ledger przychodow i wydatkow smart spending."""
    return SmartSpendingService(request.app.state.settings).list_entries(limit=limit)


@router.post("/smart-spending/revenue")
async def record_smart_spending_revenue(request: Request, payload: SmartSpendingRevenueRequest) -> dict[str, Any]:
    """Dodaj przychod, ktory zwieksza saldo do dalszych wydatkow."""
    try:
        return SmartSpendingService(request.app.state.settings).record_revenue(
            amount_pln=payload.amount_pln,
            reason=payload.reason,
            account_id=payload.account_id,
            source_id=payload.source_id,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"status": "blocked", "message": str(exc)}) from exc


@router.get("/autonomy-plan")
async def procurement_autonomy_plan(
    request: Request,
    account_id: str | None = None,
    include_optional: bool = True,
) -> dict[str, Any]:
    """Zbuduj plan potrzeb, zakupow i dostepow dla autonomii Atnosa."""
    with request.app.state.database.session_scope() as session:
        return AutonomyPlanner(request.app.state.settings, session=session).build_plan(
            account_id=account_id,
            include_optional=include_optional,
        )


@router.post("/autonomy-plan/propose")
async def propose_autonomy_plan_requests(request: Request, payload: AutonomyPlanProposeRequest) -> dict[str, Any]:
    """Utworz requesty zakupowe z brakujacych elementow planu autonomii."""
    try:
        with request.app.state.database.session_scope() as session:
            return AutonomyPlanner(request.app.state.settings, session=session).propose_missing_purchases(
                account_id=payload.account_id,
                include_optional=payload.include_optional,
                max_requests=payload.max_requests,
            )
    except ProcurementBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.payload) from exc


@router.get("/requests")
async def procurement_requests(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return ProcurementService(request.app.state.settings).list_requests(status=status_filter, limit=limit)


@router.get("/expense-notifications")
async def procurement_expense_notifications(
    request: Request,
    delivery_status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Zwroc outbox powiadomien o kosztach i wnioskach zakupowych."""
    return ExpenseNotifier(request.app.state.settings).list_notifications(
        delivery_status=delivery_status,
        limit=limit,
    )


@router.post("/expense-notifications/dispatch-gmail")
async def dispatch_procurement_expense_notifications(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Wyslij oczekujace powiadomienia kosztowe przez Gmail OAuth, jesli dostep dziala."""
    with request.app.state.database.session_scope() as session:
        return ExpenseNotifier(request.app.state.settings).dispatch_pending_gmail(session, limit=limit)


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def create_procurement_request(request: Request, payload: ProcurementRequest) -> dict[str, Any]:
    """Utwórz wniosek zakupowy. Nie przyjmuje i nie zapisuje danych karty."""
    try:
        return ProcurementService(request.app.state.settings).propose(
            provider_id=payload.provider_id,
            reason=payload.reason,
            account_id=payload.account_id,
            estimated_monthly_cost_pln=payload.estimated_monthly_cost_pln,
            metadata=payload.metadata,
        )
    except ProcurementBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.payload) from exc


@router.post("/requests/{request_id}/approve-manual-checkout")
async def approve_procurement_manual_checkout(request_id: str, request: Request) -> dict[str, Any]:
    """Zatwierdź wniosek do ręcznego checkoutu u dostawcy."""
    try:
        return ProcurementService(request.app.state.settings).approve_for_manual_checkout(request_id)
    except ProcurementBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.payload) from exc


@router.post("/requests/{request_id}/access-ready")
async def mark_procurement_access_ready(
    request_id: str,
    request: Request,
    payload: ProcurementAccessReadyRequest,
) -> dict[str, Any]:
    """Oznacz, że dostęp/API key został skonfigurowany poza danymi karty."""
    try:
        return ProcurementService(request.app.state.settings).mark_access_ready(
            request_id,
            access_note=payload.access_note,
        )
    except ProcurementBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.payload) from exc
