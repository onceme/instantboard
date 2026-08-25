import asyncio
import logging
import time
from datetime import UTC
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.config import settings
from app.core.api_keys import AllKeysRateLimited, AllKeysUnavailable, APIKeyManager

logger = logging.getLogger(__name__)


class FinnhubCollector(BaseCollector):
    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 2.0
    rate_limit_per_minute = 60
    base_url = "https://finnhub.io/api/v1"

    MARKET_INDICES = {
        "^GSPC": {"name": "S&P 500", "region": "US"},
        "^DJI": {"name": "Dow Jones Industrial Average", "region": "US"},
        "^IXIC": {"name": "NASDAQ Composite", "region": "US"},
    }

    COMMODITY_SYMBOLS = {
        "GOLD": {"name": "Gold", "unit": "USD/oz"},
        "SILVER": {"name": "Silver", "unit": "USD/oz"},
        "CL=F": {"name": "Crude Oil WTI", "unit": "USD/bbl"},
    }

    def __init__(self, key_manager: APIKeyManager | None = None) -> None:
        super().__init__()
        self._api_keys: list[str] = self._load_api_keys()
        if key_manager is not None:
            self._key_manager = key_manager
        else:
            self._key_manager = APIKeyManager("finnhub", self._api_keys)

    def _load_api_keys(self) -> list[str]:
        keys: list[str] = []
        if settings.finnhub_api_keys:
            keys.extend(settings.finnhub_api_keys)
        if settings.finnhub_api_key and settings.finnhub_api_key not in keys:
            keys.append(settings.finnhub_api_key)
        return keys

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

        api_key = await self._next_key_or_none()
        if not api_key:
            logger.error("Finnhub API key not configured or no usable key available")
            return None

        match data_type:
            case "stock_quote":
                return await self._fetch_stock_quotes(symbols, api_key)
            case "market_indices":
                return await self._fetch_market_indices(api_key)
            case "commodities":
                return await self._fetch_commodities(symbols, api_key)
            case "search":
                query = config.get("query", "")
                return await self._search_symbols(query, api_key)
            case "company_profile":
                return await self._fetch_company_profiles(symbols, api_key)
            case _:
                return await self._fetch_stock_quotes(symbols, api_key)

    async def _fetch_stock_quotes(self, symbols: list[str], api_key: str) -> list[dict] | None:
        if not symbols:
            logger.warning("No symbols configured for Finnhub stock quote fetch")
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
                        logger.warning(f"Finnhub rate limited for {symbol}, rotating API key")
                        await self._key_manager.mark_rate_limited(api_key)
                    elif status in (401, 403):
                        logger.error(f"Finnhub API key invalid or forbidden for {symbol}")
                        await self._key_manager.mark_invalid(api_key)
                    else:
                        logger.warning(f"Finnhub HTTP error for {symbol}: {e}")
                        break
                    next_key = await self._next_key_or_none()
                    if next_key is None or (status in (401, 403) and next_key == api_key):
                        # No other usable key: fail the fetch so the caller fails over,
                        # matching the previous "bad key → collection fails" semantics.
                        return None
                    api_key = next_key
                except Exception as e:
                    logger.warning(f"Finnhub fetch failed for {symbol}: {e}")
                    break
            if quote:
                results.append(quote)
            await asyncio.sleep(1.0)

        return results

    async def _fetch_single_quote(self, symbol: str, api_key: str) -> dict | None:
        url = f"{self.base_url}/quote"
        params = {"symbol": symbol, "token": api_key}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            if not data or data.get("c", 0) == 0:
                logger.debug(f"Finnhub returned empty quote for {symbol}")
                return None

            company_name = await self._fetch_company_name(symbol, api_key)
            return self._normalize_quote(symbol, data, company_name)

    async def _fetch_company_name(self, symbol: str, api_key: str) -> str:
        try:
            url = f"{self.base_url}/stock/profile2"
            params = {"symbol": symbol, "token": api_key}
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("name", symbol)
        except Exception:
            pass
        return symbol

    def _normalize_quote(self, symbol: str, data: dict, name: str | None = None) -> dict:
        current_price = data.get("c", 0)
        previous_close = data.get("pc", 0)
        change = data.get("d", 0)
        change_percent = data.get("dp", 0)

        if not change and previous_close:
            change = round(current_price - previous_close, 4)
        if not change_percent and previous_close:
            change_percent = round((change / previous_close) * 100, 2)

        timestamp = data.get("t", 0)
        if timestamp:
            from datetime import datetime

            ts_str = datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        return {
            "symbol": symbol,
            "name": name or symbol,
            "type": self._determine_type(symbol),
            "current_price": round(float(current_price), 2),
            "previous_close": round(float(previous_close), 2),
            "change": round(float(change), 2),
            "change_percent": round(float(change_percent), 2),
            "open": round(float(data.get("o", 0)), 2),
            "high": round(float(data.get("h", 0)), 2),
            "low": round(float(data.get("l", 0)), 2),
            "volume": 0,
            "currency": "USD",
            "exchange": "",
            "market_status": "unknown",
            "timestamp": ts_str,
            "source": "Finnhub",
        }

    def _determine_type(self, symbol: str) -> str:
        if symbol.startswith("^"):
            return "index"
        commodity_symbols = {"GC=F", "SI=F", "CL=F", "NG=F", "HG=F", "ZS=F", "ZC=F"}
        if symbol in commodity_symbols or symbol in self.COMMODITY_SYMBOLS:
            return "commodity"
        if symbol.endswith(".SS") or symbol.endswith(".SZ"):
            return "cn_stock"
        return "stock"

    async def _fetch_market_indices(self, api_key: str) -> list[dict]:
        results = []
        for symbol, info in self.MARKET_INDICES.items():
            try:
                quote = await self._fetch_single_quote(symbol, api_key)
                if quote:
                    quote["name"] = info["name"]
                    quote["region"] = info["region"]
                    quote["type"] = "index"
                    results.append(quote)
                await asyncio.sleep(1.0)
            except httpx.HTTPStatusError as e:
                await self._mark_key_error(api_key, e.response.status_code)
                logger.warning(f"Finnhub market index fetch failed for {symbol}: {e}")
            except Exception as e:
                logger.warning(f"Finnhub market index fetch failed for {symbol}: {e}")

        return results

    async def _mark_key_error(self, api_key: str, status_code: int) -> None:
        if status_code == 429:
            await self._key_manager.mark_rate_limited(api_key)
        elif status_code in (401, 403):
            await self._key_manager.mark_invalid(api_key)

    async def _fetch_commodities(self, symbols: list[str], api_key: str) -> list[dict]:
        target_symbols = symbols if symbols else list(self.COMMODITY_SYMBOLS.keys())
        results = []

        for symbol in target_symbols:
            try:
                quote = await self._fetch_single_quote(symbol, api_key)
                if quote:
                    commodity_info = self.COMMODITY_SYMBOLS.get(symbol, {})
                    quote["name"] = commodity_info.get("name", symbol)
                    quote["type"] = "commodity"
                    quote["unit"] = commodity_info.get("unit", "")
                    results.append(quote)
                else:
                    logger.debug(f"Finnhub commodity {symbol} returned empty, trying fallback")
                await asyncio.sleep(1.0)
            except httpx.HTTPStatusError as e:
                await self._mark_key_error(api_key, e.response.status_code)
                if e.response.status_code in (401, 403):
                    logger.error(f"Finnhub API key invalid for commodity {symbol}")
                else:
                    logger.warning(f"Finnhub commodity fetch error for {symbol}: {e}")
            except Exception as e:
                logger.warning(f"Finnhub commodity fetch failed for {symbol}: {e}")

        return results

    async def _search_symbols(self, query: str, api_key: str) -> list[dict] | None:
        if not query:
            return []

        url = f"{self.base_url}/search"
        params = {"q": query, "token": api_key}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()

                data = response.json()
                results = data.get("result", [])
                return [
                    {
                        "symbol": item.get("symbol", ""),
                        "description": item.get("description", ""),
                        "type": item.get("type", ""),
                        "mic": item.get("mic", ""),
                        "displaySymbol": item.get("displaySymbol", ""),
                    }
                    for item in results
                ]
            except httpx.HTTPStatusError as e:
                await self._mark_key_error(api_key, e.response.status_code)
                logger.warning(f"Finnhub search error: {e}")
                return None
            except Exception as e:
                logger.warning(f"Finnhub search failed: {e}")
                return None

    async def _fetch_company_profiles(self, symbols: list[str], api_key: str) -> list[dict]:
        results = []
        for symbol in symbols:
            try:
                profile = await self._fetch_company_profile(symbol, api_key)
                if profile:
                    results.append(profile)
                await asyncio.sleep(1.0)
            except httpx.HTTPStatusError as e:
                await self._mark_key_error(api_key, e.response.status_code)
                logger.warning(f"Finnhub company profile failed for {symbol}: {e}")
            except Exception as e:
                logger.warning(f"Finnhub company profile failed for {symbol}: {e}")

        return results

    async def _fetch_company_profile(self, symbol: str, api_key: str) -> dict | None:
        url = f"{self.base_url}/stock/profile2"
        params = {"symbol": symbol, "token": api_key}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            if not data:
                return None

            return {
                "symbol": symbol,
                "name": data.get("name", ""),
                "ticker": data.get("ticker", symbol),
                "exchange": data.get("exchange", ""),
                "currency": data.get("currency", "USD"),
                "market_cap": data.get("marketCapitalization", 0),
                "country": data.get("country", ""),
                "industry": data.get("finnhubIndustry", ""),
                "ipo_date": data.get("ipo", ""),
                "share_outstanding": data.get("shareOutstanding", 0),
                "logo": data.get("logo", ""),
                "weburl": data.get("weburl", ""),
            }

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
