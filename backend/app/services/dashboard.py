import asyncio
import json
import logging
import platform
import time
from datetime import UTC, datetime

import psutil
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

# Fix: import SYSTEM_TENANT_ID directly from core.constants (single source of truth, a
# UUID constant). It used to be re-exported indirectly via source.py, and hardcoded
# literals were scattered across call sites.
from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys, redis_get, redis_set
from app.core.sse_router import SSEEventType, event_router
from app.models.dashboard import DashboardSnapshot
from app.models.source import Source, SourceHealth
from app.models.sse import SSEConnection as SSEConnectionModel
from app.scheduler.manager import scheduler_manager
from app.services.source import SourceService

logger = logging.getLogger(__name__)

METRIC_THRESHOLDS = {
    "cpu_change_percent": 5.0,
    "memory_change_percent": 5.0,
}

_metrics_collection_task: asyncio.Task | None = None
_last_metrics: dict = {}


class DashboardService:
    def __init__(self, db: AsyncSession, redis: Redis | None):
        self.db = db
        self.redis = redis

    async def get_system_info(self, start_time: datetime) -> dict:
        uptime = int((datetime.now(UTC) - start_time).total_seconds())

        cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 0.5)
        cpu_count = psutil.cpu_count()
        mem = await asyncio.to_thread(psutil.virtual_memory)
        disk = await asyncio.to_thread(psutil.disk_usage, "/")
        await asyncio.to_thread(psutil.net_io_counters)

        pg_connections = None
        pg_active_queries = None
        redis_connected = None
        redis_memory_mb = None

        try:
            pg_result = await self.db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'"))
            pg_active_queries = pg_result.scalar() or 0

            pool_status = self.db.get_bind().pool.status()
            pg_connections = pool_status.checked_out() if hasattr(pool_status, "checked_out") else None
        except Exception as e:
            logger.warning(f"Failed to query PostgreSQL stats: {e}")

        try:
            if self.redis is None:
                redis_connected = False
                redis_memory_mb = None
            else:
                redis_connected = await self.redis.ping()
                info = await self.redis.info("memory")
                redis_memory_mb = float(info.get("used_memory", 0)) / (1024 * 1024)
        except Exception as e:
            logger.warning(f"Failed to query Redis stats: {e}")
            redis_connected = False

        result = {
            "version": "1.0.0",
            "uptime_seconds": uptime,
            "environment": settings.env,
            "python_version": platform.python_version(),
            "cpu_count": cpu_count,
            "cpu_usage_percent": cpu_usage,
            "memory_total_mb": int(mem.total / (1024 * 1024)),
            "memory_used_mb": int(mem.used / (1024 * 1024)),
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "database": {
                "postgres_connections": pg_connections,
                "postgres_active_queries": pg_active_queries,
                "redis_connected": redis_connected,
                "redis_memory_used_mb": round(redis_memory_mb, 1) if redis_memory_mb else None,
            },
        }

        return result

    async def get_services_health(self) -> list[dict]:
        services = []

        pg_start = time.monotonic()
        try:
            await self.db.execute(text("SELECT 1"))
            pg_duration = int((time.monotonic() - pg_start) * 1000)

            pg_conn_result = await self.db.execute(text("SELECT count(*) FROM pg_stat_activity"))
            pg_conn_count = pg_conn_result.scalar() or 0

            pg_size_result = await self.db.execute(text("SELECT pg_database_size(current_database())"))
            pg_size_mb = round((pg_size_result.scalar() or 0) / (1024 * 1024), 1)

            pg_version_result = await self.db.execute(text("SELECT version()"))
            pg_version = pg_version_result.scalar() or "unknown"

            services.append(
                {
                    "service": "postgresql",
                    "status": "healthy",
                    "response_time_ms": pg_duration,
                    "connection_count": pg_conn_count,
                    "details": {
                        "version": pg_version.split()[1] if pg_version != "unknown" else pg_version,
                        "database_size_mb": pg_size_mb,
                        "active_connections": pg_conn_count,
                    },
                }
            )
        except Exception as e:
            pg_duration = int((time.monotonic() - pg_start) * 1000)
            services.append(
                {
                    "service": "postgresql",
                    "status": "down",
                    "response_time_ms": pg_duration,
                    "connection_count": 0,
                    "details": {"error": str(e)},
                }
            )

        redis_start = time.monotonic()
        try:
            if self.redis is None:
                redis_duration = 0
                services.append(
                    {
                        "service": "redis",
                        "status": "down",
                        "response_time_ms": redis_duration,
                        "connection_count": 0,
                        "details": {"error": "Redis client not available"},
                    }
                )
            else:
                redis_pong = await self.redis.ping()
                redis_duration = int((time.monotonic() - redis_start) * 1000)

                redis_info = await self.redis.info()
                redis_clients = redis_info.get("connected_clients", 0)
                redis_memory_human = redis_info.get("used_memory_human", "0B")

                services.append(
                    {
                        "service": "redis",
                        "status": "healthy" if redis_pong else "down",
                        "response_time_ms": redis_duration,
                        "connection_count": redis_clients,
                        "details": {
                            "memory_used": redis_memory_human,
                            "connected_clients": redis_clients,
                            "uptime_seconds": redis_info.get("uptime_in_seconds", 0),
                        },
                    }
                )
        except Exception as e:
            redis_duration = int((time.monotonic() - redis_start) * 1000)
            services.append(
                {
                    "service": "redis",
                    "status": "down",
                    "response_time_ms": redis_duration,
                    "connection_count": 0,
                    "details": {"error": str(e)},
                }
            )

        scheduler_running = scheduler_manager._running
        jobs_status = await scheduler_manager.get_jobs_status()
        running_jobs = [j for j in jobs_status if not j.get("pending", False)]
        paused_jobs = [j for j in jobs_status if j.get("pending", False)]

        services.append(
            {
                "service": "scheduler",
                "status": "healthy"
                if scheduler_running and running_jobs
                else "degraded"
                if scheduler_running
                else "down",
                "response_time_ms": 0,
                "connection_count": None,
                "details": {
                    "is_running": scheduler_running,
                    "total_jobs": len(jobs_status),
                    "running_jobs": len(running_jobs),
                    "paused_jobs": len(paused_jobs),
                },
            }
        )

        sse_stats = event_router.get_stats()
        sse_active_count = sse_stats.get("total_connections", 0)
        sse_status = "healthy" if sse_active_count > 0 else "degraded" if scheduler_running else "healthy"

        services.append(
            {
                "service": "sse",
                "status": sse_status,
                "response_time_ms": 0,
                "connection_count": sse_active_count,
                "details": {
                    "active_connections": sse_active_count,
                    "total_events_pushed": sse_stats.get("total_events_pushed", 0),
                    "avg_connection_duration_seconds": sse_stats.get("avg_connection_duration_seconds", 0),
                },
            }
        )

        return services

    async def get_data_sources_health_summary(self, tenant_id: str) -> dict:
        source_service = SourceService(self.db, self.redis) if self.redis else None
        if source_service:
            summary = await source_service.get_all_sources_health_summary(tenant_id)
        else:
            total_stmt = (
                select(func.count())
                .select_from(Source)
                .where(
                    or_(
                        Source.tenant_id == tenant_id,
                        Source.tenant_id == SYSTEM_TENANT_ID,
                    )
                )
            )
            total = (await self.db.execute(total_stmt)).scalar() or 0
            summary = {
                "total_sources": total,
                "healthy": 0,
                "degraded": 0,
                "down": 0,
            }
            health_stmt = (
                select(SourceHealth.status, func.count())
                .join(Source, SourceHealth.source_id == Source.id)
                .where(
                    or_(
                        Source.tenant_id == tenant_id,
                        Source.tenant_id == SYSTEM_TENANT_ID,
                    )
                )
                .group_by(SourceHealth.status)
            )
            rows = (await self.db.execute(health_stmt)).all()
            for status, count in rows:
                summary[status] = count

        stmt = (
            select(Source, SourceHealth)
            .join(SourceHealth, Source.id == SourceHealth.source_id)
            .where(
                or_(
                    Source.tenant_id == tenant_id,
                    Source.tenant_id == SYSTEM_TENANT_ID,
                )
            )
            .order_by(
                text("CASE source_health.status WHEN 'down' THEN 1 WHEN 'degraded' THEN 2 ELSE 3 END"),
                Source.name.asc(),
            )
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        sources = []
        for source, health in rows:
            success_rate = None
            if health.total_fetches_24h > 0:
                success_rate = health.success_count_24h / health.total_fetches_24h

            sources.append(
                {
                    "id": str(source.id),
                    "name": source.name,
                    "source_type": source.source_type,
                    "category_id": str(source.category_id),
                    "status": health.status,
                    "success_rate_24h": success_rate,
                    "avg_response_time_ms": health.avg_response_time_ms,
                    "last_success_at": health.last_success_at,
                    "last_failure_at": health.last_failure_at,
                    "consecutive_failures": health.consecutive_failures,
                    "total_fetches_24h": health.total_fetches_24h,
                    "last_error": health.last_error_message,
                }
            )

        return {
            "total_sources": summary["total_sources"],
            "healthy": summary["healthy"],
            "degraded": summary["degraded"],
            "down": summary["down"],
            "sources": sources,
        }

    async def get_data_source_health_detail(self, tenant_id: str, source_id: str) -> dict:
        stmt = (
            select(Source, SourceHealth)
            .join(SourceHealth, Source.id == SourceHealth.source_id)
            .where(Source.id == source_id)
        )
        result = await self.db.execute(stmt)
        row = result.one_or_none()

        if row is None:
            source_stmt = select(Source).where(Source.id == source_id)
            source_result = await self.db.execute(source_stmt)
            source = source_result.scalar_one_or_none()
            if source is None:
                return None

            return {
                "source_id": str(source.id),
                "name": source.name,
                "source_type": source.source_type,
                "status": "healthy",
                "success_rate_24h": None,
                "avg_response_time_ms": 0,
                "last_success_at": None,
                "last_failure_at": None,
                "consecutive_failures": 0,
                "total_fetches_24h": 0,
                "last_error": None,
                "health_history": [],
                "response_time_trend": [],
            }

        source, health = row

        redis_health_key = RedisKeys.source_health_key(str(source_id))
        health_history = []
        try:
            cached = await redis_get(redis_health_key)
            if cached:
                health_data = json.loads(cached)
                health_history.append(health_data)
        except Exception:
            pass

        success_rate = None
        if health.total_fetches_24h > 0:
            success_rate = health.success_count_24h / health.total_fetches_24h

        response_time_trend = []
        try:
            trend_key = f"response_time_trend:{source_id}"
            trend_data = await redis_get(trend_key)
            if trend_data:
                response_time_trend = json.loads(trend_data)
        except Exception:
            pass

        return {
            "source_id": str(source.id),
            "name": source.name,
            "source_type": source.source_type,
            "status": health.status,
            "success_rate_24h": success_rate,
            "avg_response_time_ms": health.avg_response_time_ms,
            "last_success_at": health.last_success_at,
            "last_failure_at": health.last_failure_at,
            "consecutive_failures": health.consecutive_failures,
            "total_fetches_24h": health.total_fetches_24h,
            "last_error": health.last_error_message,
            "health_history": health_history,
            "response_time_trend": response_time_trend,
        }

    async def get_scheduler_status(self) -> dict:
        jobs_status = await scheduler_manager.get_jobs_status()

        running_jobs = []
        paused_jobs = []

        for job in jobs_status:
            is_paused = job.get("pending", False)
            job_info = {
                "job_id": job["job_id"],
                "source_id": job["job_id"].replace("collect_", "") if job["job_id"].startswith("collect_") else None,
                "name": job.get("name", job["job_id"]),
                "schedule": job.get("trigger", ""),
                "original_interval": job.get("original_interval", 0),
                "current_interval": job.get("original_interval", 0),
                "adaptive_multiplier": job.get("adaptive_multiplier", 1.0),
                "last_run": None,
                "next_run": job.get("next_run"),
                "status": "paused" if is_paused else "active",
                "success_count_24h": None,
                "failure_count_24h": None,
            }

            original = job.get("original_interval", 0)
            multiplier = job.get("adaptive_multiplier", 1.0)
            job_info["current_interval"] = int(original * multiplier)

            if is_paused:
                paused_jobs.append(job_info)
            else:
                running_jobs.append(job_info)

        return {
            "total_jobs": len(jobs_status),
            "running_jobs": running_jobs,
            "paused_jobs": paused_jobs,
            "all_jobs": running_jobs + paused_jobs,
        }

    async def get_sse_stats(self) -> dict:
        stats = event_router.get_stats()

        peak_today = stats.get("total_connections", 0)
        total_today = 0

        try:
            today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

            peak_result = await self.db.execute(
                select(func.count())
                .select_from(SSEConnectionModel)
                .where(SSEConnectionModel.connected_at >= today_start)
            )
            total_today = peak_result.scalar() or 0

            active_result = await self.db.execute(
                select(func.count())
                .select_from(SSEConnectionModel)
                .where(
                    and_(
                        SSEConnectionModel.connected_at >= today_start,
                        SSEConnectionModel.disconnected_at.is_(None),
                    )
                )
            )
            current_active = active_result.scalar() or 0
            peak_today = max(peak_today, current_active)
        except Exception as e:
            logger.warning(f"Failed to query SSE stats from DB: {e}")

        total_events = stats.get("total_events_pushed", 0)
        avg_events_per_minute = 0.0
        try:
            events_key = "dashboard:sse_events_counter"
            events_counter = await redis_get(events_key)
            if events_counter:
                avg_events_per_minute = float(events_counter) / 60.0
        except Exception:
            pass

        return {
            "total_connections": stats.get("total_connections", 0),
            "connections_by_channel": stats.get("connections_by_channel", {}),
            "peak_connections_24h": peak_today,
            "peak_connections_today": peak_today,
            "total_connections_today": total_today,
            "total_events_pushed": total_events,
            "average_events_per_minute": round(avg_events_per_minute, 2),
            "avg_connection_duration_seconds": stats.get("avg_connection_duration_seconds", 0),
        }

    # Fix: default tenant changed from the dubious "system" string to SYSTEM_TENANT_ID
    # (kept as str for SSE/Redis JSON serialization).
    async def collect_and_push_metrics(self, start_time: datetime, tenant_id: str = str(SYSTEM_TENANT_ID)) -> None:
        cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 0.5)
        mem = await asyncio.to_thread(psutil.virtual_memory)
        disk = await asyncio.to_thread(psutil.disk_usage, "/")
        net = await asyncio.to_thread(psutil.net_io_counters)

        current_metrics = {
            "cpu_usage_percent": cpu_usage,
            "memory_usage_percent": mem.percent,
            "disk_usage_percent": disk.percent,
            "network_bytes_sent": net.bytes_sent if net else 0,
            "network_bytes_recv": net.bytes_recv if net else 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        should_push = False
        incremental_data = {}

        if _last_metrics:
            cpu_diff = abs(current_metrics["cpu_usage_percent"] - _last_metrics.get("cpu_usage_percent", 0))
            mem_diff = abs(current_metrics["memory_usage_percent"] - _last_metrics.get("memory_usage_percent", 0))

            if cpu_diff >= METRIC_THRESHOLDS["cpu_change_percent"]:
                should_push = True
                incremental_data["cpu_usage_percent"] = current_metrics["cpu_usage_percent"]

            if mem_diff >= METRIC_THRESHOLDS["memory_change_percent"]:
                should_push = True
                incremental_data["memory_usage_percent"] = current_metrics["memory_usage_percent"]

            if current_metrics["network_bytes_sent"] != _last_metrics.get("network_bytes_sent", 0):
                should_push = True
                incremental_data["network_bytes_sent"] = current_metrics["network_bytes_sent"]

            if current_metrics["network_bytes_recv"] != _last_metrics.get("network_bytes_recv", 0):
                should_push = True
                incremental_data["network_bytes_recv"] = current_metrics["network_bytes_recv"]
        else:
            should_push = True
            incremental_data = current_metrics

        _last_metrics.update(current_metrics)

        try:
            metrics_json = json.dumps(current_metrics)
            await redis_set(RedisKeys.SYSTEM_METRICS, metrics_json, ex=10)
        except Exception as e:
            logger.warning(f"Failed to cache metrics in Redis: {e}")

        if should_push and incremental_data:
            try:
                await event_router.push_event(
                    category="dashboard",
                    event_type=SSEEventType.SYSTEM_METRIC_UPDATE,
                    data=incremental_data,
                    tenant_id=tenant_id,
                )
            except Exception as e:
                logger.warning(f"Failed to push SSE metric update: {e}")

    # Fix: use the SYSTEM_TENANT_ID constant instead of the hardcoded system-tenant UUID
    # literal (kept as str to preserve the original runtime semantics).
    async def archive_snapshot(self, tenant_id: str = str(SYSTEM_TENANT_ID)) -> None:
        cpu_usage = _last_metrics.get("cpu_usage_percent", 0)
        mem_total = await asyncio.to_thread(lambda: psutil.virtual_memory().total)
        mem_used = await asyncio.to_thread(lambda: psutil.virtual_memory().used)
        disk_used = await asyncio.to_thread(lambda: psutil.disk_usage("/").used)
        disk_total = await asyncio.to_thread(lambda: psutil.disk_usage("/").total)

        stats = event_router.get_stats()
        sse_connections = stats.get("total_connections", 0)
        events_hour = stats.get("total_events_pushed", 0)

        healthy_count = 0
        degraded_count = 0
        down_count = 0
        try:
            health_stmt = select(SourceHealth.status, func.count()).group_by(SourceHealth.status)
            health_result = await self.db.execute(health_stmt)
            for status, count in health_result.all():
                if status == "healthy":
                    healthy_count = count
                elif status == "degraded":
                    degraded_count = count
                elif status == "down":
                    down_count = count
        except Exception as e:
            logger.warning(f"Failed to query source health for snapshot: {e}")

        jobs_status = await scheduler_manager.get_jobs_status()
        active_jobs = len([j for j in jobs_status if not j.get("pending", False)])

        snapshot = DashboardSnapshot(
            tenant_id=tenant_id,
            cpu_usage_percent=cpu_usage,
            memory_used_mb=int(mem_used / (1024 * 1024)),
            memory_total_mb=int(mem_total / (1024 * 1024)),
            disk_used_gb=round(disk_used / (1024**3), 2),
            disk_total_gb=round(disk_total / (1024**3), 2),
            active_sse_connections=sse_connections,
            events_pushed_hour=events_hour,
            healthy_sources=healthy_count,
            degraded_sources=degraded_count,
            down_sources=down_count,
            scheduler_jobs_active=active_jobs,
        )

        self.db.add(snapshot)
        await self.db.commit()


# Fix: default tenant changed from the dubious "system" string to SYSTEM_TENANT_ID.
# This value flows into SSE/Redis json.dumps (needs str) and is stored as the snapshot
# tenant_id, hence str(constant).
async def start_metrics_collection(start_time: datetime, tenant_id: str = str(SYSTEM_TENANT_ID)) -> asyncio.Task:
    async def _periodic_loop():
        db_session_factory = None
        from app.db.session import async_session_factory

        db_session_factory = async_session_factory

        collect_interval = 30
        archive_interval = 300
        last_archive_time = time.monotonic()

        while True:
            try:
                await asyncio.sleep(collect_interval)

                async with db_session_factory() as session:
                    redis_client = None
                    try:
                        from app.core.redis import get_redis_client

                        redis_client = await get_redis_client()
                    except Exception:
                        pass

                    service = DashboardService(session, redis_client or None)
                    await service.collect_and_push_metrics(start_time, tenant_id)

                    now = time.monotonic()
                    if now - last_archive_time >= archive_interval:
                        try:
                            await service.archive_snapshot(tenant_id)
                            last_archive_time = now
                        except Exception as e:
                            logger.warning(f"Dashboard snapshot archive failed: {e}")

            except asyncio.CancelledError:
                logger.info("Dashboard metrics collection cancelled")
                break
            except Exception as e:
                logger.error(f"Dashboard metrics collection error: {e}")
                await asyncio.sleep(5)

    global _metrics_collection_task
    _metrics_collection_task = asyncio.create_task(_periodic_loop())
    logger.info("Dashboard metrics collection started (interval=30s, archive=5min)")
    return _metrics_collection_task


def stop_metrics_collection():
    global _metrics_collection_task
    if _metrics_collection_task and not _metrics_collection_task.done():
        _metrics_collection_task.cancel()
        _metrics_collection_task = None
        logger.info("Dashboard metrics collection stopped")
