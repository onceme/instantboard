"""Unit tests for app/collectors/finance/fund_nav_collector.py (TiantianFundCollector).

Covers registry wiring, the f10 lsjz fetch contract (Referer mandatory,
per-code 429/403/timeout tolerance), payload parsing (normal / missing fields
/ empty list / in-band error shapes) and the 天天基金 seed wiring
(finance-tab.md §3.3).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.collectors import COLLECTOR_REGISTRY, resolve_collector
from app.collectors.finance.fund_nav_collector import TiantianFundCollector

LSJZ_PAYLOAD = {
    "Data": {
        "LSJZList": [
            {
                "FSRQ": "2026-08-26",
                "DWJZ": "4.2133",
                "LJJZ": "6.0033",
                "JZZZL": "0.23",
            }
        ]
    },
    "ErrCode": 0,
    "TotalCount": 1,
}


def _make_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", "source-id")
    src.name = kwargs.get("name", "天天基金-官方NAV")
    src.url = kwargs.get("url", "")
    src.config = kwargs.get("config", {"fund_codes": ["110011"]})
    src.tenant_id = kwargs.get("tenant_id", "test-tenant")
    return src


def _mock_response(status_code: int = 200, payload: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload if payload is not None else {})
    return resp


def _mock_http_client(*responses):
    """Mock httpx.AsyncClient. Repeats a single response; pops through a sequence.

    Requests are recorded in client.calls as (url, params, headers) tuples.
    """
    client = AsyncMock()
    seq = list(responses)
    calls: list[tuple[str, dict | None, dict | None]] = []

    async def mock_get(url, params=None, headers=None):
        calls.append((url, params, headers))
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    client.get = mock_get
    client.calls = calls
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ── Registry / resolve_collector ─────────────────────────────────
class TestFundNavRegistry:
    def test_registered_in_collector_registry(self):
        assert len(COLLECTOR_REGISTRY) == 12
        assert "tiantian_fund" in COLLECTOR_REGISTRY
        assert COLLECTOR_REGISTRY["tiantian_fund"] is TiantianFundCollector

    def test_resolve_collector_library_overrides_source_type(self):
        """The seed uses source_type=api + library=tiantian_fund; bare "api"
        resolves to nothing, so the library override is what makes the source
        collectable (the collector_available check in the source service)."""
        assert resolve_collector("api", {"library": "tiantian_fund"}) is TiantianFundCollector
        assert resolve_collector("web_scrape", {"library": "tiantian_fund"}) is TiantianFundCollector
        assert resolve_collector("api", None) is None
        assert resolve_collector("api", {}) is None


class TestTiantianFundSeed:
    def test_seed_active_fund_nav_source(self):
        """The 天天基金 seed is a dedicated active fund-NAV source now (no
        longer the inactive web_scrape template)."""
        from app.db.init_db import FINANCE_SOURCES

        fund = next(src for src in FINANCE_SOURCES if src["name"] == "天天基金-官方NAV")
        assert fund["is_active"] is True
        assert fund["source_type"] == "api"
        assert fund["config"]["library"] == "tiantian_fund"
        fund_codes = fund["config"]["fund_codes"]
        assert fund_codes, "seed must carry a default fund_codes list"
        assert all(len(code) == 6 and code.isdigit() for code in fund_codes)
        assert resolve_collector(fund["source_type"], fund["config"]) is TiantianFundCollector


# ── fetch_data ────────────────────────────────────────────────────
class TestFundNavFetch:
    async def test_fetch_success_sends_referer_and_pagination(self):
        c = TiantianFundCollector()
        source = _make_source(config={"fund_codes": ["110011"]})
        mock_client = _mock_http_client(_mock_response(200, LSJZ_PAYLOAD))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert result == [{"code": "110011", "payload": LSJZ_PAYLOAD}]
        url, params, headers = mock_client.calls[0]
        assert url == TiantianFundCollector.BASE_URL
        assert params == {"fundCode": "110011", "pageIndex": 1, "pageSize": 1}
        # Mandatory: without the fundf10 referer the API answers an in-band
        # error (Data="" / ErrCode=-999) despite HTTP 200.
        assert headers["Referer"] == "https://fundf10.eastmoney.com/"
        assert headers["User-Agent"]

    async def test_fetch_merges_multiple_codes(self):
        c = TiantianFundCollector()
        other_payload = json.loads(json.dumps(LSJZ_PAYLOAD))
        other_payload["Data"]["LSJZList"][0]["DWJZ"] = "1.2340"
        source = _make_source(config={"fund_codes": ["110011", "161725"]})
        mock_client = _mock_http_client(_mock_response(200, LSJZ_PAYLOAD), _mock_response(200, other_payload))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            result = await c.fetch_data(source)

        assert [entry["code"] for entry in result] == ["110011", "161725"]
        # Courtesy delay between per-code requests.
        mock_sleep.assert_awaited_with(TiantianFundCollector.INTER_REQUEST_DELAY_SECONDS)

    async def test_fetch_no_fund_codes_returns_empty(self):
        c = TiantianFundCollector()
        for config in ({}, {"fund_codes": []}, {"fund_codes": ["", "  "]}):
            source = _make_source(config=config)
            result = await c.fetch_data(source)
            assert result == []

    async def test_fetch_429_skips_code_without_raising(self):
        c = TiantianFundCollector()
        source = _make_source(config={"fund_codes": ["110011", "161725"]})
        mock_client = _mock_http_client(_mock_response(429), _mock_response(200, LSJZ_PAYLOAD))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await c.fetch_data(source)

        # The rate-limited code is dropped, the second one still lands.
        assert [entry["code"] for entry in result] == ["161725"]

    async def test_fetch_403_skips_code_without_raising(self):
        c = TiantianFundCollector()
        source = _make_source(config={"fund_codes": ["110011"]})
        mock_client = _mock_http_client(_mock_response(403))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_timeout_skips_code_without_raising(self):
        import httpx

        c = TiantianFundCollector()
        source = _make_source(config={"fund_codes": ["110011", "161725"]})
        mock_client = _mock_http_client(_mock_response(200, LSJZ_PAYLOAD))

        call_count = 0

        async def flaky_get(url, params=None, headers=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("read timed out")
            return _mock_response(200, LSJZ_PAYLOAD)

        mock_client.get = flaky_get
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await c.fetch_data(source)

        assert [entry["code"] for entry in result] == ["161725"]

    async def test_fetch_non_200_skips_code_without_raising(self):
        c = TiantianFundCollector()
        source = _make_source(config={"fund_codes": ["110011"]})
        mock_client = _mock_http_client(_mock_response(500))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_invalid_json_skips_code_without_raising(self):
        c = TiantianFundCollector()
        source = _make_source(config={"fund_codes": ["110011"]})
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(side_effect=ValueError("not json"))
        mock_client = _mock_http_client(resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []


# ── parse_data ────────────────────────────────────────────────────
class TestFundNavParse:
    async def test_parse_normal(self):
        c = TiantianFundCollector()
        items = await c.parse_data([{"code": "110011", "payload": LSJZ_PAYLOAD}], _make_source())
        assert items == [
            {
                "symbol": "110011",
                "nav": 4.2133,
                "nav_date": "2026-08-26",
                "source": "tiantian_fund",
            }
        ]

    async def test_parse_empty_input(self):
        c = TiantianFundCollector()
        assert await c.parse_data([], _make_source()) == []
        assert await c.parse_data(None, _make_source()) == []
        assert await c.parse_data({"not": "a list"}, _make_source()) == []

    async def test_parse_empty_lsjz_list(self):
        c = TiantianFundCollector()
        payload = {"Data": {"LSJZList": []}, "ErrCode": 0}
        assert await c.parse_data([{"code": "110011", "payload": payload}], _make_source()) == []

    async def test_parse_inband_error_shapes(self):
        """The endpoint answers HTTP 200 with in-band errors: Data="" /
        ErrCode=-999 (missing referer), Data=null / ErrCode=4 (unknown fund
        code). All of them must yield no rows."""
        c = TiantianFundCollector()
        for payload in (
            {"Data": "", "ErrCode": -999, "ErrMsg": ""},
            {"Data": None, "ErrCode": 4, "ErrMsg": "404"},
            {"Data": "anything-but-a-dict"},
        ):
            assert await c.parse_data([{"code": "110011", "payload": payload}], _make_source()) == []

    async def test_parse_skips_row_with_empty_dw_jz(self):
        """Newly listed funds can publish a row with an empty DWJZ."""
        c = TiantianFundCollector()

        def _payload(dw_jz):
            return {
                "Data": {"LSJZList": [{"FSRQ": "2026-08-26", "DWJZ": dw_jz}]},
                "ErrCode": 0,
            }

        for dw_jz in ("", None, "not-a-number", "0", "-1.2"):
            assert await c.parse_data([{"code": "110011", "payload": _payload(dw_jz)}], _make_source()) == []

    async def test_parse_skips_row_with_unparsable_date(self):
        c = TiantianFundCollector()
        payload = {"Data": {"LSJZList": [{"FSRQ": "26-8-26", "DWJZ": "4.2133"}]}, "ErrCode": 0}
        assert await c.parse_data([{"code": "110011", "payload": payload}], _make_source()) == []

    async def test_parse_takes_first_parseable_row(self):
        c = TiantianFundCollector()
        payload = {
            "Data": {
                "LSJZList": [
                    {"FSRQ": "2026-08-26", "DWJZ": ""},
                    {"FSRQ": "2026-08-25", "DWJZ": "4.2035"},
                ]
            },
            "ErrCode": 0,
        }
        items = await c.parse_data([{"code": "110011", "payload": payload}], _make_source())
        assert items == [{"symbol": "110011", "nav": 4.2035, "nav_date": "2026-08-25", "source": "tiantian_fund"}]


# ── validate_data ─────────────────────────────────────────────────
class TestFundNavValidate:
    async def test_validate_keeps_complete_rows(self):
        c = TiantianFundCollector()
        good = {"symbol": "110011", "nav": 4.2, "nav_date": "2026-08-26", "source": "tiantian_fund"}
        assert await c.validate_data([good], _make_source()) == [good]

    async def test_validate_drops_incomplete_rows(self):
        c = TiantianFundCollector()
        drop_symbol = {"symbol": "", "nav": 4.2, "nav_date": "2026-08-26"}
        drop_nav = {"symbol": "110011", "nav": None, "nav_date": "2026-08-26"}
        drop_date = {"symbol": "110011", "nav": 4.2, "nav_date": ""}
        assert await c.validate_data([drop_symbol, drop_nav, drop_date], _make_source()) == []


# ── collect() end to end ──────────────────────────────────────────
class TestFundNavCollect:
    async def test_collect_success(self):
        c = TiantianFundCollector()
        c.max_retries = 1
        source = _make_source(config={"fund_codes": ["110011"]})
        mock_client = _mock_http_client(_mock_response(200, LSJZ_PAYLOAD))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)

        assert result.success is True
        assert result.items == [
            {"symbol": "110011", "nav": 4.2133, "nav_date": "2026-08-26", "source": "tiantian_fund"}
        ]

    async def test_collect_blocked_endpoint_is_empty_success(self):
        """429 across the board → no rows but a successful round (not an
        error), matching the 'log and move on' contract."""
        c = TiantianFundCollector()
        c.max_retries = 1
        source = _make_source(config={"fund_codes": ["110011"]})
        mock_client = _mock_http_client(_mock_response(429))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)

        assert result.success is True
        assert result.items == []

    async def test_collect_inband_error_is_empty_success(self):
        """Missing-referer style in-band error (HTTP 200, Data="") must not
        fail the round."""
        c = TiantianFundCollector()
        c.max_retries = 1
        source = _make_source(config={"fund_codes": ["110011"]})
        mock_client = _mock_http_client(_mock_response(200, {"Data": "", "ErrCode": -999}))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)

        assert result.success is True
        assert result.items == []


class TestFundNavCollectorConfig:
    def test_class_attributes(self):
        assert TiantianFundCollector.BASE_URL == "https://api.fund.eastmoney.com/f10/lsjz"
        assert TiantianFundCollector.REFERER == "https://fundf10.eastmoney.com/"
        assert TiantianFundCollector.SOURCE_NAME == "tiantian_fund"
        assert TiantianFundCollector.max_retries >= 1

    def test_extract_latest_nav_direct(self):
        from datetime import date

        c = TiantianFundCollector()
        assert c._extract_latest_nav(LSJZ_PAYLOAD, "110011") == {
            "nav": 4.2133,
            "nav_date": date(2026, 8, 26),
        }
        assert c._extract_latest_nav(None, "110011") is None
        assert c._extract_latest_nav("garbage", "110011") is None
