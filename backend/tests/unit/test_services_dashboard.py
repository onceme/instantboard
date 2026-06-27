import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.dashboard import DashboardService, stop_metrics_collection, start_metrics_collection


def _mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.commit = AsyncMock()
    db.add = MagicMock()
    # get_bind() is sync in SQLAlchemy AsyncSession, use regular MagicMock
    db.get_bind = MagicMock()
    db.get_bind.return_value = MagicMock()
    db.get_bind.return_value.pool.status.return_value = MagicMock()
    return db, mock_result


def _mock_redis():
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.info = AsyncMock(return_value={"used_memory": 1024 * 1024, "connected_clients": 5, "uptime_in_seconds": 1000})
    return redis


class TestGetSystemInfo:
    @patch("app.services.dashboard.psutil")
    async def test_get_system_info_success(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 25.0
        mock_psutil.cpu_count.return_value = 4
        mock_psutil.virtual_memory.return_value = MagicMock(total=8 * 1024**3, used=4 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=500 * 1024**3, used=200 * 1024**3)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        db, mock_result = _mock_db()
        mock_result.scalar.return_value = 5

        pool_mock = MagicMock()
        pool_mock.status.return_value = "5 connections"
        db.get_bind.return_value.pool = pool_mock

        redis = _mock_redis()

        start_time = datetime.now(UTC)
        service = DashboardService(db, redis)
        result = await service.get_system_info(start_time)

        assert result["version"] == "1.0.0"
        assert result["cpu_count"] == 4
        assert result["cpu_usage_percent"] == 25.0
        assert result["environment"] == "development"
        assert "uptime_seconds" in result

    @patch("app.services.dashboard.psutil")
    async def test_get_system_info_no_redis(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.cpu_count.return_value = 2
        mock_psutil.virtual_memory.return_value = MagicMock(total=4 * 1024**3, used=2 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=100 * 1024**3, used=50 * 1024**3)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=500, bytes_recv=500)

        db, mock_result = _mock_db()
        mock_result.scalar.return_value = 0

        start_time = datetime.now(UTC)
        service = DashboardService(db, None)
        result = await service.get_system_info(start_time)

        assert result["database"]["redis_connected"] is False

    @patch("app.services.dashboard.psutil")
    async def test_get_system_info_db_error(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.cpu_count.return_value = 2
        mock_psutil.virtual_memory.return_value = MagicMock(total=4 * 1024**3, used=2 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=100 * 1024**3, used=50 * 1024**3)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=500, bytes_recv=500)

        db, mock_result = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("DB error"))

        redis = _mock_redis()

        start_time = datetime.now(UTC)
        service = DashboardService(db, redis)
        result = await service.get_system_info(start_time)

        assert result is not None


class TestGetServicesHealth:
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_all_healthy(self, mock_sched, mock_router):
        db, mock_result = _mock_db()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar.return_value = 1
            elif call_count == 2:
                mock_r.scalar.return_value = 5
            elif call_count == 3:
                mock_r.scalar.return_value = 1024 * 1024 * 100
            elif call_count == 4:
                mock_r.scalar.return_value = "PostgreSQL 15.0"
            return mock_r

        db.execute = execute_side_effect

        mock_sched._running = True
        mock_sched.get_jobs_status = AsyncMock(return_value=[
            {"job_id": "test", "pending": False}
        ])
        mock_router.get_stats.return_value = {
            "total_connections": 5,
            "total_events_pushed": 100,
            "avg_connection_duration_seconds": 30,
        }

        redis = _mock_redis()

        service = DashboardService(db, redis)
        result = await service.get_services_health()

        assert len(result) == 4
        pg = next(s for s in result if s["service"] == "postgresql")
        assert pg["status"] == "healthy"

        redis_svc = next(s for s in result if s["service"] == "redis")
        assert redis_svc["status"] == "healthy"

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "healthy"

        sse = next(s for s in result if s["service"] == "sse")
        assert sse["status"] == "healthy"

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_pg_down(self, mock_sched, mock_router):
        db, mock_result = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("connection refused"))

        mock_sched._running = False
        mock_sched.get_jobs_status = AsyncMock(return_value=[])
        mock_router.get_stats.return_value = {"total_connections": 0, "total_events_pushed": 0, "avg_connection_duration_seconds": 0}

        service = DashboardService(db, None)
        result = await service.get_services_health()

        pg = next(s for s in result if s["service"] == "postgresql")
        assert pg["status"] == "down"

        redis_svc = next(s for s in result if s["service"] == "redis")
        assert redis_svc["status"] == "down"

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "down"

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_redis_error(self, mock_sched, mock_router):
        db, mock_result = _mock_db()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            mock_r.scalar.return_value = 1
            return mock_r

        db.execute = execute_side_effect

        mock_sched._running = True
        mock_sched.get_jobs_status = AsyncMock(return_value=[{"job_id": "j", "pending": False}])
        mock_router.get_stats.return_value = {"total_connections": 1, "total_events_pushed": 10, "avg_connection_duration_seconds": 5}

        redis = _mock_redis()
        redis.ping = AsyncMock(side_effect=Exception("Redis error"))

        service = DashboardService(db, redis)
        result = await service.get_services_health()

        redis_svc = next(s for s in result if s["service"] == "redis")
        assert redis_svc["status"] == "down"


class TestGetDataSourcesHealthSummary:
    @patch("app.services.dashboard.SourceService")
    async def test_with_source_service(self, mock_ss_class):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_ss = AsyncMock()
        mock_ss.get_all_sources_health_summary = AsyncMock(return_value={
            "total_sources": 10, "healthy": 8, "degraded": 1, "down": 1
        })
        mock_ss_class.return_value = mock_ss

        mock_result.all.return_value = []

        service = DashboardService(db, redis)
        result = await service.get_data_sources_health_summary("tenant-1")

        assert result["total_sources"] == 10
        assert len(result["sources"]) == 0

    async def test_without_redis(self):
        db, mock_result = _mock_db()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar.return_value = 5
            elif call_count == 2:
                mock_r.all.return_value = [("healthy", 3), ("degraded", 2)]
            elif call_count == 3:
                mock_r.all.return_value = []
            return mock_r

        db.execute = execute_side_effect

        service = DashboardService(db, None)
        result = await service.get_data_sources_health_summary("tenant-1")
        assert result["total_sources"] == 5


class TestGetDataSourceHealthDetail:
    @patch("app.services.dashboard.redis_get", new_callable=AsyncMock, return_value=None)
    async def test_detail_found(self, mock_redis_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = MagicMock()
        src.id = uuid.uuid4()
        src.name = "Test Source"
        src.source_type = "rss"

        health = MagicMock()
        health.status = "healthy"
        health.total_fetches_24h = 10
        health.success_count_24h = 9
        health.avg_response_time_ms = 50
        health.last_success_at = datetime.now(UTC)
        health.last_failure_at = None
        health.consecutive_failures = 0
        health.last_error_message = None

        mock_result.one_or_none.return_value = (src, health)

        service = DashboardService(db, redis)
        result = await service.get_data_source_health_detail("tenant-1", str(src.id))

        assert result["name"] == "Test Source"
        assert result["status"] == "healthy"

    async def test_detail_not_found_no_source(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.one_or_none.return_value = None
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = None
            return mock_r

        db.execute = execute_side_effect

        service = DashboardService(db, redis)
        result = await service.get_data_source_health_detail("tenant-1", "bad-id")
        assert result is None

    async def test_detail_no_health_record(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = MagicMock()
        src.id = uuid.uuid4()
        src.name = "Test Source"
        src.source_type = "rss"

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.one_or_none.return_value = None
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        service = DashboardService(db, redis)
        result = await service.get_data_source_health_detail("tenant-1", str(src.id))
        assert result["status"] == "healthy"
        assert result["health_history"] == []

    @patch("app.services.dashboard.redis_get", new_callable=AsyncMock)
    async def test_detail_with_cached_history(self, mock_redis_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = MagicMock()
        src.id = uuid.uuid4()
        src.name = "Source"
        src.source_type = "api"

        health = MagicMock()
        health.status = "degraded"
        health.total_fetches_24h = 10
        health.success_count_24h = 5
        health.avg_response_time_ms = 100
        health.last_success_at = datetime.now(UTC)
        health.last_failure_at = datetime.now(UTC)
        health.consecutive_failures = 5
        health.last_error_message = "timeout"

        mock_result.one_or_none.return_value = (src, health)

        mock_redis_get.side_effect = [
            json.dumps({"status": "degraded"}),
            json.dumps([{"time": "2024-01-01", "ms": 100}]),
        ]

        service = DashboardService(db, redis)
        result = await service.get_data_source_health_detail("tenant-1", str(src.id))
        assert result["status"] == "degraded"
        assert len(result["health_history"]) == 1
        assert len(result["response_time_trend"]) == 1


class TestGetSchedulerStatus:
    @patch("app.services.dashboard.scheduler_manager")
    async def test_get_status(self, mock_sched):
        mock_sched.get_jobs_status = AsyncMock(return_value=[
            {
                "job_id": "collect_src-123",
                "name": "Collect RSS",
                "trigger": "interval[0:05:00]",
                "original_interval": 300,
                "adaptive_multiplier": 1.0,
                "next_run": "2024-01-01 12:00:00",
                "pending": False,
            },
            {
                "job_id": "collect_src-456",
                "name": "Collect API",
                "trigger": "interval[0:10:00]",
                "original_interval": 600,
                "adaptive_multiplier": 2.0,
                "next_run": None,
                "pending": True,
            },
        ])

        db, _ = _mock_db()
        redis = _mock_redis()

        service = DashboardService(db, redis)
        result = await service.get_scheduler_status()

        assert result["total_jobs"] == 2
        assert len(result["running_jobs"]) == 1
        assert len(result["paused_jobs"]) == 1
        assert len(result["all_jobs"]) == 2

        paused = result["paused_jobs"][0]
        assert paused["status"] == "paused"
        assert paused["current_interval"] == 1200


class TestGetSSEStats:
    @patch("app.services.dashboard.redis_get", new_callable=AsyncMock, return_value="3600")
    @patch("app.services.dashboard.event_router")
    async def test_get_stats(self, mock_router, mock_redis_get):
        mock_router.get_stats.return_value = {
            "total_connections": 10,
            "connections_by_channel": {"finance": 5},
            "total_events_pushed": 1000,
            "avg_connection_duration_seconds": 120,
        }

        db, mock_result = _mock_db()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar.return_value = 50
            elif call_count == 2:
                mock_r.scalar.return_value = 10
            return mock_r

        db.execute = execute_side_effect

        redis = _mock_redis()

        service = DashboardService(db, redis)
        result = await service.get_sse_stats()

        assert result["total_connections"] == 10
        assert result["total_connections_today"] == 50

    @patch("app.services.dashboard.event_router")
    async def test_get_stats_db_error(self, mock_router):
        mock_router.get_stats.return_value = {
            "total_connections": 5,
            "connections_by_channel": {},
            "total_events_pushed": 100,
            "avg_connection_duration_seconds": 30,
        }

        db, mock_result = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("DB error"))

        redis = _mock_redis()

        service = DashboardService(db, redis)
        result = await service.get_sse_stats()
        assert result["total_connections"] == 5


class TestCollectAndPushMetrics:
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_first_metrics_push(self, mock_psutil, mock_set, mock_router):
        mock_psutil.cpu_percent.return_value = 30.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()

        from app.services.dashboard import _last_metrics
        _last_metrics.clear()

        service = DashboardService(db, _mock_redis())
        await service.collect_and_push_metrics(datetime.now(UTC))
        mock_router.push_event.assert_called_once()

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_incremental_metrics_push(self, mock_psutil, mock_set, mock_router):
        mock_psutil.cpu_percent.return_value = 40.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=60.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=2000, bytes_recv=3000)

        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        redis = _mock_redis()

        from app.services import dashboard as dash_mod
        dash_mod._last_metrics = {
            "cpu_usage_percent": 35.0,
            "memory_usage_percent": 55.0,
            "network_bytes_sent": 1000,
            "network_bytes_recv": 2000,
        }

        service = DashboardService(db, redis)
        await service.collect_and_push_metrics(datetime.now(UTC))
        mock_router.push_event.assert_called_once()

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_no_change_no_push(self, mock_psutil, mock_set, mock_router):
        mock_psutil.cpu_percent.return_value = 30.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        redis = _mock_redis()

        from app.services import dashboard as dash_mod
        dash_mod._last_metrics = {
            "cpu_usage_percent": 30.0,
            "memory_usage_percent": 50.0,
            "network_bytes_sent": 1000,
            "network_bytes_recv": 2000,
        }

        service = DashboardService(db, redis)
        await service.collect_and_push_metrics(datetime.now(UTC))
        mock_router.push_event.assert_not_called()

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock, side_effect=Exception("Redis error"))
    @patch("app.services.dashboard.psutil")
    async def test_redis_set_error_handled(self, mock_psutil, mock_set, mock_router):
        mock_psutil.cpu_percent.return_value = 30.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()

        from app.services import dashboard as dash_mod
        dash_mod._last_metrics.clear()

        service = DashboardService(db, _mock_redis())
        await service.collect_and_push_metrics(datetime.now(UTC))


class TestArchiveSnapshot:
    @patch("app.services.dashboard.scheduler_manager")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.psutil")
    async def test_archive_success(self, mock_psutil, mock_router, mock_sched):
        mock_psutil.virtual_memory.return_value = MagicMock(total=8 * 1024**3, used=4 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=500 * 1024**3, used=200 * 1024**3)

        mock_router.get_stats.return_value = {"total_connections": 5, "total_events_pushed": 100}

        mock_sched.get_jobs_status = AsyncMock(return_value=[{"pending": False}, {"pending": True}])

        db, mock_result = _mock_db()
        mock_result.all.return_value = [("healthy", 5), ("degraded", 2), ("down", 1)]

        service = DashboardService(db, _mock_redis())
        await service.archive_snapshot()
        db.add.assert_called_once()

    @patch("app.services.dashboard.scheduler_manager")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.psutil")
    async def test_archive_db_error(self, mock_psutil, mock_router, mock_sched):
        mock_psutil.virtual_memory.return_value = MagicMock(total=8 * 1024**3, used=4 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=500 * 1024**3, used=200 * 1024**3)

        mock_router.get_stats.return_value = {"total_connections": 5, "total_events_pushed": 100}
        mock_sched.get_jobs_status = AsyncMock(return_value=[])

        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("DB error"))

        service = DashboardService(db, _mock_redis())
        await service.archive_snapshot()
        db.add.assert_called_once()


class TestStartStopMetricsCollection:
    def test_stop_no_task(self):
        from app.services import dashboard as dash_mod
        dash_mod._metrics_collection_task = None
        stop_metrics_collection()
        assert dash_mod._metrics_collection_task is None

    def test_stop_done_task(self):
        from app.services import dashboard as dash_mod
        mock_task = MagicMock()
        mock_task.done.return_value = True
        dash_mod._metrics_collection_task = mock_task
        stop_metrics_collection()

    def test_stop_running_task(self):
        from app.services import dashboard as dash_mod
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        dash_mod._metrics_collection_task = mock_task
        stop_metrics_collection()
        mock_task.cancel.assert_called_once()
        assert dash_mod._metrics_collection_task is None


class TestStartMetricsCollection:
    async def test_start_creates_task(self):
        with patch("app.services.dashboard.asyncio.create_task") as mock_create:
            mock_create.return_value = MagicMock()
            task = await start_metrics_collection(datetime.now(UTC))
            mock_create.assert_called_once()
            assert task is not None


class TestGetSystemInfoExtraPaths:
    @patch("app.services.dashboard.psutil")
    async def test_pg_pool_with_checked_out(self, mock_psutil):
        """Cover line 57: pool_status has checked_out attribute."""
        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.cpu_count.return_value = 2
        mock_psutil.virtual_memory.return_value = MagicMock(total=4 * 1024**3, used=2 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=100 * 1024**3, used=50 * 1024**3)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=500, bytes_recv=500)

        db, mock_result = _mock_db()
        mock_result.scalar.return_value = 3

        pool_status_mock = MagicMock()
        pool_status_mock.checked_out = MagicMock(return_value=10)
        pool_mock = MagicMock()
        pool_mock.status.return_value = pool_status_mock
        db.get_bind.return_value.pool = pool_mock

        redis = _mock_redis()
        start_time = datetime.now(UTC)
        service = DashboardService(db, redis)
        result = await service.get_system_info(start_time)
        assert result["database"]["postgres_connections"] == 10

    @patch("app.services.dashboard.psutil")
    async def test_redis_error_during_info(self, mock_psutil):
        """Cover lines 69-71: Redis raises exception on ping/info."""
        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.cpu_count.return_value = 2
        mock_psutil.virtual_memory.return_value = MagicMock(total=4 * 1024**3, used=2 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=100 * 1024**3, used=50 * 1024**3)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=500, bytes_recv=500)

        db, mock_result = _mock_db()
        mock_result.scalar.return_value = 0

        redis = _mock_redis()
        redis.ping = AsyncMock(side_effect=Exception("Redis timeout"))

        start_time = datetime.now(UTC)
        service = DashboardService(db, redis)
        result = await service.get_system_info(start_time)
        assert result["database"]["redis_connected"] is False


class TestDataSourceHealthSummaryRows:
    """Cover lines 282-286: data source rows with health data."""

    @patch("app.services.dashboard.SourceService")
    async def test_sources_with_health_data(self, mock_ss_class):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_ss = AsyncMock()
        mock_ss.get_all_sources_health_summary = AsyncMock(return_value={
            "total_sources": 2, "healthy": 1, "degraded": 1, "down": 0
        })
        mock_ss_class.return_value = mock_ss

        # Create mock source and health row
        src = MagicMock()
        src.id = uuid.uuid4()
        src.name = "RSS Feed"
        src.source_type = "rss"
        src.category_id = uuid.uuid4()

        health = MagicMock()
        health.status = "degraded"
        health.total_fetches_24h = 20
        health.success_count_24h = 15
        health.avg_response_time_ms = 75
        health.last_success_at = datetime.now(UTC)
        health.last_failure_at = datetime.now(UTC)
        health.consecutive_failures = 2
        health.last_error_message = "timeout"

        rows_result = MagicMock()
        rows_result.all.return_value = [(src, health)]

        # With SourceService mocked, only the join query calls db.execute
        db.execute = AsyncMock(return_value=rows_result)

        service = DashboardService(db, redis)
        result = await service.get_data_sources_health_summary("tenant-1")

        assert len(result["sources"]) == 1
        source = result["sources"][0]
        assert source["success_rate_24h"] == 15 / 20
        assert source["consecutive_failures"] == 2
        assert source["last_error"] == "timeout"

    @patch("app.services.dashboard.SourceService")
    async def test_sources_zero_fetches(self, mock_ss_class):
        """success_rate should be None when total_fetches is 0."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_ss = AsyncMock()
        mock_ss.get_all_sources_health_summary = AsyncMock(return_value={
            "total_sources": 1, "healthy": 1, "degraded": 0, "down": 0
        })
        mock_ss_class.return_value = mock_ss

        src = MagicMock()
        src.id = uuid.uuid4()
        src.name = "API Source"
        src.source_type = "api"
        src.category_id = uuid.uuid4()

        health = MagicMock()
        health.status = "healthy"
        health.total_fetches_24h = 0
        health.success_count_24h = 0
        health.avg_response_time_ms = 0
        health.last_success_at = None
        health.last_failure_at = None
        health.consecutive_failures = 0
        health.last_error_message = None

        rows_result = MagicMock()
        rows_result.all.return_value = [(src, health)]

        # With SourceService mocked, only the join query calls db.execute
        db.execute = AsyncMock(return_value=rows_result)

        service = DashboardService(db, redis)
        result = await service.get_data_sources_health_summary("tenant-1")
        assert result["sources"][0]["success_rate_24h"] is None


class TestDataSourceHealthDetailExtraPaths:
    @patch("app.services.dashboard.redis_get", new_callable=AsyncMock, side_effect=Exception("Redis error"))
    async def test_redis_exception_on_health_history(self, mock_redis_get):
        """Cover lines 352-353: Redis exception reading health history."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = MagicMock()
        src.id = uuid.uuid4()
        src.name = "Test"
        src.source_type = "rss"

        health = MagicMock()
        health.status = "healthy"
        health.total_fetches_24h = 5
        health.success_count_24h = 5
        health.avg_response_time_ms = 30
        health.last_success_at = datetime.now(UTC)
        health.last_failure_at = None
        health.consecutive_failures = 0
        health.last_error_message = None

        mock_result.one_or_none.return_value = (src, health)

        service = DashboardService(db, redis)
        result = await service.get_data_source_health_detail("tenant-1", str(src.id))
        # Should still succeed despite Redis error
        assert result["health_history"] == []
        assert result["response_time_trend"] == []

    @patch("app.services.dashboard.redis_get", new_callable=AsyncMock)
    async def test_redis_exception_on_trend(self, mock_redis_get):
        """Cover lines 365-366: Redis exception reading response time trend."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = MagicMock()
        src.id = uuid.uuid4()
        src.name = "Test"
        src.source_type = "rss"

        health = MagicMock()
        health.status = "healthy"
        health.total_fetches_24h = 5
        health.success_count_24h = 5
        health.avg_response_time_ms = 30
        health.last_success_at = datetime.now(UTC)
        health.last_failure_at = None
        health.consecutive_failures = 0
        health.last_error_message = None

        mock_result.one_or_none.return_value = (src, health)

        # First call succeeds (health history), second call raises
        mock_redis_get.side_effect = [None, Exception("Redis error")]

        service = DashboardService(db, redis)
        result = await service.get_data_source_health_detail("tenant-1", str(src.id))
        assert result["response_time_trend"] == []


class TestCollectAndPushMetricsExtraPaths:
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_push_event_error_handled(self, mock_psutil, mock_set, mock_router):
        """Cover lines 532-533: push event error is caught."""
        mock_psutil.cpu_percent.return_value = 30.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        mock_router.push_event = AsyncMock(side_effect=Exception("SSE push failed"))

        db, _ = _mock_db()

        from app.services import dashboard as dash_mod
        dash_mod._last_metrics.clear()

        service = DashboardService(db, _mock_redis())
        # Should not raise despite push error
        await service.collect_and_push_metrics(datetime.now(UTC))


class TestSchedulerDegradedStatus:
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_scheduler_degraded(self, mock_sched, mock_router):
        """Scheduler is running but no running jobs -> degraded."""
        db, mock_result = _mock_db()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            mock_r.scalar.return_value = 1
            return mock_r

        db.execute = execute_side_effect

        mock_sched._running = True
        mock_sched.get_jobs_status = AsyncMock(return_value=[])
        mock_router.get_stats.return_value = {"total_connections": 0, "total_events_pushed": 0, "avg_connection_duration_seconds": 0}

        redis = _mock_redis()

        service = DashboardService(db, redis)
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "degraded"

        sse = next(s for s in result if s["service"] == "sse")
        # with 0 connections and scheduler running, SSE should be "degraded"
        assert sse["status"] == "degraded"
