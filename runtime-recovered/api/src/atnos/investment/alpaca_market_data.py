from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"


class AlpacaMarketDataError(RuntimeError):
    """Blad bezpiecznego adaptera Alpaca Paper read-only."""


@dataclass(frozen=True, slots=True)
class AlpacaLatestQuote:
    symbol: str
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    timestamp: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "timestamp": self.timestamp,
        }


class AlpacaPaperReadOnlyAdapter:
    """Adapter Alpaca Paper bez metod skladania, zmiany i anulowania zlecen."""

    def __init__(
        self,
        *,
        api_key_id: str,
        api_secret_key: str,
        timeout_s: float = 10.0,
        trading_base_url: str = ALPACA_PAPER_BASE_URL,
        data_base_url: str = ALPACA_DATA_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key_id = str(api_key_id or "").strip()
        self.api_secret_key = str(api_secret_key or "").strip()
        self.timeout_s = max(2.0, min(float(timeout_s or 10.0), 30.0))
        self.trading_base_url = trading_base_url.rstrip("/")
        self.data_base_url = data_base_url.rstrip("/")
        self.transport = transport

    async def verify_read_only_access(self, *, symbols: list[str] | None = None) -> dict[str, Any]:
        """Sprawdz konto paper, pozycje i aktualne ceny bez wysylania zlecen."""
        safe_symbols = self._normalize_symbols(symbols or ["SPY", "QQQ"])
        account = await self.get_account()
        positions = await self.get_positions()
        quotes = await self.get_latest_quotes(safe_symbols)
        return {
            "status": "read_only_verified",
            "environment": "paper",
            "account_status": str(account.get("status") or "unknown"),
            "currency": str(account.get("currency") or "USD"),
            "trading_blocked": bool(account.get("trading_blocked")),
            "account_blocked": bool(account.get("account_blocked")),
            "position_count": len(positions),
            "symbols": safe_symbols,
            "quote_count": len(quotes),
            "quotes": quotes,
            "allowed_operations": ["get_account", "get_positions", "get_latest_quotes"],
            "forbidden_operations": ["place_order", "replace_order", "cancel_order", "close_position"],
            "real_orders_enabled": False,
        }

    async def get_account(self) -> dict[str, Any]:
        payload = await self._get(f"{self.trading_base_url}/v2/account")
        if not isinstance(payload, dict):
            raise AlpacaMarketDataError("Alpaca returned an invalid account response.")
        return payload

    async def get_positions(self) -> list[dict[str, Any]]:
        payload = await self._get(f"{self.trading_base_url}/v2/positions")
        if not isinstance(payload, list):
            raise AlpacaMarketDataError("Alpaca returned an invalid positions response.")
        return [item for item in payload if isinstance(item, dict)]

    async def get_latest_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        safe_symbols = self._normalize_symbols(symbols)
        payload = await self._get(
            f"{self.data_base_url}/v2/stocks/quotes/latest",
            params={"symbols": ",".join(safe_symbols), "feed": "iex"},
        )
        raw_quotes = payload.get("quotes") if isinstance(payload, dict) else None
        if not isinstance(raw_quotes, dict):
            raise AlpacaMarketDataError("Alpaca returned an invalid latest-quotes response.")
        quotes: list[dict[str, Any]] = []
        for symbol in safe_symbols:
            item = raw_quotes.get(symbol)
            if not isinstance(item, dict):
                continue
            quotes.append(
                AlpacaLatestQuote(
                    symbol=symbol,
                    bid=_optional_float(item.get("bp")),
                    ask=_optional_float(item.get("ap")),
                    bid_size=_optional_float(item.get("bs")),
                    ask_size=_optional_float(item.get("as")),
                    timestamp=str(item.get("t")) if item.get("t") is not None else None,
                ).to_dict()
            )
        return quotes

    async def _get(self, url: str, *, params: dict[str, str] | None = None) -> Any:
        self._validate_credentials()
        headers = {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret_key,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise AlpacaMarketDataError(f"Alpaca API is unreachable: {exc}") from exc
        if response.status_code in {401, 403}:
            raise AlpacaMarketDataError("Alpaca Paper API rejected the API key or secret.")
        if response.status_code >= 400:
            request_id = response.headers.get("x-request-id")
            suffix = f" Request ID: {request_id}." if request_id else ""
            raise AlpacaMarketDataError(f"Alpaca API returned HTTP {response.status_code}.{suffix}")
        try:
            return response.json()
        except ValueError as exc:
            raise AlpacaMarketDataError("Alpaca API returned invalid JSON.") from exc

    def _validate_credentials(self) -> None:
        if not self.api_key_id:
            raise AlpacaMarketDataError("Alpaca Paper API key ID is missing.")
        if not self.api_secret_key:
            raise AlpacaMarketDataError("Alpaca Paper API secret key is missing from the local vault.")

    @staticmethod
    def _normalize_symbols(symbols: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in symbols:
            symbol = str(value or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            if len(symbol) > 16 or not all(char.isalnum() or char in ".-" for char in symbol):
                raise ValueError("Alpaca symbol contains unsupported characters.")
            normalized.append(symbol)
            seen.add(symbol)
        if not normalized:
            raise ValueError("At least one Alpaca symbol is required.")
        return normalized[:50]


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
