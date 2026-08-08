"""Regression tests for the finance failover chain hardening.

Guards: yfinance HTTP 429 rate-limiting is treated as "no data" so the failover
chain advances to the next source; Yahoo requests carry browser-like headers
(anti-bot); and eastmoney batch results are remapped from secid-style symbols
(e.g. "1.000001") back to standard symbols ("000001.SS"), with identity entries
letting already-standard symbols pass through and unmatched secids dropped.

Note: the chain order [eastmoney, yfinance] for market indices is asserted in
tests/unit/test_services_finance.py::TestGetFailoverChain::test_market_index,
and the merge-by-symbol behavior in test_fetch_indices_with_failover_merge.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from app.collectors.base import CollectionResult
from app.collectors.finance.yfinance_collector import YFinanceCollector
from app.core.constants import YAHOO_BROWSER_HEADERS
from app.services.finance import FinanceService


def _make_source(config=None):
    src = MagicMock()
    src.name = "test-source"
    src.config = config or {}
    return src


class TestYFinanceRateLimit:
    async def test_fetch_symbol_returns_none_on_429(self):
        """Rate-limiting is not a failure of this service: _fetch_symbol must
        return None (not raise) so the failover chain advances."""
        collector = YFinanceCollector()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await collector._fetch_symbol("AAPL")

        assert result is None

    async def test_fetch_data_skips_rate_limited_symbols(self):
        """A 429ed symbol yields no items, so collect() reports success with an
        empty item list — the trigger for advancing the failover chain."""
        collector = YFinanceCollector()
        source = _make_source(config={"symbols": ["AAPL", "^GSPC"]})
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await collector.fetch_data(source)

        assert result == []

    async def test_request_carries_browser_headers(self):
        """Staging showed the default client fingerprint gets 429ed; every Yahoo
        request must carry the shared browser-like headers."""
        collector = YFinanceCollector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "chart": {"result": [{"meta": {
                "regularMarketPrice": 150,
                "chartPreviousClose": 148,
                "shortName": "Apple",
            }}]}
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await collector._fetch_symbol("AAPL")

        sent_headers = mock_client.get.call_args.kwargs["headers"]
        assert sent_headers == YAHOO_BROWSER_HEADERS
        assert "Mozilla" in sent_headers["User-Agent"]


class TestRateLimitAdvancesFailoverChain:
    async def test_none_result_falls_through_to_next_source(self):
        """A rate-limited source returns None (no exception raised); the chain
        must silently advance to the next entry and return its result."""
        service = FinanceService(AsyncMock(), AsyncMock())
        quote = {"symbol": "AAPL", "current_price": 150, "source": "alpha_vantage"}
        chain = service._get_failover_chain("stock_quote", "AAPL")
        assert chain[0]["name"] == "yfinance"

        with patch.object(
            FinanceService,
            "_try_collector",
            new_callable=AsyncMock,
            # yfinance rate-limited -> None; alpha_vantage succeeds
            side_effect=[None, quote],
        ):
            result = await service._fetch_with_failover("tenant-1", "AAPL", chain)

        assert result == quote

    async def test_indices_chain_continues_after_rate_limited_batch(self):
        """Same behavior on the batch path used by market indices: an all-429
        eastmoney/yfinance batch (None) must advance until data is found."""
        service = FinanceService(AsyncMock(), AsyncMock())
        items = [{"symbol": "^GSPC", "current_price": 5000}]

        with patch.object(
            FinanceService,
            "_try_collector_batch",
            new_callable=AsyncMock,
            side_effect=[None, items],
        ):
            result = await service._fetch_with_failover_batch("tenant-1", ["^GSPC"], [
                {"name": "eastmoney", "collector": "eastmoney"},
                {"name": "yfinance", "collector": "yfinance"},
            ])

        assert result == items


class TestEastmoneyBatchSymbolRemap:
    async def test_secids_map_back_to_standard_symbols(self):
        """eastmoney returns secid-style symbols; the service must remap them to
        the requested standard symbols, pass through already-standard ones via the
        identity map, and drop unmatched secids."""
        service = FinanceService(AsyncMock(), AsyncMock())

        fake_collector_cls = MagicMock()
        collector = MagicMock()
        collector.collect = AsyncMock(return_value=CollectionResult(
            items=[
                # secid form -> must be remapped to 000001.SS
                {"symbol": "1.000001", "current_price": 3500},
                # already a standard symbol -> identity entry keeps it as-is
                {"symbol": "399001.SZ", "current_price": 11000},
                # not requested -> dropped
                {"symbol": "9.999999", "current_price": 1},
            ],
            success=True,
        ))
        fake_collector_cls.return_value = collector

        registry = {"eastmoney": fake_collector_cls}
        with patch("app.collectors.COLLECTOR_REGISTRY", registry):
            result = await service._try_collector_batch(
                "tenant-1",
                ["000001.SS", "399001.SZ"],
                {"name": "eastmoney", "collector": "eastmoney"},
            )

        by_symbol = {item["symbol"]: item for item in result}
        assert set(by_symbol) == {"000001.SS", "399001.SZ"}
        assert by_symbol["000001.SS"]["current_price"] == 3500
        assert by_symbol["399001.SZ"]["current_price"] == 11000
        assert by_symbol["000001.SS"]["source"] == "eastmoney"

    async def test_eastmoney_batch_requests_cn_indices_data_type(self):
        """The mock source handed to eastmoney must request cn_indices so the
        collector hits the index endpoint with the CN_MARKET_INDICES secids."""
        service = FinanceService(AsyncMock(), AsyncMock())

        fake_collector_cls = MagicMock()
        collector = MagicMock()
        collector.collect = AsyncMock(return_value=CollectionResult(items=[], success=True))
        fake_collector_cls.return_value = collector

        registry = {"eastmoney": fake_collector_cls}
        with patch("app.collectors.COLLECTOR_REGISTRY", registry):
            await service._try_collector_batch(
                "tenant-1",
                ["000001.SS"],
                {"name": "eastmoney", "collector": "eastmoney"},
            )

        mock_source = collector.collect.call_args.args[0]
        assert mock_source.config["data_type"] == "cn_indices"
        assert mock_source.config["symbols"] == ["000001.SS"]
