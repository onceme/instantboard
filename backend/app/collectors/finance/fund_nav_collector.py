import asyncio
import logging
from datetime import date
from typing import Any

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class TiantianFundCollector(BaseCollector):
    """Official NAV collector for Chinese mutual funds (天天基金 / EastMoney).

    Endpoint: https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=1
    — the EastMoney f10 historical-NAV JSON API. Chosen over scraping the
    https://fundf10.eastmoney.com/jjjz_{code}.html page: it is the very data
    source that page renders, it returns structured JSON (no DOM fragility)
    and it verified reachable from staging (2026-08-27). Caveat: the endpoint
    answers HTTP 200 but with an in-band error (Data="" / ErrCode=-999) unless
    the request carries a Referer from the fundf10.eastmoney.com site, so that
    header is mandatory.

    Fund codes come from config.fund_codes and are fetched one by one and
    merged; a 429/403/timeout on one code is logged and skipped so a single
    blocked code never breaks the whole round. Items carry {symbol, nav,
    nav_date, source} (nav_date is an ISO date string); validate_data is
    overridden because the generic title/url contract does not apply to NAV
    rows. When run through the generic collect_{source_id} items pipeline the
    rows are dropped by the FilterProcessor like the other finance seeds —
    the NAV display chain consumes them via FinanceService.update_official_nav
    instead (finance-tab.md §3.3).
    """

    timeout_seconds = 10
    max_retries = 2
    retry_base_delay_seconds = 1.0
    rate_limit_per_minute = 30

    BASE_URL = "https://api.fund.eastmoney.com/f10/lsjz"
    # Mandatory: without a fundf10.eastmoney.com referer the API answers an
    # in-band error (Data="" / ErrCode=-999) despite HTTP 200.
    REFERER = "https://fundf10.eastmoney.com/"
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 instantboard-collector/1.0"
    )
    SOURCE_NAME = "tiantian_fund"
    # Courtesy delay between per-code requests (once-a-day job, few codes).
    INTER_REQUEST_DELAY_SECONDS = 0.5

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        fund_codes = [str(code).strip() for code in (config.get("fund_codes") or []) if str(code).strip()]
        if not fund_codes:
            logger.warning(f"No fund_codes configured for source {getattr(source, 'name', 'unknown')}")
            return []

        headers = {
            "Referer": self.REFERER,
            "User-Agent": str(config.get("user_agent") or self.DEFAULT_USER_AGENT),
            "Accept": "application/json, text/plain, */*",
        }

        fetched: list[dict] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for index, code in enumerate(fund_codes):
                if index > 0:
                    await asyncio.sleep(self.INTER_REQUEST_DELAY_SECONDS)
                try:
                    payload = await self._fetch_single_nav(client, code, headers)
                except httpx.TimeoutException:
                    logger.warning(f"Tiantian fund NAV fetch timed out for {code}, skipping")
                    continue
                except httpx.HTTPError as e:
                    logger.warning(f"Tiantian fund NAV fetch failed for {code}: {e}")
                    continue
                if payload is not None:
                    fetched.append({"code": code, "payload": payload})
        return fetched

    async def _fetch_single_nav(self, client: httpx.AsyncClient, code: str, headers: dict) -> dict | None:
        params = {"fundCode": code, "pageIndex": 1, "pageSize": 1}
        response = await client.get(self.BASE_URL, params=params, headers=headers)

        if response.status_code in (403, 429):
            logger.warning(
                f"Tiantian fund NAV blocked/rate limited ({response.status_code}) for {code}; skipping this code"
            )
            return None
        if response.status_code != 200:
            logger.warning(f"Tiantian fund NAV HTTP {response.status_code} for {code}; skipping this code")
            return None

        try:
            return response.json()
        except (ValueError, TypeError) as e:
            logger.warning(f"Tiantian fund NAV invalid JSON for {code}: {e}")
            return None

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if not isinstance(raw_data, list):
            return []

        items: list[dict] = []
        for entry in raw_data:
            code = str(entry.get("code") or "")
            nav_info = self._extract_latest_nav(entry.get("payload"), code)
            if nav_info is None:
                continue
            items.append(
                {
                    "symbol": code,
                    "nav": nav_info["nav"],
                    "nav_date": nav_info["nav_date"].isoformat(),
                    "source": self.SOURCE_NAME,
                }
            )
        return items

    def _extract_latest_nav(self, payload: Any, code: str) -> dict | None:
        """Latest official unit NAV + NAV date from one lsjz payload.

        The API signals failures in-band with HTTP 200 (Data null/"" and a
        non-zero ErrCode, e.g. -999 without a referer, 4 for unknown fund
        codes); any of those shapes yields None instead of a row. Rows with an
        empty/non-numeric DWJZ or an unparsable FSRQ are skipped too.
        """
        if not isinstance(payload, dict):
            return None

        data = payload.get("Data")
        if not isinstance(data, dict):
            logger.debug(
                f"Tiantian fund NAV in-band error for {code}: "
                f"ErrCode={payload.get('ErrCode')} ErrMsg={payload.get('ErrMsg')!r}"
            )
            return None

        for row in data.get("LSJZList") or []:
            if not isinstance(row, dict):
                continue
            try:
                nav = float(row.get("DWJZ") or 0)
            except (TypeError, ValueError):
                continue
            if nav <= 0:
                continue
            try:
                nav_date = date.fromisoformat(str(row.get("FSRQ") or "").strip())
            except ValueError:
                continue
            return {"nav": nav, "nav_date": nav_date}
        return None

    async def validate_data(self, items: list[dict], source: Any) -> list[dict]:
        return [item for item in items if item.get("symbol") and item.get("nav") and item.get("nav_date")]
