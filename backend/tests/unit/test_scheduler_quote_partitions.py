"""Unit tests for the daily finance_quotes partition roll (database.md §3.1):
cron registration (00:30 UTC), job-body result recording, exception tolerance,
and the wiring into both scheduler entry points (main.py lifespan / worker
main — asserted in test_main.py / test_scheduler.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.scheduler.manager import (
    QUOTE_PARTITION_ROLL_HOUR,
    QUOTE_PARTITION_ROLL_JOB_ID,
    QUOTE_PARTITION_ROLL_MINUTE,
    QUOTE_PARTITION_ROLL_TIMEZONE,
    AsyncSchedulerManager,
)


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


class TestAddQuotePartitionJob:
    async def test_registers_cron_00_30_utc(self):
        mgr = _make_manager()
        await mgr.add_quote_partition_job()

        mgr.scheduler.add_job.assert_called_once()
        kwargs = mgr.scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == QUOTE_PARTITION_ROLL_JOB_ID
        assert kwargs["replace_existing"] is True

        trigger = kwargs["trigger"]
        assert isinstance(trigger, CronTrigger)
        assert str(trigger) == "cron[hour='0', minute='30']"
        assert trigger.timezone == ZoneInfo("UTC")
        # Constants stay in sync with the trigger built from them.
        assert QUOTE_PARTITION_ROLL_HOUR == 0
        assert QUOTE_PARTITION_ROLL_MINUTE == 30
        assert QUOTE_PARTITION_ROLL_TIMEZONE == "UTC"

    async def test_job_body_is_the_partition_roll_body(self):
        mgr = _make_manager()
        await mgr.add_quote_partition_job()
        func = mgr.scheduler.add_job.call_args.args[0]
        assert func == mgr._run_quote_partition_roll

    async def test_reregistration_replaces_existing(self):
        mgr = _make_manager()
        await mgr.add_quote_partition_job()
        await mgr.add_quote_partition_job()
        assert mgr.scheduler.add_job.call_count == 2
        assert mgr.scheduler.add_job.call_args.kwargs["replace_existing"] is True

    async def test_cron_job_not_in_interval_bookkeeping(self):
        """The adaptive/load rescheduling machinery is interval-based; the cron
        job must not be tracked in _original_intervals, otherwise multiplier
        reschedules could corrupt the trigger."""
        mgr = _make_manager()
        await mgr.add_quote_partition_job()
        assert QUOTE_PARTITION_ROLL_JOB_ID not in mgr._original_intervals
        assert QUOTE_PARTITION_ROLL_JOB_ID not in mgr._adaptive_multipliers
        assert QUOTE_PARTITION_ROLL_JOB_ID not in mgr._load_multipliers

    async def test_independent_of_market_refresh_switch(self):
        """market_refresh_jobs_enabled only governs the interval market jobs;
        the partition roll registers regardless."""
        mgr = _make_manager()
        mgr.market_refresh_jobs_enabled = False
        await mgr.add_quote_partition_job()
        mgr.scheduler.add_job.assert_called_once()


class TestQuotePartitionRollBody:
    async def test_success_with_created_partitions(self):
        mgr = _make_manager()
        with patch("app.db.partitions.ensure_quote_partitions", AsyncMock(return_value=["finance_quotes_y2026m09"])):
            await mgr._run_quote_partition_roll()

        result = mgr._last_run_results[QUOTE_PARTITION_ROLL_JOB_ID]
        assert result["success"] is True
        assert result["items_count"] == 1
        assert QUOTE_PARTITION_ROLL_JOB_ID in mgr._last_run_times

    async def test_nothing_to_create_is_success(self):
        """The normal outcome 27/30 days a month (and always on SQLite): the
        partitions already exist, nothing is created, still a success."""
        mgr = _make_manager()
        with patch("app.db.partitions.ensure_quote_partitions", AsyncMock(return_value=[])):
            await mgr._run_quote_partition_roll()

        result = mgr._last_run_results[QUOTE_PARTITION_ROLL_JOB_ID]
        assert result["success"] is True
        assert result["items_count"] == 0

    async def test_exception_is_logged_not_raised(self):
        mgr = _make_manager()
        with patch(
            "app.db.partitions.ensure_quote_partitions",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            # must not raise: a periodic job survives one bad round
            await mgr._run_quote_partition_roll()

        result = mgr._last_run_results[QUOTE_PARTITION_ROLL_JOB_ID]
        assert result["success"] is False
        assert "db down" in result["error"]
        assert result["items_count"] == 0
