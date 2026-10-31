from __future__ import annotations

"""Rejestr źródeł edukacyjnych dla Portfolio Alpha.

Rejestr oddziela publiczne materiały referencyjne od połączeń, które należą
wyłącznie do środowiska Codexa. Zapisanie źródła nie oznacza, że Atnos
wyciągnął z niego wnioski ani że może wykonywać transakcje.
"""

from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, rows_to_dicts, utc_now_iso


CURATED_EDUCATION_SOURCES: tuple[dict[str, str], ...] = (
    {
        "source_id": "xtb_demo_education",
        "title": "XTB: konto demonstracyjne i edukacja",
        "publisher": "XTB",
        "category": "demo_practice",
        "url": "https://www.xtb.com/pl/edukacja/konto-demo",
        "access_scope": "public_reference",
        "limitation": "Materiał do nauki i konta demo. Nie jest potwierdzeniem strategii ani zgodą na realny handel.",
    },
    {
        "source_id": "investor_gov_risk",
        "title": "Investor.gov: czym jest ryzyko inwestycyjne",
        "publisher": "U.S. Securities and Exchange Commission",
        "category": "risk_basics",
        "url": "https://www.investor.gov/introduction-investing/investing-basics/what-risk",
        "access_scope": "public_reference",
        "limitation": "Edukacja o ryzyku. Nie jest indywidualną poradą inwestycyjną.",
    },
    {
        "source_id": "finra_investor_tools",
        "title": "FINRA: narzędzia i kalkulatory dla inwestorów",
        "publisher": "FINRA",
        "category": "investor_protection",
        "url": "https://www.finra.org/investors/tools-and-calculators",
        "access_scope": "public_reference",
        "limitation": "Materiał do kontroli ryzyka i analizy własnej. Nie jest feedem cen ani sygnałem transakcyjnym.",
    },
    {
        "source_id": "binance_public_market_data",
        "title": "Binance: publiczny snapshot danych BTCUSDT",
        "publisher": "Binance",
        "category": "market_data_reference",
        "url": "https://data-api.binance.vision/api/v3/ticker/24hr?symbol=BTCUSDT",
        "access_scope": "public_reference_only",
        "limitation": "Wyłącznie publiczny snapshot danych dla krypto. Nie daje dostępu do konta Binance, nie jest sygnałem i nie może sterować XTB ani składać zleceń.",
    },
    {
        "source_id": "codex_investment_banking_methodology",
        "title": "Metodologia Investment Banking w Codexie",
        "publisher": "Codex plugin",
        "category": "analysis_methodology",
        "url": "",
        "access_scope": "external_copilot_only",
        "limitation": "Dostępna dla Codexa jako metodologia pracy analitycznej. Nie jest połączeniem dostępnym dla runtime Atnosa, źródłem cen ani poradą inwestycyjną.",
    },
)


class InvestmentEducationSourceRegistry:
    """Przechowuje źródła wraz z wynikiem ich ostatniej kontroli HTTP."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_sources(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT source_id, title, publisher, category, url, access_scope,
                       limitation, verification_status, http_status, checked_at,
                       last_error, created_at, updated_at
                FROM investment_education_sources
                ORDER BY category ASC, title ASC
                """
            ).fetchall()
        return rows_to_dicts(rows)

    async def bootstrap_curated_sources(self) -> dict[str, Any]:
        """Zarejestruj katalog i sprawdź tylko publicznie dostępne adresy."""
        self._ensure_schema()
        checked_at = utc_now_iso()
        results: list[dict[str, Any]] = []

        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": "AtnosEducationRegistry/1.0"},
        ) as client:
            for source in CURATED_EDUCATION_SOURCES:
                result = await self._check_source(client, source, checked_at=checked_at)
                self._upsert_source(result)
                results.append(result)

        verified_urls = [
            item["url"]
            for item in results
            if item["verification_status"] == "available" and item.get("url")
        ]
        unavailable_count = sum(1 for item in results if item["verification_status"] == "unavailable")
        external_only_count = sum(
            1 for item in results if item["verification_status"] == "external_copilot_only"
        )
        return {
            "status": "completed" if unavailable_count == 0 else "degraded",
            "checked_at": checked_at,
            "sources": self.list_sources(),
            "verified_runtime_urls": verified_urls,
            "verified_count": len(verified_urls),
            "unavailable_count": unavailable_count,
            "external_copilot_only_count": external_only_count,
            "learning_state": "sources_registered_not_synthesized",
            "next_action": "Run a separate cited research task before changing any paper strategy.",
            "real_trading_enabled": False,
        }

    async def _check_source(
        self,
        client: httpx.AsyncClient,
        source: dict[str, str],
        *,
        checked_at: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {**source, "checked_at": checked_at, "http_status": None, "last_error": None}
        if source["access_scope"] == "external_copilot_only":
            result["verification_status"] = "external_copilot_only"
            return result

        try:
            response = await client.head(source["url"])
            if response.status_code in {403, 405}:
                response = await client.get(source["url"], headers={"Range": "bytes=0-1024"})
            result["http_status"] = response.status_code
            result["verification_status"] = "available" if 200 <= response.status_code < 400 else "unavailable"
            if result["verification_status"] == "unavailable":
                result["last_error"] = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            result["verification_status"] = "unavailable"
            result["last_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        return result

    def _upsert_source(self, source: dict[str, Any]) -> None:
        now = utc_now_iso()
        with connect(self.settings) as db:
            existing = db.execute(
                "SELECT created_at FROM investment_education_sources WHERE source_id = ?",
                (source["source_id"],),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """
                INSERT INTO investment_education_sources (
                    source_id, title, publisher, category, url, access_scope, limitation,
                    verification_status, http_status, checked_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    title = excluded.title,
                    publisher = excluded.publisher,
                    category = excluded.category,
                    url = excluded.url,
                    access_scope = excluded.access_scope,
                    limitation = excluded.limitation,
                    verification_status = excluded.verification_status,
                    http_status = excluded.http_status,
                    checked_at = excluded.checked_at,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    source["source_id"],
                    source["title"],
                    source["publisher"],
                    source["category"],
                    source["url"],
                    source["access_scope"],
                    source["limitation"],
                    source["verification_status"],
                    source.get("http_status"),
                    source["checked_at"],
                    source.get("last_error"),
                    created_at,
                    now,
                ),
            )
            db.commit()

    def _ensure_schema(self) -> None:
        with connect(self.settings) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS investment_education_sources (
                    source_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    publisher TEXT NOT NULL,
                    category TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    access_scope TEXT NOT NULL,
                    limitation TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    http_status INTEGER,
                    checked_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.commit()
