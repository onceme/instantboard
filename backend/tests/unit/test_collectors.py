"""Unit tests for app/collectors package."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.collectors import COLLECTOR_REGISTRY, get_collector
from app.collectors.base import BaseCollector, CollectionResult
from app.collectors.finance.alpha_vantage_collector import AlphaVantageCollector, asyncio_sleep
from app.collectors.finance.eastmoney_collector import EastMoneyCollector
from app.collectors.finance.finnhub_collector import FinnhubCollector
from app.collectors.finance.yfinance_collector import YFinanceCollector
from app.collectors.tech.arxiv_collector import ArxivCollector
from app.collectors.tech.hackernews_collector import HackerNewsCollector
from app.collectors.tech.rss_collector import RSSCollector, _parse_feedparser_date
from app.core.api_keys import APIKeyManager


def _make_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", "source-id")
    src.name = kwargs.get("name", "Test Source")
    src.url = kwargs.get("url", "https://example.com")
    src.config = kwargs.get("config", {})
    src.tenant_id = kwargs.get("tenant_id", "test-tenant")
    src.category = kwargs.get("category", MagicMock())
    return src


# ── COLLECTOR_REGISTRY ────────────────────────────────────────────
class TestCollectorRegistry:
    def test_all_collectors_registered(self):
        assert len(COLLECTOR_REGISTRY) == 9
        assert "yfinance" in COLLECTOR_REGISTRY
        assert "alpha_vantage" in COLLECTOR_REGISTRY
        assert "eastmoney" in COLLECTOR_REGISTRY
        assert "finnhub" in COLLECTOR_REGISTRY
        assert "iex_cloud" in COLLECTOR_REGISTRY
        assert "rss" in COLLECTOR_REGISTRY
        assert "hackernews" in COLLECTOR_REGISTRY
        assert "arxiv" in COLLECTOR_REGISTRY
        assert "reddit" in COLLECTOR_REGISTRY

    def test_get_collector_known_type(self):
        assert get_collector("yfinance") is YFinanceCollector
        assert get_collector("rss") is RSSCollector

    def test_get_collector_unknown_type_returns_none(self):
        assert get_collector("nonexistent") is None


# ── BaseCollector ────────────────────────────────────────────────
class TestBaseCollector:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseCollector()

    def test_content_hash(self):
        class ConcreteCollector(BaseCollector):
            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCollector()
        h1 = c._content_hash("title", "url")
        h2 = c._content_hash("title", "url")
        h3 = c._content_hash("other", "url")
        assert h1 == h2
        assert h1 != h3
        assert isinstance(h1, str)
        assert len(h1) == 32

    async def test_collect_success(self):
        class ConcreteCol(BaseCollector):
            max_retries = 1

            async def fetch_data(self, source):
                return [{"title": "t", "url": "u"}]

            async def parse_data(self, raw_data, source):
                return raw_data

        c = ConcreteCol()
        source = _make_source()
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert isinstance(result, CollectionResult)
        assert result.success is True

    async def test_collect_rate_limited(self):
        class ConcreteCol(BaseCollector):
            max_retries = 1
            rate_limit_per_minute = 10

            async def fetch_data(self, source):
                return []

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        source = _make_source()
        with patch.object(c, "rate_limit_check", new_callable=AsyncMock, return_value=False):
            result = await c.collect(source)
        assert result.success is False
        assert "Rate limit" in result.error

    async def test_collect_all_retries_fail(self):
        class ConcreteCol(BaseCollector):
            max_retries = 2
            retry_base_delay_seconds = 0.01

            async def fetch_data(self, source):
                raise ValueError("fail")

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        source = _make_source()
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is False
        assert "retry" in result.error.lower()

    async def test_collect_parse_error(self):
        class ConcreteCol(BaseCollector):
            max_retries = 1

            async def fetch_data(self, source):
                return {"data": "x"}

            async def parse_data(self, raw_data, source):
                raise RuntimeError("parse fail")

        c = ConcreteCol()
        source = _make_source()
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is False
        assert "parse fail" in result.error

    async def test_collect_validation_error(self):
        class ConcreteCol(BaseCollector):
            max_retries = 1

            async def fetch_data(self, source):
                return []

            async def parse_data(self, raw_data, source):
                return [{"title": "t", "url": "u"}]

            async def validate_data(self, items, source):
                raise RuntimeError("valid fail")

        c = ConcreteCol()
        source = _make_source()
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is False
        assert "valid fail" in result.error

    async def test_validate_data_default_filters_missing_fields(self):
        class ConcreteCol(BaseCollector):
            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        valid = await c.validate_data([{"title": "ok", "url": "http://x"}, {"title": "", "url": "x"}], _make_source())
        assert len(valid) == 1

    async def test_rate_limit_check_no_limit(self):
        class ConcreteCol(BaseCollector):
            rate_limit_per_minute = 0

            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        result = await c.rate_limit_check(_make_source())
        assert result is True

    async def test_rate_limit_check_within_limit(self):
        class ConcreteCol(BaseCollector):
            rate_limit_per_minute = 10

            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value="5"),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.rate_limit_check(_make_source())
        assert result is True

    async def test_rate_limit_check_exceeded(self):
        class ConcreteCol(BaseCollector):
            rate_limit_per_minute = 10

            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        with patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value="10"):
            result = await c.rate_limit_check(_make_source())
        assert result is False

    async def test_rate_limit_check_no_existing_count(self):
        class ConcreteCol(BaseCollector):
            rate_limit_per_minute = 10

            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.rate_limit_check(_make_source())
        assert result is True

    async def test_rate_limit_check_redis_unavailable(self):
        class ConcreteCol(BaseCollector):
            rate_limit_per_minute = 10

            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        with patch("app.collectors.base.redis_get", new_callable=AsyncMock, side_effect=Exception("redis down")):
            result = await c.rate_limit_check(_make_source())
        assert result is True

    async def test_record_health_success_no_existing(self):
        class ConcreteCol(BaseCollector):
            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        source = _make_source()
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            await c.record_health(source, True, 100)

    async def test_record_health_with_existing(self):
        import json

        class ConcreteCol(BaseCollector):
            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        source = _make_source()
        existing = json.dumps(
            {
                "status": "healthy",
                "consecutive_failures": 0,
                "total_fetches_24h": 1,
                "success_count_24h": 1,
                "avg_response_time_ms": 50,
            }
        )
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=existing),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            await c.record_health(source, True, 100)

    async def test_record_health_failures_degraded(self):
        import json

        class ConcreteCol(BaseCollector):
            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        source = _make_source()
        existing = json.dumps(
            {
                "status": "healthy",
                "consecutive_failures": 0,
                "total_fetches_24h": 5,
                "success_count_24h": 5,
                "avg_response_time_ms": 50,
            }
        )
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=existing),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            await c.record_health(source, False, 1000, "some error")

    async def test_record_health_failures_down(self):
        import json

        class ConcreteCol(BaseCollector):
            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        source = _make_source()
        existing = json.dumps(
            {
                "status": "degraded",
                "consecutive_failures": 9,
                "total_fetches_24h": 10,
                "success_count_24h": 1,
                "avg_response_time_ms": 50,
            }
        )
        with (
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=existing),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            await c.record_health(source, False, 1000, "some error")

    async def test_record_health_redis_failure(self):
        class ConcreteCol(BaseCollector):
            async def fetch_data(self, source):
                return None

            async def parse_data(self, raw_data, source):
                return []

        c = ConcreteCol()
        source = _make_source()
        with patch("app.collectors.base.redis_get", new_callable=AsyncMock, side_effect=Exception("no redis")):
            await c.record_health(source, True, 100)


# ── YFinanceCollector ────────────────────────────────────────────
class TestYFinanceCollector:
    async def test_fetch_data_success(self):
        c = YFinanceCollector()
        source = _make_source(config={"symbols": ["AAPL"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 150,
                            "chartPreviousClose": 148,
                            "shortName": "Apple",
                            "regularMarketVolume": 1000,
                            "currency": "USD",
                            "exchangeName": "NASDAQ",
                        }
                    }
                ]
            }
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["current_price"] == 150

    async def test_fetch_data_no_symbols(self):
        c = YFinanceCollector()
        source = _make_source(config={"symbols": []})
        result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_data_api_not_200(self):
        c = YFinanceCollector()
        source = _make_source(config={"symbols": ["XYZ"]})
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_data_empty_chart(self):
        c = YFinanceCollector()
        source = _make_source(config={"symbols": ["XYZ"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"chart": {"result": []}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_determine_types(self):
        c = YFinanceCollector()
        assert c._determine_type("^GSPC") == "index"
        assert c._determine_type("GC=F") == "commodity"
        assert c._determine_type("000001.SS") == "cn_stock"
        assert c._determine_type("AAPL") == "stock"

    async def test_parse_data_list(self):
        c = YFinanceCollector()
        result = await c.parse_data([{"a": 1}], _make_source())
        assert result == [{"a": 1}]

    async def test_parse_data_dict(self):
        c = YFinanceCollector()
        result = await c.parse_data({"a": 1}, _make_source())
        assert result == [{"a": 1}]

    async def test_parse_data_none(self):
        c = YFinanceCollector()
        result = await c.parse_data(None, _make_source())
        assert result == []

    async def test_validate_data(self):
        c = YFinanceCollector()
        valid = await c.validate_data(
            [{"symbol": "AAPL", "current_price": 100}, {"symbol": "", "current_price": 100}],
            _make_source(),
        )
        assert len(valid) == 1

    async def test_parse_meta_edge_case_no_previous_close(self):
        c = YFinanceCollector()
        meta = {"regularMarketPrice": 100, "shortName": "Test"}
        result = c._parse_meta("AAPL", meta)
        assert result["change"] == 0
        assert result["change_percent"] == 0


# ── AlphaVantageCollector ────────────────────────────────────────
class TestAlphaVantageCollector:
    async def test_fetch_data_no_api_key(self):
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=[]):
            c = AlphaVantageCollector()
        source = _make_source(config={"symbols": ["AAPL"], "function": "TIME_SERIES_INTRADAY"})
        result = await c.fetch_data(source)
        assert result is None

    async def test_load_api_keys_multiple(self):
        with patch("app.collectors.finance.alpha_vantage_collector.settings") as mock_settings:
            mock_settings.alpha_vantage_api_keys = "key1,key2,key3"
            mock_settings.alpha_vantage_api_key = "single"
            c = AlphaVantageCollector()
        assert c._api_keys == ["key1", "key2", "key3"]

    async def test_load_api_keys_multiple_strips_entries(self):
        with patch("app.collectors.finance.alpha_vantage_collector.settings") as mock_settings:
            mock_settings.alpha_vantage_api_keys = " key1 ,, key2 ,"
            mock_settings.alpha_vantage_api_key = None
            c = AlphaVantageCollector()
        assert c._api_keys == ["key1", "key2"]

    async def test_load_api_keys_single_key_fallback(self):
        with patch("app.collectors.finance.alpha_vantage_collector.settings") as mock_settings:
            mock_settings.alpha_vantage_api_keys = None
            mock_settings.alpha_vantage_api_key = "single"
            c = AlphaVantageCollector()
        assert c._api_keys == ["single"]

    async def test_load_api_keys_blank_multi_key_falls_back(self):
        with patch("app.collectors.finance.alpha_vantage_collector.settings") as mock_settings:
            mock_settings.alpha_vantage_api_keys = " , ,"
            mock_settings.alpha_vantage_api_key = "single"
            c = AlphaVantageCollector()
        assert c._api_keys == ["single"]

    async def test_load_api_keys_none_configured(self):
        with patch("app.collectors.finance.alpha_vantage_collector.settings") as mock_settings:
            mock_settings.alpha_vantage_api_keys = None
            mock_settings.alpha_vantage_api_key = None
            c = AlphaVantageCollector()
        assert c._api_keys == []

    async def test_key_rotation_order(self, redis_mock):
        manager = APIKeyManager("alpha_vantage", ["key1", "key2"], redis_client=redis_mock)
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["key1", "key2"]):
            c = AlphaVantageCollector(key_manager=manager)
        assert await c._key_manager.get_key() == "key1"
        assert await c._key_manager.get_key() == "key2"
        assert await c._key_manager.get_key() == "key1"

    async def test_get_next_key_empty_returns_none(self):
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=[]):
            c = AlphaVantageCollector()
        assert await c._next_key_or_none() is None

    async def test_fetch_data_rotates_keys_across_requests(self, redis_mock):
        manager = APIKeyManager("alpha_vantage", ["key1", "key2"], redis_client=redis_mock)
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["key1", "key2"]):
            c = AlphaVantageCollector(key_manager=manager)
        source = _make_source(config={"symbols": ["AAPL", "MSFT"], "function": "TIME_SERIES_INTRADAY"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Meta Data": {"2. Symbol": "AAPL"},
            "Time Series (5min)": {
                "2024-01-01 10:00:00": {
                    "1. open": "100",
                    "2. high": "110",
                    "3. low": "99",
                    "4. close": "105",
                    "5. volume": "10000",
                }
            },
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.finance.alpha_vantage_collector.asyncio_sleep", new=AsyncMock()),
        ):
            result = await c.fetch_data(source)
        assert len(result) == 2
        calls = mock_client.get.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["params"]["apikey"] == "key1"
        assert calls[1].kwargs["params"]["apikey"] == "key2"

    async def test_fetch_data_success_intraday(self):
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["test-key"]):
            c = AlphaVantageCollector()
        source = _make_source(config={"symbols": ["AAPL"], "function": "TIME_SERIES_INTRADAY"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Meta Data": {"2. Symbol": "AAPL"},
            "Time Series (5min)": {
                "2024-01-01 10:00:00": {
                    "1. open": "100",
                    "2. high": "110",
                    "3. low": "99",
                    "4. close": "105",
                    "5. volume": "10000",
                }
            },
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.finance.alpha_vantage_collector.asyncio_sleep", new=AsyncMock()),
        ):
            result = await c.fetch_data(source)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    async def test_fetch_data_rate_limited(self):
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["test-key"]):
            c = AlphaVantageCollector()
        source = _make_source(config={"symbols": ["AAPL"]})
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.finance.alpha_vantage_collector.asyncio_sleep", new=AsyncMock()),
        ):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_data_api_error(self):
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["test-key"]):
            c = AlphaVantageCollector()
        source = _make_source(config={"symbols": ["AAPL"]})
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.finance.alpha_vantage_collector.asyncio_sleep", new=AsyncMock()),
        ):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_data_api_error_response(self):
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["test-key"]):
            c = AlphaVantageCollector()
        source = _make_source(config={"symbols": ["AAPL"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "bad request"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.finance.alpha_vantage_collector.asyncio_sleep", new=AsyncMock()),
        ):
            result = await c.fetch_data(source)
        assert result == []

    async def test_parse_response_global_quote(self):
        c = AlphaVantageCollector()
        data = {
            "Global Quote": {
                "01. symbol": "AAPL",
                "05. price": "100.00",
                "08. previous close": "99.00",
                "09. change": "1.00",
                "10. change percent": "1.01%",
                "06. volume": "5000",
                "07. latest trading day": "2024-01-01",
            }
        }
        result = c._parse_response("AAPL", "GLOBAL_QUOTE", data)
        assert result is not None
        assert result["current_price"] == 100.0

    async def test_parse_response_unknown_function(self):
        c = AlphaVantageCollector()
        result = c._parse_response("AAPL", "UNKNOWN", {})
        assert result is None

    async def test_parse_response_intraday_no_time_series(self):
        c = AlphaVantageCollector()
        result = c._parse_response("AAPL", "TIME_SERIES_INTRADAY", {})
        assert result is None


# ── EastMoneyCollector ───────────────────────────────────────────
class TestEastMoneyCollector:
    async def test_fetch_market_indices_success(self):
        c = EastMoneyCollector()
        source = _make_source(config={"data_type": "cn_indices"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "diff": [{"f12": "000001", "f14": "上证指数", "f2": 3000, "f3": 10, "f4": 0.3, "f5": 1000, "f6": 5000}]
            }
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert len(result) == 1
        assert result[0]["name"] == "上证指数"

    async def test_fetch_api_not_200(self):
        c = EastMoneyCollector()
        source = _make_source(config={"data_type": "cn_indices"})
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_cn_stock(self):
        c = EastMoneyCollector()
        source = _make_source(config={"data_type": "cn_stock", "symbols": ["000001.SS"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "f43": 15000,
                "f60": 14800,
                "f57": "000001",
                "f58": "平安银行",
                "f47": 1000,
                "f48": 5000,
                "f116": 1000000,
            }
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert len(result) == 1
        assert result[0]["name"] == "平安银行"

    def test_convert_symbol_to_secid(self):
        c = EastMoneyCollector()
        assert c._convert_symbol_to_secid("1.000001") == "1.000001"
        assert c._convert_symbol_to_secid("600519.SS") == "1.600519"
        assert c._convert_symbol_to_secid("000001.SZ") == "0.000001"
        assert c._convert_symbol_to_secid("OTHER") == "OTHER"

    async def test_parse_data(self):
        c = EastMoneyCollector()
        assert await c.parse_data([1, 2], None) == [1, 2]
        assert await c.parse_data({"a": 1}, None) == [{"a": 1}]
        assert await c.parse_data(None, None) == []

    async def test_validate_data(self):
        c = EastMoneyCollector()
        result = await c.validate_data(
            [{"symbol": "X", "current_price": 100}, {"symbol": "", "current_price": 0}], None
        )
        assert len(result) == 1


# ── FinnhubCollector ─────────────────────────────────────────────
class TestFinnhubCollector:
    async def test_fetch_no_api_key(self):
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        result = await c.fetch_data(source)
        assert result is None

    async def test_fetch_stock_quote_success(self):
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["test-key"]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["AAPL"]})
        mock_quote_response = MagicMock()
        mock_quote_response.status_code = 200
        mock_quote_response.raise_for_status = MagicMock()
        mock_quote_response.json.return_value = {
            "c": 150,
            "pc": 148,
            "d": 2,
            "dp": 1.35,
            "t": 1700000000,
            "o": 148,
            "h": 151,
            "l": 147,
        }
        mock_profile_response = MagicMock()
        mock_profile_response.status_code = 200
        mock_profile_response.json.return_value = {"name": "Apple Inc"}
        mock_client = AsyncMock()
        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            if "/quote" in url:
                return mock_quote_response
            return mock_profile_response

        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result is not None
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    async def test_fetch_empty_quote(self):
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["test-key"]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "stock_quote", "symbols": ["EMPTY"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_search(self):
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["test-key"]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "search", "query": "Apple"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "result": [
                {
                    "symbol": "AAPL",
                    "description": "Apple Inc",
                    "type": "Common Stock",
                    "mic": "XNAS",
                    "displaySymbol": "AAPL",
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    async def test_fetch_search_empty_query(self):
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["test-key"]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "search", "query": ""})
        result = await c.fetch_data(source)
        assert result == []

    async def test_determine_type(self):
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()
        assert c._determine_type("^GSPC") == "index"
        assert c._determine_type("GC=F") == "commodity"
        assert c._determine_type("GOLD") == "commodity"
        assert c._determine_type("000001.SS") == "cn_stock"
        assert c._determine_type("AAPL") == "stock"

    async def test_validate_data(self):
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()
        valid = await c.validate_data(
            [{"symbol": "AAPL", "current_price": 100}, {"symbol": "X", "description": "some"}],
            _make_source(),
        )
        assert len(valid) == 2

    async def test_key_rotation(self, redis_mock):
        manager = APIKeyManager("finnhub", ["key1", "key2"], redis_client=redis_mock)
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["key1", "key2"]):
            c = FinnhubCollector(key_manager=manager)
        k1 = await c._key_manager.get_key()
        k2 = await c._key_manager.get_key()
        k3 = await c._key_manager.get_key()
        assert k1 == "key1"
        assert k2 == "key2"
        assert k3 == "key1"


# ── RSSCollector ─────────────────────────────────────────────────
class TestRSSCollector:
    async def test_fetch_data_success(self):
        c = RSSCollector()
        source = _make_source(url="https://example.com/rss")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<rss>content</rss>"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == "<rss>content</rss>"

    async def test_fetch_data_no_url(self):
        c = RSSCollector()
        source = _make_source(url="")
        result = await c.fetch_data(source)
        assert result is None

    async def test_fetch_data_not_200(self):
        """A non-200 response should raise RuntimeError"""
        c = RSSCollector()
        source = _make_source(url="https://x.com")
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(RuntimeError, match="HTTP 500"):
            await c.fetch_data(source)

    async def test_parse_data_entries(self):
        import time as _t

        c = RSSCollector()
        source = _make_source(config={"parse_rules": {}})
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Test Article</title><link>http://x.com/1</link><description>A description</description></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1
        assert result[0]["title"] == "Test Article"
        assert result[0]["url"] == "http://x.com/1"

    async def test_parse_data_empty_raw(self):
        c = RSSCollector()
        result = await c.parse_data(None, _make_source())
        assert result == []

    async def test_parse_data_invalid_xml(self):
        c = RSSCollector()
        result = await c.parse_data("<<<not xml>>>", _make_source(config={"parse_rules": {}}))
        assert isinstance(result, list)

    async def test_parse_feedparser_date_published_parsed(self):
        import time as _t

        entry = {"published_parsed": _t.struct_time((2024, 1, 15, 10, 30, 0, 0, 0, 0))}
        result = _parse_feedparser_date(entry)
        assert "2024-01-15" in result

    async def test_parse_feedparser_date_string(self):
        entry = {"published": "2024-01-15T10:30:00Z"}
        result = _parse_feedparser_date(entry)
        assert "2024-01-15" in result

    async def test_parse_feedparser_date_no_date(self):
        entry = {}
        result = _parse_feedparser_date(entry)
        assert isinstance(result, str)

    async def test_parse_data_summary_rules(self):
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {"summary": "description"}})
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Test</title><link>http://x.com/1</link><description>Desc here</description><summary>Summary</summary></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1


# ── HackerNewsCollector ──────────────────────────────────────────
class TestHackerNewsCollector:
    async def test_fetch_data_success(self):
        c = HackerNewsCollector()
        source = _make_source(config={"story_type": "topstories"})
        story_ids_response = MagicMock()
        story_ids_response.status_code = 200
        story_ids_response.json.return_value = [1234, 5678]
        item_response = MagicMock()
        item_response.status_code = 200
        item_response.json.return_value = {
            "id": 1234,
            "type": "story",
            "title": "HN Post",
            "url": "http://x.com",
            "score": 100,
            "descendants": 10,
            "by": "user",
            "time": 1700000000,
        }
        mock_client = AsyncMock()
        call_count = 0

        async def mock_get(url):
            nonlocal call_count
            call_count += 1
            if "topstories" in url:
                return story_ids_response
            item_response.json.return_value["id"] = call_count
            return item_response

        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert isinstance(result, list)
        assert len(result) >= 1

    async def test_fetch_data_empty_ids(self):
        c = HackerNewsCollector()
        source = _make_source(config={"story_type": "topstories"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_data_api_error(self):
        c = HackerNewsCollector()
        source = _make_source(config={"story_type": "topstories"})
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_parse_data_stories(self):
        c = HackerNewsCollector()
        raw_data = [
            {
                "id": 1,
                "type": "story",
                "title": "HN Story",
                "url": "http://x.com",
                "score": 50,
                "descendants": 5,
                "by": "user",
                "time": 1700000000,
            },
            {"id": 2, "type": "comment"},
        ]
        result = await c.parse_data(raw_data, _make_source())
        assert len(result) == 1
        assert result[0]["title"] == "HN Story"
        assert "50" in result[0]["summary"]

    async def test_parse_data_no_url_fallback(self):
        c = HackerNewsCollector()
        raw_data = [
            {
                "id": 99,
                "type": "story",
                "title": "Ask HN",
                "score": 10,
                "descendants": 0,
                "by": "user",
                "time": 1700000000,
                "text": "Some question",
            },
        ]
        result = await c.parse_data(raw_data, _make_source())
        assert len(result) == 1
        assert "news.ycombinator.com" in result[0]["url"]
        assert "Some question" in result[0]["summary"]

    async def test_parse_data_empty(self):
        c = HackerNewsCollector()
        assert await c.parse_data(None, _make_source()) == []
        assert await c.parse_data([], _make_source()) == []
        assert await c.parse_data("not list", _make_source()) == []

    async def test_parse_data_query_filter_keeps_matching_stories(self):
        c = HackerNewsCollector()
        source = _make_source(config={"story_type": "newstories", "query": "AI machine learning LLM"})
        raw_data = [
            {
                "id": 1,
                "type": "story",
                "title": "New LLM beats benchmarks",
                "url": "http://a.com",
                "score": 90,
                "time": 1700000000,
            },
            {
                "id": 2,
                "type": "story",
                "title": "Email maintenance window",
                "url": "http://b.com",
                "score": 40,
                "time": 1700000000,
            },
            {
                "id": 3,
                "type": "story",
                "title": "Show HN: machine learning toolkit",
                "url": "http://c.com",
                "score": 25,
                "time": 1700000000,
            },
        ]
        result = await c.parse_data(raw_data, source)
        # "ai" must match whole words only (not inside "email"/"maintenance")
        assert [item["extra_data"]["hn_id"] for item in result] == [1, 3]

    async def test_parse_data_query_filter_case_insensitive(self):
        c = HackerNewsCollector()
        source = _make_source(config={"query": "robot"})
        raw_data = [
            {
                "id": 1,
                "type": "story",
                "title": "ROBOT deliveries expand",
                "url": "http://a.com",
                "score": 5,
                "time": 1700000000,
            },
            {
                "id": 2,
                "type": "story",
                "title": "A robotics startup",
                "url": "http://b.com",
                "score": 5,
                "time": 1700000000,
            },
            {"id": 3, "type": "story", "title": "Cooking tips", "url": "http://c.com", "score": 5, "time": 1700000000},
        ]
        result = await c.parse_data(raw_data, source)
        assert [item["extra_data"]["hn_id"] for item in result] == [1]

    async def test_parse_data_no_query_no_filter(self):
        c = HackerNewsCollector()
        raw_data = [
            {"id": 1, "type": "story", "title": "Anything", "url": "http://a.com", "score": 5, "time": 1700000000},
            {"id": 2, "type": "story", "title": "Else", "url": "http://b.com", "score": 5, "time": 1700000000},
        ]
        result = await c.parse_data(raw_data, _make_source())
        assert len(result) == 2


# ── ArxivCollector ───────────────────────────────────────────────
class TestArxivCollector:
    async def test_fetch_data_success(self):
        c = ArxivCollector()
        source = _make_source(config={"categories": ["cs.AI"], "max_results": 5})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<feed>xml content</feed>"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == "<feed>xml content</feed>"

    async def test_fetch_data_api_not_200(self):
        c = ArxivCollector()
        source = _make_source(config={})
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result is None

    async def test_parse_data_entries(self):
        c = ArxivCollector()
        raw_xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <title>AI Paper Title</title>
            <summary>An abstract about AI</summary>
            <id>http://arxiv.org/abs/2401.0001</id>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
            <author><name>Author One</name></author>
            <category term="cs.AI"/>
            <arxiv:primary_category term="cs.AI"/>
          </entry>
        </feed>"""
        result = await c.parse_data(raw_xml, _make_source())
        assert len(result) == 1
        assert "AI Paper" in result[0]["title"]
        assert result[0]["author"] == "Author One"
        assert result[0]["extra_data"]["primary_category"] == "cs.AI"

    async def test_parse_data_empty(self):
        c = ArxivCollector()
        result = await c.parse_data(None, _make_source())
        assert result == []

    async def test_parse_data_invalid_xml(self):
        c = ArxivCollector()
        result = await c.parse_data("<<< invalid xml >>>", _make_source())
        assert result == []


# ── Asyncio sleep wrapper ───────────────────────────────────────
class TestAsyncioSleep:
    async def test_asyncio_sleep(self):
        import time

        start = time.monotonic()
        await asyncio_sleep(0.01)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.01


# ── Additional FinnhubCollector Tests ─────────────────────────────
class TestFinnhubCollectorExtended:
    """Tests covering missing lines in finnhub_collector.py."""

    async def test_load_api_keys_finnhub_api_keys_list(self):
        """Lines 40-45: _load_api_keys with finnhub_api_keys list."""
        with patch("app.collectors.finance.finnhub_collector.settings") as mock_settings:
            mock_settings.finnhub_api_keys = ["key1", "key2"]
            mock_settings.finnhub_api_key = "key3"  # not in list, should be added
            c = FinnhubCollector()
            # Re-init with patched settings
            c.__init__()
            # Should have "key1", "key2", "key3"
            # But init reads from self so test the method directly
            keys = c._load_api_keys()
            assert "key1" in keys
            assert "key2" in keys
            assert "key3" in keys

    async def test_load_api_keys_finnhub_api_key_dedup(self):
        """Lines 43-44: dedup if finnhub_api_key already in list."""
        with patch("app.collectors.finance.finnhub_collector.settings") as mock_settings:
            mock_settings.finnhub_api_keys = ["key1", "key2"]
            mock_settings.finnhub_api_key = "key1"  # already in list
            c = FinnhubCollector()
            keys = c._load_api_keys()
            # key1 should not be duplicated
            assert len(keys) == 2

    async def test_fetch_market_indices(self):
        """Line 68: data_type='market_indices' path."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "market_indices"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = [
            {
                "c": 4400,
                "pc": 4390,
                "o": 4395,
                "h": 4410,
                "l": 4385,
                "t": 1700000000,
                "d": 10,
                "dp": 0.23,
            },  # quote ^GSPC
            {"name": "S&P 500 Index"},  # profile
            {
                "c": 34000,
                "pc": 33800,
                "o": 33900,
                "h": 34100,
                "l": 33700,
                "t": 1700000000,
                "d": 200,
                "dp": 0.59,
            },  # quote ^DJI
            {"name": "Dow Jones"},  # profile
            {
                "c": 14000,
                "pc": 13900,
                "o": 13950,
                "h": 14050,
                "l": 13850,
                "t": 1700000000,
                "d": 100,
                "dp": 0.72,
            },  # quote ^IXIC
            {"name": "Nasdaq"},  # profile
        ]
        call_count = -1
        mock_client = AsyncMock()

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            return mock_response

        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert isinstance(result, list)

    async def test_fetch_commodities(self):
        """Line 70: data_type='commodities' path."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "commodities", "symbols": ["GC=F", "SI=F"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = [
            {"c": 2000.5, "pc": 1990, "o": 1995, "h": 2010, "l": 1980, "t": 1700000000, "d": 10, "dp": 0.5},
            {"name": "Gold"},
            {"c": 24.1, "pc": 23.9, "o": 24.0, "h": 24.3, "l": 23.8, "t": 1700000000, "d": 0.2, "dp": 0.83},
            {"name": "Silver"},
        ]
        mock_client = AsyncMock()

        async def mock_get(url, params=None):
            return mock_response

        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert len(result) == 2
        assert result[0]["type"] == "commodity"

    async def test_fetch_company_profiles(self):
        """Lines 74-75, 259-269, 272-283: company_profile data_type and impl."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()
        source = _make_source(
            config={
                "data_type": "company_profile",
                "symbols": ["AAPL", "MSFT"],
            }
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        # first call for AAPL, second for MSFT
        mock_response.json.side_effect = [
            {
                "name": "Apple Inc",
                "ticker": "AAPL",
                "exchange": "NASDAQ",
                "currency": "USD",
                "marketCapitalization": 2e12,
                "country": "US",
                "finnhubIndustry": "Technology",
                "ipo": "1980-12-12",
                "shareOutstanding": 15e9,
                "logo": "https://logo.co/apple.png",
                "weburl": "https://www.apple.com",
            },
            {
                "name": "Microsoft Corporation",
                "ticker": "MSFT",
                "exchange": "NASDAQ",
                "currency": "USD",
                "marketCapitalization": 2.5e12,
                "country": "US",
                "finnhubIndustry": "Technology",
                "ipo": "1986-03-13",
                "shareOutstanding": 7.5e9,
                "logo": "https://logo.co/msft.png",
                "weburl": "https://www.microsoft.com",
            },
        ]
        mock_client = AsyncMock()

        async def mock_get(url, params=None):
            return mock_response

        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert len(result) == 2
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["name"] == "Apple Inc"
        assert result[0]["exchange"] == "NASDAQ"
        assert result[1]["symbol"] == "MSFT"

    async def test_fetch_default_case(self):
        """Lines 76-77: unknown data_type defaults to stock_quote."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()
        source = _make_source(config={"data_type": "unknown_data_type", "symbols": ["AAPL"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = [
            {"c": 150, "pc": 148, "o": 148, "h": 151, "l": 147, "t": 1700000000, "d": 2, "dp": 1.35},
            {"name": "Apple Inc"},
        ]
        mock_client = AsyncMock()

        async def mock_get(url, params=None):
            return mock_response

        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert isinstance(result, list)
        assert result[0]["symbol"] == "AAPL"

    async def test_fetch_stock_quote_no_symbols(self):
        """Lines 81-82: empty symbols returns empty list."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()
        result = await c._fetch_stock_quotes([], "key")
        assert result == []

    async def test_fetch_rate_limited(self):
        """Lines 91-96: HTTP 429 triggers 60s wait then retry."""
        import httpx as _httpx

        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()

        rate_limited_response = MagicMock()
        rate_limited_response.status_code = 429

        good_response = MagicMock()
        good_response.status_code = 200
        good_response.raise_for_status = MagicMock()
        good_response.json.side_effect = [
            {"c": 150, "pc": 148, "o": 148, "h": 151, "l": 147, "t": 1700000000, "d": 2, "dp": 1.35},
            {"name": "Apple Inc"},
        ]

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _httpx.HTTPStatusError("rate limited", request=MagicMock(), response=rate_limited_response)
            return good_response

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), patch("asyncio.sleep"):
            result = await c._fetch_stock_quotes(["AAPL"], "key")
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    async def test_fetch_auth_error(self):
        """Lines 98-100: HTTP 401/403 returns None."""
        import httpx as _httpx

        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()

        forbidden_response = MagicMock()
        forbidden_response.status_code = 403

        async def mock_get(url, params=None):
            raise _httpx.HTTPStatusError("forbidden", request=MagicMock(), response=forbidden_response)

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_stock_quotes(["AAPL"], "key")
        assert result is None

    async def test_fetch_generic_http_error(self):
        """Lines 101-102: other HTTP errors logged and skipped."""
        import httpx as _httpx

        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()

        error_response = MagicMock()
        error_response.status_code = 500

        async def mock_get(url, params=None):
            raise _httpx.HTTPStatusError("server error", request=MagicMock(), response=error_response)

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), patch("asyncio.sleep"):
            result = await c._fetch_stock_quotes(["AAPL"], "key")
        assert result == []  # exception caught, returns empty

    async def test_fetch_generic_exception(self):
        """Lines 103-104: generic exception handled gracefully."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()

        async def mock_get(url, params=None):
            raise Exception("connection reset")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), patch("asyncio.sleep"):
            result = await c._fetch_stock_quotes(["AAPL"], "key")
        assert result == []

    async def test_fetch_company_name_error(self):
        """Lines 133-135: company profile fetch fails → returns symbol."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()

        async def mock_get(url, params=None):
            raise Exception("profile unavailable")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_company_name("AAPL", "key")
        assert result == "AAPL"

    async def test_normalize_quote_no_change_calculated(self):
        """Lines 143-146: change/change_percent calculated when missing."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()

        data = {
            "c": 150,  # current
            "pc": 140,  # previous close
            "d": 0,  # no change
            "dp": 0,  # no change_percent
            "o": 149,
            "h": 152,
            "l": 148,
            "t": 0,  # no timestamp
        }
        normalized = c._normalize_quote("AAPL", data)
        # change should be calculated: 150 - 140 = 10
        assert normalized["change"] == 10.0
        # change_percent: (10 / 140) * 100 ≈ 7.14
        assert round(normalized["change_percent"], 2) == 7.14

    async def test_normalize_quote_no_timestamp(self):
        """Line 154: timestamp 0 uses gmtime fallback."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()

        data = {"c": 100, "pc": 99, "d": 1, "dp": 1.01, "o": 99, "h": 101, "l": 98, "t": 0}
        normalized = c._normalize_quote("TEST", data)
        assert isinstance(normalized["timestamp"], str)
        # Should contain current date
        import datetime

        today = datetime.date.today().isoformat()
        assert today in normalized["timestamp"]

    async def test_search_symbols_error(self):
        """Lines 251-256: search HTTP error and generic error."""
        import httpx as _httpx

        with patch.object(FinnhubCollector, "_load_api_keys", return_value=["tk"]):
            c = FinnhubCollector()

        # Test HTTP error
        err_resp = MagicMock()
        err_resp.status_code = 500

        async def mock_http_err(url, params=None):
            raise _httpx.HTTPStatusError("err", request=MagicMock(), response=err_resp)

        mock_client = AsyncMock()
        mock_client.get = mock_http_err
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._search_symbols("Apple", "tk")
        assert result is None

        # Test generic exception
        async def mock_gen_err(url, params=None):
            raise Exception("boom")

        mock_client2 = AsyncMock()
        mock_client2.get = mock_gen_err
        mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
        mock_client2.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client2):
            result = await c._search_symbols("Apple", "tk")
        assert result is None

    async def test_company_profile_empty_data(self):
        """Lines 279-281: empty profile response returns None."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_company_profile("EMPTY", "tk")
        assert result is None

    async def test_parse_data_list_dict_none(self):
        """Lines 299-303: parse_data handles list, dict, and other."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()
        source = _make_source()

        assert await c.parse_data([{"x": 1}], source) == [{"x": 1}]
        assert await c.parse_data({"x": 1}, source) == [{"x": 1}]
        assert await c.parse_data(None, source) == []
        assert await c.parse_data("string", source) == []

    async def test_commodities_auth_error_logged(self):
        """Lines 217-221: HTTP 401/403 during commodity fetch."""
        import httpx as _httpx

        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()

        auth_resp = MagicMock()
        auth_resp.status_code = 401

        async def mock_get(url, params=None):
            raise _httpx.HTTPStatusError("unauth", request=MagicMock(), response=auth_resp)

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_commodities(["GC=F"], "bad-key")
        assert result == []  # error logged, item skipped

    async def test_commodities_empty_quote_with_fallback_log(self):
        """Line 215: commodity quote returns None (fallback logged)."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()

        # Returns empty dict (no quote data)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"c": 0}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_commodities(["GOLD"], "key")
        assert result == []  # None returned, fallback logged

    async def test_commodities_generic_exception(self):
        """Line 222-223: generic exception during commodity fetch."""
        with patch.object(FinnhubCollector, "_load_api_keys", return_value=[]):
            c = FinnhubCollector()

        async def mock_get(url, params=None):
            raise Exception("network error")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), patch("asyncio.sleep"):
            result = await c._fetch_commodities(["GC=F"], "key")
        assert result == []


# ── Extended RSS Collector Tests ──────────────────────────────────
class TestRSSCollectorExtended:
    """Tests covering missing lines in rss_collector.py."""

    async def test_fetch_data_timeout(self):
        """httpx.TimeoutException is re-raised by fetch_data."""
        import httpx as _httpx

        c = RSSCollector()
        source = _make_source(url="https://slow.com/rss")

        async def mock_get(url, **kwargs):
            raise _httpx.TimeoutException("timed out")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(_httpx.TimeoutException):
            await c.fetch_data(source)

    async def test_fetch_data_http_error(self):
        """httpx.HTTPError (non-timeout) is re-raised by fetch_data."""
        import httpx as _httpx

        c = RSSCollector()
        source = _make_source(url="https://broken.com/rss")

        async def mock_get(url, **kwargs):
            raise _httpx.HTTPError("connection refused")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(_httpx.HTTPError):
            await c.fetch_data(source)

    async def test_parse_data_feedparser_exception(self):
        """Lines 44-46: feedparser raises exception."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {}})

        with patch("app.collectors.tech.rss_collector.feedparser.parse", side_effect=ValueError("bad feed")):
            result = await c.parse_data("some data", source)
        assert result == []

    async def test_parse_data_summary_comments_text(self):
        """Line 69-70: summary_rule='comments_text' uses summary or description."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {"summary": "comments_text"}})
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Post</title><link>http://x.com/3</link><description>A description</description></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1

    async def test_parse_data_summary_excerpt(self):
        """Line 71-72: summary_rule='excerpt'."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {"summary": "excerpt"}})
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Excerpt Post</title><link>http://x.com/4</link><description>Excerpt desc</description></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1
        assert result[0]["title"] == "Excerpt Post"

    async def test_parse_data_summary_unknown_rule(self):
        """Lines 73-74: unknown summary_rule falls through to default."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {"summary": "nonexistent_rule"}})
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Unknown</title><link>http://x.com/5</link><description>Desc</description></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1

    async def test_parse_data_summary_fallback_to_description(self):
        """Line 77: empty summary falls back to description."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {"summary": "summary"}})
        # Entry has no summary field but has description
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Fallback</title><link>http://x.com/6</link><description>Only Description</description></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1
        assert "Only Description" in result[0]["summary"]

    async def test_parse_data_extra_rules(self):
        """Line 82: extra_data populated from parse_rules.extra."""
        c = RSSCollector()
        source = _make_source(
            config={"parse_rules": {"summary": "summary", "extra": {"guid": "guid", "category": "category"}}}
        )
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Extra</title><link>http://x.com/7</link><description>Desc</description>
        <guid>guid-123</guid><category>Tech</category></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1
        assert result[0]["extra_data"].get("guid") == "guid-123"
        assert result[0]["extra_data"].get("category") == "Tech"

    async def test_parse_data_media_content_image(self):
        """Lines 86-89: image_url from media_content."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {}})
        raw_xml = """<?xml version="1.0"?>
        <rss xmlns:media="http://search.yahoo.com/mrss/" version="2.0">
        <channel>
        <item>
            <title>Image</title>
            <link>http://x.com/8</link>
            <description>Desc</description>
            <media:content url="https://img.example.com/photo.jpg" type="image/jpeg"/>
        </item>
        </channel>
        </rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1
        assert result[0].get("image_url") == "https://img.example.com/photo.jpg"

    async def test_parse_data_enclosures_image(self):
        """Lines 92-95: image_url from enclosures when media_content absent."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {}})
        raw_xml = """<?xml version="1.0"?>
        <rss version="2.0">
        <channel>
        <item>
            <title>Enclosure</title>
            <link>http://x.com/9</link>
            <description>Desc</description>
            <enclosure url="https://img.example.com/enc.jpg" type="image/jpeg"/>
        </item>
        </channel>
        </rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1
        assert result[0].get("image_url") == "https://img.example.com/enc.jpg"

    async def test_parse_feedparser_date_invalid_struct_time(self):
        """Lines 116-117: ValueError from datetime construction falls through."""
        # Published parsed exists but with invalid values
        entry = {"published_parsed": None, "published": "not-a-date-zzz"}
        result = _parse_feedparser_date(entry)
        # Should fall through to published string → ValueError → now()
        assert isinstance(result, str)

    async def test_parse_feedparser_date_published_str_invalid(self):
        """Lines 124-125: invalid published string (ValueError)."""
        entry = {"published": "totally-invalid-date-format!!!"}
        result = _parse_feedparser_date(entry)
        # Falls through to datetime.now()
        assert isinstance(result, str)

    async def test_parse_data_summary_abstract(self):
        """Line 69-70: summary_rule='abstract' path."""
        c = RSSCollector()
        source = _make_source(config={"parse_rules": {"summary": "abstract"}})
        raw_xml = """<?xml version="1.0"?>
        <rss><channel>
        <item><title>Abstract</title><link>http://x.com/10</link><description>Abstract desc</description></item>
        </channel></rss>"""
        result = await c.parse_data(raw_xml, source)
        assert len(result) >= 1


# ── Extended HackerNews Collector Tests ───────────────────────────
class TestHackerNewsCollectorExtended:
    """Tests covering missing lines in hackernews_collector.py."""

    async def test_fetch_data_unhandled_exception(self):
        """Lines 33-35: top-level exception returns None."""
        c = HackerNewsCollector()
        source = _make_source(config={"story_type": "topstories"})

        with patch(
            "app.collectors.tech.hackernews_collector.HackerNewsCollector._fetch_story_ids",
            new_callable=AsyncMock,
            side_effect=Exception("total failure"),
        ):
            result = await c.fetch_data(source)
        assert result is None

    async def test_fetch_story_ids_timeout(self):
        """Lines 47-49: httpx.TimeoutException."""
        import httpx as _httpx

        c = HackerNewsCollector()

        async def mock_get(url):
            raise _httpx.TimeoutException("timed out")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_story_ids("topstories")
        assert result == []

    async def test_fetch_story_ids_http_error(self):
        """Lines 50-52: httpx.HTTPError."""
        import httpx as _httpx

        c = HackerNewsCollector()

        async def mock_get(url):
            raise _httpx.HTTPError("connection error")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_story_ids("topstories")
        assert result == []

    async def test_fetch_items_batch_item_exception(self):
        """Lines 66-67: exception during individual item fetch is caught."""
        c = HackerNewsCollector()

        good_response = MagicMock()
        good_response.status_code = 200
        good_response.json.return_value = {"id": 111, "type": "story", "title": "OK"}

        call_count = 0

        async def mock_get(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("item unavailable")
            return good_response

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_items_batch([111, 222])
        # Item 111 fails, item 222 succeeds
        assert len(result) == 1
        assert result[0]["id"] == 111  # second call returns id=111 due to mock


# ── Extended AlphaVantage Collector Tests ─────────────────────────
class TestAlphaVantageCollectorExtended:
    """Tests covering missing lines in alpha_vantage_collector.py."""

    async def test_fetch_data_exception_in_loop(self):
        """Lines 37-38: generic exception during symbol fetch."""
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["test-key"]):
            c = AlphaVantageCollector()
        source = _make_source(config={"symbols": ["AAPL"], "function": "TIME_SERIES_INTRADAY"})

        async def mock_get(url, params=None):
            raise Exception("network error")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_fetch_quote_timeout(self):
        """Lines 68-69: httpx.TimeoutException."""
        import httpx as _httpx

        c = AlphaVantageCollector()

        async def mock_get(url, params=None):
            raise _httpx.TimeoutException("timeout")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_quote("AAPL", "TIME_SERIES_INTRADAY", "key")
        assert result is None

    async def test_fetch_quote_http_error(self):
        """Lines 70-73: generic httpx.HTTPError."""
        import httpx as _httpx

        c = AlphaVantageCollector()

        async def mock_get(url, params=None):
            raise _httpx.HTTPError("http err")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_quote("AAPL", "TIME_SERIES_INTRADAY", "key")
        assert result is None

    async def test_parse_response_global_quote_empty(self):
        """Line 104: Global Quote is empty → return None."""
        c = AlphaVantageCollector()
        data = {"Global Quote": {}}
        result = c._parse_response("AAPL", "GLOBAL_QUOTE", data)
        assert result is None

    async def test_parse_data_variants(self):
        """Lines 128-132: parse_data with list, dict, None."""
        c = AlphaVantageCollector()
        assert await c.parse_data([{"a": 1}], _make_source()) == [{"a": 1}]
        assert await c.parse_data({"a": 1}, _make_source()) == [{"a": 1}]
        assert await c.parse_data(None, _make_source()) == []

    async def test_validate_data(self):
        """Lines 135-139: validate_data filters items."""
        c = AlphaVantageCollector()
        valid = await c.validate_data(
            [
                {"symbol": "AAPL", "current_price": 100},
                {"symbol": "", "current_price": 100},
                {"symbol": "MSFT", "current_price": 0},
            ],
            _make_source(),
        )
        # Only AAPL has both symbol and non-zero price
        assert len(valid) == 1

    async def test_fetch_data_note_response(self):
        """Line 63-65: API returns 'Note' key."""
        with patch.object(AlphaVantageCollector, "_load_api_keys", return_value=["test-key"]):
            c = AlphaVantageCollector()
        source = _make_source(config={"symbols": ["AAPL"]})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Note": "Rate limit exceeded"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.finance.alpha_vantage_collector.asyncio_sleep", new=AsyncMock()),
        ):
            result = await c.fetch_data(source)
        assert result == []


# ── Extended EastMoney Collector Tests ────────────────────────────
class TestEastMoneyCollectorExtended:
    """Tests covering missing lines in eastmoney_collector.py."""

    async def test_fetch_default_path(self):
        """Line 39: unknown data_type falls back to _fetch_market_indices."""
        c = EastMoneyCollector()
        source = _make_source(config={"data_type": "unknown"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"diff": []}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []

    async def test_market_indices_empty_diff(self):
        """Line 63: diff is empty."""
        c = EastMoneyCollector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"diff": []}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_market_indices()
        assert result == []

    async def test_market_indices_string_current_and_change(self):
        """Lines 76, 78: string values for current/change_pct."""
        c = EastMoneyCollector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "diff": [
                    {"f12": "000001", "f14": "上证指数", "f2": "3000.5", "f3": 10, "f4": "0.33", "f5": 1000, "f6": 5000}
                ]
            }
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_market_indices()
        assert len(result) == 1
        assert result[0]["current_price"] == 3000.5

    async def test_market_indices_string_dash_values(self):
        """Lines 76, 78: '-' string values → 0."""
        c = EastMoneyCollector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "diff": [{"f12": "000001", "f14": "上证指数", "f2": "-", "f3": 10, "f4": "-", "f5": 1000, "f6": 5000}]
            }
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_market_indices()
        assert len(result) == 1
        assert result[0]["current_price"] == 0

    async def test_market_indices_timeout(self):
        """Lines 97-99: httpx.TimeoutException."""
        import httpx as _httpx

        c = EastMoneyCollector()

        async def mock_get(url, params=None):
            raise _httpx.TimeoutException("timed out")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_market_indices()
        assert result == []

    async def test_market_indices_http_error(self):
        """Lines 100-102: httpx.HTTPError."""
        import httpx as _httpx

        c = EastMoneyCollector()

        async def mock_get(url, params=None):
            raise _httpx.HTTPError("connection lost")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_market_indices()
        assert result == []

    async def test_fetch_cn_stocks_exception(self):
        """Lines 112-113: exception per-stock is caught gracefully."""
        c = EastMoneyCollector()

        async def mock_get(url, params=None):
            raise Exception("network error")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_cn_stocks(["000001.SS"])
        assert result == []

    async def test_single_stock_not_200(self):
        """Line 127: non-200 response returns None."""
        c = EastMoneyCollector()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_single_stock("1.000001")
        assert result is None

    async def test_single_stock_no_data(self):
        """Line 131: data dict is empty."""
        c = EastMoneyCollector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_single_stock("1.000001")
        assert result is None

    async def test_single_stock_generic_exception(self):
        """Lines 154-155: generic exception during single stock fetch."""
        c = EastMoneyCollector()

        async def mock_get(url, params=None):
            raise Exception("network down")

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c._fetch_single_stock("1.000001")
        assert result is None
