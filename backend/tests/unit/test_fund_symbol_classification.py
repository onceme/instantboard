"""Unit tests for fund-symbol usability (fund-intraday-nav.md, M2 phase A).

Covers the CN fund-code heuristic and type classification override (external
indexes report CN listed funds as EQUITY), the OTC fund auto-registration on
search, the bare-code/suffixed-spelling fallbacks in add_to_watchlist and
get_fund_nav, and the seed↔binding consistency guard.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.services.finance import FinanceService
from app.services.fund_holdings import _normalize_fund_code_shared


def _svc(db=None):
    return FinanceService(db=db or AsyncMock(), redis=None)


class TestIsCnFundCode:
    def test_listed_etf_prefixes(self):
        # SSE listed-fund family (leading 5) and SZSE family (15/16/18)
        for code in ("510300", "512880", "588000", "159915", "160119", "184001"):
            assert FinanceService._is_cn_fund_code(code) is True, code

    def test_otc_mainstream_ranges(self):
        for code in ("005827", "006000", "007000", "008001", "009999"):
            assert FinanceService._is_cn_fund_code(code) is True, code

    def test_equity_families_never_classify_as_fund(self):
        for code in ("600519", "601318", "688001", "000001", "000858", "300750", "830799", "900901"):
            assert FinanceService._is_cn_fund_code(code) is False, code

    def test_otc_codes_colliding_with_equity_ranges_excluded(self):
        # 000-004 OTC fund ranges collide with SZSE main-board equities and
        # are deliberately excluded (they reach the pipeline via seed data).
        for code in ("000961", "001548", "003765", "110011"):
            assert FinanceService._is_cn_fund_code(code) is False, code

    def test_invalid_shapes(self):
        for bad in (None, "", "51030", "5103001", "51A300", "AAPL"):
            assert FinanceService._is_cn_fund_code(bad) is False, repr(bad)


class TestClassifySymbolType:
    def test_cn_listed_fund_overrides_equity(self):
        assert FinanceService._classify_symbol_type("510300.SS", "EQUITY") == "fund"
        assert FinanceService._classify_symbol_type("159915.SZ", "EQUITY") == "fund"

    def test_cn_otc_code_overrides_equity(self):
        assert FinanceService._classify_symbol_type("005827", "EQUITY") == "fund"

    def test_yahoo_etf_type_stays_fund(self):
        assert FinanceService._classify_symbol_type("510300.SS", "ETF") == "fund"

    def test_plain_equities_unaffected(self):
        assert FinanceService._classify_symbol_type("600519", "EQUITY") == "stock"
        assert FinanceService._classify_symbol_type("AAPL", "EQUITY") == "stock"

    def test_non_equity_types_pass_through(self):
        assert FinanceService._classify_symbol_type("^GSPC", "INDEX") == "index"
        assert FinanceService._classify_symbol_type("GC=F", "FUTURE") == "futures"


class TestMaybeAutoregisterFundCode:
    async def test_non_code_query_returns_none(self):
        svc = _svc()
        assert await svc._maybe_autoregister_fund_code("t1", "kweichow") is None
        svc.db.execute.assert_not_awaited()

    async def test_stock_code_query_returns_none(self):
        svc = _svc()
        assert await svc._maybe_autoregister_fund_code("t1", "600519") is None
        svc.db.execute.assert_not_awaited()

    async def test_excluded_otc_range_returns_none(self):
        svc = _svc()
        assert await svc._maybe_autoregister_fund_code("t1", "000961") is None

    async def test_existing_variant_reused(self):
        db = AsyncMock()
        existing = MagicMock()
        existing.symbol = "510300.SS"
        existing.name = "华泰柏瑞沪深300ETF"
        existing.type = "fund"
        existing.market = "CN"
        existing.exchange = ""
        existing.currency = "CNY"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=result_mock)
        svc = _svc(db)

        out = await svc._maybe_autoregister_fund_code("t1", "510300")
        assert out is not None
        assert out["symbol"] == "510300.SS"
        assert out["type"] == "fund"
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_new_otc_code_registered_as_fund(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()
        svc = _svc(db)

        out = await svc._maybe_autoregister_fund_code("t1", "005827")
        assert out is not None
        assert out["symbol"] == "005827"
        assert out["type"] == "fund"
        db.add.assert_called_once()
        added = db.add.call_args.args[0]
        assert added.type == "fund"
        assert added.market == "CN"
        assert added.tenant_id == "t1"
        db.commit.assert_awaited_once()

    async def test_commit_failure_is_swallowed(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock(side_effect=RuntimeError("unique violation"))
        db.rollback = AsyncMock()
        svc = _svc(db)

        out = await svc._maybe_autoregister_fund_code("t1", "005827")
        # request still gets the entry (the winner row surfaces next search)
        assert out is not None and out["symbol"] == "005827"
        db.rollback.assert_awaited_once()


class TestSearchFallback:
    async def test_empty_search_autoregisters_fund_code(self):
        svc = _svc()
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        empty_result.scalar_one_or_none.return_value = None
        svc.db.execute = AsyncMock(return_value=empty_result)
        svc.db.add = MagicMock()
        svc.db.commit = AsyncMock()

        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch.object(svc, "_search_symbols_external", new_callable=AsyncMock, return_value=[]),
        ):
            page = await svc.search_symbols("t1", "005827", type="fund")

        assert page["meta"]["total"] == 1
        assert page["data"][0]["symbol"] == "005827"
        assert page["data"][0]["type"] == "fund"
        svc.db.add.assert_called_once()

    async def test_empty_search_for_stock_code_registers_nothing(self):
        svc = _svc()
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        empty_result.scalar_one_or_none.return_value = None
        svc.db.execute = AsyncMock(return_value=empty_result)
        svc.db.add = MagicMock()

        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch.object(svc, "_search_symbols_external", new_callable=AsyncMock, return_value=[]),
        ):
            page = await svc.search_symbols("t1", "600519", type="all")

        assert page["meta"]["total"] == 0
        svc.db.add.assert_not_called()


class TestWatchlistBareCodeFallback:
    async def test_bare_fund_code_resolves_to_suffixed_symbol(self):
        db = AsyncMock()
        fin_symbol = MagicMock()
        fin_symbol.id = "sym-id"
        fin_symbol.symbol = "510300.SS"
        fin_symbol.type = "fund"
        fin_symbol.name = "华泰柏瑞沪深300ETF"
        fin_symbol.alert_threshold_percent = None
        fin_symbol.notes = None

        # execute sequence: exact lookup(None) → variant lookup(symbol) →
        # count(0) → duplicate check(None) → max order(-1)
        exact_result = MagicMock()
        exact_result.scalar_one_or_none.return_value = None
        variant_result = MagicMock()
        variant_result.scalar_one_or_none.return_value = fin_symbol
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = None
        order_result = MagicMock()
        order_result.scalar.return_value = -1
        symbol_result = MagicMock()
        symbol_result.scalar_one_or_none.return_value = fin_symbol
        db.execute = AsyncMock(
            side_effect=[exact_result, variant_result, count_result, dup_result, order_result, symbol_result]
        )
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        svc = _svc(db)

        async def _refresh(item):
            item.__dict__.update(
                id="item-id",
                symbol_id="sym-id",
                display_order=0,
                notes=None,
                alert_threshold_percent=None,
            )

        db.refresh.side_effect = _refresh

        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_delete", new_callable=AsyncMock),
            patch("app.services.finance.FinanceService._get_cached_quote", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.FinanceService._normalize_fund_code", return_value="510300"),
            patch("app.services.fund_holdings.invalidate_followed_codes_cache", new_callable=AsyncMock),
            patch("app.services.fund_holdings.spawn_holdings_ingestion") as mock_spawn,
        ):
            result = await svc.add_to_watchlist("t1", "u1", {"symbol": "510300"})

        assert result["symbol"] == "510300.SS"
        # holdings ingest hook fired with the normalized code (§5.1)
        mock_spawn.assert_called_once_with("510300")

    async def test_unknown_symbol_still_raises(self):
        import pytest

        from app.core.exceptions import SymbolNotFound

        db = AsyncMock()
        none_result = MagicMock()
        none_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=none_result)
        svc = _svc(db)

        with pytest.raises(SymbolNotFound):
            await svc.add_to_watchlist("t1", "u1", {"symbol": "NOSUCH"})


class TestGetFundNavBareCodeFallback:
    async def test_bare_code_resolves_suffixed_symbol(self):
        db = AsyncMock()
        fund = MagicMock()
        fund.id = "sym-id"
        fund.symbol = "510300.SS"
        fund.type = "fund"
        fund.name = "华泰柏瑞沪深300ETF"

        exact_result = MagicMock()
        exact_result.scalar_one_or_none.return_value = None
        variant_result = MagicMock()
        variant_result.scalar_one_or_none.return_value = fund
        none_result = MagicMock()
        none_result.scalar_one_or_none.return_value = None
        # execute sequence: exact(None) → variant(fund) → official anchor(None)
        # → latest row(None) → binding(None) → legacy binding(None)
        db.execute = AsyncMock(
            side_effect=[exact_result, variant_result, none_result, none_result, none_result, none_result]
        )
        svc = _svc(db)

        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.finance.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            result = await svc.get_fund_nav("t1", "510300", estimate_type="latest_official")

        assert result["symbol"] == "510300"
        assert result["name"] == "华泰柏瑞沪深300ETF"
        assert result["estimate_method"] is None  # no anchor, no binding


class TestSeedBindingConsistency:
    def test_every_binding_has_a_seeded_symbol(self):
        from app.db.init_db import FUND_INDEX_BINDINGS, FUND_SYMBOL_SEEDS

        seeded = {_normalize_fund_code_shared(symbol) for symbol, _ in FUND_SYMBOL_SEEDS}
        for code, _index in FUND_INDEX_BINDINGS:
            assert code in seeded, f"binding {code} has no seeded fund symbol"

    def test_seed_size_meets_design(self):
        from app.db.init_db import FUND_SYMBOL_SEEDS

        assert 20 <= len(FUND_SYMBOL_SEEDS) <= 30
        symbols = [symbol for symbol, _ in FUND_SYMBOL_SEEDS]
        assert len(symbols) == len(set(symbols))  # no duplicates
