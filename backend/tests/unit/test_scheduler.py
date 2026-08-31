"""Unit tests for app/scheduler package."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.manager import (
    SOURCE_TYPE_DEFAULT_INTERVALS,
    AsyncSchedulerManager,
    _source_category_cache,
    scheduler_manager,
)


def _make_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", "source-id-123")
    src.name = kwargs.get("name", "Test Source")
    src.is_active = kwargs.get("is_active", True)
    src.refresh_interval_seconds = kwargs.get("refresh_interval_seconds", 300)
    src.tenant_id = kwargs.get("tenant_id", "test-tenant")
    src.category = kwargs.get("category", MagicMock())
    if src.category:
        src.category.slug = kwargs.get("category_slug", "finance")
    return src


def _make_mock_scheduler():
    mock_sched = MagicMock()
    mock_sched.start = MagicMock()
    mock_sched.shutdown = MagicMock()

    def _get_job(job_id):
        return None

    def _get_jobs():
        return []

    mock_sched.get_job = MagicMock(side_effect=lambda jid: None)
    mock_sched.get_jobs = MagicMock(return_value=[])
    mock_sched.add_job = MagicMock()
    mock_sched.remove_job = MagicMock()
    return mock_sched


# ── scheduler_manager module-level instance ──────────────────────
class TestModuleLevel:
    def test_scheduler_manager_exists(self):
        assert isinstance(scheduler_manager, AsyncSchedulerManager)

    def test_source_type_default_intervals(self):
        assert "rss" in SOURCE_TYPE_DEFAULT_INTERVALS
        assert "api" in SOURCE_TYPE_DEFAULT_INTERVALS


# ── AsyncSchedulerManager init ───────────────────────────────────
class TestAsyncSchedulerManagerInit:
    def test_init(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_sched_cls:
            mock_sched_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            assert mgr._running is False
            assert isinstance(mgr._original_intervals, dict)


# ── start / shutdown ─────────────────────────────────────────────
class TestStartShutdown:
    async def test_start(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.start()
            assert mgr._running is True
            mgr.scheduler.start.assert_called_once()

    async def test_start_already_running(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr._running = True
            await mgr.start()
            mgr.scheduler.start.assert_not_called()

    async def test_shutdown(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr._running = True
            await mgr.shutdown(wait=True)
            assert mgr._running is False
            mgr.scheduler.shutdown.assert_called_once_with(wait=True)

    async def test_shutdown_not_running(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr._running = False
            await mgr.shutdown()
            mgr.scheduler.shutdown.assert_not_called()


# ── add_job / remove_job ────────────────────────────────────────
class TestAddRemoveJob:
    async def test_add_job_new(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mock_func = AsyncMock()
            await mgr.add_job("test_job", mock_func, 60)
            mgr.scheduler.add_job.assert_called_once()
            assert mgr._original_intervals["test_job"] == 60

    async def test_add_job_existing_reschedules(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mock_func = AsyncMock()
            await mgr.add_job("test_job", mock_func, 60)
            sched.add_job.assert_not_called()

    async def test_add_collection_job_default_interval(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.add_collection_job("src-1", 0, source_type="rss")
            mgr.scheduler.add_job.assert_called_once()

    async def test_add_collection_job_too_low_interval(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.add_collection_job("src-1", 5, source_type="rss")
            mgr.scheduler.add_job.assert_called_once()

    async def test_remove_job(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["test_job"] = 60
            mgr._adaptive_multipliers["test_job"] = 1.0
            await mgr.remove_job("test_job")
            sched.remove_job.assert_called_once_with("test_job")
            assert "test_job" not in mgr._original_intervals

    async def test_remove_job_not_found(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.remove_job("nonexistent")


# ── runtime scheduling hooks (source enable/disable events) ──────
class TestSourceJobHooks:
    async def test_add_source_job_schedules_from_payload(self):
        """add_source_job must build the job purely from the event payload (no DB read)
        and seed the category cache used by adaptive_reschedule."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()

            payload = {
                "id": "src-abc",
                "category_slug": "finance",
                "refresh_interval_seconds": 120,
                "source_type": "rss",
            }
            await mgr.add_source_job(payload)

            sched.add_job.assert_called_once()
            kwargs = sched.add_job.call_args.kwargs
            assert kwargs["id"] == "collect_src-abc"
            _source_category_cache.pop("src-abc", None)

    async def test_add_source_job_missing_id_is_noop(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            await mgr.add_source_job({"name": "no id"})
            sched.add_job.assert_not_called()

    async def test_add_source_job_falls_back_to_default_interval(self):
        """An event payload without refresh_interval_seconds must fall back to the
        per-source_type default instead of scheduling a broken trigger."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            default = SOURCE_TYPE_DEFAULT_INTERVALS["web_scrape"]
            seen = []

            async def _spy(job_id, func, interval_seconds, kwargs=None):
                seen.append((job_id, interval_seconds))

            mgr.add_job = _spy
            await mgr.add_source_job({"id": "src-x", "source_type": "web_scrape"})
            assert seen == [("collect_src-x", default)]

    async def test_remove_source_job_removes_collect_job(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            sched.get_job = MagicMock(return_value=MagicMock())
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            await mgr.remove_source_job("src-abc")
            sched.remove_job.assert_called_once_with("collect_src-abc")

    async def test_remove_source_job_empty_id_noop(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            await mgr.remove_source_job("")
            sched.remove_job.assert_not_called()


# ── pause / resume / reschedule ──────────────────────────────────
class TestPauseResume:
    async def test_pause_job(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.pause = MagicMock()
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            await mgr.pause_job("test_job")
            mock_job.pause.assert_called_once()

    async def test_pause_job_not_found(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.pause_job("nonexistent")

    async def test_resume_job(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.resume = MagicMock()
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            await mgr.resume_job("test_job")
            mock_job.resume.assert_called_once()

    async def test_resume_job_not_found(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.resume_job("nonexistent")

    async def test_reschedule_job(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            await mgr.reschedule_job("test_job", 120)
            mock_job.reschedule.assert_called_once()

    async def test_reschedule_job_not_found(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.reschedule_job("nonexistent", 120)


# ── get_jobs_status ─────────────────────────────────────────────
class TestGetJobsStatus:
    async def test_get_jobs_status_empty(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            result = await mgr.get_jobs_status()
        assert result == []

    async def test_get_jobs_status_with_jobs(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.id = "collect_src-1"
            mock_job.name = "collect_src-1"
            mock_job.next_run_time = None
            mock_job.trigger = "interval"
            mock_job.pending = False
            sched.get_jobs = MagicMock(return_value=[mock_job])
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            result = await mgr.get_jobs_status()
        assert len(result) == 1
        assert result[0]["job_id"] == "collect_src-1"
        assert result[0]["source_id"] == "src-1"


# ── schedule_all_active_sources ──────────────────────────────────
class TestScheduleAllActiveSources:
    async def test_schedule_active_sources(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            source = _make_source()
            await mgr.schedule_all_active_sources([source])
            mgr.scheduler.add_job.assert_called()

    async def test_skip_inactive_sources(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            source = _make_source(is_active=False)
            await mgr.schedule_all_active_sources([source])
            mgr.scheduler.add_job.assert_not_called()


# ── setup_default_jobs ──────────────────────────────────────────
class TestSetupDefaultJobs:
    async def test_setup_default_jobs(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.setup_default_jobs()


# ── adaptive_reschedule ─────────────────────────────────────────
class TestAdaptiveReschedule:
    async def test_no_original_interval(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            await mgr.adaptive_reschedule("src-999", "healthy")

    async def test_healthy_status(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = [MagicMock()]
                mock_event_router.get_connections_count.return_value = 5
                await mgr.adaptive_reschedule("src-1", "healthy")
            assert mgr._adaptive_multipliers["collect_src-1"] == 1.0

    async def test_degraded_status(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = [MagicMock()]
                mock_event_router.get_connections_count.return_value = 5
                await mgr.adaptive_reschedule("src-1", "degraded")
            assert mgr._adaptive_multipliers["collect_src-1"] == 2.0

    async def test_down_status(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = [MagicMock()]
                mock_event_router.get_connections_count.return_value = 5
                await mgr.adaptive_reschedule("src-1", "down")
            assert mgr._adaptive_multipliers["collect_src-1"] == 10.0

    async def test_no_connections(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.pause = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = []
                mock_event_router.get_connections_count.return_value = 0
                await mgr.adaptive_reschedule("src-1", "healthy")
            mock_job.pause.assert_called_once()

    async def test_step_down_from_high_multiplier(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 8.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = [MagicMock()]
                mock_event_router.get_connections_count.return_value = 5
                await mgr.adaptive_reschedule("src-1", "healthy")
            assert mgr._adaptive_multipliers["collect_src-1"] == 4.0

    async def test_unknown_health_status(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            mock_job.pending = False
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = [MagicMock()]
                mock_event_router.get_connections_count.return_value = 5
                await mgr.adaptive_reschedule("src-1", "unknown_status")
            assert mgr._adaptive_multipliers["collect_src-1"] == 1.0


# ── _get_category_for_source ────────────────────────────────────
class TestGetCategoryForSource:
    def test_from_cache(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            _source_category_cache["src-cached"] = "tech"
            result = mgr._get_category_for_source("src-cached")
            assert result == "tech"
            _source_category_cache.pop("src-cached", None)

    def test_loop_running_returns_default(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            result = mgr._get_category_for_source("src-unknown")
            assert result == "finance"


# ── _run_collection ─────────────────────────────────────────────
class TestRunCollection:
    async def test_run_collection_source_not_found(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch("app.db.session.async_session_factory", return_value=mock_session):
                await mgr._run_collection("nonexistent-source")
            assert mgr._last_run_results["collect_nonexistent-source"]["success"] is False
            assert "not found" in mgr._last_run_results["collect_nonexistent-source"]["error"]

    async def test_run_collection_source_inactive(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = False
            mock_source.id = "inactive-source"
            mock_source.name = "Inactive"
            mock_source.category = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch("app.db.session.async_session_factory", return_value=mock_session):
                await mgr._run_collection("inactive-source")
            assert mgr._last_run_results["collect_inactive-source"]["success"] is False

    async def test_run_collection_no_collector(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = True
            mock_source.id = "src-no-collector"
            mock_source.name = "No Collector"
            mock_source.source_type = "unknown_type"
            mock_source.config = {}
            mock_source.category = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch("app.db.session.async_session_factory", return_value=mock_session):
                await mgr._run_collection("src-no-collector")
            assert mgr._last_run_results["collect_src-no-collector"]["success"] is False

    async def test_run_collection_pipeline_error(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(side_effect=Exception("db error"))

            with patch("app.db.session.async_session_factory", return_value=mock_session):
                await mgr._run_collection("error-source")
            assert "db error" in mgr._last_run_results["collect_error-source"]["error"]


# ── Worker module ────────────────────────────────────────────────
class TestWorkerModule:
    async def test_worker_main(self):
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
                mock_mgr.add_fund_intraday_jobs = AsyncMock()
                mock_mgr.add_fund_holdings_job = AsyncMock()
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

                        # The worker owns the scheduler in prod, so it must also register
                        # the market indices/commodities refresh jobs (finance-tab.md §3.8.2).
                        mock_mgr.add_market_refresh_jobs.assert_awaited_once()
                        # ...and the daily official fund NAV refresh cron job.
                        mock_mgr.add_fund_nav_job.assert_awaited_once()
                        # ...and the fund intraday NAV loop + daily holdings cron
                        # (fund-intraday-nav.md §7).
                        mock_mgr.add_fund_intraday_jobs.assert_awaited_once()
                        mock_mgr.add_fund_holdings_job.assert_awaited_once()
                        # ...and the daily finance_quotes partition roll (database.md §3.1).
                        mock_mgr.add_quote_partition_job.assert_awaited_once()

    async def test_worker_shutdown(self):
        with patch("app.scheduler.worker.scheduler_manager") as mock_mgr:
            mock_mgr.shutdown = AsyncMock()
            with patch("app.scheduler.worker.close_redis", new_callable=AsyncMock):
                from app.scheduler.worker import shutdown

                await shutdown()

    def test_worker_main_block_import(self):
        from app.scheduler import worker as worker_mod

        assert hasattr(worker_mod, "main")
        assert hasattr(worker_mod, "shutdown")


# ── adaptive_reschedule – pending job resume ─────────────────────
class TestAdaptiveReschedulePending:
    async def test_resume_pending_job_on_healthy(self):
        """Line 223: when job.pending is True, resume_job is called."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.reschedule = MagicMock()
            mock_job.pending = True  # job is paused/pending
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = [MagicMock()]
                mock_event_router.get_connections_count.return_value = 5
                await mgr.adaptive_reschedule("src-1", "healthy")
            mock_job.resume.assert_called_once()

    async def test_no_connections_pending_job_not_paused(self):
        """When no connections and job is pending, pause is still called."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            sched = _make_mock_scheduler()
            mock_job = MagicMock()
            mock_job.pause = MagicMock()
            mock_job.pending = True
            sched.get_job = MagicMock(return_value=mock_job)
            mock_cls.return_value = sched
            mgr = AsyncSchedulerManager()
            mgr._original_intervals["collect_src-1"] = 60
            mgr._adaptive_multipliers["collect_src-1"] = 1.0
            with patch("app.scheduler.manager.event_router") as mock_event_router:
                mock_event_router.get_connections_by_category.return_value = []
                mock_event_router.get_connections_count.return_value = 0
                await mgr.adaptive_reschedule("src-1", "healthy")
            mock_job.pause.assert_not_called()  # pending → skip pause


# ── _get_category_for_source – non-cached paths ──────────────────
class TestGetCategoryForSourceExtended:
    def test_loop_running_returns_finance_default(self):
        """Lines 259-261: event loop is running → return 'finance'."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            # Clear cache to force lookup
            _source_category_cache.pop("src-new", None)
            result = mgr._get_category_for_source("src-new")
            assert result == "finance"

    def test_exception_returns_finance_default(self):
        """Lines 265-266: exception during lookup → return 'finance'."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            _source_category_cache.pop("src-nonexistent", None)
            with patch("app.scheduler.manager.asyncio.get_event_loop", side_effect=RuntimeError("no loop")):
                result = mgr._get_category_for_source("src-nonexistent")
            assert result == "finance"


# ── _run_collection – extended paths ─────────────────────────────
class TestRunCollectionExtended:
    async def test_run_collection_success_with_items(self):
        """Lines 321-417: full collection pipeline with items."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = True
            mock_source.id = "src-ok"
            mock_source.name = "OK Source"
            mock_source.source_type = "rss"
            mock_source.config = {}
            mock_source.tenant_id = "tenant-1"
            mock_source.category = MagicMock()
            mock_source.category.slug = "tech"
            mock_source.category_id = "cat-1"

            # Mock session and query
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()

            # Mock collector
            mock_collector = AsyncMock()
            mock_collection_result = MagicMock()
            mock_collection_result.success = True
            mock_collection_result.items = [
                {"title": "Item 1", "url": "http://x.com/1", "summary": "Sum", "published_at": "2024-01-01T00:00:00Z"}
            ]
            mock_collection_result.error = None
            mock_collection_result.response_time_ms = 100
            mock_collector.collect = AsyncMock(return_value=mock_collection_result)

            # Mock processor chain
            mock_chain = AsyncMock()
            mock_process_result = MagicMock()
            mock_process_result.item = {
                "title": "Item 1",
                "url": "http://x.com/1",
                "summary": "Sum",
                "image_url": "",
                "topic_tags": [],
                "extra_data": {},
                "priority": 5,
                "published_at": "2024-01-01T00:00:00Z",
            }
            mock_process_result.errors = []
            mock_chain.execute = AsyncMock(return_value=[mock_process_result])

            # Mock SSE service
            mock_sse = AsyncMock()
            mock_sse.publish_item_update = AsyncMock()
            mock_sse.publish_source_health_update = AsyncMock()

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.collectors.get_collector", return_value=lambda: mock_collector),
                patch("app.processors.create_default_processor_chain", return_value=mock_chain),
                patch("app.services.sse.SSEService", return_value=mock_sse),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._run_collection("src-ok")

            assert mgr._last_run_results["collect_src-ok"]["success"] is True
            assert mgr._last_run_results["collect_src-ok"]["items_count"] >= 1

    async def test_run_collection_yfinance_fallback(self):
        """Lines 309-311: when get_collector returns None but library=yfinance."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = True
            mock_source.id = "src-yf"
            mock_source.name = "YF Source"
            mock_source.source_type = "unknown_type"
            mock_source.config = {"library": "yfinance"}
            mock_source.tenant_id = "tenant-1"
            mock_source.category = MagicMock()
            mock_source.category.slug = "finance"
            mock_source.category_id = "cat-1"

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()

            mock_collection_result = MagicMock()
            mock_collection_result.success = True
            mock_collection_result.items = []
            mock_collection_result.error = None
            mock_collection_result.response_time_ms = 50

            mock_health_session = AsyncMock()
            mock_health_session.__aenter__ = AsyncMock(return_value=mock_health_session)
            mock_health_session.__aexit__ = AsyncMock(return_value=False)
            mock_health_result = MagicMock()
            mock_health_result.scalar_one_or_none.return_value = None
            mock_health_session.execute = AsyncMock(return_value=mock_health_result)
            mock_health_session.add = MagicMock()
            mock_health_session.commit = AsyncMock()

            call_count = 0

            def get_session():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return mock_session
                return mock_health_session

            with (
                patch("app.db.session.async_session_factory", side_effect=lambda: get_session()),
                patch("app.collectors.get_collector", return_value=None),
                patch(
                    "app.collectors.finance.yfinance_collector.YFinanceCollector.collect",
                    new_callable=AsyncMock,
                    return_value=mock_collection_result,
                ),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._run_collection("src-yf")

            assert mgr._last_run_results["collect_src-yf"]["success"] is True

    async def test_run_collection_failed_collection(self):
        """Lines 324-332: collection_result.success is False."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = True
            mock_source.id = "src-fail"
            mock_source.name = "Fail Source"
            mock_source.source_type = "rss"
            mock_source.config = {}
            mock_source.tenant_id = "tenant-1"
            mock_source.category = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)

            mock_collection_result = MagicMock()
            mock_collection_result.success = False
            mock_collection_result.error = "timeout"
            mock_collection_result.response_time_ms = 0

            mock_collector = AsyncMock()
            mock_collector.collect = AsyncMock(return_value=mock_collection_result)

            health_session = AsyncMock()
            health_session.__aenter__ = AsyncMock(return_value=health_session)
            health_session.__aexit__ = AsyncMock(return_value=False)
            health_result = MagicMock()
            health_result.scalar_one_or_none.return_value = None
            health_session.execute = AsyncMock(return_value=health_result)
            health_session.add = MagicMock()
            health_session.commit = AsyncMock()

            call_count = 0

            def get_session():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return mock_session
                return health_session

            with (
                patch("app.db.session.async_session_factory", side_effect=lambda: get_session()),
                patch("app.collectors.get_collector", return_value=lambda: mock_collector),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._run_collection("src-fail")

            assert mgr._last_run_results["collect_src-fail"]["success"] is False
            assert "timeout" in mgr._last_run_results["collect_src-fail"]["error"]

    async def test_run_collection_no_items(self):
        """Lines 334-338: collection succeeds but items is empty."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = True
            mock_source.id = "src-empty"
            mock_source.name = "Empty Source"
            mock_source.source_type = "rss"
            mock_source.config = {}
            mock_source.tenant_id = "t1"
            mock_source.category = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)

            mock_collection_result = MagicMock()
            mock_collection_result.success = True
            mock_collection_result.items = []
            mock_collection_result.response_time_ms = 50

            health_session = AsyncMock()
            health_session.__aenter__ = AsyncMock(return_value=health_session)
            health_session.__aexit__ = AsyncMock(return_value=False)
            health_result_obj = MagicMock()
            health_result_obj.scalar_one_or_none.return_value = None
            health_session.execute = AsyncMock(return_value=health_result_obj)
            health_session.add = MagicMock()
            health_session.commit = AsyncMock()

            call_count = 0

            def get_session():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return mock_session
                return health_session

            mock_collector = AsyncMock()
            mock_collector.collect = AsyncMock(return_value=mock_collection_result)

            with (
                patch("app.db.session.async_session_factory", side_effect=lambda: get_session()),
                patch("app.collectors.get_collector", return_value=lambda: mock_collector),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._run_collection("src-empty")

            assert mgr._last_run_results["collect_src-empty"]["success"] is True
            assert mgr._last_run_results["collect_src-empty"]["items_count"] == 0

    async def test_run_collection_category_cached(self):
        """Line 302: source.category.slug is cached."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = True
            mock_source.id = "src-cat"
            mock_source.name = "Cat Source"
            mock_source.source_type = "rss"
            mock_source.config = {}
            mock_source.tenant_id = "t1"
            mock_source.category = MagicMock()
            mock_source.category.slug = "tech"

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)

            mock_collection_result = MagicMock()
            mock_collection_result.success = True
            mock_collection_result.items = []
            mock_collection_result.response_time_ms = 50

            health_session = AsyncMock()
            health_session.__aenter__ = AsyncMock(return_value=health_session)
            health_session.__aexit__ = AsyncMock(return_value=False)
            health_result_obj = MagicMock()
            health_result_obj.scalar_one_or_none.return_value = None
            health_session.execute = AsyncMock(return_value=health_result_obj)
            health_session.add = MagicMock()
            health_session.commit = AsyncMock()

            call_count = 0

            def get_session():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return mock_session
                return health_session

            mock_collector = AsyncMock()
            mock_collector.collect = AsyncMock(return_value=mock_collection_result)

            with (
                patch("app.db.session.async_session_factory", side_effect=lambda: get_session()),
                patch("app.collectors.get_collector", return_value=lambda: mock_collector),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._run_collection("src-cat")

            assert _source_category_cache.get("src-cat") == "tech"


# ── _run_collection – topic_stats_update trigger (tech-tab.md §3.8) ──
class TestRunCollectionTopicStatsTrigger:
    """After storing items, a tech source triggers publish_topic_stats_update;
    non-tech sources never do (the per-tenant 900s throttle lives in SSEService)."""

    async def _run_successful_collection(self, category_slug: str) -> AsyncMock:
        mgr = None
        mock_sse = AsyncMock()
        mock_sse.publish_item_update = AsyncMock()
        mock_sse.publish_source_health_update = AsyncMock()
        mock_sse.publish_topic_stats_update = AsyncMock()

        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.is_active = True
            mock_source.id = f"src-{category_slug}"
            mock_source.name = "Trigger Source"
            mock_source.source_type = "rss"
            mock_source.config = {}
            mock_source.tenant_id = "tenant-1"
            mock_source.category = MagicMock()
            mock_source.category.slug = category_slug
            mock_source.category_id = "cat-1"

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_source
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()

            mock_collector = AsyncMock()
            mock_collection_result = MagicMock()
            mock_collection_result.success = True
            mock_collection_result.items = [
                {"title": "Item 1", "url": "http://x.com/1", "summary": "Sum", "published_at": "2024-01-01T00:00:00Z"}
            ]
            mock_collection_result.error = None
            mock_collection_result.response_time_ms = 100
            mock_collector.collect = AsyncMock(return_value=mock_collection_result)

            mock_chain = AsyncMock()
            mock_process_result = MagicMock()
            mock_process_result.item = {
                "title": "Item 1",
                "url": "http://x.com/1",
                "summary": "Sum",
                "image_url": "",
                "topic_tags": [],
                "extra_data": {},
                "priority": 5,
                "published_at": "2024-01-01T00:00:00Z",
            }
            mock_process_result.errors = []
            mock_chain.execute = AsyncMock(return_value=[mock_process_result])

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.collectors.get_collector", return_value=lambda: mock_collector),
                patch("app.processors.create_default_processor_chain", return_value=mock_chain),
                patch("app.services.sse.SSEService", return_value=mock_sse),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._run_collection(f"src-{category_slug}")

        assert mgr._last_run_results[f"collect_src-{category_slug}"]["success"] is True
        mock_sse.publish_item_update.assert_awaited_once()
        return mock_sse

    async def test_tech_source_triggers_topic_stats_push(self):
        mock_sse = await self._run_successful_collection("tech")
        mock_sse.publish_topic_stats_update.assert_awaited_once_with("tenant-1")

    async def test_finance_source_does_not_trigger_topic_stats_push(self):
        mock_sse = await self._run_successful_collection("finance")
        mock_sse.publish_topic_stats_update.assert_not_awaited()


# ── _update_health_after_collection ──────────────────────────────
class TestUpdateHealthAfterCollection:
    async def test_health_created_when_none_exists(self):
        """Lines 467-480: no existing health → create new SourceHealth."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-new-health"
            mock_source.tenant_id = "t1"

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.response_time_ms = 100
            mock_result.error = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_query_result = MagicMock()
            mock_query_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_query_result)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._update_health_after_collection(mock_source, mock_result)

            mock_session.add.assert_called_once()
            mock_session.commit.assert_called_once()

    async def test_health_updated_on_success(self):
        """Lines 437-452: existing health, success → reset failures, update avg."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-h-ok"
            mock_source.tenant_id = "t1"

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.response_time_ms = 200
            mock_result.error = None

            mock_health = MagicMock()
            mock_health.status = "degraded"
            mock_health.consecutive_failures = 2
            mock_health.total_fetches_24h = 5
            mock_health.success_count_24h = 3
            mock_health.avg_response_time_ms = 300
            mock_health.last_error_message = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_query_result = MagicMock()
            mock_query_result.scalar_one_or_none.return_value = mock_health
            mock_session.execute = AsyncMock(return_value=mock_query_result)
            mock_session.commit = AsyncMock()

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._update_health_after_collection(mock_source, mock_result)

            assert mock_health.consecutive_failures == 0
            assert mock_health.status == "healthy"  # degraded→healthy
            mock_session.commit.assert_called_once()

    async def test_health_updated_on_failure(self):
        """Lines 455-463: existing health, failure → increment failures."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-h-fail"
            mock_source.tenant_id = "t1"

            mock_result = MagicMock()
            mock_result.success = False
            mock_result.response_time_ms = 0
            mock_result.error = "connection timeout"

            mock_health = MagicMock()
            mock_health.status = "healthy"
            mock_health.consecutive_failures = 0
            mock_health.total_fetches_24h = 10
            mock_health.success_count_24h = 10
            mock_health.avg_response_time_ms = 100
            mock_health.last_error_message = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_query_result = MagicMock()
            mock_query_result.scalar_one_or_none.return_value = mock_health
            mock_session.execute = AsyncMock(return_value=mock_query_result)
            mock_session.commit = AsyncMock()

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._update_health_after_collection(mock_source, mock_result)

            assert mock_health.consecutive_failures == 1
            assert mock_health.last_error_message == "connection timeout"

    async def test_health_down_after_10_failures(self):
        """Lines 460-461: consecutive_failures >= 10 → status down."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-h-down"
            mock_source.tenant_id = "t1"

            mock_result = MagicMock()
            mock_result.success = False
            mock_result.response_time_ms = 0
            mock_result.error = "persistent error"

            mock_health = MagicMock()
            mock_health.status = "degraded"
            mock_health.consecutive_failures = 9
            mock_health.total_fetches_24h = 20
            mock_health.success_count_24h = 5
            mock_health.avg_response_time_ms = 100
            mock_health.last_error_message = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_query_result = MagicMock()
            mock_query_result.scalar_one_or_none.return_value = mock_health
            mock_session.execute = AsyncMock(return_value=mock_query_result)
            mock_session.commit = AsyncMock()

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._update_health_after_collection(mock_source, mock_result)

            assert mock_health.consecutive_failures == 10
            assert mock_health.status == "down"

    async def test_health_degraded_after_3_failures(self):
        """Lines 462-463: consecutive_failures >= 3 → status degraded."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-h-deg"
            mock_source.tenant_id = "t1"

            mock_result = MagicMock()
            mock_result.success = False
            mock_result.response_time_ms = 0
            mock_result.error = "intermittent error"

            mock_health = MagicMock()
            mock_health.status = "healthy"
            mock_health.consecutive_failures = 2
            mock_health.total_fetches_24h = 10
            mock_health.success_count_24h = 8
            mock_health.avg_response_time_ms = 100
            mock_health.last_error_message = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_query_result = MagicMock()
            mock_query_result.scalar_one_or_none.return_value = mock_health
            mock_session.execute = AsyncMock(return_value=mock_query_result)
            mock_session.commit = AsyncMock()

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._update_health_after_collection(mock_source, mock_result)

            assert mock_health.consecutive_failures == 3
            assert mock_health.status == "degraded"

    async def test_health_status_change_triggers_sse(self):
        """Lines 482-491: status change → publish SSE event with the full row payload.

        Contract: docs/dev-guide/design/data-flow.md §3.5.4. The event payload must carry
        every field the dashboard health table renders (keyed by source_id), and the
        tenant must be the source's own tenant so matching SSE sessions receive it.
        """
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-h-sse"
            mock_source.tenant_id = "t1"
            mock_source.name = "SSE Source"
            mock_source.source_type = "rss"

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.response_time_ms = 100
            mock_result.error = None

            mock_health = MagicMock()
            mock_health.status = "degraded"
            mock_health.consecutive_failures = 1
            mock_health.total_fetches_24h = 5
            mock_health.success_count_24h = 3
            mock_health.avg_response_time_ms = 200
            mock_health.last_error_message = "old error"
            mock_health.last_success_at = None
            mock_health.last_failure_at = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_query_result = MagicMock()
            mock_query_result.scalar_one_or_none.return_value = mock_health
            mock_session.execute = AsyncMock(return_value=mock_query_result)
            mock_session.commit = AsyncMock()

            # After update, status changes from degraded to healthy
            # We need status to change, so simulate it:
            def commit_side_effect():
                mock_health.status = "healthy"

            mock_session.commit.side_effect = commit_side_effect

            mock_sse = AsyncMock()

            mgr._original_intervals["collect_src-h-sse"] = 60
            mgr._adaptive_multipliers["collect_src-h-sse"] = 1.0

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.services.sse.SSEService", return_value=mock_sse),
                patch("app.scheduler.manager.event_router") as mock_er,
            ):
                mock_er.get_connections_by_category.return_value = [MagicMock()]
                mock_er.get_connections_count.return_value = 1
                await mgr._update_health_after_collection(mock_source, mock_result)

            mock_sse.publish_source_health_update.assert_called_once()
            payload = mock_sse.publish_source_health_update.call_args.args[0]
            assert payload["source_id"] == "src-h-sse"
            assert payload["name"] == "SSE Source"
            assert payload["source_type"] == "rss"
            assert payload["status"] == "healthy"
            assert payload["previous_status"] == "degraded"
            assert payload["last_success_at"] is not None
            assert payload["last_failure_at"] is None
            assert payload["consecutive_failures"] == 0
            assert payload["total_fetches_24h"] == 6
            assert payload["success_count_24h"] == 4
            # avg = (200 * 5 + 100) / 6 rounded down
            assert payload["avg_response_time_ms"] == 183
            # The source's own tenant scopes delivery to its SSE sessions
            assert mock_sse.publish_source_health_update.call_args.kwargs["tenant_id"] == "t1"

    async def test_health_update_exception(self):
        """Lines 495-496: exception during health update is caught."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-h-err"
            mock_source.tenant_id = "t1"

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.response_time_ms = 100
            mock_result.error = None

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(side_effect=Exception("db connection lost"))

            with patch("app.db.session.async_session_factory", return_value=mock_session):
                # Should not raise
                await mgr._update_health_after_collection(mock_source, mock_result)

    async def test_health_down_status_recovery(self):
        """Lines 452-453: status 'down' recovers to 'degraded' on success."""
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()

            mock_source = MagicMock()
            mock_source.id = "src-h-recover"
            mock_source.tenant_id = "t1"

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.response_time_ms = 150
            mock_result.error = None

            mock_health = MagicMock()
            mock_health.status = "down"
            mock_health.consecutive_failures = 15
            mock_health.total_fetches_24h = 20
            mock_health.success_count_24h = 5
            mock_health.avg_response_time_ms = 500
            mock_health.last_error_message = "was down"

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_query_result = MagicMock()
            mock_query_result.scalar_one_or_none.return_value = mock_health
            mock_session.execute = AsyncMock(return_value=mock_query_result)
            mock_session.commit = AsyncMock()

            with (
                patch("app.db.session.async_session_factory", return_value=mock_session),
                patch("app.scheduler.manager.event_router"),
            ):
                await mgr._update_health_after_collection(mock_source, mock_result)

            assert mock_health.consecutive_failures == 0
            assert mock_health.status == "degraded"  # down→degraded
