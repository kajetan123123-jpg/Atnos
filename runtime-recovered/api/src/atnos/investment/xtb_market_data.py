from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


XTB_READ_ONLY_COMMANDS = frozenset(
    {
        "login",
        "logout",
        "getServerTime",
        "getAllSymbols",
        "getSymbol",
        "getTickPrices",
        "getChartRangeRequest",
    }
)

XTB_FORBIDDEN_TRADING_COMMANDS = frozenset(
    {
        "tradeTransaction",
        "tradeTransactionStatus",
        "getTrades",
        "getTradesHistory",
        "getProfitCalculation",
        "getMarginTrade",
        "getMarginLevel",
    }
)

XTB_XAPI_RETIRED_AT = "2025-03-14"
XTB_XAPI_RETIREMENT_SOURCE = "https://developers.xstore.pro/"


class XtbMarketDataError(RuntimeError):
    """Błąd bezpiecznego adaptera danych rynkowych XTB."""


class XtbForbiddenCommandError(XtbMarketDataError):
    """Próba użycia komendy spoza allowlisty read-only."""


class XtbApiRetiredError(XtbMarketDataError):
    """XTB public xAPI is no longer available."""


@dataclass(frozen=True, slots=True)
class XtbQuote:
    symbol: str
    bid: float | None
    ask: float | None
    high: float | None
    low: float | None
    timestamp: int | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "high": self.high,
            "low": self.low,
            "timestamp": self.timestamp,
            "raw": self.raw,
        }


class XtbReadOnlyMarketDataAdapter:
    """Read-only adapter dla XTB/xStation xAPI.

    Adapter świadomie obsługuje tylko komendy odczytu. Nie ma metody składania
    zleceń, a niskopoziomowy sender blokuje każdą komendę spoza allowlisty.
    """

    DEMO_WS_URL = "wss://ws.xtb.com/demo"
    REAL_WS_URL = "wss://ws.xtb.com/real"

    def __init__(
        self,
        *,
        user_id: str,
        password: str,
        environment: str = "demo",
        timeout_s: float = 10.0,
        websocket_url: str | None = None,
        websocket_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.user_id = str(user_id or "").strip()
        self.password = str(password or "").strip()
        self.environment = self._normalize_environment(environment)
        self.timeout_s = max(2.0, min(float(timeout_s or 10.0), 30.0))
        self.websocket_url = websocket_url or (
            self.DEMO_WS_URL if self.environment == "demo" else self.REAL_WS_URL
        )
        self.websocket_factory = websocket_factory

    @classmethod
    def assert_read_only_command(cls, command: str) -> None:
        normalized = str(command or "").strip()
        if normalized in XTB_FORBIDDEN_TRADING_COMMANDS:
            raise XtbForbiddenCommandError(f"XTB command is forbidden for Atnos: {normalized}")
        if normalized not in XTB_READ_ONLY_COMMANDS:
            raise XtbForbiddenCommandError(f"XTB command is not on the read-only allowlist: {normalized}")

    async def verify_read_only_access(self, *, symbols: list[str] | None = None) -> dict[str, Any]:
        """Zaloguj się i wykonaj minimalny odczyt rynku bez zleceń."""
        safe_symbols = self._normalize_symbols(symbols or ["US500"])

        async def _operation(ws: Any) -> dict[str, Any]:
            server_time = await self._send_command(ws, "getServerTime")
            quote_payload = await self._send_command(
                ws,
                "getTickPrices",
                {"symbols": safe_symbols, "timestamp": 0},
            )
            quotes = self._parse_quotes(quote_payload)
            return {
                "status": "read_only_verified",
                "environment": self.environment,
                "symbols": safe_symbols,
                "server_time": server_time.get("returnData"),
                "quote_count": len(quotes),
                "quotes": [quote.to_dict() for quote in quotes],
                "allowed_commands": sorted(XTB_READ_ONLY_COMMANDS),
                "forbidden_trading_commands": sorted(XTB_FORBIDDEN_TRADING_COMMANDS),
                "real_orders_enabled": False,
            }

        return await self._with_login(_operation)

    async def get_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Pobierz aktualne ticki dla listy symboli bez możliwości transakcji."""
        safe_symbols = self._normalize_symbols(symbols)

        async def _operation(ws: Any) -> list[dict[str, Any]]:
            payload = await self._send_command(
                ws,
                "getTickPrices",
                {"symbols": safe_symbols, "timestamp": 0},
            )
            return [quote.to_dict() for quote in self._parse_quotes(payload)]

        return await self._with_login(_operation)

    async def get_symbol(self, symbol: str) -> dict[str, Any]:
        """Pobierz metadane jednego instrumentu."""
        safe_symbol = self._normalize_symbols([symbol])[0]

        async def _operation(ws: Any) -> dict[str, Any]:
            payload = await self._send_command(ws, "getSymbol", {"symbol": safe_symbol})
            data = payload.get("returnData")
            return data if isinstance(data, dict) else {}

        return await self._with_login(_operation)

    async def _with_login(self, operation: Callable[[Any], Awaitable[Any]]) -> Any:
        self._validate_credentials()
        async with self._connect() as ws:
            await self._send_command(
                ws,
                "login",
                {
                    "userId": self.user_id,
                    "password": self.password,
                    "appName": "Atnos read-only market data",
                },
            )
            try:
                return await operation(ws)
            finally:
                try:
                    await self._send_command(ws, "logout")
                except XtbMarketDataError:
                    pass

    def _connect(self) -> Any:
        if self.websocket_factory is not None:
            return self.websocket_factory(self.websocket_url)
        raise XtbApiRetiredError(
            "XTB xAPI was retired on 2025-03-14; ws.xtb.com no longer accepts API connections. "
            "Keep XTB Demo manual and configure a supported read-only market-data provider for paper trading."
        )

    async def _send_command(
        self,
        ws: Any,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.assert_read_only_command(command)
        payload: dict[str, Any] = {"command": command}
        if arguments:
            payload["arguments"] = arguments
        await ws.send(json.dumps(payload, ensure_ascii=False))
        raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise XtbMarketDataError("XTB returned invalid JSON.") from exc
        if not isinstance(response, dict):
            raise XtbMarketDataError("XTB returned a non-object response.")
        if response.get("status") is False:
            raise XtbMarketDataError(self._safe_error_message(response))
        return response

    @staticmethod
    def _parse_quotes(payload: dict[str, Any]) -> list[XtbQuote]:
        raw = payload.get("returnData")
        if isinstance(raw, dict):
            raw_items = [raw]
        elif isinstance(raw, list):
            raw_items = [item for item in raw if isinstance(item, dict)]
        else:
            raw_items = []
        return [
            XtbQuote(
                symbol=str(item.get("symbol") or ""),
                bid=_optional_float(item.get("bid")),
                ask=_optional_float(item.get("ask")),
                high=_optional_float(item.get("high")),
                low=_optional_float(item.get("low")),
                timestamp=_optional_int(item.get("timestamp")),
                raw={key: value for key, value in item.items() if key != "password"},
            )
            for item in raw_items
            if str(item.get("symbol") or "").strip()
        ]

    @staticmethod
    def _safe_error_message(response: dict[str, Any]) -> str:
        error_code = str(response.get("errorCode") or "").strip()
        error_descr = str(response.get("errorDescr") or "").strip()
        message = "XTB command failed"
        if error_code:
            message += f" ({error_code})"
        if error_descr:
            message += f": {error_descr[:240]}"
        return message

    @staticmethod
    def _normalize_symbols(symbols: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in symbols:
            symbol = str(item or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            if len(symbol) > 32:
                raise ValueError("XTB symbol is too long.")
            if not all(char.isalnum() or char in "._-" for char in symbol):
                raise ValueError("XTB symbol contains unsupported characters.")
            normalized.append(symbol)
            seen.add(symbol)
        if not normalized:
            raise ValueError("At least one XTB symbol is required.")
        return normalized[:20]

    @staticmethod
    def _normalize_environment(value: str) -> str:
        normalized = str(value or "demo").strip().lower()
        if normalized not in {"demo", "real"}:
            raise ValueError("XTB environment must be 'demo' or 'real'.")
        return normalized

    def _validate_credentials(self) -> None:
        if not self.user_id:
            raise XtbMarketDataError("XTB user id is missing.")
        if not self.password:
            raise XtbMarketDataError("XTB password is missing from the account vault.")


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
