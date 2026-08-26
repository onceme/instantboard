import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from operator import lt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.sql.dml import Delete

from app.core.redis import RedisKeys
from app.models.dashboard import DashboardSnapshot
from app.services.dashboard import (
    SNAPSHOT_CLEANUP_INTERVAL_SECONDS,
    SNAPSHOT_RETENTION_DAYS,
    DashboardService,
    cleanup_old_snapshots,
    snapshot_cleanup_due,
    start_metrics_collection,
    stop_metrics_collection,
)


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
    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.psutil")
    async def test_get_system_info_success(self, mock_psutil, mock_settings):
        mock_settings.env = "development"
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

    @patch("app.services.dashboard.psutil")
    async def test_get_system_info_network_rates(self, mock_psutil):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.cpu_count.return_value = 2
        mock_psutil.virtual_memory.return_value = MagicMock(total=4 * 1024**3, used=2 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=100 * 1024**3, used=50 * 1024**3)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        saved = dash_mod._last_net_sample
        dash_mod._last_net_sample = None
        try:
            db, mock_result = _mock_db()
            mock_result.scalar.return_value = 0
            service = DashboardService(db, None)
            result = await service.get_system_info(datetime.now(UTC))
        finally:
            dash_mod._last_net_sample = saved

        # First sample: rates are 0 but present (frontend renders "--" only when missing).
        assert result["network_in_kbps"] == 0.0
        assert result["network_out_kbps"] == 0.0
        # Group keeps both rate and cumulative fields for compatibility.
        assert result["network"] == {
            "network_in_kbps": 0.0,
            "network_out_kbps": 0.0,
            "bytes_sent": 1000,
            "bytes_recv": 2000,
        }


class TestGetServicesHealth:
    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_all_healthy(self, mock_sched, mock_router, mock_settings):
        # Embedded (dev) mode: the in-process scheduler singleton is the truth source.
        mock_settings.scheduler_enabled = True
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
        mock_sched.get_jobs_status = AsyncMock(return_value=[{"job_id": "test", "pending": False}])
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

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_pg_down(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = True
        db, mock_result = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("connection refused"))

        mock_sched._running = False
        mock_sched.get_jobs_status = AsyncMock(return_value=[])
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        service = DashboardService(db, None)
        result = await service.get_services_health()

        pg = next(s for s in result if s["service"] == "postgresql")
        assert pg["status"] == "down"

        redis_svc = next(s for s in result if s["service"] == "redis")
        assert redis_svc["status"] == "down"

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "down"

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_redis_error(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = True
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
        mock_router.get_stats.return_value = {
            "total_connections": 1,
            "total_events_pushed": 10,
            "avg_connection_duration_seconds": 5,
        }

        redis = _mock_redis()
        redis.ping = AsyncMock(side_effect=Exception("Redis error"))

        service = DashboardService(db, redis)
        result = await service.get_services_health()

        redis_svc = next(s for s in result if s["service"] == "redis")
        assert redis_svc["status"] == "down"

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_sse_zero_connections_healthy(self, mock_sched, mock_router, mock_settings):
        # Regression: zero SSE connections is a normal load state (nobody has the
        # real-time board open), not a health signal. The old rule reported
        # "degraded" whenever the scheduler was running with 0 connections and
        # false-fired on every dashboard page load.
        mock_settings.scheduler_enabled = True
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
        mock_sched.get_jobs_status = AsyncMock(return_value=[{"job_id": "test", "pending": False}])
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        service = DashboardService(db, _mock_redis())
        result = await service.get_services_health()

        sse = next(s for s in result if s["service"] == "sse")
        assert sse["status"] == "healthy"
        assert sse["connection_count"] == 0
        assert sse["details"]["active_connections"] == 0

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_sse_zero_connections_healthy_prod_mode(self, mock_sched, mock_router, mock_settings):
        # Same zero-connection contract when the scheduler runs in the worker
        # container (prod mode): SSE status must stay independent of it.
        mock_settings.scheduler_enabled = False
        db, mock_result = _mock_db()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            mock_r.scalar.return_value = 1
            return mock_r

        db.execute = execute_side_effect
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        service = DashboardService(db, _mock_redis())
        result = await service.get_services_health()

        sse = next(s for s in result if s["service"] == "sse")
        assert sse["status"] == "healthy"
        assert sse["connection_count"] == 0

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_services_sse_router_error_down(self, mock_sched, mock_router, mock_settings):
        # event_router unable to report stats = SSE service unavailable -> down.
        mock_settings.scheduler_enabled = True
        db, mock_result = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("connection refused"))

        mock_sched._running = True
        mock_sched.get_jobs_status = AsyncMock(return_value=[{"job_id": "test", "pending": False}])
        mock_router.get_stats.side_effect = RuntimeError("router stats unavailable")

        service = DashboardService(db, None)
        result = await service.get_services_health()

        sse = next(s for s in result if s["service"] == "sse")
        assert sse["status"] == "down"
        assert sse["connection_count"] == 0
        assert "router stats unavailable" in sse["details"]["error"]


class TestGetDataSourcesHealthSummary:
    @patch("app.services.dashboard.SourceService")
    async def test_with_source_service(self, mock_ss_class):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_ss = AsyncMock()
        mock_ss.get_all_sources_health_summary = AsyncMock(
            return_value={"total_sources": 10, "healthy": 8, "degraded": 1, "down": 1}
        )
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
    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_get_status(self, mock_sched, mock_settings):
        # Embedded (dev) mode: job list comes from the in-process scheduler.
        mock_settings.scheduler_enabled = True
        mock_sched.get_jobs_status = AsyncMock(
            return_value=[
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
            ]
        )

        db, _ = _mock_db()
        redis = _mock_redis()

        service = DashboardService(db, redis)
        result = await service.get_scheduler_status()

        assert result["total_jobs"] == 2
        assert len(result["running_jobs"]) == 1
        assert len(result["paused_jobs"]) == 1
        assert len(result["all_jobs"]) == 2
        # Embedded mode mirrors the list lengths in the count fields.
        assert result["running_jobs_count"] == 1
        assert result["paused_jobs_count"] == 1
        assert result["last_heartbeat"] is None

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
            "network_in_kbps": 0.0,
            "network_out_kbps": 0.0,
            "network_bytes_sent": 1000,
            "network_bytes_recv": 2000,
        }
        dash_mod._last_net_sample = {
            "bytes_recv": 2000,
            "bytes_sent": 1000,
            "timestamp": 100.0,
        }

        service = DashboardService(db, redis)
        with patch("app.services.dashboard.time.monotonic", return_value=110.0):
            await service.collect_and_push_metrics(datetime.now(UTC))
        mock_router.push_event.assert_called_once()

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_no_change_no_push(self, mock_psutil, mock_set, mock_router):
        mock_psutil.cpu_percent.return_value = 30.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        # Counters unchanged from the previous sample -> rate 0.0 (matches _last_metrics).
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        redis = _mock_redis()

        from app.services import dashboard as dash_mod

        dash_mod._last_metrics = {
            "cpu_usage_percent": 30.0,
            "memory_usage_percent": 50.0,
            "network_in_kbps": 0.0,
            "network_out_kbps": 0.0,
            "network_bytes_sent": 1000,
            "network_bytes_recv": 2000,
        }
        # Seed the previous net sample with identical counters so the computed
        # rate is 0.0 and nothing changes.
        dash_mod._last_net_sample = {
            "bytes_recv": 2000,
            "bytes_sent": 1000,
            "timestamp": 100.0,
        }

        service = DashboardService(db, redis)
        with patch("app.services.dashboard.time.monotonic", return_value=110.0):
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


class TestSampleNetworkRates:
    """KB/s rates computed from consecutive net_io_counters() samples.

    Module-level ``_last_net_sample`` carries state between samples, so every
    test resets it before and after running.
    """

    @pytest.fixture(autouse=True)
    def _reset_net_sample(self):
        from app.services import dashboard as dash_mod

        saved = dash_mod._last_net_sample
        dash_mod._last_net_sample = None
        yield
        dash_mod._last_net_sample = saved

    @patch("app.services.dashboard.psutil")
    def test_first_sample_returns_zero(self, mock_psutil):
        from app.services.dashboard import sample_network_rates

        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)

        with patch("app.services.dashboard.time.monotonic", return_value=100.0):
            data = sample_network_rates()

        assert data["network_in_kbps"] == 0.0
        assert data["network_out_kbps"] == 0.0
        # Cumulative counters stay available for backward compatibility.
        assert data["network_bytes_sent"] == 1000
        assert data["network_bytes_recv"] == 2000

    @patch("app.services.dashboard.psutil")
    def test_second_sample_computes_kbps(self, mock_psutil):
        from app.services import dashboard as dash_mod
        from app.services.dashboard import sample_network_rates

        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)
        with patch("app.services.dashboard.time.monotonic", return_value=100.0):
            sample_network_rates()

        # +20480 bytes recv and +10240 bytes sent over 10s -> 2.0 / 1.0 KB/s.
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=11240, bytes_recv=22480)
        with patch("app.services.dashboard.time.monotonic", return_value=110.0):
            data = sample_network_rates()

        assert data["network_in_kbps"] == 2.0
        assert data["network_out_kbps"] == 1.0
        assert data["network_bytes_sent"] == 11240
        assert data["network_bytes_recv"] == 22480
        assert dash_mod._last_net_sample["timestamp"] == 110.0

    @patch("app.services.dashboard.psutil")
    def test_counter_reset_clamped_to_zero(self, mock_psutil):
        from app.services.dashboard import sample_network_rates

        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=5000, bytes_recv=9000)
        with patch("app.services.dashboard.time.monotonic", return_value=100.0):
            sample_network_rates()

        # Reboot/wrap: counters drop below the previous sample -> clamp to 0, never negative.
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=100, bytes_recv=100)
        with patch("app.services.dashboard.time.monotonic", return_value=110.0):
            data = sample_network_rates()

        assert data["network_in_kbps"] == 0.0
        assert data["network_out_kbps"] == 0.0

    @patch("app.services.dashboard.psutil")
    def test_none_counters_keeps_state(self, mock_psutil):
        from app.services import dashboard as dash_mod
        from app.services.dashboard import sample_network_rates

        mock_psutil.net_io_counters.return_value = None

        with patch("app.services.dashboard.time.monotonic", return_value=100.0):
            data = sample_network_rates()

        assert data["network_in_kbps"] == 0.0
        assert data["network_out_kbps"] == 0.0
        assert data["network_bytes_sent"] == 0
        assert data["network_bytes_recv"] == 0
        assert dash_mod._last_net_sample is None


class TestArchiveSnapshot:
    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.psutil")
    async def test_archive_success(self, mock_psutil, mock_router, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = True
        mock_psutil.virtual_memory.return_value = MagicMock(total=8 * 1024**3, used=4 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=500 * 1024**3, used=200 * 1024**3)

        mock_router.get_stats.return_value = {"total_connections": 5, "total_events_pushed": 100}

        mock_sched.get_jobs_status = AsyncMock(return_value=[{"pending": False}, {"pending": True}])

        db, mock_result = _mock_db()
        mock_result.all.return_value = [("healthy", 5), ("degraded", 2), ("down", 1)]

        service = DashboardService(db, _mock_redis())
        await service.archive_snapshot()
        db.add.assert_called_once()
        # Embedded mode: active jobs counted from the in-process scheduler
        snapshot = db.add.call_args.args[0]
        assert snapshot.scheduler_jobs_active == 1

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.psutil")
    async def test_archive_db_error(self, mock_psutil, mock_router, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = True
        mock_psutil.virtual_memory.return_value = MagicMock(total=8 * 1024**3, used=4 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=500 * 1024**3, used=200 * 1024**3)

        mock_router.get_stats.return_value = {"total_connections": 5, "total_events_pushed": 100}
        mock_sched.get_jobs_status = AsyncMock(return_value=[])

        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("DB error"))

        service = DashboardService(db, _mock_redis())
        await service.archive_snapshot()
        db.add.assert_called_once()
        # No jobs scheduled -> 0 active jobs recorded
        snapshot = db.add.call_args.args[0]
        assert snapshot.scheduler_jobs_active == 0


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
        mock_ss.get_all_sources_health_summary = AsyncMock(
            return_value={"total_sources": 2, "healthy": 1, "degraded": 1, "down": 0}
        )
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
        mock_ss.get_all_sources_health_summary = AsyncMock(
            return_value={"total_sources": 1, "healthy": 1, "degraded": 0, "down": 0}
        )
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
    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_scheduler_degraded(self, mock_sched, mock_router, mock_settings):
        """Scheduler is running but no running jobs -> degraded (embedded mode)."""
        mock_settings.scheduler_enabled = True
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
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        redis = _mock_redis()

        service = DashboardService(db, redis)
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "degraded"

        sse = next(s for s in result if s["service"] == "sse")
        # 0 connections + scheduler running must NOT degrade SSE: the router is
        # available, and zero connections is a normal state (the old rule
        # false-fired during page load, racing the SSE handshake).
        assert sse["status"] == "healthy"
        assert sse["connection_count"] == 0
        assert sse["details"]["active_connections"] == 0


class TestSseHealthStatus:
    """SSE health depends only on router availability, never on the connection
    count: 0 connections just means nobody has the real-time board open."""

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_sse_healthy_with_zero_connections_scheduler_running(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = True
        db, _ = _mock_db()

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar.return_value = 1
            return mock_r

        db.execute = execute_side_effect

        mock_sched._running = True
        mock_sched.get_jobs_status = AsyncMock(return_value=[{"job_id": "j", "pending": False}])
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        service = DashboardService(db, _mock_redis())
        result = await service.get_services_health()

        sse = next(s for s in result if s["service"] == "sse")
        assert sse["status"] == "healthy"
        assert sse["connection_count"] == 0
        assert sse["details"]["active_connections"] == 0
        # connection counts still surface in details, just not in the status
        assert sse["details"]["total_events_pushed"] == 0

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_sse_healthy_with_zero_connections_scheduler_stopped(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = True
        db, _ = _mock_db()

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar.return_value = 1
            return mock_r

        db.execute = execute_side_effect

        mock_sched._running = False
        mock_sched.get_jobs_status = AsyncMock(return_value=[])
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        service = DashboardService(db, _mock_redis())
        result = await service.get_services_health()

        sse = next(s for s in result if s["service"] == "sse")
        assert sse["status"] == "healthy"
        assert sse["connection_count"] == 0


# ---------------------------------------------------------------------------
# Prod mode (SCHEDULER_ENABLED=false): scheduler health comes from the worker
# heartbeat stored in Redis, not from the in-process scheduler singleton.
# Contract: docs/dev-guide/design/infrastructure.md §3.5 "Worker 心跳机制".
# ---------------------------------------------------------------------------

HEARTBEAT_KEY = RedisKeys.worker_heartbeat_key()


def _heartbeat_payload(age_seconds: float = 0.0, **overrides) -> str:
    """Build a realistic worker heartbeat JSON, aged `age_seconds` into the past."""
    payload = {
        "timestamp": (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat(),
        "pid": 4242,
        "scheduler_running": True,
        "jobs_total": 12,
        "jobs_running": 10,
        "jobs_paused": 2,
        "event_listener_subscribed": True,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _redis_with_heartbeat(raw):
    redis = _mock_redis()
    redis.get = AsyncMock(return_value=raw)
    return redis


class TestServicesHealthProdMode:
    """Prod branch of get_services_health: read the worker heartbeat from Redis."""

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_fresh_heartbeat_is_healthy(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("pg not reachable"))  # pg branch irrelevant here
        mock_router.get_stats.return_value = {
            "total_connections": 3,
            "total_events_pushed": 1,
            "avg_connection_duration_seconds": 1,
        }

        raw = _heartbeat_payload(age_seconds=5)
        service = DashboardService(db, _redis_with_heartbeat(raw))
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "healthy"
        # The embedded scheduler singleton must NOT be consulted in prod mode.
        mock_sched.get_jobs_status.assert_not_called()
        # details carry job stats + last heartbeat straight from the payload
        assert sched["details"]["mode"] == "worker_heartbeat"
        assert sched["details"]["total_jobs"] == 12
        assert sched["details"]["running_jobs"] == 10
        assert sched["details"]["paused_jobs"] == 2
        assert sched["details"]["is_running"] is True
        assert sched["details"]["worker_pid"] == 4242
        assert sched["details"]["last_heartbeat"] is not None
        assert sched["details"]["last_heartbeat_age_seconds"] >= 0

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_stale_heartbeat_is_down(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("pg not reachable"))
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        raw = _heartbeat_payload(age_seconds=120)  # 120s > 45s threshold
        service = DashboardService(db, _redis_with_heartbeat(raw))
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "down"
        assert "stale" in sched["details"]["error"]
        # stale heartbeat still reports when we last heard from the worker
        assert sched["details"]["last_heartbeat"] is not None

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_missing_heartbeat_is_down(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("pg not reachable"))
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        service = DashboardService(db, _redis_with_heartbeat(None))  # key absent
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "down"
        assert sched["details"]["error"] == "worker heartbeat missing"
        assert sched["details"]["last_heartbeat"] is None

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_redis_read_failure_degrades_with_redis_status(self, mock_sched, mock_router, mock_settings):
        """Redis unavailable: scheduler cannot be judged -> down, but the details
        mirror the redis failure instead of blaming the worker."""
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("pg not reachable"))
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        redis = _mock_redis()
        redis.get = AsyncMock(side_effect=Exception("connection lost"))
        service = DashboardService(db, redis)
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "down"
        assert "redis unavailable" in sched["details"]["error"]
        assert sched["details"]["last_heartbeat"] is None

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_no_redis_client_is_down(self, mock_sched, mock_router, mock_settings):
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("pg not reachable"))
        mock_router.get_stats.return_value = {
            "total_connections": 0,
            "total_events_pushed": 0,
            "avg_connection_duration_seconds": 0,
        }

        service = DashboardService(db, None)  # no redis at all
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "down"
        assert sched["details"]["error"] == "Redis client not available"

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_listener_death_visible_in_details(self, mock_sched, mock_router, mock_settings):
        """A fresh heartbeat whose event listener died must expose that flag."""
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=Exception("pg not reachable"))
        mock_router.get_stats.return_value = {
            "total_connections": 3,
            "total_events_pushed": 1,
            "avg_connection_duration_seconds": 1,
        }

        raw = _heartbeat_payload(age_seconds=1, event_listener_subscribed=False)
        service = DashboardService(db, _redis_with_heartbeat(raw))
        result = await service.get_services_health()

        sched = next(s for s in result if s["service"] == "scheduler")
        assert sched["status"] == "healthy"  # heartbeat itself is fresh
        assert sched["details"]["event_listener_subscribed"] is False


class TestGetSchedulerStatusProdMode:
    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_counts_come_from_fresh_heartbeat(self, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        raw = _heartbeat_payload(age_seconds=2)
        service = DashboardService(db, _redis_with_heartbeat(raw))

        result = await service.get_scheduler_status()

        # the in-process scheduler must not be touched in prod mode
        mock_sched.get_jobs_status.assert_not_called()
        assert result["total_jobs"] == 12
        assert result["running_jobs_count"] == 10
        assert result["paused_jobs_count"] == 2
        # per-job list is empty cross-process; only counts come from the heartbeat
        assert result["running_jobs"] == []
        assert result["paused_jobs"] == []
        assert result["all_jobs"] == []
        assert result["last_heartbeat"] is not None

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_stale_heartbeat_yields_zero_counts(self, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        raw = _heartbeat_payload(age_seconds=300)  # stale
        service = DashboardService(db, _redis_with_heartbeat(raw))

        result = await service.get_scheduler_status()

        assert result["total_jobs"] == 0
        assert result["running_jobs_count"] == 0
        assert result["paused_jobs_count"] == 0

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    async def test_missing_heartbeat_yields_zero_counts(self, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = False
        db, _ = _mock_db()
        service = DashboardService(db, _redis_with_heartbeat(None))

        result = await service.get_scheduler_status()

        assert result["total_jobs"] == 0
        assert result["running_jobs_count"] == 0
        assert result["paused_jobs_count"] == 0
        assert result["last_heartbeat"] is None


class TestArchiveSnapshotProdMode:
    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.psutil")
    async def test_snapshot_uses_heartbeat_jobs_running(self, mock_psutil, mock_router, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = False
        mock_psutil.virtual_memory.return_value = MagicMock(total=8 * 1024**3, used=4 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=500 * 1024**3, used=200 * 1024**3)
        mock_router.get_stats.return_value = {"total_connections": 5, "total_events_pushed": 100}

        db, mock_result = _mock_db()
        mock_result.all.return_value = [("healthy", 5)]

        raw = _heartbeat_payload(age_seconds=1, jobs_running=7, jobs_total=9, jobs_paused=2)
        service = DashboardService(db, _redis_with_heartbeat(raw))
        await service.archive_snapshot()

        # in prod the in-process scheduler has no jobs; must NOT pollute with 0
        mock_sched.get_jobs_status.assert_not_called()
        snapshot = db.add.call_args.args[0]
        assert snapshot.scheduler_jobs_active == 7

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.psutil")
    async def test_snapshot_stale_heartbeat_is_zero(self, mock_psutil, mock_router, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = False
        mock_psutil.virtual_memory.return_value = MagicMock(total=8 * 1024**3, used=4 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=500 * 1024**3, used=200 * 1024**3)
        mock_router.get_stats.return_value = {"total_connections": 5, "total_events_pushed": 100}

        db, mock_result = _mock_db()
        mock_result.all.return_value = [("healthy", 5)]

        raw = _heartbeat_payload(age_seconds=300, jobs_running=7)  # stale
        service = DashboardService(db, _redis_with_heartbeat(raw))
        await service.archive_snapshot()

        snapshot = db.add.call_args.args[0]
        assert snapshot.scheduler_jobs_active == 0


# ---------------------------------------------------------------------------
# Threshold alerts + psutil graceful degradation + load-adaptive interval.
# Contract: docs/dev-guide/design/dashboard-tab.md §5 边界情况.
# All alert/degradation state is module-level in app.services.dashboard, so every
# test class below saves it before the test and restores it afterwards — the
# tests must never leak alert state into each other.
# ---------------------------------------------------------------------------


class _AlertStateReset:
    """Save/restore the module-level alert + adaptive-interval state."""

    @pytest.fixture(autouse=True)
    def _reset_alert_state(self):
        from app.services import dashboard as dash_mod

        saved_alerts = dict(dash_mod._active_alerts)
        saved_signature = dash_mod._alerts_signature
        saved_streak = dash_mod._cpu_high_streak
        saved_degraded = dash_mod._collection_degraded
        saved_last_metrics = dict(dash_mod._last_metrics)
        saved_net_sample = dash_mod._last_net_sample

        dash_mod._active_alerts.clear()
        dash_mod._alerts_signature = ()
        dash_mod._cpu_high_streak = 0
        dash_mod._collection_degraded = False
        dash_mod._last_metrics.clear()
        dash_mod._last_net_sample = None

        yield

        dash_mod._active_alerts.clear()
        dash_mod._active_alerts.update(saved_alerts)
        dash_mod._alerts_signature = saved_signature
        dash_mod._cpu_high_streak = saved_streak
        dash_mod._collection_degraded = saved_degraded
        dash_mod._last_metrics.clear()
        dash_mod._last_metrics.update(saved_last_metrics)
        dash_mod._last_net_sample = saved_net_sample


class TestPsutilDegradation(_AlertStateReset):
    """psutil missing (psutil = None) must degrade gracefully: no exception,
    system-level metrics are None/0, DB/Redis/SSE keep working."""

    @patch("app.services.dashboard.psutil", None)
    async def test_get_system_info_degraded(self):
        db, mock_result = _mock_db()
        mock_result.scalar.return_value = 0
        db.get_bind.return_value.pool.status.return_value = "Pool size: 5"

        service = DashboardService(db, _mock_redis())
        result = await service.get_system_info(datetime.now(UTC))

        assert result["psutil_available"] is False
        assert result["cpu_usage_percent"] is None
        assert result["cpu_count"] is None
        assert result["memory_total_mb"] is None
        assert result["memory_used_mb"] is None
        assert result["disk_total_gb"] is None
        assert result["disk_used_gb"] is None
        # network degrades to zeros; DB/Redis probes still run
        assert result["network_in_kbps"] == 0.0
        assert result["network_out_kbps"] == 0.0
        assert result["network"]["bytes_sent"] == 0
        assert result["database"]["redis_connected"] is True
        assert result["alerts"] == []

    @patch("app.services.dashboard.psutil", None)
    def test_sample_network_rates_degraded(self):
        from app.services import dashboard as dash_mod
        from app.services.dashboard import sample_network_rates

        data = sample_network_rates()
        assert data == {
            "network_in_kbps": 0.0,
            "network_out_kbps": 0.0,
            "network_bytes_recv": 0,
            "network_bytes_sent": 0,
        }
        # no sample state is kept while psutil is absent
        assert dash_mod._last_net_sample is None

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil", None)
    async def test_collect_and_push_degraded(self, mock_set, mock_router):
        mock_router.get_stats.return_value = {"total_connections": 0}
        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        service = DashboardService(db, None)
        # must not raise despite psutil=None
        await service.collect_and_push_metrics(datetime.now(UTC))

        mock_router.push_event.assert_called_once()
        payload = mock_router.push_event.call_args.kwargs["data"]
        assert payload["cpu_usage_percent"] is None
        assert payload["memory_usage_percent"] is None
        assert payload["disk_usage_percent"] is None
        assert payload["network_in_kbps"] == 0.0

    @patch("app.services.dashboard.settings")
    @patch("app.services.dashboard.scheduler_manager")
    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.psutil", None)
    async def test_archive_snapshot_degraded(self, mock_router, mock_sched, mock_settings):
        mock_settings.scheduler_enabled = True
        mock_router.get_stats.return_value = {"total_connections": 1, "total_events_pushed": 2}
        mock_sched.get_jobs_status = AsyncMock(return_value=[])

        db, mock_result = _mock_db()
        mock_result.all.return_value = [("healthy", 1)]

        service = DashboardService(db, _mock_redis())
        await service.archive_snapshot()

        db.add.assert_called_once()
        # psutil-backed snapshot columns are nullable and store None when degraded
        snapshot = db.add.call_args.args[0]
        assert snapshot.memory_used_mb is None
        assert snapshot.memory_total_mb is None
        assert snapshot.disk_used_gb is None
        assert snapshot.disk_total_gb is None
        assert snapshot.active_sse_connections == 1


class TestThresholdAlerts(_AlertStateReset):
    """Trigger / dedupe / recover cycles for the four threshold alerts."""

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_cpu_high_trigger_dedupe_recover(self, mock_psutil, mock_set, mock_router):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 95.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)
        mock_router.get_stats.return_value = {"total_connections": 5}
        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        service = DashboardService(db, _mock_redis())

        await service.collect_and_push_metrics(datetime.now(UTC))
        alerts = dash_mod.get_active_alerts()
        assert [a["code"] for a in alerts] == ["cpu_high"]
        first_triggered_at = alerts[0]["triggered_at"]
        # the trigger rides the incremental SSE push
        payload = mock_router.push_event.call_args.kwargs["data"]
        assert payload["alerts"] == alerts

        # second cycle while still high: deduped (same code, same triggered_at),
        # and the unchanged alert list must NOT be re-pushed
        mock_router.push_event.reset_mock()
        await service.collect_and_push_metrics(datetime.now(UTC))
        alerts = dash_mod.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0]["triggered_at"] == first_triggered_at
        mock_router.push_event.assert_not_called()

        # recovery clears the alert and pushes the now-empty list
        mock_psutil.cpu_percent.return_value = 30.0
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert dash_mod.get_active_alerts() == []
        payload = mock_router.push_event.call_args.kwargs["data"]
        assert payload["alerts"] == []

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_redis_memory_high_trigger_and_recover(self, mock_psutil, mock_set, mock_router):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)
        mock_router.get_stats.return_value = {"total_connections": 0}
        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        redis = _mock_redis()
        redis.info = AsyncMock(return_value={"used_memory": 900_000_000, "maxmemory": 1_000_000_000})

        service = DashboardService(db, redis)
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert [a["code"] for a in dash_mod.get_active_alerts()] == ["redis_memory_high"]

        # usage drops back under 80% -> alert removed
        redis.info = AsyncMock(return_value={"used_memory": 100_000_000, "maxmemory": 1_000_000_000})
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert dash_mod.get_active_alerts() == []

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_redis_memory_alert_skipped_without_maxmemory(self, mock_psutil, mock_set, mock_router):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)
        mock_router.get_stats.return_value = {"total_connections": 0}
        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        redis = _mock_redis()
        # maxmemory 0 = no limit configured -> the check is skipped entirely
        redis.info = AsyncMock(return_value={"used_memory": 10**12, "maxmemory": 0})

        service = DashboardService(db, redis)
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert dash_mod.get_active_alerts() == []

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_db_pool_exhausted_trigger_and_recover(self, mock_psutil, mock_set, mock_router):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)
        mock_router.get_stats.return_value = {"total_connections": 0}
        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        pool = db.get_bind.return_value.pool
        pool.checkedout.return_value = 10
        pool.size.return_value = 10

        service = DashboardService(db, _mock_redis())
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert [a["code"] for a in dash_mod.get_active_alerts()] == ["db_pool_exhausted"]

        # checked_out drops below pool size -> alert removed
        pool.checkedout.return_value = 4
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert dash_mod.get_active_alerts() == []

    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    @patch("app.services.dashboard.event_router")
    async def test_sse_connections_high_trigger_and_recover(self, mock_router, mock_psutil, mock_set):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=1000, bytes_recv=2000)
        mock_router.get_stats.return_value = {"total_connections": 1500}
        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        service = DashboardService(db, _mock_redis())
        await service.collect_and_push_metrics(datetime.now(UTC))
        alerts = dash_mod.get_active_alerts()
        assert [a["code"] for a in alerts] == ["sse_connections_high"]
        assert "1000" in alerts[0]["message"]

        mock_router.get_stats.return_value = {"total_connections": 500}
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert dash_mod.get_active_alerts() == []

    def test_dedupe_helper_keeps_first_triggered_at(self):
        from app.services import dashboard as dash_mod

        dash_mod._set_alert("cpu_high", "first message")
        first_triggered_at = dash_mod._active_alerts["cpu_high"]["triggered_at"]

        dash_mod._set_alert("cpu_high", "updated message")
        assert len(dash_mod._active_alerts) == 1
        assert dash_mod._active_alerts["cpu_high"]["triggered_at"] == first_triggered_at
        assert dash_mod._active_alerts["cpu_high"]["message"] == "updated message"

        dash_mod._clear_alert("cpu_high")
        assert dash_mod.get_active_alerts() == []

    @patch("app.services.dashboard.psutil")
    async def test_get_system_info_reports_alerts(self, mock_psutil):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.cpu_count.return_value = 2
        mock_psutil.virtual_memory.return_value = MagicMock(total=4 * 1024**3, used=2 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(total=100 * 1024**3, used=50 * 1024**3)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=500, bytes_recv=500)

        dash_mod._set_alert("redis_memory_high", "Redis memory usage above 80% of maxmemory")

        db, mock_result = _mock_db()
        mock_result.scalar.return_value = 0
        db.get_bind.return_value.pool.status.return_value = "Pool size: 5"

        service = DashboardService(db, _mock_redis())
        result = await service.get_system_info(datetime.now(UTC))

        assert result["psutil_available"] is True
        assert [a["code"] for a in result["alerts"]] == ["redis_memory_high"]


class TestSystemEndpointAlerts(_AlertStateReset):
    """GET /dashboard/system exposes the alerts and psutil_available fields."""

    @patch("app.api.v1.dashboard.DashboardService")
    async def test_system_response_contains_alerts_and_psutil_flag(self, mock_service_cls):
        from app.api.v1.dashboard import get_system_info
        from app.schemas.dashboard import SystemInfoResponse

        alert = {
            "code": "cpu_high",
            "message": "CPU usage above 90% threshold",
            "triggered_at": "2026-01-01T00:00:00+00:00",
        }
        data = {
            "version": "1.0.0",
            "uptime_seconds": 100,
            "environment": "test",
            "python_version": "3.11.0",
            "psutil_available": False,
            "alerts": [alert],
        }
        mock_service = MagicMock()
        mock_service.get_system_info = AsyncMock(return_value=data)
        mock_service_cls.return_value = mock_service

        response = await get_system_info(
            request=MagicMock(),
            user={"id": "admin"},
            db=AsyncMock(),
            redis_client=AsyncMock(),
        )

        assert response.success is True
        assert response.data["psutil_available"] is False
        assert response.data["alerts"] == [alert]
        # schema contract: the response model accepts the new fields
        parsed = SystemInfoResponse(**response.data)
        assert parsed.psutil_available is False
        assert parsed.alerts[0].code == "cpu_high"
        assert parsed.alerts[0].triggered_at == alert["triggered_at"]


class TestAdaptiveCollectInterval(_AlertStateReset):
    """Sustained high CPU (3 consecutive samples >90%) degrades the collection
    loop from 30s to 60s; the first recovered sample restores 30s."""

    @patch("app.services.dashboard.event_router")
    @patch("app.services.dashboard.redis_set", new_callable=AsyncMock)
    @patch("app.services.dashboard.psutil")
    async def test_high_cpu_degrades_interval_and_recovers(self, mock_psutil, mock_set, mock_router):
        from app.services import dashboard as dash_mod

        mock_psutil.cpu_percent.return_value = 95.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=60.0)
        mock_psutil.net_io_counters.return_value = MagicMock(bytes_sent=0, bytes_recv=0)
        mock_router.get_stats.return_value = {"total_connections": 0}
        mock_router.push_event = AsyncMock()

        db, _ = _mock_db()
        service = DashboardService(db, None)

        assert dash_mod.get_collect_interval() == 30
        for expected_streak, expected_interval in [(1, 30), (2, 30), (3, 60)]:
            await service.collect_and_push_metrics(datetime.now(UTC))
            assert dash_mod._cpu_high_streak == expected_streak
            assert dash_mod.get_collect_interval() == expected_interval

        # once degraded, the cpu_high alert message records the slower interval
        alert = next(a for a in dash_mod.get_active_alerts() if a["code"] == "cpu_high")
        assert "60" in alert["message"]

        # one sample back below the threshold restores the normal interval
        mock_psutil.cpu_percent.return_value = 30.0
        await service.collect_and_push_metrics(datetime.now(UTC))
        assert dash_mod._cpu_high_streak == 0
        assert dash_mod.get_collect_interval() == 30


# ---------------------------------------------------------------------------
# Snapshot retention: dashboard_snapshots rows older than SNAPSHOT_RETENTION_DAYS
# are deleted once per 24h, piggy-backed on the metrics collection loop.
# Contract: docs/dev-guide/design/dashboard-tab.md §3.9.3 "存储优化".
# ---------------------------------------------------------------------------


class TestCleanupOldSnapshots:
    """cleanup_old_snapshots: DELETE targets the snapshot `timestamp` column
    with the retention cutoff and reports the deleted row count."""

    async def test_deletes_rows_older_than_retention_and_returns_count(self):
        db, mock_result = _mock_db()
        mock_result.rowcount = 42

        deleted = await cleanup_old_snapshots(db)

        assert deleted == 42
        db.commit.assert_awaited_once()

        stmt = db.execute.call_args.args[0]
        assert isinstance(stmt, Delete)
        assert stmt.table.name == DashboardSnapshot.__tablename__

        condition = stmt.whereclause
        # The table has no created_at column: the filter must use `timestamp`.
        assert condition.left.name == "timestamp"
        assert condition.operator is lt
        expected_cutoff = datetime.now(UTC) - timedelta(days=SNAPSHOT_RETENTION_DAYS)
        assert abs((condition.right.value - expected_cutoff).total_seconds()) < 5

    async def test_custom_retention_days(self):
        db, mock_result = _mock_db()
        mock_result.rowcount = 3

        deleted = await cleanup_old_snapshots(db, retention_days=7)

        assert deleted == 3
        condition = db.execute.call_args.args[0].whereclause
        expected_cutoff = datetime.now(UTC) - timedelta(days=7)
        assert abs((condition.right.value - expected_cutoff).total_seconds()) < 5

    async def test_non_positive_rowcount_returns_zero(self):
        db, mock_result = _mock_db()
        mock_result.rowcount = -1  # some drivers report -1 when nothing matched

        assert await cleanup_old_snapshots(db) == 0

    async def test_db_error_propagates_to_caller(self):
        db, _ = _mock_db()
        db.execute = AsyncMock(side_effect=RuntimeError("DB down"))

        with pytest.raises(RuntimeError, match="DB down"):
            await cleanup_old_snapshots(db)


class TestSnapshotCleanupThrottle:
    """24h throttle (snapshot_cleanup_due): the first run is due immediately,
    afterwards only once the interval has elapsed."""

    def test_first_run_is_due(self):
        assert snapshot_cleanup_due(None, time.monotonic()) is True

    def test_not_due_before_interval(self):
        last = time.monotonic()
        assert snapshot_cleanup_due(last, last + SNAPSHOT_CLEANUP_INTERVAL_SECONDS - 1) is False

    def test_due_once_interval_elapsed(self):
        last = time.monotonic()
        assert snapshot_cleanup_due(last, last + SNAPSHOT_CLEANUP_INTERVAL_SECONDS) is True

    def test_custom_interval(self):
        assert snapshot_cleanup_due(100.0, 110.0, interval=10.0) is True
        assert snapshot_cleanup_due(100.0, 105.0, interval=10.0) is False


class TestMetricsLoopSnapshotCleanup:
    """Cleanup rides on the metrics collection loop: due on the very first
    iteration, throttled afterwards, and a failure is only logged — it must
    never interrupt collection."""

    @staticmethod
    def _session_ctx(session):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    @patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=None)
    @patch("app.db.session.async_session_factory")
    async def test_cleanup_failure_does_not_interrupt_collection(self, mock_factory, _mock_redis):
        session = AsyncMock()
        mock_factory.return_value = self._session_ctx(session)

        with (
            patch(
                "app.services.dashboard.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=[None, asyncio.CancelledError()],
            ),
            patch.object(DashboardService, "collect_and_push_metrics", new_callable=AsyncMock) as mock_collect,
            patch.object(DashboardService, "archive_snapshot", new_callable=AsyncMock) as mock_archive,
            patch(
                "app.services.dashboard.cleanup_old_snapshots",
                new_callable=AsyncMock,
                side_effect=RuntimeError("cleanup boom"),
            ) as mock_cleanup,
            patch("app.services.dashboard.logger") as mock_logger,
        ):
            task = await start_metrics_collection(datetime.now(UTC))
            await task  # must complete via CancelledError, not crash on the failure

        # Iteration 1 collected metrics, attempted the first-run cleanup and then
        # reached iteration 2 (sleep #2) despite the cleanup failure.
        mock_collect.assert_awaited_once()
        mock_cleanup.assert_awaited_once_with(session)
        mock_archive.assert_not_awaited()
        assert any("snapshot cleanup failed" in str(call) for call in mock_logger.warning.call_args_list)

    @patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=None)
    @patch("app.db.session.async_session_factory")
    async def test_cleanup_runs_once_then_throttled(self, mock_factory, _mock_redis):
        session = AsyncMock()
        mock_factory.return_value = self._session_ctx(session)

        with (
            patch(
                "app.services.dashboard.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=[None, None, asyncio.CancelledError()],
            ),
            patch.object(DashboardService, "collect_and_push_metrics", new_callable=AsyncMock) as mock_collect,
            patch(
                "app.services.dashboard.cleanup_old_snapshots",
                new_callable=AsyncMock,
                return_value=7,
            ) as mock_cleanup,
            patch("app.services.dashboard.logger") as mock_logger,
        ):
            task = await start_metrics_collection(datetime.now(UTC))
            await task

        # Two collection iterations, but cleanup only on the first one — the
        # second runs seconds, not 24h, after the successful cleanup.
        assert mock_collect.await_count == 2
        mock_cleanup.assert_awaited_once_with(session)
        assert any("removed 7 rows" in str(call) for call in mock_logger.info.call_args_list)
