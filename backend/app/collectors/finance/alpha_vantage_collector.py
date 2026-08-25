import logging
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.config import settings
from app.core.api_keys import AllKeysRateLimited, AllKeysUnavailable, APIKeyManager

logger = logging.getLogger(__name__)


class AlphaVantageCollector(BaseCollector):
    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 2.0
    rate_limit_per_minute = 5
    base_url = "https://www.alphavantage.co/query"

    def __init__(self, key_manager: APIKeyManager | None = None) -> None:
        super().__init__()
        self._api_keys: list[str] = self._load_api_keys()
        if key_manager is not None:
            self._key_manager = key_manager
        else:
            self._key_manager = APIKeyManager("alpha_vantage", self._api_keys)

    def _load_api_keys(self) -> list[str]:
        if settings.alpha_vantage_api_keys:
            keys = [key.strip() for key in settings.alpha_vantage_api_keys.split(",") if key.strip()]
            if keys:
                return keys
        if settings.alpha_vantage_api_key:
            return [settings.alpha_vantage_api_key]
        return []

    async def _next_key_or_none(self) -> str | None:
        """Return the next usable key, or None when the pool is exhausted/unavailable."""
        try:
            return await self._key_manager.get_key()
        except (AllKeysRateLimited, AllKeysUnavailable):
            return None

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        if not self._api_keys:
            logger.warning("Alpha Vantage API key not configured")
            return None

        symbols = config.get("symbols", [])
        function = config.get("function", "TIME_SERIES_INTRADAY")

        results = []
        for symbol in symbols:
            api_key = await self._next_key_or_none()
            if api_key is None:
                logger.warning("Alpha Vantage: no usable API key left, stopping batch")
                break
            try:
                quote = await self._fetch_quote(symbol, function, api_key)
                if quote:
                    results.append(quote)
                await asyncio_sleep(12)
            except Exception as e:
                logger.warning(f"Alpha Vantage fetch failed for {symbol}: {e}")

        return results

    async def _fetch_quote(self, symbol: str, function: str, api_key: str) -> dict | None:
        params = {
            "function": function,
            "symbol": symbol,
            "apikey": api_key,
            "outputsize": "compact",
        }
        if function == "TIME_SERIES_INTRADAY":
            params["interval"] = "5min"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(self.base_url, params=params)
                if response.status_code == 429:
                    logger.warning(f"Alpha Vantage rate limited for {symbol}")
                    await self._key_manager.mark_rate_limited(api_key)
                    return None
                if response.status_code in (401, 403):
                    logger.error(f"Alpha Vantage API key invalid or forbidden for {symbol}")
                    await self._key_manager.mark_invalid(api_key)
                    return None
                if response.status_code != 200:
                    logger.warning(f"Alpha Vantage API returned {response.status_code} for {symbol}")
                    return None

                data = response.json()
                if "error" in data or "Note" in data:
                    logger.warning(f"Alpha Vantage error for {symbol}: {data.get('error', data.get('Note', ''))}")
                    return None

                return self._parse_response(symbol, function, data)
            except httpx.TimeoutException:
                logger.warning(f"Alpha Vantage timeout for {symbol}")
                return None
            except httpx.HTTPError as e:
                logger.warning(f"Alpha Vantage HTTP error for {symbol}: {e}")
                return None

    def _parse_response(self, symbol: str, function: str, data: dict) -> dict | None:
        if function == "TIME_SERIES_INTRADAY":
            time_series_key = None
            for key in data:
                if "Time Series" in key:
                    time_series_key = key
                    break
            if not time_series_key:
                return None

            series = data[time_series_key]
            latest_time = sorted(series.keys(), reverse=True)[0]
            latest = series[latest_time]

            return {
                "symbol": symbol,
                "name": data.get("Meta Data", {}).get("2. Symbol", symbol),
                "type": "stock",
                "current_price": float(latest.get("4. close", 0)),
                "open": float(latest.get("1. open", 0)),
                "high": float(latest.get("2. high", 0)),
                "low": float(latest.get("3. low", 0)),
                "volume": int(latest.get("5. volume", 0)),
                "timestamp": latest_time,
                "source": "Alpha Vantage",
            }
        elif function == "GLOBAL_QUOTE":
            quote = data.get("Global Quote", {})
            if not quote:
                return None

            current = float(quote.get("05. price", 0))
            previous = float(quote.get("08. previous close", 0))
            change = float(quote.get("09. change", 0))
            change_pct_str = quote.get("10. change percent", "0%")
            change_pct = float(change_pct_str.replace("%", ""))

            return {
                "symbol": symbol,
                "name": symbol,
                "type": "stock",
                "current_price": current,
                "previous_close": previous,
                "change": change,
                "change_percent": change_pct,
                "volume": int(quote.get("06. volume", 0)),
                "timestamp": quote.get("07. latest trading day", ""),
                "source": "Alpha Vantage",
            }

        return None

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if isinstance(raw_data, list):
            return raw_data
        if isinstance(raw_data, dict):
            return [raw_data]
        return []

    async def validate_data(self, items: list[dict], source: Any) -> list[dict]:
        validated = []
        for item in items:
            if item.get("symbol") and item.get("current_price"):
                validated.append(item)
        return validated


async def asyncio_sleep(seconds: float):
    import asyncio

    await asyncio.sleep(seconds)
