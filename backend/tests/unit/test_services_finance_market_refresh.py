"""Unit tests for the market indices / commodities refresh path (finance-tab.md §3.8.2).

Covers FinanceService.refresh_market_indices / refresh_commodities (the entry points
of the market_indices_refresh / commodities_refresh scheduled jobs): the failover
fetch, the Redis cache write, the SSE array push, and the change detection that lets
a scheduled round skip the push when the payload is unchanged. Also covers
is_any_market_open, the market-hours gate signal used by the job bodies.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.sse_router import SSEEventType
from app.services.finance import (
    COMMODITIES_CONFIG,
    MARKET_INDICES_CONFIG,
    FinanceService,
)


def _mock_db():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    return db


def _service():
    return FinanceService(_mock_db(), AsyncMock())


INDICES_RESULTS = [
    {"symbol": "^GSPC", "current_price": 5000, "change": 10, "change_percent": 0.2, "timestamp": "2024-01-01"},
]

COMMODITY_RESULTS = [
    {"symbol": "GC=F", "current_price": 2000, "change": 5, "change_percent": 0.25, "timestamp": "2024-01-01"},
]


class TestRefreshMarketIndices:
    async def test_success_fetches_writes_cache_and_pushes(self):
        """No previous cache → fetch → cache write (TTL 60) → SSE array push → True."""
        with (
            patch(
                "app.services.finance.FinanceService._fetch_indices_with_failover",
                new_callable=AsyncMock,
                return_value=INDICES_RESULTS,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None) as mock_get,
            patch("app.services.finance.redis_set", new_callable=AsyncMock) as mock_set,
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await _service().refresh_market_indices("tenant-1")

        assert result is True
        mock_set.assert_awaited_once()
        args, kwargs = mock_set.call_args
        assert args[0] == "t:tenant-1:market_indices"
        assert kwargs.get("ex") == 60
        mock_router.push_event.assert_awaited_once()
        push_args = mock_router.push_event.call_args.args
        assert push_args[0] == "finance"
        assert push_args[1] == SSEEventType.MARKET_INDEX_UPDATE
        assert isinstance(push_args[2], list) and len(push_args[2]) == len(MARKET_INDICES_CONFIG)
        assert push_args[3] == "tenant-1"
        # scheduled refresh reads the previous cache exactly once (change detection)
        assert mock_get.await_count == 1

    async def test_failure_returns_false_without_cache_write_or_push(self):
        """All failover sources failed → False, no cache write, no SSE push."""
        with (
            patch(
                "app.services.finance.FinanceService._fetch_indices_with_failover",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock) as mock_set,
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await _service().refresh_market_indices("tenant-1")

        assert result is False
        mock_set.assert_not_awaited()
        mock_router.push_event.assert_not_awaited()

    async def test_unchanged_payload_skips_push_but_renews_cache(self):
        """Payload identical to the cached one → cache write (TTL renewal) but no SSE push."""
        service = _service()
        formatted = service._format_market_indices(INDICES_RESULTS)

        with (
            patch(
                "app.services.finance.FinanceService._fetch_indices_with_failover",
                new_callable=AsyncMock,
                return_value=INDICES_RESULTS,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=json.dumps(formatted)),
            patch("app.services.finance.redis_set", new_callable=AsyncMock) as mock_set,
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await service.refresh_market_indices("tenant-1")

        assert result is True
        mock_set.assert_awaited_once()
        mock_router.push_event.assert_not_awaited()

    async def test_changed_payload_pushes(self):
        """Cached payload differs from the fresh one → SSE push still happens."""
        with (
            patch(
                "app.services.finance.FinanceService._fetch_indices_with_failover",
                new_callable=AsyncMock,
                return_value=INDICES_RESULTS,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=json.dumps([])),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await _service().refresh_market_indices("tenant-1")

        assert result is True
        mock_router.push_event.assert_awaited_once()

    async def test_corrupt_cached_payload_treated_as_changed(self):
        """Unreadable cache JSON → treated as changed → push."""
        with (
            patch(
                "app.services.finance.FinanceService._fetch_indices_with_failover",
                new_callable=AsyncMock,
                return_value=INDICES_RESULTS,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value="not-json"),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await _service().refresh_market_indices("tenant-1")

        assert result is True
        mock_router.push_event.assert_awaited_once()


class TestRefreshCommodities:
    async def test_success_fetches_writes_cache_and_pushes(self):
        with (
            patch(
                "app.services.finance.FinanceService._fetch_commodities_with_failover",
                new_callable=AsyncMock,
                return_value=COMMODITY_RESULTS,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock) as mock_set,
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await _service().refresh_commodities("tenant-1")

        assert result is True
        mock_set.assert_awaited_once()
        args, kwargs = mock_set.call_args
        assert args[0] == "t:tenant-1:commodities"
        assert kwargs.get("ex") == 60
        mock_router.push_event.assert_awaited_once()
        push_args = mock_router.push_event.call_args.args
        assert push_args[1] == SSEEventType.COMMODITY_UPDATE
        assert isinstance(push_args[2], list) and len(push_args[2]) == len(COMMODITIES_CONFIG)

    async def test_failure_returns_false_without_cache_write_or_push(self):
        with (
            patch(
                "app.services.finance.FinanceService._fetch_commodities_with_failover",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock) as mock_set,
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await _service().refresh_commodities("tenant-1")

        assert result is False
        mock_set.assert_not_awaited()
        mock_router.push_event.assert_not_awaited()

    async def test_unchanged_payload_skips_push_but_renews_cache(self):
        service = _service()
        formatted = service._format_commodities(COMMODITY_RESULTS)

        with (
            patch(
                "app.services.finance.FinanceService._fetch_commodities_with_failover",
                new_callable=AsyncMock,
                return_value=COMMODITY_RESULTS,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=json.dumps(formatted)),
            patch("app.services.finance.redis_set", new_callable=AsyncMock) as mock_set,
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await service.refresh_commodities("tenant-1")

        assert result is True
        mock_set.assert_awaited_once()
        mock_router.push_event.assert_not_awaited()

    async def test_changed_payload_pushes(self):
        with (
            patch(
                "app.services.finance.FinanceService._fetch_commodities_with_failover",
                new_callable=AsyncMock,
                return_value=COMMODITY_RESULTS,
            ),
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=json.dumps([{"x": 1}])),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await _service().refresh_commodities("tenant-1")

        assert result is True
        mock_router.push_event.assert_awaited_once()


class TestOnDemandPathStillDelegatesToRefresh:
    """get_market_indices / get_commodities cache misses must go through the same
    refresh path (identical REST behavior/response as before the extraction)."""

    async def test_get_market_indices_cache_miss_uses_refresh_path(self):
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch(
                "app.services.finance.FinanceService._refresh_market_indices",
                new_callable=AsyncMock,
                return_value=[{"symbol": "^GSPC", "value": 5000}],
            ) as mock_refresh,
        ):
            result = await _service().get_market_indices("tenant-1")

        mock_refresh.assert_awaited_once_with("tenant-1")
        assert result == [{"symbol": "^GSPC", "value": 5000}]

    async def test_get_commodities_cache_miss_uses_refresh_path(self):
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch(
                "app.services.finance.FinanceService._refresh_commodities",
                new_callable=AsyncMock,
                return_value=[{"symbol": "GC=F", "value": 2000}],
            ) as mock_refresh,
        ):
            result = await _service().get_commodities("tenant-1")

        mock_refresh.assert_awaited_once_with("tenant-1")
        assert result == [{"symbol": "GC=F", "value": 2000}]


class TestIsAnyMarketOpen:
    def test_false_when_all_markets_closed(self):
        service = _service()
        with patch.object(FinanceService, "_is_market_open", return_value=False) as mock_open:
            assert service.is_any_market_open() is False
        # every configured market region must have been consulted
        assert mock_open.call_count > 0

    def test_true_when_any_market_open(self):
        service = _service()
        with patch.object(FinanceService, "_is_market_open", side_effect=lambda region: region == "US"):
            assert service.is_any_market_open() is True

    def test_weekends_gate_through_is_market_open(self):
        """is_any_market_open delegates to _is_market_open, which already rejects weekends;
        a Saturday midnight check returns False for a real instance (smoke)."""
        from datetime import UTC, datetime

        service = _service()
        saturday = datetime(2024, 1, 6, 12, 0, tzinfo=UTC)
        with patch("app.services.finance.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            assert service.is_any_market_open() is False
