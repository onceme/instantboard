import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import (
    DuplicateWatchlistItem,
    ServiceUnavailable,
    SymbolNotFound,
    ValidationError,
)
from app.services.finance import (
    DATA_TYPE_MARKET_INDICES,
    MARKET_INDICES_CONFIG,
    MAX_WATCHLIST_ITEMS,
    FinanceService,
    _MockSource,
)


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
    sym_id=None,
    tenant_id="tenant-1",
    symbol="AAPL",
    name="Apple Inc",
    type_="stock",
    market="US",
    exchange="NASDAQ",
    currency="USD",
    is_active=True,
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
    item_id=None,
    tenant_id="tenant-1",
    user_id="user-1",
    symbol_id=None,
    display_order=0,
    notes=None,
    alert_threshold_percent=None,
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


def _item_then_symbol_execute(item, symbol):
    """db.execute side effect: 1st call returns the watchlist item, 2nd the symbol."""
    call_count = 0

    async def execute_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_r = MagicMock()
        if call_count == 1:
            mock_r.scalar_one_or_none.return_value = item
        elif call_count == 2:
            mock_r.scalar_one_or_none.return_value = symbol
        return mock_r

    return execute_side_effect


class TestSearchSymbols:
    @patch("app.services.finance.redis_get", new_callable=AsyncMock)
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    async def test_search_from_cache(self, mock_redis_set, mock_redis_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cached_data = json.dumps(
            [
                {
                    "symbol": "AAPL",
                    "name": "Apple",
                    "type": "stock",
                    "market": "US",
                    "exchange": "NASDAQ",
                    "current_price": 150.0,
                    "change_percent": 1.5,
                    "currency": "USD",
                }
            ]
        )
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

        with patch.object(
            FinanceService,
            "_get_cached_quote",
            new_callable=AsyncMock,
            return_value={"current_price": 150, "change_percent": 1.5},
        ):
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
            {
                "symbol": "AAPL",
                "name": "Apple",
                "type": "stock",
                "market": "US",
                "exchange": "NASDAQ",
                "current_price": None,
                "change_percent": None,
                "currency": "USD",
            }
        ]

        with patch.object(
            FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=external_results
        ):
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

        with (
            patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None),
            patch.object(FinanceService, "_fetch_quote_with_failover", new_callable=AsyncMock, return_value=quote_data),
            patch.object(FinanceService, "_store_quote_to_db", new_callable=AsyncMock),
            patch.object(FinanceService, "_cache_quote", new_callable=AsyncMock),
        ):
            service = FinanceService(db, redis)
            result = await service.get_quote("tenant-1", "AAPL")
            assert result["symbol"] == "AAPL"

    async def test_get_quote_not_available(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        with (
            patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None),
            patch.object(FinanceService, "_fetch_quote_with_failover", new_callable=AsyncMock, return_value=None),
        ):
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

        with patch.object(
            FinanceService, "_fetch_indices_with_failover", new_callable=AsyncMock, return_value=indices_data
        ):
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

        with patch.object(
            FinanceService, "_fetch_commodities_with_failover", new_callable=AsyncMock, return_value=commodities_data
        ):
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

        with patch.object(
            FinanceService, "_fetch_commodities_with_failover", new_callable=AsyncMock, return_value=None
        ):
            service = FinanceService(db, redis)
            with pytest.raises(ServiceUnavailable):
                await service.get_commodities("tenant-1")


def _make_nav_record(
    nav_official=None,
    nav_official_date=None,
    nav_estimate=None,
    nav_estimate_deviation_percent=None,
    estimate_method=None,
    underlying_index_symbol=None,
    underlying_index_value=None,
    underlying_index_change_percent=None,
):
    record = MagicMock()
    record.nav_official = nav_official
    record.nav_official_date = nav_official_date
    record.nav_estimate = nav_estimate
    record.nav_estimate_deviation_percent = nav_estimate_deviation_percent
    record.estimate_method = estimate_method
    record.underlying_index_symbol = underlying_index_symbol
    record.underlying_index_value = underlying_index_value
    record.underlying_index_change_percent = underlying_index_change_percent
    return record


def _fund_nav_execute(db, *, symbol, official=None, latest=None, save_lookup=None):
    """db.execute side effect for get_fund_nav (+ optional _save_nav_estimate).

    Call order: 1 symbol lookup, 2 official row, 3 latest row, 4 the estimate
    upsert lookup inside _save_nav_estimate (only when the estimate branch
    fires). Unknown/extra calls return an empty result so a MagicMock never
    leaks into float() conversions.
    """
    call_count = 0

    async def execute_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_r = MagicMock()
        if call_count == 1:
            mock_r.scalar_one_or_none.return_value = symbol
        elif call_count == 2:
            mock_r.scalar_one_or_none.return_value = official
        elif call_count == 3:
            mock_r.scalar_one_or_none.return_value = latest
        elif call_count == 4 and save_lookup is not None:
            mock_r.scalar_one_or_none.return_value = save_lookup
        else:
            mock_r.scalar_one_or_none.return_value = None
        return mock_r

    db.execute = execute_side_effect


class TestGetFundNav:
    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_fund_nav_success(self, mock_get, mock_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fund = _make_finance_symbol(type_="fund")
        nav_record = _make_nav_record(
            nav_official=1.5,
            nav_official_date=date(2024, 1, 1),
            nav_estimate=1.52,
            nav_estimate_deviation_percent=1.3,
            estimate_method="official",
        )

        _fund_nav_execute(db, symbol=fund, official=nav_record, latest=nav_record)

        service = FinanceService(db, redis)
        result = await service.get_fund_nav("tenant-1", "FUND001")
        assert result["symbol"] == "FUND001"
        assert result["nav_official"] == 1.5
        assert result["nav_official_date"] == "2024-01-01"

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
    async def test_get_fund_nav_official_row_preferred_over_latest(self, mock_get, mock_set, mock_router):
        """nav_official must come from the latest row carrying an official NAV,
        even when a newer estimate row (without nav_official) is the latest row."""
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fund = _make_finance_symbol(type_="fund")
        official_record = _make_nav_record(
            nav_official=3.3,
            nav_official_date=date(2024, 3, 1),
            estimate_method="official",
        )
        latest_record = _make_nav_record(
            nav_estimate=3.31,
            nav_estimate_deviation_percent=0.3,
            estimate_method="index_tracking",
        )

        _fund_nav_execute(db, symbol=fund, official=official_record, latest=latest_record)

        service = FinanceService(db, redis)
        result = await service.get_fund_nav("tenant-1", "FUND001", estimate_type="latest")
        assert result["nav_official"] == 3.3
        assert result["nav_official_date"] == "2024-03-01"
        # estimate fields still come from the latest row
        assert result["nav_estimate"] == 3.31
        assert result["estimate_method"] == "index_tracking"

    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_fund_nav_realtime_estimate(self, mock_get, mock_set, mock_router):
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fund = _make_finance_symbol(type_="fund")
        official_record = _make_nav_record(
            nav_official=2.0,
            nav_official_date=date(2024, 1, 1),
            estimate_method="official",
        )
        latest_record = _make_nav_record(
            nav_official=2.0,
            nav_official_date=date(2024, 1, 1),
            nav_estimate=2.01,
            nav_estimate_deviation_percent=0.5,
            estimate_method="index_tracking",
            underlying_index_symbol="000300.SS",
            underlying_index_value=3500,
            underlying_index_change_percent=1.0,
        )

        # Estimate branch fires → _save_nav_estimate runs a 4th query (the
        # upsert lookup); return an existing row so it takes the update path.
        existing_estimate_row = MagicMock()
        _fund_nav_execute(
            db, symbol=fund, official=official_record, latest=latest_record, save_lookup=existing_estimate_row
        )

        index_quote = {"name": "CSI 300", "current_price": 3550, "change_percent": 1.5}

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=index_quote):
            service = FinanceService(db, redis)
            result = await service.get_fund_nav("tenant-1", "FUND001", estimate_type="realtime")
            assert result["estimate_method"] == "index_tracking"
            assert result["underlying_index"]["name"] == "CSI 300"

        # A successful realtime estimate is persisted: the existing estimate
        # row was updated in place and committed.
        assert existing_estimate_row.nav_estimate == pytest.approx(2.0 * (1 + 1.5 / 100))
        assert existing_estimate_row.estimate_method == "index_tracking"
        db.commit.assert_awaited()

    @patch("app.services.finance.event_router")
    @patch("app.services.finance.redis_set", new_callable=AsyncMock)
    @patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_get_fund_nav_no_nav_record(self, mock_get, mock_set, mock_router):
        """No rows at all → a gentle all-None response, never an error."""
        mock_router.push_event = AsyncMock()
        db, mock_result = _mock_db()
        redis = _mock_redis()

        fund = _make_finance_symbol(type_="fund")
        _fund_nav_execute(db, symbol=fund)

        service = FinanceService(db, redis)
        result = await service.get_fund_nav("tenant-1", "FUND001")
        assert result["nav_official"] is None
        assert result["nav_official_date"] is None
        assert result["nav_estimate"] is None
        assert result["estimate_method"] is None
        assert result["underlying_index"] is None
        # Cached anyway (Redis semantics unchanged) and pushed to SSE.
        mock_set.assert_awaited()
        mock_router.push_event.assert_awaited()


class TestSaveNavEstimate:
    """Persistence of successful realtime estimates (_save_nav_estimate)."""

    async def test_insert_new_estimate_row(self):
        db, _ = _mock_db()
        fund = _make_finance_symbol(type_="fund")

        mock_r = MagicMock()
        mock_r.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_r)

        service = FinanceService(db, None)
        await service._save_nav_estimate(
            tenant_id="tenant-1",
            fund=fund,
            nav_official=2.0,
            nav_official_date=date(2024, 1, 1),
            nav_estimate=2.03,
            nav_estimate_deviation_percent=1.5,
            underlying_index_info={"symbol": "000300.SS", "current_value": 3550, "change_percent": 1.5},
        )

        assert db.add.call_count == 1
        row = db.add.call_args[0][0]
        assert row.nav_official == 2.0
        assert row.nav_official_date == date(2024, 1, 1)
        assert row.nav_estimate == 2.03
        assert row.estimate_method == "index_tracking"
        assert row.underlying_index_symbol == "000300.SS"
        db.commit.assert_awaited_once()

    async def test_update_existing_estimate_row(self):
        db, _ = _mock_db()
        fund = _make_finance_symbol(type_="fund")

        existing = MagicMock()
        mock_r = MagicMock()
        mock_r.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_r)

        service = FinanceService(db, None)
        await service._save_nav_estimate(
            tenant_id="tenant-1",
            fund=fund,
            nav_official=2.0,
            nav_official_date=date(2024, 1, 1),
            nav_estimate=2.04,
            nav_estimate_deviation_percent=2.0,
            underlying_index_info={"symbol": "000300.SS", "current_value": 3560, "change_percent": 2.0},
        )

        db.add.assert_not_called()
        assert existing.nav_estimate == 2.04
        assert existing.underlying_index_change_percent == 2.0
        db.commit.assert_awaited_once()

    async def test_skip_persist_without_official_nav_date(self):
        db, _ = _mock_db()
        fund = _make_finance_symbol(type_="fund")

        service = FinanceService(db, None)
        await service._save_nav_estimate(
            tenant_id="tenant-1",
            fund=fund,
            nav_official=2.0,
            nav_official_date=None,
            nav_estimate=2.03,
            nav_estimate_deviation_percent=1.5,
            underlying_index_info=None,
        )

        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_commit_failure_rolled_back_not_raised(self):
        db, _ = _mock_db()
        fund = _make_finance_symbol(type_="fund")

        mock_r = MagicMock()
        mock_r.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_r)
        db.commit = AsyncMock(side_effect=RuntimeError("db gone"))
        db.rollback = AsyncMock()

        service = FinanceService(db, None)
        # Must not raise: persistence trouble never breaks the read path.
        await service._save_nav_estimate(
            tenant_id="tenant-1",
            fund=fund,
            nav_official=2.0,
            nav_official_date=date(2024, 1, 1),
            nav_estimate=2.03,
            nav_estimate_deviation_percent=1.5,
            underlying_index_info=None,
        )
        db.rollback.assert_awaited_once()


class TestUpdateOfficialNav:
    """Daily official NAV refresh service entry (fund_nav_official_refresh job)."""

    def _fund(self, symbol="110011"):
        return _make_finance_symbol(symbol=symbol, type_="fund")

    def _execute_for(self, db, funds, upsert_row=None):
        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalars.return_value.all.return_value = funds
            else:
                mock_r.scalar_one_or_none.return_value = upsert_row
            return mock_r

        db.execute = execute_side_effect

    async def test_inserts_official_rows(self):
        from app.collectors.base import CollectionResult

        db, _ = _mock_db()
        fund = self._fund()
        self._execute_for(db, [fund], upsert_row=None)

        collection = CollectionResult(
            items=[{"symbol": "110011", "nav": 4.2133, "nav_date": "2026-08-26", "source": "tiantian_fund"}],
            success=True,
        )
        mock_collector = MagicMock()
        mock_collector.collect = AsyncMock(return_value=collection)

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector", return_value=mock_collector):
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 1
        assert db.add.call_count == 1
        row = db.add.call_args[0][0]
        assert row.nav_official == 4.2133
        assert row.nav_official_date == date(2026, 8, 26)
        assert row.estimate_method == "official"
        db.commit.assert_awaited_once()
        # Collector received the normalized fund code from finance_symbols.
        collect_source = mock_collector.collect.call_args[0][0]
        assert collect_source.config["fund_codes"] == ["110011"]

    async def test_updates_existing_official_row_same_day(self):
        """Re-running the daily job on the same NAV date updates, not duplicates."""
        from app.collectors.base import CollectionResult

        db, _ = _mock_db()
        fund = self._fund()
        existing = MagicMock()
        self._execute_for(db, [fund], upsert_row=existing)

        collection = CollectionResult(
            items=[{"symbol": "110011", "nav": 4.22, "nav_date": "2026-08-26"}],
            success=True,
        )
        mock_collector = MagicMock()
        mock_collector.collect = AsyncMock(return_value=collection)

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector", return_value=mock_collector):
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 1
        db.add.assert_not_called()
        assert existing.nav_official == 4.22
        assert existing.estimate_method == "official"
        db.commit.assert_awaited_once()

    async def test_normalizes_yaml_style_symbols(self):
        from app.collectors.base import CollectionResult

        db, _ = _mock_db()
        fund = self._fund(symbol="510300.SS")
        self._execute_for(db, [fund], upsert_row=None)

        collection = CollectionResult(items=[{"symbol": "510300", "nav": 3.9, "nav_date": "2026-08-26"}], success=True)
        mock_collector = MagicMock()
        mock_collector.collect = AsyncMock(return_value=collection)

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector", return_value=mock_collector):
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 1
        collect_source = mock_collector.collect.call_args[0][0]
        assert collect_source.config["fund_codes"] == ["510300"]

    async def test_no_fund_symbols_is_noop(self):
        db, _ = _mock_db()
        self._execute_for(db, [])

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector") as mock_cls:
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 0
        mock_cls.assert_not_called()
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_unmappable_symbols_is_noop(self):
        db, _ = _mock_db()
        # type=fund but the symbol is not a usable 6-digit Chinese fund code.
        self._execute_for(db, [self._fund(symbol="AAPL")])

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector") as mock_cls:
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 0
        mock_cls.assert_not_called()

    async def test_collection_failure_returns_zero_without_raising(self):
        from app.collectors.base import CollectionResult

        db, _ = _mock_db()
        self._execute_for(db, [self._fund()])

        collection = CollectionResult(items=[], success=False, error="All retry attempts failed")
        mock_collector = MagicMock()
        mock_collector.collect = AsyncMock(return_value=collection)

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector", return_value=mock_collector):
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 0
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_collector_exception_returns_zero_without_raising(self):
        db, _ = _mock_db()
        self._execute_for(db, [self._fund()])

        mock_collector = MagicMock()
        mock_collector.collect = AsyncMock(side_effect=RuntimeError("network partition"))

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector", return_value=mock_collector):
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 0
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_items_not_matching_tracked_funds_are_skipped(self):
        from app.collectors.base import CollectionResult

        db, _ = _mock_db()
        self._execute_for(db, [self._fund(symbol="110011")])

        collection = CollectionResult(
            items=[
                {"symbol": "999999", "nav": 1.0, "nav_date": "2026-08-26"},
                {"symbol": "110011", "nav": None, "nav_date": "2026-08-26"},
                {"symbol": "110011", "nav": 4.2, "nav_date": "not-a-date"},
            ],
            success=True,
        )
        mock_collector = MagicMock()
        mock_collector.collect = AsyncMock(return_value=collection)

        with patch("app.collectors.finance.fund_nav_collector.TiantianFundCollector", return_value=mock_collector):
            service = FinanceService(db, None)
            updated = await service.update_official_nav("tenant-1")

        assert updated == 0
        db.commit.assert_not_awaited()


class TestNormalizeFundCode:
    def test_bare_six_digit_code(self):
        assert FinanceService._normalize_fund_code("110011") == "110011"

    def test_strips_market_suffixes(self):
        assert FinanceService._normalize_fund_code("510300.SS") == "510300"
        assert FinanceService._normalize_fund_code("159915.SZ") == "159915"
        assert FinanceService._normalize_fund_code("110011.OF") == "110011"

    def test_non_codes_return_none(self):
        assert FinanceService._normalize_fund_code("AAPL") is None
        assert FinanceService._normalize_fund_code("FUND001") is None
        assert FinanceService._normalize_fund_code("12345") is None
        assert FinanceService._normalize_fund_code("") is None


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

        with patch.object(
            FinanceService,
            "_get_cached_quote",
            new_callable=AsyncMock,
            return_value={"current_price": 150, "change": 2, "change_percent": 1.3},
        ):
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

    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_add_success_with_symbol_only(self, mock_redis_del):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        sym = _make_finance_symbol(symbol="AAPL")

        new_item = MagicMock()
        new_item.id = uuid.uuid4()
        new_item.symbol_id = sym.id
        new_item.display_order = 0
        new_item.notes = None
        new_item.alert_threshold_percent = None

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = sym
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 4:
                mock_r.scalar.return_value = -1
            elif call_count == 5:
                mock_r.scalar_one_or_none.return_value = sym
            return mock_r

        db.execute = execute_side_effect

        with patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None):
            service = FinanceService(db, redis)
            result = await service.add_to_watchlist("tenant-1", "user-1", {"symbol": "aapl"})
            assert result["symbol_id"] == str(sym.id)
            assert result["symbol"] == "AAPL"
        db.add.assert_called_once()
        added = db.add.call_args.args[0]
        assert added.symbol_id == str(sym.id)

    async def test_add_symbol_only_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_result.scalar_one_or_none.return_value = None

        service = FinanceService(db, redis)
        with pytest.raises(SymbolNotFound, match="Symbol not found"):
            await service.add_to_watchlist("tenant-1", "user-1", {"symbol": "NOPE"})


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
        await service.reorder_watchlist(
            "tenant-1",
            "user-1",
            [
                {"item_id": str(item1.id), "display_order": 1},
                {"item_id": str(item2.id), "display_order": 0},
            ],
        )
        assert item1.display_order == 1
        assert item2.display_order == 0

    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_reorder_missing_item_skipped(self, mock_redis_del):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = FinanceService(db, redis)
        await service.reorder_watchlist(
            "tenant-1",
            "user-1",
            [
                {"item_id": "nonexistent", "display_order": 0},
            ],
        )
        db.commit.assert_called_once()


class TestGetWatchlistQuotes:
    async def test_get_watchlist_quotes(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        watchlist_items = [
            {"symbol": "AAPL", "symbol_id": str(uuid.uuid4())},
            {"symbol": "GOOG", "symbol_id": str(uuid.uuid4())},
        ]

        with (
            patch.object(FinanceService, "get_watchlist", new_callable=AsyncMock, return_value=watchlist_items),
            patch.object(
                FinanceService,
                "_get_cached_quote",
                new_callable=AsyncMock,
                side_effect=[{"symbol": "AAPL", "price": 150}, None],
            ),
            patch.object(
                FinanceService, "get_quote", new_callable=AsyncMock, return_value={"symbol": "GOOG", "price": 2800}
            ),
        ):
            service = FinanceService(db, redis)
            result = await service.get_watchlist_quotes("tenant-1", "user-1")
            assert len(result) == 2

    async def test_get_watchlist_quotes_symbol_not_found_suppressed(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        watchlist_items = [{"symbol": "INVALID", "symbol_id": str(uuid.uuid4())}]

        with (
            patch.object(FinanceService, "get_watchlist", new_callable=AsyncMock, return_value=watchlist_items),
            patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None),
            patch.object(FinanceService, "get_quote", new_callable=AsyncMock, side_effect=SymbolNotFound()),
        ):
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


class TestUpdateWatchlistAlertThreshold:
    async def test_threshold_below_min_rejected(self):
        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())

        with pytest.raises(ValidationError, match="alert_threshold_percent"):
            await service.update_watchlist_alert_threshold("tenant-1", "user-1", "wi-1", 0.4)
        # Validation runs before any DB access
        db.execute.assert_not_called()

    async def test_threshold_above_max_rejected(self):
        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())

        with pytest.raises(ValidationError, match="alert_threshold_percent"):
            await service.update_watchlist_alert_threshold("tenant-1", "user-1", "wi-1", 50.1)
        db.execute.assert_not_called()

    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_threshold_boundaries_accepted(self, mock_redis_del):
        for boundary in (0.5, 50.0):
            db, _ = _mock_db()
            item = _make_watchlist_item()
            sym = _make_finance_symbol()
            db.execute = _item_then_symbol_execute(item, sym)

            service = FinanceService(db, _mock_redis())
            result = await service.update_watchlist_alert_threshold("tenant-1", "user-1", str(item.id), boundary)
            assert item.alert_threshold_percent == boundary
            assert result["alert_threshold_percent"] == boundary

    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_null_threshold_clears_alert(self, mock_redis_del):
        db, _ = _mock_db()
        item = _make_watchlist_item(alert_threshold_percent=3.0)
        sym = _make_finance_symbol()
        db.execute = _item_then_symbol_execute(item, sym)

        service = FinanceService(db, _mock_redis())
        result = await service.update_watchlist_alert_threshold("tenant-1", "user-1", str(item.id), None)
        assert item.alert_threshold_percent is None
        assert result["alert_threshold_percent"] is None
        db.commit.assert_called_once()
        # Watchlist cache invalidated after the update
        mock_redis_del.assert_called_once()

    async def test_item_not_found(self):
        db, mock_result = _mock_db()
        mock_result.scalar_one_or_none.return_value = None

        service = FinanceService(db, _mock_redis())
        with pytest.raises(SymbolNotFound, match="Watchlist item not found"):
            await service.update_watchlist_alert_threshold("tenant-1", "user-1", "bad-id", 2.0)
        db.commit.assert_not_called()

    @patch("app.services.finance.redis_delete", new_callable=AsyncMock)
    async def test_success_returns_updated_item(self, mock_redis_del):
        db, _ = _mock_db()
        item = _make_watchlist_item()
        item.display_order = 2
        item.notes = "hold"
        sym = _make_finance_symbol()
        db.execute = _item_then_symbol_execute(item, sym)

        service = FinanceService(db, _mock_redis())
        result = await service.update_watchlist_alert_threshold("tenant-1", "user-1", str(item.id), 2.5)

        assert result == {
            "id": str(item.id),
            "symbol_id": str(item.symbol_id),
            "symbol": "AAPL",
            "name": "Apple Inc",
            "display_order": 2,
            "notes": "hold",
            "alert_threshold_percent": 2.5,
            "current_price": None,
            "change": None,
            "change_percent": None,
        }


class TestAlertThresholdDetection:
    def _item(self, threshold=2.0):
        return {
            "id": "wi-1",
            "symbol": "AAPL",
            "name": "Apple Inc",
            "alert_threshold_percent": threshold,
        }

    def _quote(self, change_percent=2.5):
        return {"symbol": "AAPL", "name": "Apple Inc", "current_price": 231.5, "change_percent": change_percent}

    def _mock_cooldown(self, acquired=True):
        client = AsyncMock()
        client.set.return_value = True if acquired else None
        mock_get_client = AsyncMock(return_value=client)
        return mock_get_client, client

    async def test_breach_fires_alert_with_full_payload(self):
        from app.core.redis import RedisKeys
        from app.core.sse_router import SSEEventType, event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        mock_get_client, client = self._mock_cooldown(acquired=True)

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            await service._check_alert_threshold("tenant-1", self._item(2.0), self._quote(2.5))

        mock_push.assert_awaited_once()
        category, event_type, payload, tenant_id = mock_push.call_args.args
        assert category == "finance"
        assert event_type == SSEEventType.ALERT_UPDATE
        assert tenant_id == "tenant-1"
        assert payload["symbol"] == "AAPL"
        assert payload["name"] == "Apple Inc"
        assert payload["price"] == 231.5
        assert payload["change_percent"] == 2.5
        assert payload["threshold_percent"] == 2.0
        assert payload["direction"] == "up"
        # ISO-8601 timestamp
        datetime.fromisoformat(payload["triggered_at"])
        # Cooldown marker: SET NX EX 3600 on the per-item key
        client.set.assert_awaited_once()
        call_kwargs = client.set.call_args.kwargs
        assert client.set.call_args.args[0] == RedisKeys.alert_fired_key("tenant-1", "wi-1")
        assert call_kwargs["nx"] is True
        assert call_kwargs["ex"] == 3600

    async def test_down_breach_direction_down(self):
        from app.core.sse_router import event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        mock_get_client, _ = self._mock_cooldown(acquired=True)

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            await service._check_alert_threshold("tenant-1", self._item(2.0), self._quote(-3.1))

        payload = mock_push.call_args.args[2]
        assert payload["direction"] == "down"
        assert payload["change_percent"] == -3.1

    async def test_exact_threshold_fires(self):
        from app.core.sse_router import event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        mock_get_client, _ = self._mock_cooldown(acquired=True)

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            await service._check_alert_threshold("tenant-1", self._item(2.0), self._quote(-2.0))

        mock_push.assert_awaited_once()

    async def test_below_threshold_no_alert_no_cooldown(self):
        from app.core.sse_router import event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        mock_get_client, client = self._mock_cooldown(acquired=True)

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            await service._check_alert_threshold("tenant-1", self._item(2.0), self._quote(1.5))

        mock_push.assert_not_awaited()
        # Cooldown marker must not be consumed by a non-breach
        client.set.assert_not_called()

    async def test_no_threshold_skipped(self):
        from app.core.sse_router import event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        mock_get_client, _ = self._mock_cooldown()

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            await service._check_alert_threshold("tenant-1", self._item(None), self._quote(9.9))

        mock_push.assert_not_awaited()

    async def test_missing_change_percent_skipped(self):
        from app.core.sse_router import event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        mock_get_client, _ = self._mock_cooldown()

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            await service._check_alert_threshold(
                "tenant-1", self._item(1.0), {"current_price": 100.0, "change_percent": None}
            )

        mock_push.assert_not_awaited()

    async def test_cooldown_suppresses_repeat_alert(self):
        from app.core.sse_router import event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        # SET NX returns None: marker already present (fired within the 1h window)
        mock_get_client, _ = self._mock_cooldown(acquired=False)

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            await service._check_alert_threshold("tenant-1", self._item(2.0), self._quote(5.0))

        mock_push.assert_not_awaited()

    async def test_redis_unavailable_skips_silently(self):
        from app.core.sse_router import event_router

        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())
        mock_get_client = AsyncMock(side_effect=ConnectionError("redis down"))

        with (
            patch("app.services.finance.get_redis_client", mock_get_client),
            patch.object(event_router, "push_event", new_callable=AsyncMock) as mock_push,
        ):
            # Must not raise
            await service._check_alert_threshold("tenant-1", self._item(2.0), self._quote(5.0))

        mock_push.assert_not_awaited()

    async def test_get_watchlist_quotes_checks_each_item(self):
        db, _ = _mock_db()
        service = FinanceService(db, _mock_redis())

        watchlist_items = [
            {"id": "wi-1", "symbol": "AAPL", "alert_threshold_percent": 2.0},
            {"id": "wi-2", "symbol": None, "alert_threshold_percent": 1.0},
        ]
        quote = {"symbol": "AAPL", "current_price": 231.5, "change_percent": 2.5}

        with (
            patch.object(FinanceService, "get_watchlist", new_callable=AsyncMock, return_value=watchlist_items),
            patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=quote),
            patch.object(FinanceService, "_check_alert_threshold", new_callable=AsyncMock) as mock_check,
        ):
            result = await service.get_watchlist_quotes("tenant-1", "user-1")

        assert len(result) == 1
        # Checked once for the resolved quote; symbol-less item never reaches the check
        mock_check.assert_awaited_once_with("tenant-1", watchlist_items[0], quote)


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

        from datetime import time as dt_time
        from unittest.mock import patch as mock_patch

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
        await service._store_quote_to_db(
            "tenant-1",
            "AAPL",
            {
                "name": "Apple",
                "current_price": 150,
                "open": 148,
                "high": 152,
                "low": 147,
                "volume": 1000000,
                "change": 2,
                "change_percent": 1.3,
                "market_cap": 3000000000,
                "pe_ratio": 25,
                "52_week_high": 180,
                "52_week_low": 120,
                "source": "yfinance",
                "type": "stock",
                "market": "US",
                "exchange": "NASDAQ",
                "currency": "USD",
            },
        )
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
        await service._store_quote_to_db("tenant-1", "AAPL", {"current_price": 150, "source": "yfinance"})
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
        # Unified spelling via the market_indices constant; indices chain is eastmoney
        # (domestic source, covers A-share indices) with yfinance as fallback
        chain = service._get_failover_chain(DATA_TYPE_MARKET_INDICES)
        assert len(chain) == 2
        assert chain[0]["name"] == "eastmoney"
        assert chain[1]["name"] == "yfinance"

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
                {
                    "symbol": "AAPL",
                    "shortname": "Apple Inc",
                    "quoteType": "EQUITY",
                    "exchange": "NASDAQ",
                    "currency": "USD",
                },
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_httpx_module = MagicMock()
        mock_httpx_module.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_module.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_yf_module = MagicMock()
        with patch.dict(
            "sys.modules", {"httpx": mock_httpx_module, "app.collectors.finance.yfinance_collector": mock_yf_module}
        ):
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
            result = await service._fetch_with_failover(
                "tenant-1", "AAPL", [{"name": "yfinance", "collector": "yfinance"}]
            )
            assert result is None

    async def test_fetch_with_failover_batch_all_fail(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        with patch.object(
            FinanceService, "_try_collector_batch", new_callable=AsyncMock, side_effect=Exception("fail")
        ):
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

        async def fake_try_batch(tenant_id, symbols, source_config):
            return [{"symbol": s, "current_price": 5000} for s in symbols]

        # Indices failover now merges by symbol; patch _try_collector_batch directly
        with patch.object(FinanceService, "_try_collector_batch", new_callable=AsyncMock, side_effect=fake_try_batch):
            result = await service._fetch_indices_with_failover("tenant-1")
            assert len(result) == len(MARKET_INDICES_CONFIG)

    async def test_fetch_indices_with_failover_merge(self):
        """eastmoney returns only A-share indices (partial symbols); yfinance fills in the rest; merged by symbol."""
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        cn_symbols = {"000001.SS", "399001.SZ", "000300.SS"}
        tried = []

        async def fake_try_batch(tenant_id, symbols, source_config):
            tried.append(source_config["name"])
            if source_config["name"] == "eastmoney":
                # eastmoney covers only A-share indices
                return [{"symbol": s, "current_price": 3500} for s in symbols if s in cn_symbols]
            return [{"symbol": s, "current_price": 5000} for s in symbols]

        with patch.object(FinanceService, "_try_collector_batch", new_callable=AsyncMock, side_effect=fake_try_batch):
            result = await service._fetch_indices_with_failover("tenant-1")

        assert tried == ["eastmoney", "yfinance"]
        assert len(result) == len(MARKET_INDICES_CONFIG)
        by_symbol = {r["symbol"]: r for r in result}
        assert by_symbol["000001.SS"]["current_price"] == 3500  # CN indices came from eastmoney
        assert by_symbol["^GSPC"]["current_price"] == 5000  # the rest filled in by yfinance

    async def test_fetch_commodities_with_failover(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        commodities = [{"symbol": "GC=F", "current_price": 2000}]
        with patch.object(
            FinanceService, "_fetch_with_failover_batch", new_callable=AsyncMock, return_value=commodities
        ):
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
        with patch.object(
            FinanceService, "_try_collector", new_callable=AsyncMock, side_effect=[Exception("fail"), quote]
        ):
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
            result = await service._try_collector("tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"})
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
            result = await service._try_collector("tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"})
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
            result = await service._try_collector("tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"})
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
            result = await service._try_collector("tenant-1", "AAPL", {"name": "yfinance", "collector": "yfinance"})
            assert result is None

    async def test_try_collector_not_registered(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = FinanceService(db, redis)

        with patch("app.collectors.COLLECTOR_REGISTRY", {}):
            result = await service._try_collector("tenant-1", "AAPL", {"name": "unknown", "collector": "unknown"})
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

        with patch.object(
            FinanceService, "_fetch_commodities_with_failover", new_callable=AsyncMock, return_value=None
        ):
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
                {
                    "symbol": "",
                    "shortname": "Empty Symbol",
                    "quoteType": "EQUITY",
                    "exchange": "NYSE",
                    "currency": "USD",
                },
                {
                    "symbol": "AAPL",
                    "shortname": "Apple Inc",
                    "quoteType": "EQUITY",
                    "exchange": "NASDAQ",
                    "currency": "USD",
                },
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_httpx_module = MagicMock()
        mock_httpx_module.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_module.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_yf_module = MagicMock()
        with patch.dict(
            "sys.modules", {"httpx": mock_httpx_module, "app.collectors.finance.yfinance_collector": mock_yf_module}
        ):
            service = FinanceService(db, redis)
            with patch.object(db, "commit", new_callable=AsyncMock):
                result = await service._search_symbols_external("test", "tenant-1")
            # Only AAPL should be returned, empty symbol skipped
            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"
