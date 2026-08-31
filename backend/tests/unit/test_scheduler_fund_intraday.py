"""Unit tests for the fund intraday NAV scheduling (fund-intraday-nav.md §7).

Covers registration (interval job + enable switch + cron holdings job), the
CN market gate (open / lunch break / weekend / holiday, via a frozen clock),
the Redis-down skip, exception tolerance, and the empty-union zero-request
short circuit inside the compute cycle.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from app.scheduler.manager import (
    FUND_HOLDINGS_REFRESH_JOB_ID,
    FUND_NAV_INTRADAY_JOB_ID,
    AsyncSchedulerManager,
)
from app.services.finance import FinanceService

SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        return AsyncSchedulerManager()


def _mock_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _freeze_finance_now(fixed: datetime):
    """Patch the datetime symbol seen by FinanceService._is_market_open."""
    fake = MagicMock(wraps=datetime)
    fake.now = MagicMock(return_value=fixed)
    return patch("app.services.finance.datetime", fake)


class TestRegistration:
    async def test_interval_job_registered_with_settings_interval(self):
        from app.config import settings

        mgr = _make_manager()
        await mgr.add_fund_intraday_jobs()
        mgr.scheduler.add_job.assert_called_once()
        kwargs = mgr.scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == FUND_NAV_INTRADAY_JOB_ID
        # Interval semantics: tracked in _original_intervals (adaptive/load
        # throttling applies), first run immediate via next_run_time.
        assert mgr._original_intervals[FUND_NAV_INTRADAY_JOB_ID] == settings.fund_nav_intraday_refresh_interval
        assert kwargs["trigger"].interval.total_seconds() == settings.fund_nav_intraday_refresh_interval
        assert kwargs["next_run_time"] is not None

    async def test_disabled_switch_registers_nothing(self):
        from app.config import settings

        mgr = _make_manager()
        original = settings.fund_nav_intraday_enabled
        try:
            settings.fund_nav_intraday_enabled = False
            await mgr.add_fund_intraday_jobs()
        finally:
            settings.fund_nav_intraday_enabled = original
        mgr.scheduler.add_job.assert_not_called()
        assert FUND_NAV_INTRADAY_JOB_ID not in mgr._original_intervals

    async def test_holdings_cron_registered_like_fund_nav_job(self):
        from app.config import settings

        mgr = _make_manager()
        await mgr.add_fund_holdings_job()
        mgr.scheduler.add_job.assert_called_once()
        kwargs = mgr.scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == FUND_HOLDINGS_REFRESH_JOB_ID
        assert kwargs["replace_existing"] is True
        # Cron contract: NOT tracked in _original_intervals (same convention as
        # add_fund_nav_job — interval-based throttling never touches cron jobs).
        assert FUND_HOLDINGS_REFRESH_JOB_ID not in mgr._original_intervals
        trigger = kwargs["trigger"]
        assert str(trigger) == f"cron[hour='{settings.fund_holdings_refresh_hour}', minute='0']"
        assert str(trigger.timezone) == "Asia/Shanghai"


class TestCycleGate:
    async def test_cn_closed_skips_cycle(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "_is_market_open", return_value=False) as mock_gate,
            patch("app.core.redis.get_redis_client", new_callable=AsyncMock) as mock_rc,
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_cycle",
                new_callable=AsyncMock,
            ) as mock_cycle,
        ):
            mock_rc.return_value.ping = AsyncMock(return_value=True)
            await mgr._run_fund_intraday_refresh()
        mock_gate.assert_called_once_with("CN")  # CN gate only, not the union
        mock_cycle.assert_not_awaited()
        mock_rc.assert_not_awaited()  # no Redis probe when the gate rejects
        result = mgr._last_run_results[FUND_NAV_INTRADAY_JOB_ID]
        assert result["success"] is True and result["items_count"] == 0

    async def test_cn_open_runs_cycle(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "_is_market_open", return_value=True),
            patch("app.core.redis.get_redis_client", new_callable=AsyncMock) as mock_rc,
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_cycle",
                new_callable=AsyncMock,
                return_value={"codes": 3, "results": [], "pushed_tenants": 1},
            ) as mock_cycle,
        ):
            mock_rc.return_value.ping = AsyncMock(return_value=True)
            await mgr._run_fund_intraday_refresh()
        mock_cycle.assert_awaited_once()
        assert mgr._last_run_results[FUND_NAV_INTRADAY_JOB_ID]["items_count"] == 3
        assert mgr._fund_intraday_gate_open is True

    async def test_redis_down_skips_round(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "_is_market_open", return_value=True),
            patch("app.core.redis.get_redis_client", new_callable=AsyncMock) as mock_rc,
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_cycle",
                new_callable=AsyncMock,
            ) as mock_cycle,
        ):
            mock_rc.return_value.ping = AsyncMock(side_effect=RuntimeError("redis down"))
            await mgr._run_fund_intraday_refresh()
        mock_cycle.assert_not_awaited()
        result = mgr._last_run_results[FUND_NAV_INTRADAY_JOB_ID]
        assert result["success"] is False
        assert "redis unavailable" in result["error"]

    async def test_exception_recorded_not_raised(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "_is_market_open", return_value=True),
            patch("app.core.redis.get_redis_client", new_callable=AsyncMock) as mock_rc,
            patch(
                "app.services.fund_intraday.FundIntradayService.compute_cycle",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            mock_rc.return_value.ping = AsyncMock(return_value=True)
            # must not raise: a periodic job survives one bad round (§7.2 step 12)
            await mgr._run_fund_intraday_refresh()
        result = mgr._last_run_results[FUND_NAV_INTRADAY_JOB_ID]
        assert result["success"] is False and "boom" in result["error"]

    async def test_gate_open_to_closed_edge_writes_close_snapshot(self):
        """§3.4 case 3 (M2): the open→closed edge invokes the closing snapshot
        write with the cycle's service session."""
        mgr = _make_manager()
        mgr._fund_intraday_gate_open = True  # the loop was running
        session = _mock_session()
        with (
            patch("app.db.session.async_session_factory", return_value=session),
            patch.object(FinanceService, "_is_market_open", return_value=False),
            patch(
                "app.services.fund_intraday.write_close_snapshots",
                new_callable=AsyncMock,
                return_value=2,
            ) as mock_close,
        ):
            await mgr._run_fund_intraday_refresh()
        mock_close.assert_awaited_once_with(session)
        assert mgr._fund_intraday_gate_open is False
        result = mgr._last_run_results[FUND_NAV_INTRADAY_JOB_ID]
        assert result["success"] is True and result["items_count"] == 0

    async def test_gate_closed_twice_no_duplicate_snapshot(self):
        """No prior open cycle → no snapshot write on a plain closed round."""
        mgr = _make_manager()
        assert mgr._fund_intraday_gate_open is False
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "_is_market_open", return_value=False),
            patch(
                "app.services.fund_intraday.write_close_snapshots",
                new_callable=AsyncMock,
            ) as mock_close,
        ):
            await mgr._run_fund_intraday_refresh()
        mock_close.assert_not_awaited()
        assert mgr._last_run_results[FUND_NAV_INTRADAY_JOB_ID]["success"] is True


class TestMarketCalendarGateTruth:
    """The scheduler gate consumes FinanceService._is_market_open('CN'); these
    pin the calendar truth it delegates to (lunch break, weekend, holiday)."""

    def _is_cn_open(self, fixed: datetime) -> bool:
        service = FinanceService(db=None, redis=None)
        with _freeze_finance_now(fixed):
            return service._is_market_open("CN")

    def test_monday_morning_open(self):
        assert self._is_cn_open(datetime(2026, 8, 31, 10, 0, tzinfo=SHANGHAI)) is True

    def test_monday_lunch_break_closed(self):
        assert self._is_cn_open(datetime(2026, 8, 31, 12, 0, tzinfo=SHANGHAI)) is False

    def test_monday_afternoon_open(self):
        assert self._is_cn_open(datetime(2026, 8, 31, 13, 30, tzinfo=SHANGHAI)) is True

    def test_after_close_closed(self):
        assert self._is_cn_open(datetime(2026, 8, 31, 15, 30, tzinfo=SHANGHAI)) is False

    def test_weekend_closed(self):
        assert self._is_cn_open(datetime(2026, 8, 29, 10, 0, tzinfo=SHANGHAI)) is False

    def test_holiday_closed(self):
        assert self._is_cn_open(datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI)) is False


class TestHoldingsRound:
    async def test_empty_union_is_noop(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch(
                "app.services.fund_holdings.FundHoldingsService.get_followed_codes",
                new_callable=AsyncMock,
                return_value=([], {}),
            ),
        ):
            await mgr._run_fund_holdings_refresh()
        assert mgr._last_run_results[FUND_HOLDINGS_REFRESH_JOB_ID] == {"success": True, "items_count": 0}

    async def test_ingests_every_followed_code_patiently(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch(
                "app.services.fund_holdings.FundHoldingsService.get_followed_codes",
                new_callable=AsyncMock,
                return_value=(["510300", "005827"], {"t1": {"510300", "005827"}}),
            ),
            patch(
                "app.services.fund_holdings.FundHoldingsService.ingest_fund",
                new_callable=AsyncMock,
                side_effect=[True, False],
            ) as mock_ingest,
        ):
            await mgr._run_fund_holdings_refresh()
        assert mock_ingest.await_count == 2
        assert all(call.kwargs.get("patient") is True for call in mock_ingest.await_args_list)
        assert mgr._last_run_results[FUND_HOLDINGS_REFRESH_JOB_ID]["items_count"] == 1

    async def test_single_fund_failure_does_not_kill_round(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch(
                "app.services.fund_holdings.FundHoldingsService.get_followed_codes",
                new_callable=AsyncMock,
                return_value=(["510300", "005827"], {}),
            ),
            patch(
                "app.services.fund_holdings.FundHoldingsService.ingest_fund",
                new_callable=AsyncMock,
                side_effect=[RuntimeError("upstream exploded"), True],
            ) as mock_ingest,
        ):
            await mgr._run_fund_holdings_refresh()
        assert mock_ingest.await_count == 2  # second fund still processed
        result = mgr._last_run_results[FUND_HOLDINGS_REFRESH_JOB_ID]
        assert result["success"] is True and result["items_count"] == 1


class TestCycleEmptyUnion:
    """Empty followed union → zero upstream work (§7.2 step 4)."""

    async def test_empty_union_short_circuits(self):
        from app.services.fund_intraday import FundIntradayService

        svc = FundIntradayService(db=AsyncMock(), governor=AsyncMock())
        with (
            patch.object(svc, "_load_followed", new_callable=AsyncMock, return_value=([], {})),
            patch("app.services.fund_intraday.fetch_quotes", new_callable=AsyncMock) as mock_fetch,
        ):
            summary = await svc.compute_cycle()
        assert summary == {"codes": 0, "results": [], "pushed_tenants": 0}
        mock_fetch.assert_not_awaited()  # zero quote requests on empty union
