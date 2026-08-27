"""Regression tests for the adaptive-pause switch and immediate first collection.

Guards the worker-scheduler fix: the worker process has no SSE connections (the
connection registry lives in the api process), so it must disable adaptive
pausing; otherwise every source would be paused permanently after its first
collection round ("no SSE subscribers"). Also locks down that newly added jobs
fire immediately (next_run_time=now) instead of waiting a full interval.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.scheduler.manager import AsyncSchedulerManager


def _make_mock_scheduler():
    mock_sched = MagicMock()
    mock_sched.start = MagicMock()
    mock_sched.shutdown = MagicMock()
    mock_sched.get_job = MagicMock(side_effect=lambda jid: None)
    mock_sched.get_jobs = MagicMock(return_value=[])
    mock_sched.add_job = MagicMock()
    mock_sched.remove_job = MagicMock()
    return mock_sched


class TestAdaptivePauseSwitch:
    def test_enabled_by_default(self):
        """The api-embedded scheduler relies on adaptive pausing; default is on."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            assert mgr.adaptive_pause_enabled is True

    def test_disable_adaptive_pause_sets_flag(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr.disable_adaptive_pause()
            assert mgr.adaptive_pause_enabled is False

    async def test_no_pause_when_disabled_and_zero_connections(self):
        """The regression scenario: in the worker process the SSE connection count
        is permanently 0. With adaptive pausing disabled, adaptive_reschedule must
        NOT pause the job — it must be rescheduled with the normal multiplier."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr.disable_adaptive_pause()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0

            with patch("app.scheduler.manager.event_router") as mock_event_router:
                # Worker reality: zero global and zero per-category SSE subscribers
                mock_event_router.get_connections_by_category.return_value = []
                mock_event_router.get_connections_count.return_value = 0
                await mgr.adaptive_reschedule("src-1", "healthy")

            mock_job.pause.assert_not_called()
            mock_job.reschedule.assert_called_once()
            assert mgr._adaptive_multipliers["collect_src-1"] == 1.0

    async def test_no_pause_when_disabled_and_no_category_subscribers(self):
        """Variant: other categories have subscribers but this source's category has
        none — the pause branch must still be skipped while pausing is disabled."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr.disable_adaptive_pause()
            mgr._original_intervals["collect_src-2"] = 120
            mgr._adaptive_multipliers["collect_src-2"] = 1.0

            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = []
                mock_event_router.get_connections_count.return_value = 5
                await mgr.adaptive_reschedule("src-2", "degraded")

            mock_job.pause.assert_not_called()
            # Without pausing, the health-based multiplier is applied normally
            assert mgr._adaptive_multipliers["collect_src-2"] == 2.0
            mock_job.reschedule.assert_called_once()


class TestAddJobFirstRunImmediate:
    async def test_add_job_next_run_time_is_now_utc(self):
        """First collection must run immediately: add_job must pass a timezone-aware
        next_run_time ≈ now. Without it, IntervalTrigger waits a full interval
        before the first fire (up to 30 minutes for slow sources)."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()

            before = datetime.now(UTC)
            await mgr.add_job("collect_src-1", AsyncMock(), 60)
            after = datetime.now(UTC)

            kwargs = sched.add_job.call_args.kwargs
            next_run_time = kwargs.get("next_run_time")
            assert next_run_time is not None, "add_job must pass next_run_time so the first run is immediate"
            # Timezone-aware (asyncpg/APScheduler-friendly) and not shifted into the future
            assert next_run_time.tzinfo is not None
            assert before <= next_run_time <= after


class TestWorkerDisablesAdaptivePause:
    async def test_worker_main_disables_adaptive_pause_before_start(self):
        """The worker entrypoint must call disable_adaptive_pause() before starting
        the scheduler; otherwise the first health update pauses every job forever."""
        with (
            patch("app.scheduler.worker.create_tables", new_callable=AsyncMock),
            patch("app.scheduler.worker.get_redis_client", new_callable=AsyncMock) as mock_redis,
        ):
            mock_redis.return_value = AsyncMock()
            with patch("app.scheduler.worker.scheduler_manager") as mock_mgr:
                mock_mgr.start = AsyncMock()
                mock_mgr.schedule_all_active_sources = AsyncMock()
                mock_mgr.add_market_refresh_jobs = AsyncMock()
                mock_mgr.add_fund_nav_job = AsyncMock()
                mock_mgr.add_quote_partition_job = AsyncMock()
                mock_session = AsyncMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_result = MagicMock()
                mock_result.scalars.return_value.all.return_value = []
                mock_session.execute = AsyncMock(return_value=mock_result)
                with (
                    patch("app.scheduler.worker.async_session_factory", return_value=mock_session),
                    patch("app.scheduler.worker.asyncio.Event") as mock_event_cls,
                ):
                    mock_event = MagicMock()
                    mock_event.wait = AsyncMock()
                    mock_event_cls.return_value = mock_event
                    with patch("app.scheduler.worker.asyncio.get_running_loop") as mock_loop:
                        mock_loop.return_value = MagicMock()
                        from app.scheduler.worker import main

                        await main()

                mock_mgr.disable_adaptive_pause.assert_called_once()
                # Ordering matters: pausing must be disabled before the scheduler starts
                call_names = [c[0] for c in mock_mgr.method_calls]
                assert call_names.index("disable_adaptive_pause") < call_names.index("start")
