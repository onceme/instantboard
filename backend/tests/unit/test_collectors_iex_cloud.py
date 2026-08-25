"""Unit tests for app/collectors/finance/iex_cloud_collector.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.collectors import COLLECTOR_REGISTRY, get_collector, resolve_collector
from app.collectors.finance.iex_cloud_collector import IEXCloudCollector
from app.core.api_keys import APIKeyManager, key_hash
from app.core.redis import RedisKeys


def _make_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", "source-id")
    src.name = kwargs.get("name", "Test Source")
    src.url = kwargs.get("url", "https://example.com")
    src.config = kwargs.get("config", {})
    src.tenant_id = kwargs.get("tenant_id", "test-tenant")
    src.category = kwargs.get("category", MagicMock())
    return src


def _http_error(status_code: int, url: str = "https://cloud.iexapis.com/stable") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def _mock_response(status_code: int = 200, json_data=None, error: httpx.HTTPStatusError | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    if error is not None:
        resp.raise_for_status = MagicMock(side_effect=error)
    else:
        resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


def _mock_http_client(*responses):
    """Mock httpx.AsyncClient. Repeats a single response; pops through a sequence.

    Requests are recorded in ``client.calls`` as (url, params) tuples.
    """
    client = AsyncMock()
    seq = list(responses)
    calls: list[tuple[str, dict | None]] = []

    async def mock_get(url, params=None):
        calls.append((url, params))
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    client.get = mock_get
    client.calls = calls
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _sleep_patch():
    return patch("app.collectors.finance.iex_cloud_collector.asyncio.sleep", new_callable=AsyncMock)


IEX_QUOTE_PAYLOAD = {
    "symbol": "AAPL",
    "companyName": "Apple Inc.",
    "primaryExchange": "NASDAQ",
    "open": 148.35,
    "high": 151.2,
    "low": 147.9,
    "latestPrice": 150.5,
    "latestSource": "Close",
    "latestUpdate": 1629921600000,
    "latestVolume": 75000000,
    "previousClose": 148.0,
    "previousVolume": 72000000,
    "change": 2.5,
    "changePercent": 0.0169,
    "volume": 75000000,
    "isUSMarketOpen": False,
}


# ── Registry ──────────────────────────────────────────────────────
class TestIEXCloudRegistry:
    def test_registered_in_collector_registry(self):
        assert "iex_cloud" in COLLECTOR_REGISTRY
        assert get_collector("iex_cloud") is IEXCloudCollector

    def test_resolve_collector_library_fallback(self):
        assert resolve_collector("api", {"library": "iex_cloud"}) is IEXCloudCollector
        assert resolve_collector("api", {"library": "iex_cloud", "data_type": "stock_quote"}) is IEXCloudCollector

    def test_resolve_collector_unknown_library_still_none(self):
        assert resolve_collector("api", {"library": "nonexistent"}) is None


# ── Key handling ──────────────────────────────────────────────────
class TestIEXCloudKeyHandling:
    def test_load_api_keys_from_settings(self):
        with patch("app.collectors.finance.iex_cloud_collector.settings") as mock_settings:
            mock_settings.iex_cloud_api_key = "iex-key"
            mock_settings.iex_cloud_base_url = "https://cloud.iexapis.com/stable"
            c = IEXCloudCollector()
        assert c._api_keys == ["iex-key"]

    def test_load_api_keys_unset(self):
        with patch("app.collectors.finance.iex_cloud_collector.settings") as mock_settings:
            mock_settings.iex_cloud_api_key = None
            mock_settings.iex_cloud_base_url = "https://cloud.iexapis.com/stable"
            c = IEXCloudCollector()
        assert c._api_keys == []

    async def test_fetch_no_api_key_returns_none(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=[]):
            c = IEXCloudCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        result = await c.fetch_data(source)
        assert result is None

    async def test_fetch_all_keys_invalid_returns_none(self, redis_mock):
        manager = APIKeyManager("iex_cloud", ["key1"], redis_client=redis_mock)
        await manager.mark_invalid("key1")
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["key1"]):
            c = IEXCloudCollector(key_manager=manager)
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        result = await c.fetch_data(source)
        assert result is None


# ── stock_quote ───────────────────────────────────────────────────
class TestIEXCloudStockQuote:
    async def test_fetch_stock_quote_parses_payload(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        mock_client = _mock_http_client(_mock_response(200, IEX_QUOTE_PAYLOAD))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            _sleep_patch(),
        ):
            result = await c.fetch_data(source)

        assert result is not None
        assert len(result) == 1
        quote = result[0]
        assert quote["symbol"] == "AAPL"
        assert quote["name"] == "Apple Inc."
        assert quote["type"] == "stock"
        assert quote["current_price"] == 150.5
        assert quote["previous_close"] == 148.0
        assert quote["change"] == 2.5
        # IEX changePercent is a fraction: 0.0169 -> 1.69 (%)
        assert quote["change_percent"] == 1.69
        assert quote["open"] == 148.35
        assert quote["high"] == 151.2
        assert quote["low"] == 147.9
        assert quote["volume"] == 75000000
        assert quote["currency"] == "USD"
        assert quote["exchange"] == "NASDAQ"
        assert quote["market_status"] == "closed"
        # latestUpdate is epoch milliseconds (UTC)
        assert quote["timestamp"] == "2021-08-25T20:00:00Z"
        assert quote["source"] == "IEX Cloud"

    async def test_quote_request_url_and_token(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        mock_client = _mock_http_client(_mock_response(200, IEX_QUOTE_PAYLOAD))
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            await c.fetch_data(source)

        url, params = mock_client.calls[0]
        assert url == "https://cloud.iexapis.com/stable/stock/AAPL/quote"
        assert params == {"token": "test-key"}

    async def test_base_url_override_used(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        c.base_url = "https://sandbox.iexapis.com/stable"
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        mock_client = _mock_http_client(_mock_response(200, IEX_QUOTE_PAYLOAD))
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            await c.fetch_data(source)

        assert mock_client.calls[0][0].startswith("https://sandbox.iexapis.com/stable/stock/")

    async def test_quote_missing_change_derived_from_previous_close(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        payload = {
            "symbol": "MSFT",
            "companyName": "Microsoft Corp.",
            "latestPrice": 300.0,
            "previousClose": 290.0,
            # no change / changePercent / latestUpdate
        }
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["MSFT"]})
        mock_client = _mock_http_client(_mock_response(200, payload))
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)

        quote = result[0]
        assert quote["change"] == 10.0
        assert quote["change_percent"] == 3.45
        assert quote["volume"] == 0
        assert quote["market_status"] == "unknown"
        # falls back to now() in UTC format
        assert quote["timestamp"].endswith("Z") and len(quote["timestamp"]) == 20

    async def test_empty_quote_response_skipped(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["EMPTY"]})
        mock_client = _mock_http_client(_mock_response(200, {}))
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)
        assert result == []

    async def test_no_symbols_returns_empty_list(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": []})
        result = await c.fetch_data(source)
        assert result == []

    async def test_default_data_type_is_stock_quote(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        source = _make_source(config={"symbols": ["AAPL"]})
        mock_client = _mock_http_client(_mock_response(200, IEX_QUOTE_PAYLOAD))
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    async def test_multiple_symbols_collected(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        msft_payload = dict(IEX_QUOTE_PAYLOAD, symbol="MSFT", companyName="Microsoft Corp.")
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL", "MSFT"]})
        mock_client = _mock_http_client(_mock_response(200, IEX_QUOTE_PAYLOAD), _mock_response(200, msft_payload))
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)
        assert [quote["symbol"] for quote in result] == ["AAPL", "MSFT"]


# ── 429/401 key marking ───────────────────────────────────────────
class TestIEXCloudKeyRotation:
    async def test_429_marks_rate_limited_and_rotates(self, redis_mock):
        manager = APIKeyManager("iex_cloud", ["key1", "key2"], redis_client=redis_mock)
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["key1", "key2"]):
            c = IEXCloudCollector(key_manager=manager)
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        error_resp = _mock_response(429, error=_http_error(429))
        ok_resp = _mock_response(200, IEX_QUOTE_PAYLOAD)
        mock_client = _mock_http_client(error_resp, ok_resp)
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)

        assert result is not None and len(result) == 1
        limited_key = RedisKeys.APIKEY_LIMITED.format(service="iex_cloud", key_hash=key_hash("key1"))
        assert await redis_mock.exists(limited_key) == 1
        # key2 was never marked
        limited_key2 = RedisKeys.APIKEY_LIMITED.format(service="iex_cloud", key_hash=key_hash("key2"))
        assert await redis_mock.exists(limited_key2) == 0

    async def test_429_single_key_returns_none(self, redis_mock):
        manager = APIKeyManager("iex_cloud", ["key1"], redis_client=redis_mock)
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["key1"]):
            c = IEXCloudCollector(key_manager=manager)
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        error_resp = _mock_response(429, error=_http_error(429))
        mock_client = _mock_http_client(error_resp)
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)

        assert result is None
        limited_key = RedisKeys.APIKEY_LIMITED.format(service="iex_cloud", key_hash=key_hash("key1"))
        assert await redis_mock.exists(limited_key) == 1

    async def test_401_marks_invalid_and_fails_fetch(self, redis_mock):
        manager = APIKeyManager("iex_cloud", ["key1"], redis_client=redis_mock)
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["key1"]):
            c = IEXCloudCollector(key_manager=manager)
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        error_resp = _mock_response(401, error=_http_error(401))
        mock_client = _mock_http_client(error_resp)
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)

        assert result is None
        invalid_key = RedisKeys.APIKEY_INVALID.format(service="iex_cloud", key_hash=key_hash("key1"))
        assert await redis_mock.exists(invalid_key) == 1

    async def test_403_marks_invalid_and_fails_fetch(self, redis_mock):
        manager = APIKeyManager("iex_cloud", ["key1"], redis_client=redis_mock)
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["key1"]):
            c = IEXCloudCollector(key_manager=manager)
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        error_resp = _mock_response(403, error=_http_error(403))
        mock_client = _mock_http_client(error_resp)
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)

        assert result is None
        invalid_key = RedisKeys.APIKEY_INVALID.format(service="iex_cloud", key_hash=key_hash("key1"))
        assert await redis_mock.exists(invalid_key) == 1

    async def test_other_http_error_skips_symbol_without_marking(self, redis_mock):
        manager = APIKeyManager("iex_cloud", ["key1"], redis_client=redis_mock)
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["key1"]):
            c = IEXCloudCollector(key_manager=manager)
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["UNKNOWN"]})
        error_resp = _mock_response(404, error=_http_error(404))
        mock_client = _mock_http_client(error_resp)
        with patch("httpx.AsyncClient", return_value=mock_client), _sleep_patch():
            result = await c.fetch_data(source)

        assert result == []
        # 404 must not mark the key rate limited or invalid (only the rotation counter exists)
        limited_key = RedisKeys.APIKEY_LIMITED.format(service="iex_cloud", key_hash=key_hash("key1"))
        invalid_key = RedisKeys.APIKEY_INVALID.format(service="iex_cloud", key_hash=key_hash("key1"))
        assert await redis_mock.exists(limited_key) == 0
        assert await redis_mock.exists(invalid_key) == 0


# ── search ────────────────────────────────────────────────────────
class TestIEXCloudSearch:
    async def test_search_success_maps_fields(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        source = _make_source(config={"data_type": "search", "query": "Apple"})
        search_payload = [
            {
                "symbol": "AAPL",
                "securityName": "Apple Inc.",
                "exchange": "Nasdaq Global Select",
                "type": "cs",
                "region": "US",
                "currency": "USD",
            }
        ]
        mock_client = _mock_http_client(_mock_response(200, search_payload))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert result is not None and len(result) == 1
        assert result[0] == {
            "symbol": "AAPL",
            "description": "Apple Inc.",
            "type": "cs",
            "exchange": "Nasdaq Global Select",
            "region": "US",
            "currency": "USD",
        }

    async def test_search_empty_query_returns_empty(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["test-key"]):
            c = IEXCloudCollector()
        source = _make_source(config={"data_type": "search", "query": ""})
        result = await c.fetch_data(source)
        assert result == []

    async def test_search_error_marks_key_and_returns_none(self, redis_mock):
        manager = APIKeyManager("iex_cloud", ["key1"], redis_client=redis_mock)
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=["key1"]):
            c = IEXCloudCollector(key_manager=manager)
        source = _make_source(config={"data_type": "search", "query": "Apple"})
        error_resp = _mock_response(429, error=_http_error(429))
        mock_client = _mock_http_client(error_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert result is None
        limited_key = RedisKeys.APIKEY_LIMITED.format(service="iex_cloud", key_hash=key_hash("key1"))
        assert await redis_mock.exists(limited_key) == 1


# ── parse/validate ────────────────────────────────────────────────
class TestIEXCloudParseValidate:
    async def test_parse_data_list_dict_none(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=[]):
            c = IEXCloudCollector()
        source = _make_source()
        assert await c.parse_data([{"symbol": "AAPL"}], source) == [{"symbol": "AAPL"}]
        assert await c.parse_data({"symbol": "AAPL"}, source) == [{"symbol": "AAPL"}]
        assert await c.parse_data(None, source) == []

    async def test_validate_data(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=[]):
            c = IEXCloudCollector()
        valid = await c.validate_data(
            [
                {"symbol": "AAPL", "current_price": 150.5},
                {"symbol": "AAPL", "description": "Apple Inc."},
                {"symbol": "", "current_price": 1.0},
                {"name": "no symbol"},
            ],
            _make_source(),
        )
        assert len(valid) == 2

    async def test_determine_type(self):
        with patch.object(IEXCloudCollector, "_load_api_keys", return_value=[]):
            c = IEXCloudCollector()
        assert c._determine_type("AAPL") == "stock"
        assert c._determine_type("^GSPC") == "index"
