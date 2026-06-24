import logging
import time
from typing import Any

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class EastMoneyCollector(BaseCollector):
    timeout_seconds = 15
    max_retries = 3
    retry_base_delay_seconds = 1.0
    rate_limit_per_minute = 10

    MARKET_INDEX_API = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    STOCK_API = "https://push2.eastmoney.com/api/qt/stock/get"

    CN_MARKET_INDICES = {
        "1.000001": "上证指数",
        "0.399001": "深证成指",
        "1.000300": "沪深300",
        "1.000016": "上证50",
        "0.399006": "创业板指",
    }

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        symbols = config.get("symbols", [])
        data_type = config.get("data_type", "cn_indices")

        if data_type == "cn_indices":
            return await self._fetch_market_indices()
        elif data_type == "cn_stock":
            return await self._fetch_cn_stocks(symbols)
        else:
            return await self._fetch_market_indices()

    async def _fetch_market_indices_params(self) -> dict:
        secids = ",".join(self.CN_MARKET_INDICES.keys())
        return {
            "fltt": 2,
            "secids": secids,
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21",
            "_": str(int(time.time() * 1000)),
        }

    async def _fetch_market_indices(self) -> list[dict]:
        params = await self._fetch_market_indices_params()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(self.MARKET_INDEX_API, params=params)
                if response.status_code != 200:
                    logger.warning(f"EastMoney API returned {response.status_code}")
                    return []

                data = response.json()
                diff = data.get("data", {}).get("diff", [])
                if not diff:
                    return []

                results = []
                for item in diff:
                    secid = item.get("f12", "")
                    name = item.get("f14", "")
                    current = item.get("f2", 0)
                    change = item.get("f3", 0)
                    change_pct = item.get("f4", 0)
                    volume = item.get("f5", 0)
                    turnover = item.get("f6", 0)

                    if isinstance(current, str):
                        current = float(current) if current != "-" else 0
                    if isinstance(change_pct, str):
                        change_pct = float(change_pct) if change_pct != "-" else 0

                    results.append(
                        {
                            "symbol": secid,
                            "name": name,
                            "type": "cn_index",
                            "current_price": round(current, 2),
                            "change": round(change, 2),
                            "change_percent": round(change_pct, 2),
                            "volume": volume,
                            "turnover": turnover,
                            "region": "CN",
                            "currency": "CNY",
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "source": "东方财富",
                        }
                    )
                return results
            except httpx.TimeoutException:
                logger.warning("EastMoney timeout for market indices")
                return []
            except httpx.HTTPError as e:
                logger.warning(f"EastMoney HTTP error for market indices: {e}")
                return []

    async def _fetch_cn_stocks(self, symbols: list[str]) -> list[dict]:
        results = []
        for symbol in symbols:
            try:
                secid = self._convert_symbol_to_secid(symbol)
                quote = await self._fetch_single_stock(secid)
                if quote:
                    results.append(quote)
            except Exception as e:
                logger.warning(f"EastMoney fetch failed for {symbol}: {e}")
        return results

    async def _fetch_single_stock(self, secid: str) -> dict | None:
        params = {
            "secid": secid,
            "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f116,f117,f170",
            "_": str(int(time.time() * 1000)),
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(self.STOCK_API, params=params)
                if response.status_code != 200:
                    return None

                data = response.json().get("data", {})
                if not data:
                    return None

                current = float(data.get("f43", 0)) / 100 if data.get("f43") else 0
                previous = float(data.get("f60", 0)) / 100 if data.get("f60") else 0
                change = current - previous
                change_pct = (change / previous * 100) if previous else 0

                return {
                    "symbol": data.get("f57", secid),
                    "name": data.get("f58", ""),
                    "type": "cn_stock",
                    "current_price": round(current, 2),
                    "previous_close": round(previous, 2),
                    "change": round(change, 2),
                    "change_percent": round(change_pct, 2),
                    "volume": data.get("f47", 0),
                    "turnover": data.get("f48", 0),
                    "market_cap": data.get("f116", 0),
                    "currency": "CNY",
                    "region": "CN",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": "东方财富",
                }
            except Exception:
                return None

    def _convert_symbol_to_secid(self, symbol: str) -> str:
        if symbol in self.CN_MARKET_INDICES:
            return symbol
        if symbol.endswith(".SS"):
            code = symbol.replace(".SS", "")
            return f"1.{code}"
        if symbol.endswith(".SZ"):
            code = symbol.replace(".SZ", "")
            return f"0.{code}"
        return symbol

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
