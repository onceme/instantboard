"""Unit tests for load-aware collection throttling (finance-tab.md §3.8.3).

Covers:
  - evaluate_load_multiplier(): the four decision branches (SSE connection
    gauge over threshold, Redis memory over threshold, both over — capped,
    signals missing — fail-open to ×1.0) plus error tolerance;
  - AsyncSchedulerManager._apply_load_multiplier(): reschedule only when the
    evaluated multiplier changes, composing with the health multiplier;
  - adaptive_reschedule(): health multiplier × load multiplier combination;
  - AsyncSchedulerManager.resume_paused_jobs(): the first-subscriber resume
    hook target on the scheduler side.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.scheduler.manager import (
    LOAD_MULTIPLIER,
    REDIS_MEM_LOAD_THRESHOLD,
    SSE_LOAD_THRESHOLD,
    AsyncSchedulerManager,
    evaluate_load_multiplier,
)


def _make_mock_scheduler():
    mock_sched = MagicMock()
    mock_sched.get_job = MagicMock(return_value=None)
    mock_sched.get_jobs = MagicMock(return_value=[])
    mock_sched.add_job = MagicMock()
    mock_sched.remove_job = MagicMock()
    return mock_sched


def _make_manager(sched):
    with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
        mock_cls.return_value = sched
        return AsyncSchedulerManager()


def _mock_redis_client(conn_value=None, used_memory=10, maxmemory=100, conn_raises=None, info_raises=None):
    """Build a fake redis client for evaluate_load_multiplier.

    conn_value: what GET sse:active_connections returns (str/None); a callable
    exception object in conn_raises/info_raises makes that call blow up.
    """
    client = MagicMock()
    if conn_raises:
        client.get = AsyncMock(side_effect=conn_raises)
    else:
        client.get = AsyncMock(return_value=conn_value)
    if info_raises:
        client.info = AsyncMock(side_effect=info_raises)
    else:
        client.info = AsyncMock(return_value={"used_memory": used_memory, "maxmemory": maxmemory})
    return client


# ── constants ─────────────────────────────────────────────────────
class TestThresholdConstants:
    def test_thresholds(self):
        assert SSE_LOAD_THRESHOLD == 500
        assert REDIS_MEM_LOAD_THRESHOLD == 0.8
        assert LOAD_MULTIPLIER == 2.0


# ── evaluate_load_multiplier ──────────────────────────────────────
class TestEvaluateLoadMultiplier:
    async def test_connections_over_threshold(self):
        client = _mock_redis_client(conn_value=str(SSE_LOAD_THRESHOLD + 1))
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == LOAD_MULTIPLIER

    async def test_connections_at_threshold_not_throttled(self):
        # The contract is strictly "greater than".
        client = _mock_redis_client(conn_value=str(SSE_LOAD_THRESHOLD))
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0

    async def test_memory_over_threshold(self):
        client = _mock_redis_client(conn_value="10", used_memory=85, maxmemory=100)
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == LOAD_MULTIPLIER

    async def test_memory_at_threshold_not_throttled(self):
        # 0.8 exactly is not "above 80%".
        client = _mock_redis_client(conn_value="10", used_memory=80, maxmemory=100)
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0

    async def test_both_over_threshold_capped(self):
        # Two overloaded signals must NOT stack (×2, never ×4).
        client = _mock_redis_client(conn_value="999", used_memory=95, maxmemory=100)
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == LOAD_MULTIPLIER

    async def test_no_signals(self):
        client = _mock_redis_client(conn_value="10", used_memory=10, maxmemory=100)
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0

    async def test_missing_gauge_key(self):
        # api process down / gauge expired → connection signal skipped.
        client = _mock_redis_client(conn_value=None, used_memory=10, maxmemory=100)
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0

    async def test_maxmemory_zero_skips_memory_signal(self):
        # maxmemory == 0 means "no limit configured"; even huge used_memory
        # must not throttle.
        client = _mock_redis_client(conn_value="10", used_memory=10_000_000, maxmemory=0)
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0

    async def test_redis_client_unavailable(self):
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, side_effect=ConnectionError("no redis")):
            assert await evaluate_load_multiplier() == 1.0

    async def test_gauge_read_error_fails_open(self):
        client = _mock_redis_client(conn_raises=ConnectionError("gone"), used_memory=10, maxmemory=100)
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0

    async def test_gauge_value_unparseable_fails_open(self):
        client = _mock_redis_client(conn_value="not-a-number")
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0

    async def test_info_error_keeps_connection_signal(self):
        # INFO may fail independently; the connection signal still applies.
        client = _mock_redis_client(conn_value="999", info_raises=ConnectionError("gone"))
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == LOAD_MULTIPLIER

    async def test_info_error_no_connection_signal(self):
        client = _mock_redis_client(conn_value="10", info_raises=ConnectionError("gone"))
        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await evaluate_load_multiplier() == 1.0


# ── _apply_load_multiplier ────────────────────────────────────────
class TestApplyLoadMultiplier:
    async def test_change_triggers_reschedule_on_combined_interval(self):
        sched = _make_mock_scheduler()
        job = MagicMock()
        job.reschedule = MagicMock()
        sched.get_job = MagicMock(return_value=job)
        mgr = _make_manager(sched)

        mgr._original_intervals["collect_src-1"] = 60
        mgr._adaptive_multipliers["collect_src-1"] = 2.0  # source already degraded
        mgr._load_multipliers["collect_src-1"] = 1.0

        with patch(
            "app.scheduler.manager.evaluate_load_multiplier", new_callable=AsyncMock, return_value=2.0
        ) as mock_eval:
            await mgr._apply_load_multiplier("src-1")

        mock_eval.assert_awaited_once()
        # 60 (original) × 2.0 (health) × 2.0 (load) = 240
        job.reschedule.assert_called_once()
        trigger = job.reschedule.call_args.kwargs["trigger"]
        assert trigger.interval_length == 240
        assert mgr._load_multipliers["collect_src-1"] == 2.0

    async def test_no_change_does_not_reschedule(self):
        sched = _make_mock_scheduler()
        job = MagicMock()
        sched.get_job = MagicMock(return_value=job)
        mgr = _make_manager(sched)

        mgr._original_intervals["collect_src-1"] = 60
        mgr._load_multipliers["collect_src-1"] = 2.0

        with patch("app.scheduler.manager.evaluate_load_multiplier", new_callable=AsyncMock, return_value=2.0):
            await mgr._apply_load_multiplier("src-1")

        job.reschedule.assert_not_called()
        assert mgr._load_multipliers["collect_src-1"] == 2.0

    async def test_multiplier_returns_to_normal(self):
        sched = _make_mock_scheduler()
        job = MagicMock()
        job.reschedule = MagicMock()
        sched.get_job = MagicMock(return_value=job)
        mgr = _make_manager(sched)

        mgr._original_intervals["collect_src-1"] = 60
        mgr._adaptive_multipliers["collect_src-1"] = 1.0
        mgr._load_multipliers["collect_src-1"] = 2.0

        with patch("app.scheduler.manager.evaluate_load_multiplier", new_callable=AsyncMock, return_value=1.0):
            await mgr._apply_load_multiplier("src-1")

        trigger = job.reschedule.call_args.kwargs["trigger"]
        assert trigger.interval_length == 60
        assert mgr._load_multipliers["collect_src-1"] == 1.0

    async def test_unknown_job_is_noop_without_signal_read(self):
        mgr = _make_manager(_make_mock_scheduler())
        with patch(
            "app.scheduler.manager.evaluate_load_multiplier", new_callable=AsyncMock, return_value=2.0
        ) as mock_eval:
            await mgr._apply_load_multiplier("ghost")
        # No job registered → do not even hit Redis for the signal.
        mock_eval.assert_not_awaited()

    async def test_missing_scheduler_job_only_records_state(self):
        # Interval bookkeeping must survive a job that vanished from APScheduler
        # (e.g. mid-removal), so later paths compose with the right multiplier.
        sched = _make_mock_scheduler()
        sched.get_job = MagicMock(return_value=None)
        mgr = _make_manager(sched)
        mgr._original_intervals["collect_src-1"] = 60

        with patch("app.scheduler.manager.evaluate_load_multiplier", new_callable=AsyncMock, return_value=2.0):
            await mgr._apply_load_multiplier("src-1")

        assert mgr._load_multipliers["collect_src-1"] == 2.0


# ── adaptive_reschedule × load multiplier combination ─────────────
class TestAdaptiveRescheduleLoadCombination:
    async def test_health_and_load_multipliers_multiply(self):
        sched = _make_mock_scheduler()
        job = MagicMock()
        job.reschedule = MagicMock()
        job.pending = False
        sched.get_job = MagicMock(return_value=job)
        mgr = _make_manager(sched)

        mgr._original_intervals["collect_src-1"] = 60
        mgr._adaptive_multipliers["collect_src-1"] = 1.0
        mgr._load_multipliers["collect_src-1"] = 2.0  # load throttle already active

        with patch("app.scheduler.manager.event_router") as mock_event_router:
            mock_event_router.get_connections_by_category.return_value = [MagicMock()]
            mock_event_router.get_connections_count.return_value = 5
            await mgr.adaptive_reschedule("src-1", "degraded")

        # Health ×2.0 applied on top of the existing load ×2.0 → 240, not 120.
        assert mgr._adaptive_multipliers["collect_src-1"] == 2.0
        assert mgr._load_multipliers["collect_src-1"] == 2.0
        trigger = job.reschedule.call_args.kwargs["trigger"]
        assert trigger.interval_length == 240

    async def test_default_load_multiplier_keeps_health_behavior(self):
        sched = _make_mock_scheduler()
        job = MagicMock()
        job.reschedule = MagicMock()
        job.pending = False
        sched.get_job = MagicMock(return_value=job)
        mgr = _make_manager(sched)

        mgr._original_intervals["collect_src-1"] = 60
        mgr._adaptive_multipliers["collect_src-1"] = 1.0

        with patch("app.scheduler.manager.event_router") as mock_event_router:
            mock_event_router.get_connections_by_category.return_value = [MagicMock()]
            mock_event_router.get_connections_count.return_value = 5
            await mgr.adaptive_reschedule("src-1", "degraded")

        trigger = job.reschedule.call_args.kwargs["trigger"]
        assert trigger.interval_length == 120


# ── resume_paused_jobs (first-subscriber hook target) ─────────────
class TestResumePausedJobs:
    def _make_job(self, job_id, pending):
        job = MagicMock()
        job.id = job_id
        job.pending = pending
        job.resume = MagicMock()
        job.reschedule = MagicMock()
        return job

    async def test_resumes_paused_jobs_on_combined_interval(self):
        sched = _make_mock_scheduler()
        paused = self._make_job("collect_src-a", pending=True)
        running = self._make_job("collect_src-b", pending=False)
        jobs_by_id = {j.id: j for j in (paused, running)}
        sched.get_jobs = MagicMock(return_value=[paused, running])
        sched.get_job = MagicMock(side_effect=lambda jid: jobs_by_id.get(jid))
        mgr = _make_manager(sched)

        mgr._original_intervals["collect_src-a"] = 60
        mgr._adaptive_multipliers["collect_src-a"] = 2.0
        mgr._load_multipliers["collect_src-a"] = 1.0
        mgr._original_intervals["collect_src-b"] = 30

        resumed = await mgr.resume_paused_jobs()

        assert resumed == 1
        paused.resume.assert_called_once()
        # Resumed at the effective interval: 60 × 2.0 (health) × 1.0 (load).
        trigger = paused.reschedule.call_args.kwargs["trigger"]
        assert trigger.interval_length == 120
        running.resume.assert_not_called()

    async def test_no_paused_jobs_is_noop(self):
        sched = _make_mock_scheduler()
        running = self._make_job("collect_src-a", pending=False)
        sched.get_jobs = MagicMock(return_value=[running])
        mgr = _make_manager(sched)
        mgr._original_intervals["collect_src-a"] = 60

        assert await mgr.resume_paused_jobs() == 0
        running.resume.assert_not_called()
        running.reschedule.assert_not_called()

    async def test_empty_scheduler_is_noop(self):
        mgr = _make_manager(_make_mock_scheduler())
        assert await mgr.resume_paused_jobs() == 0

    async def test_untracked_pending_job_not_resumed(self):
        # A pending job this manager did not schedule (no original interval)
        # must be left alone.
        sched = _make_mock_scheduler()
        foreign = self._make_job("housekeeping", pending=True)
        sched.get_jobs = MagicMock(return_value=[foreign])
        mgr = _make_manager(sched)

        assert await mgr.resume_paused_jobs() == 0
        foreign.resume.assert_not_called()

    async def test_multiple_paused_jobs_all_resumed(self):
        sched = _make_mock_scheduler()
        jobs = [self._make_job(f"collect_src-{i}", pending=True) for i in range(3)]
        jobs_by_id = {j.id: j for j in jobs}
        sched.get_jobs = MagicMock(return_value=jobs)
        sched.get_job = MagicMock(side_effect=lambda jid: jobs_by_id.get(jid))
        mgr = _make_manager(sched)
        for i in range(3):
            mgr._original_intervals[f"collect_src-{i}"] = 30

        assert await mgr.resume_paused_jobs() == 3
        for job in jobs:
            job.resume.assert_called_once()
            trigger = job.reschedule.call_args.kwargs["trigger"]
            assert trigger.interval_length == 30


# ── _run_collection evaluates the load multiplier each round ──────
class TestRunCollectionLoadHook:
    async def test_run_collection_applies_load_multiplier_first(self):
        sched = _make_mock_scheduler()
        mgr = _make_manager(sched)

        applied: list[str] = []

        async def _spy(source_id):
            applied.append(source_id)

        mgr._apply_load_multiplier = _spy

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # source missing → early exit
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("app.db.session.async_session_factory", return_value=mock_session):
            await mgr._run_collection("src-x")

        # The load evaluation ran exactly once for this source, even though the
        # round aborted early.
        assert applied == ["src-x"]
