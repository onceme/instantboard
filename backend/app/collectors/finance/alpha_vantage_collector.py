import logging
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.config import settings

logger = logging.getLogger(__name__)


class AlphaVantageCollector(BaseCollector):
    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 2.0
    rate_limit_per_minute = 5
    base_url = "https://www.alphavantage.co/query"

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        api_key = config.get("api_key_env", "ALPHA_VANTAGE_API_KEY")
        key_value = getattr(settings, api_key.lower(), None) or getattr(settings, "alpha_vantage_api_key", None)
        if not key_value:
            logger.warning("Alpha Vantage API key not configured")
            return None

        symbols = config.get("symbols", [])
        function = config.get("function", "TIME_SERIES_INTRADAY")

        results = []
        for symbol in symbols:
            try:
                quote = await self._fetch_quote(symbol, function, key_value)
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
