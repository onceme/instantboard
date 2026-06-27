import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import (
    DuplicateWatchlistItem,
    ServiceUnavailable,
    SymbolNotFound,
    ValidationError,
)
from app.services.finance import FinanceService, MAX_WATCHLIST_ITEMS, _MockSource


def _mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db, mock_result


def _mock_redis():
    return AsyncMock()


def _make_finance_symbol(
    sym_id=None, tenant_id="tenant-1", symbol="AAPL", name="Apple Inc",
    type_="stock", market="US", exchange="NASDAQ", currency="USD", is_active=True
):
    fs = MagicMock()
    fs.id = sym_id or uuid.uuid4()
    fs.tenant_id = tenant_id
    fs.symbol = symbol
    fs.name = name
    fs.type = type_
    fs.market = market
    fs.exchange = exchange
    fs.currency = currency
    fs.is_active = is_active
    return fs


def _make_watchlist_item(
    item_id=None, tenant_id="tenant-1", user_id="user-1",
    symbol_id=None, display_order=0, notes=None, alert_threshold_percent=None
):
    item = MagicMock()
    item.id = item_id or uuid.uuid4()
    item.tenant_id = tenant_id
    item.user_id = user_id
    item.symbol_id = symbol_id or uuid.uuid4()
    item.display_order = display_order
    item.notes = notes
    item.alert_threshold_percent = alert_threshold_percent
    item.symbol = None
    return item


class TestSearchSymbols:
    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    async def test_search_from_cache(self, mock_redis_set, mock_redis_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cached_data = json.dumps([
            {"symbol": "AAPL", "name": "Apple", "type": "stock", "market": "US",
             "exchange": "NASDAQ", "current_price": 150.0, "change_percent": 1.5, "currency": "USD"}
        ])
        mock_redis_get.return_value = cached_data

        service = FinanceService(db, redis)
        result = await service.search_symbols("tenant-1", "AAPL")

        assert result["data"][0]["symbol"] == "AAPL"
        assert result["meta"]["total"] == 1

    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    async def test_search_from_db(self, mock_redis_set, mock_redis_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fs = _make_finance_symbol()
        scalars = MagicMock()
        scalars.all.return_value = [fs]
        mock_result.scalars.return_value = scalars

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value={"current_price": 150, "change_percent": 1.5}):
            service = FinanceService(db, redis)
            result = await service.search_symbols("tenant-1", "AAPL")

            assert len(result["data"]) >= 1

    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    async def test_search_with_type_and_market_filter(self, mock_redis_set, mock_redis_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        scalars = MagicMock()
        scalars.all.return_value = []
        mock_result.scalars.return_value = scalars

        with patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=[]):
            service = FinanceService(db, redis)
            result = await service.search_symbols("tenant-1", "AAPL", type="stock", market="US")
            assert result["meta"]["total"] == 0

    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    async def test_search_external_fallback(self, mock_redis_set, mock_redis_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        scalars = MagicMock()
        scalars.all.return_value = []
        mock_result.scalars.return_value = scalars

        external_results = [
            {"symbol": "AAPL", "name": "Apple", "type": "stock", "market": "US",
             "exchange": "NASDAQ", "current_price": None, "change_percent": None, "currency": "USD"}
        ]

        with patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=external_results):
            service = FinanceService(db, redis)
            result = await service.search_symbols("tenant-1", "AAPL")
            assert result["meta"]["total"] == 1


class TestGetQuote:
    @patch("app.services.finance.event_router")
    async def test_get_quote_cached(self, mock_router):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cached = {"symbol": "AAPL", "current_price": 150, "name": "Apple"}

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=cached):
            service = FinanceService(db, redis)
            result = await service.get_quote("tenant-1", "AAPL")
            assert result["symbol"] == "AAPL"

    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    async def test_get_quote_fetch_with_failover(self, mock_redis_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        quote_data = {"symbol": "AAPL", "current_price": 150, "name": "Apple", "change": 2.5}

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None), \
             patch.object(FinanceService, "_fetch_quote_with_failover", new_callable=AsyncMock, return_value=quote_data), \
             patch.object(FinanceService, "_store_quote_to_db", new_callable=AsyncMock), \
             patch.object(FinanceService, "_cache_quote", new_callable=AsyncMock):
            service = FinanceService(db, redis)
            result = await service.get_quote("tenant-1", "AAPL")
            assert result["symbol"] == "AAPL"

    async def test_get_quote_not_available(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None), \
             patch.object(FinanceService, "_fetch_quote_with_failover", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            with pytest.raises(SymbolNotFound, match="Quote not available"):
                await service.get_quote("tenant-1", "INVALID")


class TestGetMarketIndices:
    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_market_indices_success(self, mock_get, mock_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        indices_data = [
            {"symbol": "^GSPC", "current_price": 5000, "change": 10, "change_percent": 0.2, "timestamp": "2024-01-01"},
        ]

        with patch.object(FinanceService, "_fetch_indices_with_failover", new_callable=AsyncMock, return_value=indices_data):
            service = FinanceService(db, redis)
            result = await service.get_market_indices("tenant-1")
            assert len(result) > 0
            sp500 = next(r for r in result if r["symbol"] == "^GSPC")
            assert sp500["value"] == 5000

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_get_market_indices_cached(self, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cached = json.dumps([{"symbol": "^GSPC", "name": "S&P 500", "value": 5000}])
        mock_get.return_value = cached

        service = FinanceService(db, redis)
        result = await service.get_market_indices("tenant-1")
        assert len(result) == 1

    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_market_indices_unavailable(self, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        with patch.object(FinanceService, "_fetch_indices_with_failover", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            with pytest.raises(ServiceUnavailable):
                await service.get_market_indices("tenant-1")


class TestGetCommodities:
    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_commodities_success(self, mock_get, mock_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        commodities_data = [
            {"symbol": "GC=F", "current_price": 2000, "change": 5, "change_percent": 0.25, "timestamp": "2024-01-01"},
        ]

        with patch.object(FinanceService, "_fetch_commodities_with_failover", new_callable=AsyncMock, return_value=commodities_data):
            service = FinanceService(db, redis)
            result = await service.get_commodities("tenant-1")
            assert len(result) > 0
            gold = next(r for r in result if r["symbol"] == "GC=F")
            assert gold["value"] == 2000

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_get_commodities_cached(self, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cached = json.dumps([{"symbol": "GC=F", "name": "Gold", "value": 2000}])
        mock_get.return_value = cached

        service = FinanceService(db, redis)
        result = await service.get_commodities("tenant-1")
        assert len(result) == 1

    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_commodities_unavailable(self, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        with patch.object(FinanceService, "_fetch_commodities_with_failover", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            with pytest.raises(ServiceUnavailable):
                await service.get_commodities("tenant-1")


class TestGetFundNav:
    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_fund_nav_success(self, mock_get, mock_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fund = _make_finance_symbol(type_="fund")

        nav_record = MagicMock()
        nav_record.nav_official = 1.5
        nav_record.nav_official_date = "2024-01-01"
        nav_record.nav_estimate = 1.52
        nav_record.nav_estimate_deviation_percent = 1.3
        nav_record.estimate_method = "official"
        nav_record.underlying_index_symbol = None
        nav_record.underlying_index_value = None
        nav_record.underlying_index_change_percent = None

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = fund
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = nav_record
            return mock_r

        db.execute = execute_side_effect

        service = FinanceService(db, redis)
        result = await service.get_fund_nav("tenant-1", "FUND001")
        assert result["symbol"] == "FUND001"
        assert result["nav_official"] == 1.5

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_get_fund_nav_cached(self, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cached = json.dumps({"symbol": "FUND001", "nav_official": 1.5})
        mock_get.return_value = cached

        service = FinanceService(db, redis)
        result = await service.get_fund_nav("tenant-1", "FUND001")
        assert result["symbol"] == "FUND001"

    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_fund_nav_symbol_not_found(self, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = FinanceService(db, redis)
        with pytest.raises(SymbolNotFound, match="Fund symbol not found"):
            await service.get_fund_nav("tenant-1", "INVALID")

    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_fund_nav_realtime_estimate(self, mock_get, mock_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fund = _make_finance_symbol(type_="fund")

        nav_record = MagicMock()
        nav_record.nav_official = 2.0
        nav_record.nav_official_date = "2024-01-01"
        nav_record.nav_estimate = 2.01
        nav_record.nav_estimate_deviation_percent = 0.5
        nav_record.estimate_method = "official"
        nav_record.underlying_index_symbol = "000300.SS"
        nav_record.underlying_index_value = 3500
        nav_record.underlying_index_change_percent = 1.0

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = fund
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = nav_record
            return mock_r

        db.execute = execute_side_effect

        index_quote = {"name": "CSI 300", "current_price": 3550, "change_percent": 1.5}

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=index_quote):
            service = FinanceService(db, redis)
            result = await service.get_fund_nav("tenant-1", "FUND001", estimate_type="realtime")
            assert result["estimate_method"] == "index_tracking"
            assert result["underlying_index"]["name"] == "CSI 300"

    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_fund_nav_no_nav_record(self, mock_get, mock_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fund = _make_finance_symbol(type_="fund")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = fund
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = None
            return mock_r

        db.execute = execute_side_effect

        service = FinanceService(db, redis)
        result = await service.get_fund_nav("tenant-1", "FUND001")
        assert result["nav_official"] is None
        assert result["nav_estimate"] is None


class TestGetWatchlist:
    async def test_get_watchlist(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        item = _make_watchlist_item(display_order=0)
        sym = _make_finance_symbol()
        item.symbol = sym

        scalars = MagicMock()
        scalars.all.return_value = [item]
        mock_result.scalars.return_value = scalars

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value={"current_price": 150, "change": 2, "change_percent": 1.3}):
            service = FinanceService(db, redis)
            result = await service.get_watchlist("tenant-1", "user-1")
            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"

    async def test_get_watchlist_no_symbol(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        item = _make_watchlist_item()
        item.symbol = None

        scalars = MagicMock()
        scalars.all.return_value = [item]
        mock_result.scalars.return_value = scalars

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            result = await service.get_watchlist("tenant-1", "user-1")
            assert result[0]["symbol"] is None

    async def test_get_watchlist_with_alert_threshold(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        item = _make_watchlist_item(alert_threshold_percent=5.0)
        sym = _make_finance_symbol()
        item.symbol = sym

        scalars = MagicMock()
        scalars.all.return_value = [item]
        mock_result.scalars.return_value = scalars

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            result = await service.get_watchlist("tenant-1", "user-1")
            assert result[0]["alert_threshold_percent"] == 5.0


class TestAddToWatchlist:
    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_add_success(self, mock_redis_del):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        new_item = MagicMock()
        new_item.id = uuid.uuid4()
        new_item.symbol_id = uuid.uuid4()
        new_item.display_order = 0
        new_item.notes = "test"
        new_item.alert_threshold_percent = None

        sym = _make_finance_symbol()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar.return_value = 0
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 3:
                mock_r.scalar.return_value = -1
            elif call_count == 4:
                mock_r.scalar_one_or_none.return_value = sym
            return mock_r

        db.execute = execute_side_effect

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            result = await service.add_to_watchlist("tenant-1", "user-1", {"symbol_id": str(uuid.uuid4())})
            assert "id" in result

    async def test_add_limit_reached(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar.return_value = MAX_WATCHLIST_ITEMS

        service = FinanceService(db, redis)
        with pytest.raises(ValidationError, match="Watchlist limit reached"):
            await service.add_to_watchlist("tenant-1", "user-1", {"symbol_id": str(uuid.uuid4())})

    async def test_add_duplicate(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        existing = _make_watchlist_item()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar.return_value = 1
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = existing
            return mock_r

        db.execute = execute_side_effect

        service = FinanceService(db, redis)
        with pytest.raises(DuplicateWatchlistItem):
            await service.add_to_watchlist("tenant-1", "user-1", {"symbol_id": str(uuid.uuid4())})


class TestRemoveFromWatchlist:
    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_remove_success(self, mock_redis_del):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        item = _make_watchlist_item()
        mock_result.scalar_one_or_none.return_value = item

        service = FinanceService(db, redis)
        await service.remove_from_watchlist("tenant-1", "user-1", str(item.id))
        db.delete.assert_called_once_with(item)

    async def test_remove_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = FinanceService(db, redis)
        with pytest.raises(SymbolNotFound, match="Watchlist item not found"):
            await service.remove_from_watchlist("tenant-1", "user-1", "bad-id")


class TestReorderWatchlist:
    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_reorder(self, mock_redis_del):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        item1 = _make_watchlist_item(display_order=0)
        item2 = _make_watchlist_item(display_order=1)

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = item1
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = item2
            return mock_r

        db.execute = execute_side_effect

        service = FinanceService(db, redis)
        await service.reorder_watchlist("tenant-1", "user-1", [
            {"item_id": str(item1.id), "display_order": 1},
            {"item_id": str(item2.id), "display_order": 0},
        ])
        assert item1.display_order == 1
        assert item2.display_order == 0

    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_reorder_missing_item_skipped(self, mock_redis_del):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = FinanceService(db, redis)
        await service.reorder_watchlist("tenant-1", "user-1", [
            {"item_id": "nonexistent", "display_order": 0},
        ])
        db.commit.assert_called_once()


class TestGetWatchlistQuotes:
    async def test_get_watchlist_quotes(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        watchlist_items = [
            {"symbol": "AAPL", "symbol_id": str(uuid.uuid4())},
            {"symbol": "GOOG", "symbol_id": str(uuid.uuid4())},
        ]

        with patch.object(FinanceService, "get_watchlist", new_callable=AsyncMock, return_value=watchlist_items), \
             patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, side_effect=[{"symbol": "AAPL", "price": 150}, None]), \
             patch.object(FinanceService, "get_quote", new_callable=AsyncMock, return_value={"symbol": "GOOG", "price": 2800}):
            service = FinanceService(db, redis)
            result = await service.get_watchlist_quotes("tenant-1", "user-1")
            assert len(result) == 2

    async def test_get_watchlist_quotes_symbol_not_found_suppressed(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        watchlist_items = [{"symbol": "INVALID", "symbol_id": str(uuid.uuid4())}]

        with patch.object(FinanceService, "get_watchlist", new_callable=AsyncMock, return_value=watchlist_items), \
             patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None), \
             patch.object(FinanceService, "get_quote", new_callable=AsyncMock, side_effect=SymbolNotFound()):
            service = FinanceService(db, redis)
            result = await service.get_watchlist_quotes("tenant-1", "user-1")
            assert len(result) == 0

    async def test_get_watchlist_quotes_no_symbol(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        watchlist_items = [{"symbol": None, "symbol_id": str(uuid.uuid4())}]

        with patch.object(FinanceService, "get_watchlist", new_callable=AsyncMock, return_value=watchlist_items):
            service = FinanceService(db, redis)
            result = await service.get_watchlist_quotes("tenant-1", "user-1")
            assert len(result) == 0


class TestHelperMethods:
    def test_is_market_open(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        result = service._is_market_open("UNKNOWN")
        assert result is False

    def test_is_market_open_weekend(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        from unittest.mock import patch as mock_patch
        from datetime import time as dt_time

        with mock_patch("app.services.finance.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 5
            mock_now.time.return_value = dt_time(10, 0)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = service._is_market_open("US")
            assert result is False

    def test_infer_market(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        assert service._infer_market("AAPL") == "US"
        assert service._infer_market("600000.SS") == "CN"
        assert service._infer_market("000001.SZ") == "CN"
        assert service._infer_market("00700.HK") == "HK"
        assert service._infer_market("6758.T") == "JP"
        assert service._infer_market("BARC.L") == "GB"
        assert service._infer_market("BMW.DE") == "DE"
        assert service._infer_market("^GSPC") == "US"

    def test_map_yfinance_type(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        assert service._map_yfinance_type("EQUITY") == "stock"
        assert service._map_yfinance_type("ETF") == "fund"
        assert service._map_yfinance_type("MUTUALFUND") == "fund"
        assert service._map_yfinance_type("INDEX") == "index"
        assert service._map_yfinance_type("CURRENCY") == "currency"
        assert service._map_yfinance_type("CRYPTOCURRENCY") == "currency"
        assert service._map_yfinance_type("FUTURE") == "futures"
        assert service._map_yfinance_type("COMMODITY") == "commodity"
        assert service._map_yfinance_type("UNKNOWN") == "stock"

    def test_paginate_results(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        results = [{"symbol": f"SYM{i}"} for i in range(25)]
        page = service._paginate_results(results, page=2, page_size=10)
        assert page["meta"]["total"] == 25
        assert page["meta"]["page"] == 2
        assert len(page["data"]) == 10

    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_cached_quote_miss(self, mock_get):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        result = await service._get_cached_quote("tenant-1", "AAPL")
        assert result is None

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_get_cached_quote_invalid_json(self, mock_get):
        db, _ = _mock_db()
        redis = _mock_redis()
        mock_get.return_value = "not-json{{{"
        service = FinanceService(db, redis)
        result = await service._get_cached_quote("tenant-1", "AAPL")
        assert result is None

    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    async def test_cache_quote(self, mock_set):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        await service._cache_quote("tenant-1", "AAPL", {"price": 150})


class TestMockSource:
    def test_mock_source(self):
        ms = _MockSource("tenant-1", "test", {"key": "val"})
        assert ms.id == "mock"
        assert ms.tenant_id == "tenant-1"
        assert ms.name == "test"
        assert ms.config == {"key": "val"}
        assert ms.url == ""
        assert ms.source_type == "api"

    def test_mock_source_defaults(self):
        ms = _MockSource("tenant-1", "test")
        assert ms.config == {}


class TestStoreQuoteToDb:
    async def test_store_quote_new_symbol(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = None
            return mock_r

        db.execute = execute_side_effect

        service = FinanceService(db, redis)
        await service._store_quote_to_db("tenant-1", "AAPL", {
            "name": "Apple", "current_price": 150, "open": 148, "high": 152,
            "low": 147, "volume": 1000000, "change": 2, "change_percent": 1.3,
            "market_cap": 3000000000, "pe_ratio": 25, "52_week_high": 180,
            "52_week_low": 120, "source": "yfinance", "type": "stock",
            "market": "US", "exchange": "NASDAQ", "currency": "USD"
        })
        assert db.add.call_count == 2

    async def test_store_quote_existing_symbol(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        existing_symbol = _make_finance_symbol()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = existing_symbol
            return mock_r

        db.execute = execute_side_effect

        service = FinanceService(db, redis)
        await service._store_quote_to_db("tenant-1", "AAPL", {
            "current_price": 150, "source": "yfinance"
        })
        assert db.add.call_count == 1


class TestGetFailoverChain:
    def test_stock_quote_cn(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        chain = service._get_failover_chain("stock_quote", "600000.SS")
        assert chain[0]["name"] == "eastmoney"

    def test_stock_quote_us(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        chain = service._get_failover_chain("stock_quote", "AAPL")
        assert chain[0]["name"] == "yfinance"

    def test_market_index(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        chain = service._get_failover_chain("market_index")
        assert len(chain) == 3

    def test_commodity(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        chain = service._get_failover_chain("commodity")
        assert len(chain) == 2

    def test_default(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        chain = service._get_failover_chain("unknown_type")
        assert len(chain) == 1


class TestSearchSymbolsExternal:
    async def test_external_search_failure(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_httpx_module = MagicMock()
        mock_httpx_module.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_module.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("sys.modules", {"httpx": mock_httpx_module}):
            service = FinanceService(db, redis)
            result = await service._search_symbols_external("INVALID", "tenant-1")
            assert result == []

    async def test_external_search_success(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "quotes": [
                {"symbol": "AAPL", "shortname": "Apple Inc", "quoteType": "EQUITY", "exchange": "NASDAQ", "currency": "USD"},
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_httpx_module = MagicMock()
        mock_httpx_module.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_module.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_yf_module = MagicMock()
        with patch.dict("sys.modules", {"httpx": mock_httpx_module, "app.collectors.finance.yfinance_collector": mock_yf_module}):
            service = FinanceService(db, redis)

            with patch.object(db, "commit", new_callable=AsyncMock):
                result = await service._search_symbols_external("AAPL", "tenant-1")
            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"

    async def test_external_search_import_error(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        with patch.dict("sys.modules", {"app.collectors.finance.yfinance_collector": None}):
            service = FinanceService(db, redis)
            result = await service._search_symbols_external("AAPL", "tenant-1")
            assert result == []


class TestFetchMethods:
    async def test_fetch_with_failover_all_fail(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        with patch.object(FinanceService, "_try_collector", new_callable=AsyncMock, side_effect=Exception("fail")):
            service = FinanceService(db, redis)
            result = await service._fetch_with_failover("tenant-1", "AAPL", [{"name": "yfinance", "collector": "yfinance"}])
            assert result is None

    async def test_fetch_with_failover_batch_all_fail(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        with patch.object(FinanceService, "_try_collector_batch", new_callable=AsyncMock, side_effect=Exception("fail")):
            service = FinanceService(db, redis)
            result = await service._fetch_with_failover_batch(
                "tenant-1", ["^GSPC"], [{"name": "yfinance", "collector": "yfinance"}]
            )
            assert result is None


class TestFetchFailoverSuccessPaths:
    """Cover lines 629-630, 633-635, 638-640, 646-647, 661-662."""

    async def test_fetch_quote_with_failover(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        quote_data = {"symbol": "AAPL", "current_price": 150}

        with patch.object(FinanceService, "_fetch_with_failover", new_callable=AsyncMock, return_value=quote_data):
            result = await service._fetch_quote_with_failover("tenant-1", "AAPL")
            assert result["symbol"] == "AAPL"

    async def test_fetch_quote_with_failover_cn_symbol(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)
        quote_data = {"symbol": "000001.SS", "current_price": 3500}

        with patch.object(FinanceService, "_fetch_with_failover", new_callable=AsyncMock, return_value=quote_data):
            result = await service._fetch_quote_with_failover("tenant-1", "000001.SS")
            assert result["symbol"] == "000001.SS"

    async def test_fetch_indices_with_failover(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        indices = [{"symbol": "^GSPC", "current_price": 5000}]
        with patch.object(FinanceService, "_fetch_with_failover_batch", new_callable=AsyncMock, return_value=indices):
            result = await service._fetch_indices_with_failover("tenant-1")
            assert len(result) == 1

    async def test_fetch_commodities_with_failover(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        commodities = [{"symbol": "GC=F", "current_price": 2000}]
        with patch.object(FinanceService, "_fetch_with_failover_batch", new_callable=AsyncMock, return_value=commodities):
            result = await service._fetch_commodities_with_failover("tenant-1")
            assert len(result) == 1

    async def test_fetch_with_failover_first_succeeds(self):
        """Cover line 646-647: first collector in chain returns result."""
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        quote = {"symbol": "AAPL", "current_price": 150}
        chain = [
            {"name": "yfinance", "collector": "yfinance"},
            {"name": "alpha_vantage", "collector": "alpha_vantage"},
        ]
        with patch.object(FinanceService, "_try_collector", new_callable=AsyncMock, return_value=quote):
            result = await service._fetch_with_failover("tenant-1", "AAPL", chain)
            assert result == quote

    async def test_fetch_with_failover_first_fails_second_succeeds(self):
        """Cover failover: first fails, second returns result."""
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        quote = {"symbol": "AAPL", "current_price": 150}
        chain = [
            {"name": "yfinance", "collector": "yfinance"},
            {"name": "alpha_vantage", "collector": "alpha_vantage"},
        ]
        with patch.object(FinanceService, "_try_collector", new_callable=AsyncMock, side_effect=[Exception("fail"), quote]):
            result = await service._fetch_with_failover("tenant-1", "AAPL", chain)
            assert result == quote

    async def test_fetch_with_failover_batch_first_succeeds(self):
        """Cover lines 661-662: first batch collector returns results."""
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        batch = [{"symbol": "^GSPC", "current_price": 5000}]
        chain = [{"name": "yfinance", "collector": "yfinance"}]
        with patch.object(FinanceService, "_try_collector_batch", new_callable=AsyncMock, return_value=batch):
            result = await service._fetch_with_failover_batch("tenant-1", ["^GSPC"], chain)
            assert len(result) == 1

    async def test_fetch_with_failover_batch_empty_result_continues(self):
        """When batch returns empty list, try next collector."""
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        batch = [{"symbol": "^GSPC", "current_price": 5000}]
        chain = [
            {"name": "yfinance", "collector": "yfinance"},
            {"name": "alpha_vantage", "collector": "alpha_vantage"},
        ]
        with patch.object(FinanceService, "_try_collector_batch", new_callable=AsyncMock, side_effect=[[], batch]):
            result = await service._fetch_with_failover_batch("tenant-1", ["^GSPC"], chain)
            assert len(result) == 1


class TestTryCollector:
    """Cover lines 702-725: _try_collector method."""

    async def test_try_collector_success_with_matching_symbol(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        mock_collector = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.items = [{"symbol": "AAPL", "current_price": 150}]
        mock_collector.collect = AsyncMock(return_value=result_mock)

        mock_collector_class = MagicMock(return_value=mock_collector)

        with patch("app.collectors.COLLECTOR_REGISTRY", {"yfinance": mock_collector_class}):
            result = await service._try_collector(
                "tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"}
            )
            assert result is not None
            assert result["symbol"] == "AAPL"
            assert result["source"] == "yfinance"

    async def test_try_collector_success_no_match_returns_first(self):
        """When no symbol matches, returns first item."""
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        mock_collector = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.items = [{"symbol": "MSFT", "current_price": 300}]
        mock_collector.collect = AsyncMock(return_value=result_mock)

        mock_collector_class = MagicMock(return_value=mock_collector)

        with patch("app.collectors.COLLECTOR_REGISTRY", {"yfinance": mock_collector_class}):
            result = await service._try_collector(
                "tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"}
            )
            assert result is not None
            assert result["symbol"] == "MSFT"

    async def test_try_collector_no_items(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        mock_collector = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.items = []
        mock_collector.collect = AsyncMock(return_value=result_mock)

        mock_collector_class = MagicMock(return_value=mock_collector)

        with patch("app.collectors.COLLECTOR_REGISTRY", {"yfinance": mock_collector_class}):
            result = await service._try_collector(
                "tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"}
            )
            assert result is None

    async def test_try_collector_failed_result(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        mock_collector = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = False
        result_mock.items = []
        mock_collector.collect = AsyncMock(return_value=result_mock)

        mock_collector_class = MagicMock(return_value=mock_collector)

        with patch("app.collectors.COLLECTOR_REGISTRY", {"yfinance": mock_collector_class}):
            result = await service._try_collector(
                "tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"}
            )
            assert result is None

    async def test_try_collector_not_registered(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        with patch("app.collectors.COLLECTOR_REGISTRY", {}):
            result = await service._try_collector(
                "tenant-1", "AAPL", {"name": "unknown", "collector": "unknown"}
            )
            assert result is None


class TestTryCollectorBatch:
    """Cover lines 728-755: _try_collector_batch method."""

    async def test_try_collector_batch_success(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        mock_collector = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.items = [
            {"symbol": "^GSPC", "current_price": 5000},
            {"symbol": "^DJI", "current_price": 35000},
        ]
        mock_collector.collect = AsyncMock(return_value=result_mock)

        mock_collector_class = MagicMock(return_value=mock_collector)

        with patch("app.collectors.COLLECTOR_REGISTRY", {"yfinance": mock_collector_class}):
            result = await service._try_collector_batch(
                "tenant-1", ["^GSPC", "^DJI"], {"name": "yfinance", "collector": "yfinance"}
            )
            assert len(result) == 2
            assert result[0]["source"] == "yfinance"

    async def test_try_collector_batch_eastmoney_config(self):
        """Cover lines with eastmoney-specific mock source config."""
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        mock_collector = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.items = [{"symbol": "000001.SS", "current_price": 3500}]
        mock_collector.collect = AsyncMock(return_value=result_mock)

        mock_collector_class = MagicMock(return_value=mock_collector)

        with patch("app.collectors.COLLECTOR_REGISTRY", {"eastmoney": mock_collector_class}):
            result = await service._try_collector_batch(
                "tenant-1", ["000001.SS"], {"name": "eastmoney", "collector": "eastmoney"}
            )
            assert len(result) == 1

    async def test_try_collector_batch_not_registered(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        with patch("app.collectors.COLLECTOR_REGISTRY", {}):
            result = await service._try_collector_batch(
                "tenant-1", ["^GSPC"], {"name": "unknown", "collector": "unknown"}
            )
            assert result is None

    async def test_try_collector_batch_failure(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        mock_collector = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = False
        result_mock.items = []
        mock_collector.collect = AsyncMock(return_value=result_mock)

        mock_collector_class = MagicMock(return_value=mock_collector)

        with patch("app.collectors.COLLECTOR_REGISTRY", {"yfinance": mock_collector_class}):
            result = await service._try_collector_batch(
                "tenant-1", ["^GSPC"], {"name": "yfinance", "collector": "yfinance"}
            )
            assert result is None


class TestCacheDecodeErrors:
    """Cover lines 106-107, 202-203, 250-251, 295-296: JSONDecodeError on cached data."""

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_search_cache_invalid_json(self, mock_redis_get):
        """Cover lines 106-107: invalid JSON in search cache."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_redis_get.return_value = "invalid-json{{{"

        scalars = MagicMock()
        scalars.all.return_value = []
        mock_result.scalars.return_value = scalars

        with patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=[]):
            service = FinanceService(db, redis)
            result = await service.search_symbols("tenant-1", "INVALID")
            assert result["meta"]["total"] == 0

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_market_indices_cache_invalid_json(self, mock_redis_get):
        """Cover lines 202-203: invalid JSON in market indices cache."""
        db, _ = _mock_db()
        redis = _mock_redis()

        mock_redis_get.return_value = "not-json{{{"

        with patch.object(FinanceService, "_fetch_indices_with_failover", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            with pytest.raises(ServiceUnavailable):
                await service.get_market_indices("tenant-1")

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_commodities_cache_invalid_json(self, mock_redis_get):
        """Cover lines 250-251: invalid JSON in commodities cache."""
        db, _ = _mock_db()
        redis = _mock_redis()

        mock_redis_get.return_value = "broken-json"

        with patch.object(FinanceService, "_fetch_commodities_with_failover", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            with pytest.raises(ServiceUnavailable):
                await service.get_commodities("tenant-1")

    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    async def test_fund_nav_cache_invalid_json(self, mock_redis_get):
        """Cover lines 295-296: invalid JSON in fund nav cache."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_redis_get.return_value = "{bad json"

        mock_result.scalar_one_or_none.return_value = None

        service = FinanceService(db, redis)
        with pytest.raises(SymbolNotFound, match="Fund symbol not found"):
            await service.get_fund_nav("tenant-1", "INVALID")


class TestSearchExternalEmptySymbol:
    """Cover line 782: continue when symbol is empty in external search."""

    async def test_external_search_skips_empty_symbol(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "quotes": [
                {"symbol": "", "shortname": "Empty Symbol", "quoteType": "EQUITY", "exchange": "NYSE", "currency": "USD"},
                {"symbol": "AAPL", "shortname": "Apple Inc", "quoteType": "EQUITY", "exchange": "NASDAQ", "currency": "USD"},
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_httpx_module = MagicMock()
        mock_httpx_module.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_module.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_yf_module = MagicMock()
        with patch.dict("sys.modules", {"httpx": mock_httpx_module, "app.collectors.finance.yfinance_collector": mock_yf_module}):
            service = FinanceService(db, redis)
            with patch.object(db, "commit", new_callable=AsyncMock):
                result = await service._search_symbols_external("test", "tenant-1")
            # Only AAPL should be returned, empty symbol skipped
            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"
