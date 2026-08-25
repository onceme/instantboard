import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.collectors.base import BaseCollector
from app.config import settings
from app.core.api_keys import AllKeysRateLimited, AllKeysUnavailable, APIKeyManager

logger = logging.getLogger(__name__)


class IEXCloudCollector(BaseCollector):
    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 2.0

    def __init__(self, key_manager: APIKeyManager | None = None) -> None:
        super().__init__()
        self.base_url = settings.iex_cloud_base_url.rstrip("/")
        self._api_keys: list[str] = self._load_api_keys()
        if key_manager is not None:
            self._key_manager = key_manager
        else:
            self._key_manager = APIKeyManager("iex_cloud", self._api_keys)

    def _load_api_keys(self) -> list[str]:
        if settings.iex_cloud_api_key:
            return [settings.iex_cloud_api_key]
        return []

    async def _next_key_or_none(self) -> str | None:
        """Return the next usable key, or None when the pool is exhausted/unavailable."""
        try:
            return await self._key_manager.get_key()
        except (AllKeysRateLimited, AllKeysUnavailable):
            return None

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        data_type = config.get("data_type", "stock_quote")
        symbols = config.get("symbols", [])

        if not self._api_keys:
            logger.warning("IEX Cloud API key not configured")
            return None

        api_key = await self._next_key_or_none()
        if not api_key:
            logger.warning("IEX Cloud: no usable API key available")
            return None

        match data_type:
            case "search":
                query = config.get("query", "")
                return await self._search_symbols(query, api_key)
            case _:
                return await self._fetch_stock_quotes(symbols, api_key)

    async def _fetch_stock_quotes(self, symbols: list[str], api_key: str) -> list[dict] | None:
        if not symbols:
            logger.warning("No symbols configured for IEX Cloud stock quote fetch")
            return []

        results = []
        max_attempts = max(len(self._api_keys), 1) + 1
        for symbol in symbols:
            quote = None
            for _ in range(max_attempts):
                try:
                    quote = await self._fetch_single_quote(symbol, api_key)
                    break
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status == 429:
                        logger.warning(f"IEX Cloud rate limited for {symbol}, rotating API key")
                        await self._key_manager.mark_rate_limited(api_key)
                    elif status in (401, 403):
                        logger.error(f"IEX Cloud API key invalid or forbidden for {symbol}")
                        await self._key_manager.mark_invalid(api_key)
                    else:
                        logger.warning(f"IEX Cloud HTTP error for {symbol}: {e}")
                        break
                    next_key = await self._next_key_or_none()
                    if next_key is None or (status in (401, 403) and next_key == api_key):
                        # No other usable key: fail the fetch so the caller fails over.
                        return None
                    api_key = next_key
                except Exception as e:
                    logger.warning(f"IEX Cloud fetch failed for {symbol}: {e}")
                    break
            if quote:
                results.append(quote)
            await asyncio.sleep(1.0)

        return results

    async def _fetch_single_quote(self, symbol: str, api_key: str) -> dict | None:
        url = f"{self.base_url}/stock/{quote(symbol, safe='')}/quote"
        params = {"token": api_key}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            if not data:
                logger.debug(f"IEX Cloud returned empty quote for {symbol}")
                return None

            return self._normalize_quote(symbol, data)

    def _normalize_quote(self, symbol: str, data: dict) -> dict:
        current_price = data.get("latestPrice") or data.get("close") or 0
        previous_close = data.get("previousClose") or 0
        change = data.get("change") or 0
        change_percent = data.get("changePercent") or 0

        if not change and previous_close and current_price:
            change = round(current_price - previous_close, 4)
        if change_percent:
            # IEX returns changePercent as a fraction (0.0236 == 2.36%).
            change_percent = change_percent * 100
        elif previous_close and change:
            change_percent = round((change / previous_close) * 100, 2)

        timestamp_ms = data.get("latestUpdate") or data.get("lastTradeTime") or 0
        if timestamp_ms:
            ts_str = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        market_status = "unknown"
        if data.get("isUSMarketOpen") is not None:
            market_status = "open" if data["isUSMarketOpen"] else "closed"

        return {
            "symbol": data.get("symbol") or symbol,
            "name": data.get("companyName") or symbol,
            "type": self._determine_type(symbol),
            "current_price": round(float(current_price), 2),
            "previous_close": round(float(previous_close), 2),
            "change": round(float(change), 2),
            "change_percent": round(float(change_percent), 2),
            "open": round(float(data.get("open") or 0), 2),
            "high": round(float(data.get("high") or 0), 2),
            "low": round(float(data.get("low") or 0), 2),
            "volume": int(data.get("latestVolume") or data.get("volume") or 0),
            "currency": "USD",
            "exchange": data.get("primaryExchange") or "",
            "market_status": market_status,
            "timestamp": ts_str,
            "source": "IEX Cloud",
        }

    def _determine_type(self, symbol: str) -> str:
        if symbol.startswith("^"):
            return "index"
        return "stock"

    async def _search_symbols(self, query: str, api_key: str) -> list[dict] | None:
        if not query:
            return []

        url = f"{self.base_url}/search/{quote(query, safe='')}"
        params = {"token": api_key}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()

                data = response.json()
                if not isinstance(data, list):
                    return []
                return [
                    {
                        "symbol": item.get("symbol", ""),
                        "description": item.get("securityName", ""),
                        "type": item.get("type", ""),
                        "exchange": item.get("exchange", ""),
                        "region": item.get("region", ""),
                        "currency": item.get("currency", ""),
                    }
                    for item in data
                ]
            except httpx.HTTPStatusError as e:
                await self._mark_key_error(api_key, e.response.status_code)
                logger.warning(f"IEX Cloud search error: {e}")
                return None
            except Exception as e:
                logger.warning(f"IEX Cloud search failed: {e}")
                return None

    async def _mark_key_error(self, api_key: str, status_code: int) -> None:
        if status_code == 429:
            await self._key_manager.mark_rate_limited(api_key)
        elif status_code in (401, 403):
            await self._key_manager.mark_invalid(api_key)

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if isinstance(raw_data, list):
            return raw_data
        if isinstance(raw_data, dict):
            return [raw_data]
        return []

    async def validate_data(self, items: list[dict], source: Any) -> list[dict]:
        validated = []
        for item in items:
            if item.get("symbol") and (item.get("current_price") or item.get("description")):
                validated.append(item)
        return validated
