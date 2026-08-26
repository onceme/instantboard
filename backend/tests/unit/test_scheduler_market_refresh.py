"""Unit tests for the periodic market indices / commodities refresh jobs
(finance-tab.md §3.8.2): registration (ids, intervals, enable switch), the
market-hours gate in the job bodies, and exception tolerance.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.core.constants import SYSTEM_TENANT_ID
from app.scheduler.manager import (
    COMMODITIES_REFRESH_JOB_ID,
    MARKET_INDICES_REFRESH_JOB_ID,
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


class TestAddMarketRefreshJobs:
    async def test_registers_both_jobs_with_default_intervals(self):
        mgr = _make_manager()
        seen = []

        async def _spy(job_id, func, interval_seconds, kwargs=None):
            seen.append((job_id, interval_seconds))

        mgr.add_job = _spy
        await mgr.add_market_refresh_jobs()

        assert seen == [
            (MARKET_INDICES_REFRESH_JOB_ID, 30),
            (COMMODITIES_REFRESH_JOB_ID, 60),
        ]

    async def test_intervals_follow_settings(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "market_indices_refresh_interval", 45)
        monkeypatch.setattr(settings, "commodities_refresh_interval", 90)

        mgr = _make_manager()
        seen = []

        async def _spy(job_id, func, interval_seconds, kwargs=None):
            seen.append((job_id, interval_seconds))

        mgr.add_job = _spy
        await mgr.add_market_refresh_jobs()

        assert seen == [
            (MARKET_INDICES_REFRESH_JOB_ID, 45),
            (COMMODITIES_REFRESH_JOB_ID, 90),
        ]

    async def test_disabled_switch_registers_nothing(self):
        mgr = _make_manager()
        mgr.market_refresh_jobs_enabled = False
        await mgr.add_market_refresh_jobs()
        mgr.scheduler.add_job.assert_not_called()
        assert MARKET_INDICES_REFRESH_JOB_ID not in mgr._original_intervals
        assert COMMODITIES_REFRESH_JOB_ID not in mgr._original_intervals

    async def test_enabled_by_default(self):
        mgr = _make_manager()
        assert mgr.market_refresh_jobs_enabled is True

    async def test_registered_jobs_use_shared_run_bodies(self):
        """The job funcs must be the gated _run_market_* bodies, so both jobs get
        the market-hours gate and exception tolerance."""
        mgr = _make_manager()
        captured = {}

        async def _spy(job_id, func, interval_seconds, kwargs=None):
            captured[job_id] = func

        mgr.add_job = _spy
        await mgr.add_market_refresh_jobs()
        assert captured[MARKET_INDICES_REFRESH_JOB_ID] == mgr._run_market_indices_refresh
        assert captured[COMMODITIES_REFRESH_JOB_ID] == mgr._run_commodities_refresh


class TestMarketRefreshJobBodyGate:
    async def test_skipped_when_all_markets_closed(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "is_any_market_open", return_value=False),
            patch.object(FinanceService, "refresh_market_indices", new_callable=AsyncMock) as mock_refresh,
        ):
            await mgr._run_market_indices_refresh()

        mock_refresh.assert_not_awaited()
        assert mgr._last_run_results[MARKET_INDICES_REFRESH_JOB_ID]["success"] is True

    async def test_runs_when_any_market_open(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "is_any_market_open", return_value=True),
            patch.object(
                FinanceService, "refresh_market_indices", new_callable=AsyncMock, return_value=True
            ) as mock_refresh,
        ):
            await mgr._run_market_indices_refresh()

        mock_refresh.assert_awaited_once_with(str(SYSTEM_TENANT_ID))
        assert mgr._last_run_results[MARKET_INDICES_REFRESH_JOB_ID]["success"] is True

    async def test_refresh_failure_recorded_without_raising(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "is_any_market_open", return_value=True),
            patch.object(FinanceService, "refresh_market_indices", new_callable=AsyncMock, return_value=False),
        ):
            await mgr._run_market_indices_refresh()

        result = mgr._last_run_results[MARKET_INDICES_REFRESH_JOB_ID]
        assert result["success"] is False
        assert "failover" in result["error"]

    async def test_exception_is_logged_not_raised(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "is_any_market_open", return_value=True),
            patch.object(
                FinanceService,
                "refresh_market_indices",
                new_callable=AsyncMock,
                side_effect=RuntimeError("redis down"),
            ),
        ):
            # must not raise: a periodic job survives one bad round
            await mgr._run_market_indices_refresh()

        result = mgr._last_run_results[MARKET_INDICES_REFRESH_JOB_ID]
        assert result["success"] is False
        assert "redis down" in result["error"]

    async def test_commodities_job_runs_when_market_open(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "is_any_market_open", return_value=True),
            patch.object(
                FinanceService, "refresh_commodities", new_callable=AsyncMock, return_value=True
            ) as mock_refresh,
        ):
            await mgr._run_commodities_refresh()

        mock_refresh.assert_awaited_once_with(str(SYSTEM_TENANT_ID))
        assert mgr._last_run_results[COMMODITIES_REFRESH_JOB_ID]["success"] is True

    async def test_commodities_job_skipped_when_all_markets_closed(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "is_any_market_open", return_value=False),
            patch.object(FinanceService, "refresh_commodities", new_callable=AsyncMock) as mock_refresh,
        ):
            await mgr._run_commodities_refresh()

        mock_refresh.assert_not_awaited()
        assert mgr._last_run_results[COMMODITIES_REFRESH_JOB_ID]["success"] is True

    async def test_commodities_exception_is_tolerated(self):
        mgr = _make_manager()
        with (
            patch("app.db.session.async_session_factory", return_value=_mock_session()),
            patch.object(FinanceService, "is_any_market_open", return_value=True),
            patch.object(
                FinanceService,
                "refresh_commodities",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            await mgr._run_commodities_refresh()

        assert mgr._last_run_results[COMMODITIES_REFRESH_JOB_ID]["success"] is False
