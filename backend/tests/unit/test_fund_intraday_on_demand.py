"""Unit tests for the REST on-demand intraday compute path (fund-intraday-nav.md
§9) and the official-NAV startup catch-up (§7).

The on-demand path exists because a searched-but-not-followed fund is not in
the 3s worker cycle union: while the CN market is open the batch / single
endpoints must compute its estimate right now (rt-cache write included), and
off-market they must stay zero-upstream. The catch-up covers missed nightly
20:00 official-NAV runs (container redeployed across the cron).
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core.constants import SYSTEM_TENANT_ID
from app.services import fund_intraday
from app.services.finance import FinanceService
from app.services.fund_intraday import FundIntradayService

CN_TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _clean_cycle_state():
    fund_intraday.reset_cycle_state()
    yield
    fund_intraday.reset_cycle_state()


def _entry(symbol: str, method="holdings_weighted", change=1.25) -> dict:
    return {
        "symbol": symbol,
        "name": f"fund-{symbol}",
        "nav_official": 4.5,
        "nav_official_date": "2026-08-28",
        "nav_estimate": 4.5563,
        "estimate_change_percent": change,
        "estimate_method": method,
        "coverage_percent": 83.47,
        "holdings_report_date": "2026-06-30",
        "quote_status": "realtime",
        "delayed_markets": [],
        "holdings_stale": False,
        "estimate_timestamp": "2026-09-01T02:00:00+00:00",
    }


class TestComputeOnDemand:
    def _svc(self):
        return FundIntradayService(db=AsyncMock(), governor=AsyncMock())

    async def test_computes_and_writes_rt_cache(self):
        svc = self._svc()
        results = [_entry("017811"), _entry("510500", method="index_tracking", change=-0.4)]
        ids_per_code = {"017811": ["i1"], "510500": ["i2"]}
        with (
            patch.object(svc, "_compute_results", new_callable=AsyncMock, return_value=(results, {}, ids_per_code)),
            patch.object(svc, "_write_rt_cache", new_callable=AsyncMock) as mock_cache,
        ):
            out = await svc.compute_on_demand(["017811", "510500"])
        assert set(out) == {"017811", "510500"}
        assert out["017811"]["estimate_change_percent"] == 1.25
        mock_cache.assert_awaited_once_with(results)

    async def test_unknown_codes_excluded(self):
        """Codes absent from the symbol map get no entry — the API caller then
        degrades them to explicit error entries instead of empty estimates."""
        svc = self._svc()
        results = [_entry("017811")]
        ids_per_code = {"017811": ["id-1"]}  # "999999" unknown
        with (
            patch.object(
                svc,
                "_compute_results",
                new_callable=AsyncMock,
                return_value=(results + [_entry("999999")], {}, ids_per_code),
            ),
            patch.object(svc, "_write_rt_cache", new_callable=AsyncMock) as mock_cache,
        ):
            out = await svc.compute_on_demand(["017811", "999999"])
        assert set(out) == {"017811"}
        cached = mock_cache.await_args.args[0]
        assert [r["symbol"] for r in cached] == ["017811"]

    async def test_failure_degrades_to_empty(self):
        svc = self._svc()
        with (
            patch.object(svc, "_compute_results", new_callable=AsyncMock, side_effect=RuntimeError("db down")),
            patch.object(svc, "_write_rt_cache", new_callable=AsyncMock) as mock_cache,
        ):
            out = await svc.compute_on_demand(["017811"])
        assert out == {}
        mock_cache.assert_not_awaited()

    async def test_empty_codes_noop(self):
        svc = self._svc()
        with patch.object(svc, "_compute_results", new_callable=AsyncMock) as mock_compute:
            assert await svc.compute_on_demand([]) == {}
        mock_compute.assert_not_awaited()


class TestBatchOnDemandWiring:
    """finance.get_fund_nav_batch: rt-cache hit wins; market-open misses go
    through compute_on_demand; off-market misses stay latest_official; codes
    without a usable snapshot fire the ingest hook and carry
    holdings_ingesting (UI: 「持仓数据摄取中…」)."""

    def _svc(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        return FinanceService(db=db, redis=AsyncMock())

    async def test_rt_cache_hit_short_circuits(self):
        svc = self._svc()
        cached = _entry("017811")
        with (
            patch.object(svc, "_read_fund_nav_rt", new_callable=AsyncMock, return_value=cached),
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_on_demand", new_callable=AsyncMock
            ) as mock_od,
        ):
            entries = await svc.get_fund_nav_batch("tenant-1", ["017811"])
        assert entries[0]["estimate_change_percent"] == 1.25
        mock_od.assert_not_awaited()

    async def test_market_open_triggers_on_demand(self):
        svc = self._svc()
        computed = {"017811": _entry("017811")}
        with (
            patch.object(svc, "_read_fund_nav_rt", new_callable=AsyncMock, return_value=None),
            patch.object(svc, "_is_market_open", return_value=True),
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_on_demand",
                new_callable=AsyncMock,
                return_value=computed,
            ) as mock_od,
            patch.object(
                svc, "_fund_nav_latest_official_entries", new_callable=AsyncMock, return_value={}
            ) as mock_fallback,
            patch(
                "app.services.fund_holdings.FundHoldingsService.codes_needing_ingestion",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch("app.services.fund_holdings.spawn_holdings_ingestion") as mock_spawn,
        ):
            entries = await svc.get_fund_nav_batch("tenant-1", ["017811"])
        assert entries[0]["estimate_change_percent"] == 1.25
        mock_od.assert_awaited_once_with(["017811"])
        mock_fallback.assert_not_awaited()  # on-demand answered, no fallback needed
        mock_spawn.assert_not_called()

    async def test_market_closed_keeps_zero_upstream_fallback(self):
        svc = self._svc()
        fallback_entry = {
            **_entry("017811", method="latest_official"),
            "estimate_change_percent": None,
            "quote_status": "frozen",
        }
        with (
            patch.object(svc, "_read_fund_nav_rt", new_callable=AsyncMock, return_value=None),
            patch.object(svc, "_is_market_open", return_value=False),
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_on_demand", new_callable=AsyncMock
            ) as mock_od,
            patch.object(
                svc,
                "_fund_nav_latest_official_entries",
                new_callable=AsyncMock,
                return_value={"017811": fallback_entry},
            ),
            patch(
                "app.services.fund_holdings.FundHoldingsService.codes_needing_ingestion",
                new_callable=AsyncMock,
                return_value={"017811"},
            ),
            patch("app.services.fund_holdings.spawn_holdings_ingestion") as mock_spawn,
        ):
            entries = await svc.get_fund_nav_batch("tenant-1", ["017811"])
        mock_od.assert_not_awaited()  # off-market: the on-demand path is inert
        assert entries[0]["estimate_method"] == "latest_official"
        assert entries[0]["holdings_ingesting"] is True  # ingestion in flight → UI explains
        mock_spawn.assert_called_once_with("017811")

    async def test_holdings_ingesting_flag_on_market_open_entries(self):
        svc = self._svc()
        computed = {"017811": _entry("017811", method="latest_official", change=None)}
        with (
            patch.object(svc, "_read_fund_nav_rt", new_callable=AsyncMock, return_value=None),
            patch.object(svc, "_is_market_open", return_value=True),
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_on_demand",
                new_callable=AsyncMock,
                return_value=computed,
            ),
            patch.object(svc, "_fund_nav_latest_official_entries", new_callable=AsyncMock, return_value={}),
            patch(
                "app.services.fund_holdings.FundHoldingsService.codes_needing_ingestion",
                new_callable=AsyncMock,
                return_value={"017811"},
            ),
            patch("app.services.fund_holdings.spawn_holdings_ingestion") as mock_spawn,
        ):
            entries = await svc.get_fund_nav_batch("tenant-1", ["017811"])
        assert entries[0]["holdings_ingesting"] is True
        mock_spawn.assert_called_once_with("017811")

    async def test_unknown_code_error_entry_preserved(self):
        svc = self._svc()
        with (
            patch.object(svc, "_read_fund_nav_rt", new_callable=AsyncMock, return_value=None),
            patch.object(svc, "_is_market_open", return_value=False),
            patch.object(
                svc,
                "_fund_nav_latest_official_entries",
                new_callable=AsyncMock,
                return_value={"999999": {"symbol": "999999", "error": "fund symbol not found"}},
            ),
            patch(
                "app.services.fund_holdings.FundHoldingsService.codes_needing_ingestion",
                new_callable=AsyncMock,
                return_value=set(),
            ),
        ):
            entries = await svc.get_fund_nav_batch("tenant-1", ["999999"])
        assert entries[0]["error"] == "fund symbol not found"


class TestOnDemandSingleFundFastPath:
    """finance.get_fund_nav: rt miss while market open → on-demand payload is
    returned directly (and cached); off-market falls through to legacy path."""

    @staticmethod
    def _result(scalar=None):
        result = MagicMock()
        result.scalar_one_or_none.return_value = scalar
        return result

    def _svc_with_symbol(self):
        db = AsyncMock()
        fund = MagicMock()
        fund.id = "sym-id"
        fund.name = "东方人工智能主题混合C"
        fund.type = "fund"
        fund.symbol = "017811"
        # Call order in the legacy fall-through: symbol select, official-row
        # select, latest-row select, binding select, legacy binding select —
        # everything after the symbol resolves to "no row".
        db.execute = AsyncMock(
            side_effect=[
                self._result(fund),
                self._result(None),
                self._result(None),
                self._result(None),
                self._result(None),
            ]
        )
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        return FinanceService(db=db, redis=AsyncMock())

    async def test_market_open_rt_miss_uses_on_demand(self):
        svc = self._svc_with_symbol()
        payload = _entry("017811")
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch.object(svc, "_is_market_open", return_value=True),
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_on_demand",
                new_callable=AsyncMock,
                return_value={"017811": payload},
            ) as mock_od,
        ):
            out = await svc.get_fund_nav("tenant-1", "017811")
        assert out["estimate_change_percent"] == 1.25
        mock_od.assert_awaited_once_with(["017811"])

    async def test_on_demand_failure_falls_through(self):
        svc = self._svc_with_symbol()
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch.object(svc, "_is_market_open", return_value=True),
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_on_demand",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.core.sse_router.SSEEventRouter.push_event", new_callable=AsyncMock),
        ):
            out = await svc.get_fund_nav("tenant-1", "017811")
        # Legacy path answered (no official row → nulls, no crash).
        assert out["symbol"] == "017811"
        assert out["nav_official"] is None


class TestExpectedNavDate:
    def test_trading_day_before_fetch_expects_previous_day(self):
        # Wed 2026-09-02 10:00 CST → Tue 2026-09-01
        now = datetime(2026, 9, 2, 10, 0, tzinfo=CN_TZ)
        assert FinanceService.latest_expected_official_nav_date(now) == date(2026, 9, 1)

    def test_trading_day_after_fetch_expects_today(self):
        # Wed 2026-09-02 20:30 CST → the 20:00 job should already have run
        now = datetime(2026, 9, 2, 20, 30, tzinfo=CN_TZ)
        assert FinanceService.latest_expected_official_nav_date(now) == date(2026, 9, 2)

    def test_monday_expects_friday(self):
        now = datetime(2026, 8, 31, 10, 0, tzinfo=CN_TZ)
        assert FinanceService.latest_expected_official_nav_date(now) == date(2026, 8, 28)

    def test_weekend_expects_last_trading_day(self):
        now = datetime(2026, 9, 5, 12, 0, tzinfo=CN_TZ)  # Saturday
        assert FinanceService.latest_expected_official_nav_date(now) == date(2026, 9, 4)

    def test_holiday_stretch_skipped(self):
        # Thu 2026-10-08 10:00 after the 中秋+国庆 stretch → Wed 2026-09-30
        now = datetime(2026, 10, 8, 10, 0, tzinfo=CN_TZ)
        assert FinanceService.latest_expected_official_nav_date(now) == date(2026, 9, 30)


class TestAnchorLaggingProbe:
    async def _probe(self, fund_count: int, latest: date | None, raises: bool = False):
        db = AsyncMock()
        if raises:
            db.execute = AsyncMock(side_effect=RuntimeError("db down"))
        else:
            count_result = MagicMock()
            count_result.scalar.return_value = fund_count
            max_result = MagicMock()
            max_result.scalar.return_value = latest
            db.execute = AsyncMock(side_effect=[count_result, max_result])
        svc = FinanceService(db=db, redis=None)
        return await svc.official_nav_anchor_lagging(str(SYSTEM_TENANT_ID))

    async def test_no_funds_not_lagging(self):
        assert await self._probe(fund_count=0, latest=None) is False

    async def test_funds_without_any_nav_lagging(self):
        with patch.object(FinanceService, "latest_expected_official_nav_date", return_value=date(2026, 9, 1)):
            assert await self._probe(fund_count=3, latest=None) is True

    async def test_current_anchor_not_lagging(self):
        with patch.object(FinanceService, "latest_expected_official_nav_date", return_value=date(2026, 9, 1)):
            assert await self._probe(fund_count=3, latest=date(2026, 9, 1)) is False

    async def test_stale_anchor_lagging(self):
        with patch.object(FinanceService, "latest_expected_official_nav_date", return_value=date(2026, 9, 1)):
            assert await self._probe(fund_count=3, latest=date(2026, 8, 28)) is True

    async def test_probe_failure_degrades_to_false(self):
        """A broken probe must never trigger an upstream fetch storm on every
        container restart."""
        assert await self._probe(fund_count=1, latest=None, raises=True) is False


def _make_mock_scheduler():
    mock_sched = MagicMock()
    mock_sched.start = MagicMock()
    mock_sched.shutdown = MagicMock()
    mock_sched.get_job = MagicMock(side_effect=lambda jid: None)
    mock_sched.get_jobs = MagicMock(return_value=[])
    mock_sched.add_job = MagicMock()
    mock_sched.remove_job = MagicMock()
    return mock_sched


def _make_manager():
    with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
        mock_cls.return_value = _make_mock_scheduler()
        from app.scheduler.manager import AsyncSchedulerManager

        return AsyncSchedulerManager()


class TestSchedulerCatchUp:
    def _mock_session(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    async def test_current_anchor_skips_backfill(self):
        from app.scheduler.manager import FUND_NAV_OFFICIAL_CATCHUP_JOB_ID

        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=self._mock_session()),
            patch(
                "app.services.finance.FinanceService.official_nav_anchor_lagging",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("app.services.finance.FinanceService.update_official_nav", new_callable=AsyncMock) as mock_update,
        ):
            await mgr._run_fund_nav_official_catchup()
        mock_update.assert_not_awaited()
        result = mgr._last_run_results[FUND_NAV_OFFICIAL_CATCHUP_JOB_ID]
        assert result == {"success": True, "items_count": 0}

    async def test_lagging_anchor_backfills(self):
        from app.scheduler.manager import FUND_NAV_OFFICIAL_CATCHUP_JOB_ID

        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=self._mock_session()),
            patch(
                "app.services.finance.FinanceService.official_nav_anchor_lagging",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.finance.FinanceService.update_official_nav",
                new_callable=AsyncMock,
                return_value=3,
            ) as mock_update,
            patch.object(mgr, "_run_night_calibration", new_callable=AsyncMock) as mock_calib,
        ):
            await mgr._run_fund_nav_official_catchup()
        mock_update.assert_awaited_once_with(str(SYSTEM_TENANT_ID))
        mock_calib.assert_awaited_once()
        assert mgr._last_run_results[FUND_NAV_OFFICIAL_CATCHUP_JOB_ID]["items_count"] == 3

    async def test_failure_recorded_not_raised(self):
        from app.scheduler.manager import FUND_NAV_OFFICIAL_CATCHUP_JOB_ID

        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=self._mock_session()),
            patch(
                "app.services.finance.FinanceService.official_nav_anchor_lagging",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db down"),
            ),
        ):
            await mgr._run_fund_nav_official_catchup()  # must not raise
        result = mgr._last_run_results[FUND_NAV_OFFICIAL_CATCHUP_JOB_ID]
        assert result["success"] is False
        assert "db down" in result["error"]

    async def test_add_fund_nav_job_registers_catchup(self):
        """The cron and the one-shot catch-up are both registered together."""
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.date import DateTrigger

        from app.scheduler.manager import FUND_NAV_OFFICIAL_CATCHUP_JOB_ID, FUND_NAV_OFFICIAL_REFRESH_JOB_ID

        mgr = _make_manager()
        await mgr.add_fund_nav_job()
        calls = {c.kwargs["id"]: c for c in mgr.scheduler.add_job.call_args_list}
        assert FUND_NAV_OFFICIAL_REFRESH_JOB_ID in calls
        assert FUND_NAV_OFFICIAL_CATCHUP_JOB_ID in calls
        assert isinstance(calls[FUND_NAV_OFFICIAL_REFRESH_JOB_ID].kwargs["trigger"], CronTrigger)
        assert isinstance(calls[FUND_NAV_OFFICIAL_CATCHUP_JOB_ID].kwargs["trigger"], DateTrigger)
