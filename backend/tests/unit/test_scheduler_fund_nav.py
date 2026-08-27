"""Unit tests for the daily official fund NAV refresh job (finance-tab.md §3.8.2):
cron registration (20:00 Asia/Shanghai), job-body result recording, exception
tolerance, and the wiring into both scheduler entry points (main.py lifespan /
worker main — asserted in test_main.py / test_scheduler.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from app.core.constants import SYSTEM_TENANT_ID
from app.scheduler.manager import (
    FUND_NAV_OFFICIAL_REFRESH_JOB_ID,
    FUND_NAV_REFRESH_HOUR,
    FUND_NAV_REFRESH_MINUTE,
    FUND_NAV_REFRESH_TIMEZONE,
    AsyncSchedulerManager,
)
from app.services.finance import FinanceService


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


class TestAddFundNavJob:
    async def test_registers_cron_20_00_asia_shanghai(self):
        mgr = _make_manager()
        await mgr.add_fund_nav_job()

        mgr.scheduler.add_job.assert_called_once()
        kwargs = mgr.scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == FUND_NAV_OFFICIAL_REFRESH_JOB_ID
        assert kwargs["replace_existing"] is True

        trigger = kwargs["trigger"]
        assert isinstance(trigger, CronTrigger)
        assert str(trigger) == "cron[hour='20', minute='0']"
        assert trigger.timezone == ZoneInfo(FUND_NAV_REFRESH_TIMEZONE)
        # Constants stay in sync with the trigger built from them.
        assert FUND_NAV_REFRESH_HOUR == 20
        assert FUND_NAV_REFRESH_MINUTE == 0
        assert FUND_NAV_REFRESH_TIMEZONE == "Asia/Shanghai"

    async def test_job_body_is_the_gated_run_body(self):
        mgr = _make_manager()
        await mgr.add_fund_nav_job()
        func = mgr.scheduler.add_job.call_args.args[0]
        assert func == mgr._run_fund_nav_official_refresh

    async def test_reregistration_replaces_existing(self):
        mgr = _make_manager()
        await mgr.add_fund_nav_job()
        await mgr.add_fund_nav_job()
        assert mgr.scheduler.add_job.call_count == 2
        assert mgr.scheduler.add_job.call_args.kwargs["replace_existing"] is True

    async def test_cron_job_not_in_interval_bookkeeping(self):
        """The adaptive/load rescheduling machinery is interval-based; the cron
        job must not be tracked in _original_intervals, otherwise multiplier
        reschedules could corrupt the trigger."""
        mgr = _make_manager()
        await mgr.add_fund_nav_job()
        assert FUND_NAV_OFFICIAL_REFRESH_JOB_ID not in mgr._original_intervals
        assert FUND_NAV_OFFICIAL_REFRESH_JOB_ID not in mgr._adaptive_multipliers
        assert FUND_NAV_OFFICIAL_REFRESH_JOB_ID not in mgr._load_multipliers

    async def test_independent_of_market_refresh_switch(self):
        """market_refresh_jobs_enabled only governs the interval market jobs;
        the daily NAV cron registers regardless."""
        mgr = _make_manager()
        mgr.market_refresh_jobs_enabled = False
        await mgr.add_fund_nav_job()
        mgr.scheduler.add_job.assert_called_once()


class TestFundNavJobBody:
    async def test_success_with_updates(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "update_official_nav", new_callable=AsyncMock, return_value=2) as mock_update,
        ):
            await mgr._run_fund_nav_official_refresh()

        # System-tenant scoped like the other display-chain refresh jobs.
        mock_update.assert_awaited_once_with(str(SYSTEM_TENANT_ID))
        result = mgr._last_run_results[FUND_NAV_OFFICIAL_REFRESH_JOB_ID]
        assert result["success"] is True
        assert result["items_count"] == 2
        assert FUND_NAV_OFFICIAL_REFRESH_JOB_ID in mgr._last_run_times

    async def test_zero_updates_is_success(self):
        """No fund symbols / collector returned nothing is a normal outcome,
        not a failure."""
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "update_official_nav", new_callable=AsyncMock, return_value=0),
        ):
            await mgr._run_fund_nav_official_refresh()

        result = mgr._last_run_results[FUND_NAV_OFFICIAL_REFRESH_JOB_ID]
        assert result["success"] is True
        assert result["items_count"] == 0

    async def test_exception_is_logged_not_raised(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(
                FinanceService,
                "update_official_nav",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db down"),
            ),
        ):
            # must not raise: a periodic job survives one bad round
            await mgr._run_fund_nav_official_refresh()

        result = mgr._last_run_results[FUND_NAV_OFFICIAL_REFRESH_JOB_ID]
        assert result["success"] is False
        assert "db down" in result["error"]
        assert result["items_count"] == 0
