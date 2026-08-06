import logging
import time
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.core.constants import YAHOO_BROWSER_HEADERS

logger = logging.getLogger(__name__)


class YFinanceCollector(BaseCollector):
    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 1.0

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        symbols = config.get("symbols", [])
        if not symbols:
            logger.warning(f"No symbols configured for source {getattr(source, 'name', 'unknown')}")
            return []

        results = []
        for symbol in symbols:
            try:
                quote = await self._fetch_symbol(symbol)
                if quote:
                    results.append(quote)
            except Exception as e:
                logger.warning(f"yfinance fetch failed for {symbol}: {e}")

        return results

    async def _fetch_symbol(self, symbol: str) -> dict | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "interval": "1d",
            "range": "5d",
            "includePrePost": "false",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                # Yahoo rate-limits default client fingerprints (429); send browser-like
                # headers to improve the success rate.
                response = await client.get(url, params=params, headers=YAHOO_BROWSER_HEADERS)
                if response.status_code == 429:
                    # Rate-limiting is not a failure of this service; log a warning and let
                    # failover advance to the next data source.
                    logger.warning(f"yfinance rate-limited (429) for {symbol}, falling over")
                    return None
                if response.status_code != 200:
                    logger.warning(f"yfinance API returned {response.status_code} for {symbol}")
                    return None

                data = response.json()
                chart = data.get("chart", {})
                result = chart.get("result", [])
                if not result:
                    return None

                meta = result[0].get("meta", {})
                return self._parse_meta(symbol, meta)
            except httpx.TimeoutException:
                logger.warning(f"yfinance timeout for {symbol}")
                return None
            except httpx.HTTPError as e:
                logger.warning(f"yfinance HTTP error for {symbol}: {e}")
                return None

    def _parse_meta(self, symbol: str, meta: dict) -> dict:
        current_price = meta.get("regularMarketPrice", 0)
        previous_close = meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0)
        change = current_price - previous_close if previous_close else 0
        change_percent = (change / previous_close * 100) if previous_close else 0

        return {
            "symbol": symbol,
            "name": meta.get("shortName", symbol),
            "type": self._determine_type(symbol),
            "current_price": round(current_price, 2),
            "previous_close": round(previous_close, 2),
            "change": round(change, 2),
            "change_percent": round(change_percent, 2),
            "volume": meta.get("regularMarketVolume", 0),
            "market_cap": meta.get("marketCap", 0),
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("exchangeName", ""),
            "market_status": meta.get("currentTradingPeriod", {}).get("regular", {}).get("marketState", "unknown"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _determine_type(self, symbol: str) -> str:
        if symbol.startswith("^"):
            return "index"
        commodity_symbols = {"GC=F", "SI=F", "CL=F", "NG=F", "HG=F", "ZS=F", "ZC=F"}
        if symbol in commodity_symbols:
            return "commodity"
        if symbol.endswith(".SS") or symbol.endswith(".SZ"):
            return "cn_stock"
        return "stock"

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
