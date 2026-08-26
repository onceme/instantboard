import asyncio
import json
import logging
import platform
import time
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

# Fix: import SYSTEM_TENANT_ID directly from core.constants (single source of truth, a
# UUID constant). It used to be re-exported indirectly via source.py, and hardcoded
# literals were scattered across call sites.
from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys, redis_get, redis_set
from app.core.sse_router import SSEEventType, event_router
from app.models.category import Category
from app.models.dashboard import DashboardSnapshot
from app.models.item import Item
from app.models.source import Source, SourceHealth
from app.models.sse import SSEConnection as SSEConnectionModel
from app.models.watchlist import WatchlistItem
from app.scheduler.manager import scheduler_manager
from app.services.source import SourceService

logger = logging.getLogger(__name__)

# Graceful degradation: psutil is optional at runtime. When it is missing every
# system-level metric (CPU/memory/disk/network) reports None/0 and the
# `psutil_available` flag flips to False, while DB/Redis/SSE metrics keep working.
# Contract: docs/dev-guide/design/dashboard-tab.md §5 "psutil 不可用".
try:
    import psutil
except ImportError:  # pragma: no cover - environment dependent
    psutil = None
    logger.warning("psutil is not installed: system metrics (CPU/memory/disk/network) are disabled")

METRIC_THRESHOLDS = {
    "cpu_change_percent": 5.0,
    "memory_change_percent": 5.0,
}

# Threshold alert contract (dashboard-tab.md §5). Alert codes:
#   redis_memory_high      -> used_memory / maxmemory > 0.8 (skipped when maxmemory unset/0)
#   db_pool_exhausted      -> checked_out connections reached the pool size
#   sse_connections_high   -> active SSE connections in THIS process > 1000
#                             (multi-process caveat: event_router is an in-process
#                             registry, so under multi-worker deployments each api
#                             process only sees its own share of connections)
#   cpu_high               -> CPU usage > 90%
ALERT_THRESHOLDS = {
    "redis_memory_ratio": 0.8,
    "sse_connection_count": 1000,
    "cpu_usage_percent": 90.0,
}

# Load-adaptive collection: after HIGH_CPU_STREAK_THRESHOLD consecutive samples
# above the cpu_high threshold the collection loop slows from
# COLLECT_INTERVAL_SECONDS to COLLECT_INTERVAL_DEGRADED_SECONDS; a single sample
# at or below the threshold restores the normal interval (dashboard-tab.md §5
# "系统负载高自动降频").
COLLECT_INTERVAL_SECONDS = 30
COLLECT_INTERVAL_DEGRADED_SECONDS = 60
HIGH_CPU_STREAK_THRESHOLD = 3

# Snapshot retention (dashboard-tab.md §3.9.3): dashboard_snapshots rows older
# than SNAPSHOT_RETENTION_DAYS are deleted once per day, piggy-backed on the
# metrics collection loop. The retention window is overridable via the
# DASHBOARD_SNAPSHOT_RETENTION_DAYS env var (see app/config.py); failures are
# logged and never interrupt metrics collection.
SNAPSHOT_RETENTION_DAYS = settings.dashboard_snapshot_retention_days
SNAPSHOT_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60

# Worker heartbeat contract (writer: app/scheduler/worker.py heartbeat_loop). In
# prod (SCHEDULER_ENABLED=false in the api process) the worker publishes
# scheduler:worker:heartbeat to Redis every 15s with a 45s TTL. A heartbeat
# younger than the TTL (= 3x the write interval) means the worker is alive.
# Contract: docs/dev-guide/design/infrastructure.md §3.5 "Worker 心跳机制".
WORKER_HEARTBEAT_FRESH_SECONDS = RedisKeys.WORKER_HEARTBEAT_TTL


def worker_heartbeat_age_seconds(payload: dict | None) -> float | None:
    """Seconds elapsed since the heartbeat timestamp, or None when missing/invalid."""
    if not payload:
        return None
    try:
        heartbeat_time = datetime.fromisoformat(payload.get("timestamp"))
    except (TypeError, ValueError):
        return None
    if heartbeat_time.tzinfo is None:
        heartbeat_time = heartbeat_time.replace(tzinfo=UTC)
    return (datetime.now(UTC) - heartbeat_time).total_seconds()


def worker_heartbeat_is_fresh(payload: dict | None) -> bool:
    age = worker_heartbeat_age_seconds(payload)
    return age is not None and age < WORKER_HEARTBEAT_FRESH_SECONDS


_metrics_collection_task: asyncio.Task | None = None
_last_metrics: dict = {}

# Previous net_io_counters() sample (cumulative bytes + monotonic timestamp) used to
# compute network transfer rates. The frontend contract expects rates
# (network_in_kbps / network_out_kbps, KB/s), not the cumulative byte counters that
# psutil exposes, so every sample diffs against this state. Module-level because both
# get_system_info() and collect_and_push_metrics() need the same series.
_last_net_sample: dict | None = None

# Previous disk_io_counters() sample (cumulative read/write bytes + monotonic
# timestamp) used to compute disk I/O rates. Mirrors _last_net_sample: the
# frontend contract expects rates (disk_read_mbps / disk_write_mbps, MB/s), not
# the cumulative byte counters psutil exposes, so every sample diffs against
# this state. Module-level because both get_system_info() and
# collect_and_push_metrics() need the same series.
_last_disk_sample: dict | None = None

# Active threshold alerts, keyed by alert code. Each value is
# {code, message, triggered_at}; triggered_at is pinned on first trigger and the
# alert is removed again once the condition recovers (dashboard-tab.md §5).
# Module-level because collect_and_push_metrics() runs in a fresh DashboardService
# per cycle while GET /dashboard/system must read the same state on demand.
_active_alerts: dict[str, dict] = {}
# (code, message) signature of the alert list at the previous evaluation, used to
# push the alerts field over SSE only when it actually changed.
_alerts_signature: tuple = ()

# Load-adaptive loop state (see COLLECT_INTERVAL_* above).
_cpu_high_streak: int = 0
_collection_degraded: bool = False


def get_active_alerts() -> list[dict]:
    """Snapshot of the currently active alerts, oldest first."""
    return [dict(alert) for alert in sorted(_active_alerts.values(), key=lambda a: a["triggered_at"])]


def _set_alert(code: str, message: str) -> None:
    existing = _active_alerts.get(code)
    if existing is None:
        _active_alerts[code] = {
            "code": code,
            "message": message,
            "triggered_at": datetime.now(UTC).isoformat(),
        }
        logger.warning(f"Dashboard alert triggered: {code}: {message}")
    else:
        # Already active: keep the original triggered_at (dedupe), refresh the message.
        existing["message"] = message


def _clear_alert(code: str) -> None:
    if code in _active_alerts:
        del _active_alerts[code]
        logger.info(f"Dashboard alert cleared: {code}")


def get_collect_interval() -> int:
    """Current collection loop interval (degraded to 60s under sustained high CPU)."""
    return COLLECT_INTERVAL_DEGRADED_SECONDS if _collection_degraded else COLLECT_INTERVAL_SECONDS


def snapshot_cleanup_due(
    last_cleanup_time: float | None,
    now: float,
    interval: float = SNAPSHOT_CLEANUP_INTERVAL_SECONDS,
) -> bool:
    """24h throttle check for the snapshot retention run (dashboard-tab.md §3.9.3).

    True when cleanup has never run in this process (first loop iteration runs it
    right away) or at least `interval` seconds elapsed since the last successful run.
    """
    return last_cleanup_time is None or (now - last_cleanup_time) >= interval


async def cleanup_old_snapshots(db: AsyncSession, retention_days: int | None = None) -> int:
    """Delete dashboard_snapshots rows older than the retention window.

    Filters on the snapshot `timestamp` column (the table has no created_at).
    Returns the number of deleted rows. Raises on DB errors — the collection loop
    treats that as non-fatal (log, keep collecting) and retries on the next cycle.
    """
    days = SNAPSHOT_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = delete(DashboardSnapshot).where(DashboardSnapshot.timestamp < cutoff)
    result = await db.execute(stmt)
    await db.commit()
    deleted = result.rowcount
    return deleted if isinstance(deleted, int) and deleted > 0 else 0


def _metric_changed(current: float | None, previous: float | None, threshold: float) -> bool:
    """None-aware incremental-push check: a metric appearing or disappearing
    (psutil degradation transitions) counts as changed, numeric pairs compare
    against the push threshold."""
    if current is None and previous is None:
        return False
    if current is None or previous is None:
        return True
    return abs(current - previous) >= threshold


def sample_network_rates() -> dict:
    """Sample net_io_counters() and compute transfer rates (KB/s) vs the previous sample.

    Returns both the rate fields the frontend renders (network_in_kbps /
    network_out_kbps) and the cumulative counters (kept for backward compatibility).
    The first sample has no previous data and reports 0 rates; counter resets
    (reboot/overflow) clamp negative deltas to 0. Without psutil the rates and
    counters degrade to 0 and no sample state is kept.
    """
    global _last_net_sample

    if psutil is None:
        return {
            "network_in_kbps": 0.0,
            "network_out_kbps": 0.0,
            "network_bytes_recv": 0,
            "network_bytes_sent": 0,
        }

    net = psutil.net_io_counters()
    now = time.monotonic()

    data = {
        "network_in_kbps": 0.0,
        "network_out_kbps": 0.0,
        "network_bytes_recv": net.bytes_recv if net else 0,
        "network_bytes_sent": net.bytes_sent if net else 0,
    }

    if net and _last_net_sample:
        elapsed = now - _last_net_sample["timestamp"]
        if elapsed > 0:
            data["network_in_kbps"] = round(max(net.bytes_recv - _last_net_sample["bytes_recv"], 0) / elapsed / 1024, 2)
            data["network_out_kbps"] = round(
                max(net.bytes_sent - _last_net_sample["bytes_sent"], 0) / elapsed / 1024, 2
            )

    if net:
        _last_net_sample = {
            "bytes_recv": net.bytes_recv,
            "bytes_sent": net.bytes_sent,
            "timestamp": now,
        }

    return data


def sample_disk_rates() -> dict:
    """Sample disk_io_counters() and compute I/O rates (MB/s) vs the previous sample.

    Returns both the rate fields the frontend renders (disk_read_mbps /
    disk_write_mbps) and the cumulative counters. The first sample has no
    previous data and reports 0 rates; counter resets (reboot/overflow) clamp
    negative deltas to 0. Degradation (same style as sample_network_rates,
    dashboard-tab.md §5): without psutil, or when disk_io_counters is
    unavailable (the attribute is missing in restricted containers, returns
    None or raises), the rates degrade to 0, no sample state is kept and no
    exception propagates.
    """
    global _last_disk_sample

    if psutil is None or not callable(getattr(psutil, "disk_io_counters", None)):
        return {
            "disk_read_mbps": 0.0,
            "disk_write_mbps": 0.0,
            "disk_read_bytes": 0,
            "disk_write_bytes": 0,
        }

    try:
        disk = psutil.disk_io_counters()
    except Exception as e:
        # Restricted environments (containers without /proc/diskstats) may raise
        # instead of returning counters; degrade silently.
        logger.debug(f"disk_io_counters() unavailable: {e}")
        disk = None

    now = time.monotonic()

    data = {
        "disk_read_mbps": 0.0,
        "disk_write_mbps": 0.0,
        "disk_read_bytes": disk.read_bytes if disk else 0,
        "disk_write_bytes": disk.write_bytes if disk else 0,
    }

    if disk and _last_disk_sample:
        elapsed = now - _last_disk_sample["timestamp"]
        if elapsed > 0:
            data["disk_read_mbps"] = round(
                max(disk.read_bytes - _last_disk_sample["read_bytes"], 0) / elapsed / (1024 * 1024), 2
            )
            data["disk_write_mbps"] = round(
                max(disk.write_bytes - _last_disk_sample["write_bytes"], 0) / elapsed / (1024 * 1024), 2
            )

    if disk:
        _last_disk_sample = {
            "read_bytes": disk.read_bytes,
            "write_bytes": disk.write_bytes,
            "timestamp": now,
        }

    return data


class DashboardService:
    def __init__(self, db: AsyncSession, redis: Redis | None):
        self.db = db
        self.redis = redis

    async def _read_worker_heartbeat(self) -> tuple[dict | None, str | None]:
        """Read the worker heartbeat written by app/scheduler/worker.py.

        Returns a (payload, error) tuple:
        - (dict, None)   -> heartbeat key present and parseable.
        - (None, None)   -> key absent (worker never started / TTL expired).
        - (None, str)    -> Redis unavailable or payload malformed; the string
                            describes the failure so callers can degrade gracefully.
        """
        if self.redis is None:
            return None, "Redis client not available"
        try:
            raw = await self.redis.get(RedisKeys.worker_heartbeat_key())
        except Exception as e:
            logger.warning(f"Failed to read worker heartbeat: {e}")
            return None, f"redis unavailable: {e}"
        if raw is None:
            return None, None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Worker heartbeat payload is not valid JSON: {e}")
            return None, f"invalid heartbeat payload: {e}"
        if not isinstance(payload, dict):
            return None, "invalid heartbeat payload: not a JSON object"
        return payload, None

    async def get_api_request_stats(self) -> dict:
        """API request statistics for the `api` group of GET /dashboard/system.

        Reads the minute buckets RequestLoggingMiddleware writes
        (core/middleware.py _record_request_stats) and derives them over a
        sliding 60s window ending now: the previous minute's bucket overlaps
        the window by (60 - seconds elapsed in the current minute) seconds, so
        its count/latency/error totals are scaled by that fraction before being
        added to the full current (partial) minute. Fields:
          qps               windowed request count / 60
          avg_response_ms   windowed latency sum / windowed request count
          error_rate_4xx    windowed 4xx count / windowed request count (0..1)
          error_rate_5xx    same for 5xx (0..1)
          requests_total    cumulative since the Redis totals key was created

        Degrades to all zeros when Redis is unavailable or the middleware has
        not written anything yet (fresh start); never raises.
        """
        zeros = {
            "qps": 0.0,
            "avg_response_ms": 0.0,
            "error_rate_4xx": 0.0,
            "error_rate_5xx": 0.0,
            "requests_total": 0,
        }
        if self.redis is None:
            return zeros

        try:
            now = int(time.time())
            minute = now // 60
            pipe = self.redis.pipeline(transaction=False)
            pipe.hgetall(RedisKeys.api_metrics_minute_key(minute - 1))
            pipe.hgetall(RedisKeys.api_metrics_minute_key(minute))
            pipe.hget(RedisKeys.API_METRICS_TOTALS, "requests_total")
            prev_bucket, cur_bucket, requests_total_raw = await pipe.execute()
        except Exception as e:
            logger.warning(f"Failed to read API request metrics from Redis: {e}")
            return zeros

        def _num(bucket: dict | None, field: str) -> float:
            try:
                return float((bucket or {}).get(field, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        # Fraction of the previous minute's bucket that lies inside the window.
        prev_fraction = (60 - now % 60) / 60

        window_count = _num(prev_bucket, "count") * prev_fraction + _num(cur_bucket, "count")

        try:
            requests_total = int(requests_total_raw or 0)
        except (TypeError, ValueError):
            requests_total = 0

        if window_count <= 0:
            return {**zeros, "requests_total": requests_total}

        window_latency = _num(prev_bucket, "latency_ms") * prev_fraction + _num(cur_bucket, "latency_ms")
        window_4xx = _num(prev_bucket, "count_4xx") * prev_fraction + _num(cur_bucket, "count_4xx")
        window_5xx = _num(prev_bucket, "count_5xx") * prev_fraction + _num(cur_bucket, "count_5xx")

        return {
            "qps": round(window_count / 60, 2),
            "avg_response_ms": round(window_latency / window_count, 2),
            "error_rate_4xx": round(window_4xx / window_count, 4),
            "error_rate_5xx": round(window_5xx / window_count, 4),
            "requests_total": requests_total,
        }

    async def get_system_info(self, start_time: datetime) -> dict:
        uptime = int((datetime.now(UTC) - start_time).total_seconds())

        if psutil is None:
            # Degraded mode: system-level metrics report None, everything else
            # (DB/Redis/SSE below) is collected as usual.
            cpu_usage = None
            cpu_count = None
            memory_total_mb = None
            memory_used_mb = None
            disk_total_gb = None
            disk_used_gb = None
            net_data = sample_network_rates()
            disk_io = sample_disk_rates()
        else:
            cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 0.5)
            cpu_count = psutil.cpu_count()
            mem = await asyncio.to_thread(psutil.virtual_memory)
            disk = await asyncio.to_thread(psutil.disk_usage, "/")
            memory_total_mb = int(mem.total / (1024 * 1024))
            memory_used_mb = int(mem.used / (1024 * 1024))
            disk_total_gb = round(disk.total / (1024**3), 1)
            disk_used_gb = round(disk.used / (1024**3), 1)
            net_data = await asyncio.to_thread(sample_network_rates)
            disk_io = await asyncio.to_thread(sample_disk_rates)

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
            "psutil_available": psutil is not None,
            # Threshold alerts evaluated by the 30s collection loop; empty when
            # everything is within limits (dashboard-tab.md §5).
            "alerts": get_active_alerts(),
            "cpu_count": cpu_count,
            "cpu_usage_percent": cpu_usage,
            "memory_total_mb": memory_total_mb,
            "memory_used_mb": memory_used_mb,
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            # Disk I/O rates (MB/s) sampled by sample_disk_rates(); the capacity
            # fields above are kept unchanged.
            "disk_read_mbps": disk_io["disk_read_mbps"],
            "disk_write_mbps": disk_io["disk_write_mbps"],
            "disk": {
                "disk_total_gb": disk_total_gb,
                "disk_used_gb": disk_used_gb,
                "disk_read_mbps": disk_io["disk_read_mbps"],
                "disk_write_mbps": disk_io["disk_write_mbps"],
                "read_bytes": disk_io["disk_read_bytes"],
                "write_bytes": disk_io["disk_write_bytes"],
            },
            # Frontend renders the flat rate fields (KB/s); the nested group keeps the
            # cumulative counters for backward compatibility.
            "network_in_kbps": net_data["network_in_kbps"],
            "network_out_kbps": net_data["network_out_kbps"],
            "network": {
                "network_in_kbps": net_data["network_in_kbps"],
                "network_out_kbps": net_data["network_out_kbps"],
                "bytes_sent": net_data["network_bytes_sent"],
                "bytes_recv": net_data["network_bytes_recv"],
            },
            "database": {
                "postgres_connections": pg_connections,
                "postgres_active_queries": pg_active_queries,
                "redis_connected": redis_connected,
                "redis_memory_used_mb": round(redis_memory_mb, 1) if redis_memory_mb else None,
            },
            # API request stats (QPS / latency / error rates) collected by
            # RequestLoggingMiddleware; all-zero when nothing was written yet
            # or Redis is unavailable (dashboard-tab.md §3.1.1).
            "api": await self.get_api_request_stats(),
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

        if settings.scheduler_enabled:
            # Dev / embedded mode: the scheduler lives inside this api process,
            # so query the in-process singleton directly (legacy behaviour).
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
        else:
            # Prod mode: the scheduler runs in a separate worker container and this
            # process's scheduler singleton is stopped. Health comes from the worker
            # heartbeat stored in Redis (written by app/scheduler/worker.py).
            payload, read_error = await self._read_worker_heartbeat()
            is_fresh = worker_heartbeat_is_fresh(payload)

            if is_fresh:
                age = worker_heartbeat_age_seconds(payload)
                details = {
                    "mode": "worker_heartbeat",
                    "last_heartbeat": payload.get("timestamp"),
                    "last_heartbeat_age_seconds": round(age, 1) if age is not None else None,
                    "is_running": bool(payload.get("scheduler_running")),
                    "event_listener_subscribed": payload.get("event_listener_subscribed"),
                    "worker_pid": payload.get("pid"),
                    "total_jobs": payload.get("jobs_total", 0),
                    "running_jobs": payload.get("jobs_running", 0),
                    "paused_jobs": payload.get("jobs_paused", 0),
                }
                status = "healthy"
            else:
                status = "down"
                details = {"mode": "worker_heartbeat", "is_running": False}
                if read_error is not None:
                    # Redis itself is unavailable: the heartbeat cannot be read at all.
                    # Mirror the redis service state instead of blaming the worker.
                    details["error"] = read_error
                    details["last_heartbeat"] = None
                elif payload is not None:
                    details["error"] = (
                        f"worker heartbeat stale (age={round(worker_heartbeat_age_seconds(payload) or 0, 1)}s, "
                        f"threshold={WORKER_HEARTBEAT_FRESH_SECONDS}s)"
                    )
                    details["last_heartbeat"] = payload.get("timestamp")
                else:
                    details["error"] = "worker heartbeat missing"
                    details["last_heartbeat"] = None

            services.append(
                {
                    "service": "scheduler",
                    "status": status,
                    "response_time_ms": 0,
                    "connection_count": None,
                    "details": details,
                }
            )

        # SSE health = event_router availability, nothing else. The router is an
        # in-process singleton: if get_stats() returns, the service is up. The
        # connection count is a load metric, not a health metric — zero active
        # connections is a normal state whenever nobody has the real-time board
        # open, and must never mark the service degraded. (The previous rule
        # "0 connections + scheduler running -> degraded" also false-fired on
        # dashboard load, where this health request races the SSE handshake.)
        # Connection counts therefore stay informational, reported in details only.
        try:
            sse_stats = event_router.get_stats()
            sse_active_count = sse_stats.get("total_connections", 0)
            services.append(
                {
                    "service": "sse",
                    "status": "healthy",
                    "response_time_ms": 0,
                    "connection_count": sse_active_count,
                    "details": {
                        "active_connections": sse_active_count,
                        "total_events_pushed": sse_stats.get("total_events_pushed", 0),
                        "avg_connection_duration_seconds": sse_stats.get("avg_connection_duration_seconds", 0),
                    },
                }
            )
        except Exception as e:
            logger.warning(f"Failed to read SSE router stats: {e}")
            services.append(
                {
                    "service": "sse",
                    "status": "down",
                    "response_time_ms": 0,
                    "connection_count": 0,
                    "details": {"error": str(e)},
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
        # Security fix: tenant scoping was missing (the query only filtered on source_id),
        # letting a tenant's admin inspect sources of any other tenant. Apply the same
        # rule as the list endpoint: the source must belong to the requesting tenant or
        # to the system tenant (shared sources). Anything else falls through to the
        # existing not-found path (None -> 404 at the API layer).
        stmt = (
            select(Source, SourceHealth)
            .join(SourceHealth, Source.id == SourceHealth.source_id)
            .where(
                Source.id == source_id,
                or_(
                    Source.tenant_id == tenant_id,
                    Source.tenant_id == SYSTEM_TENANT_ID,
                ),
            )
        )
        result = await self.db.execute(stmt)
        row = result.one_or_none()

        if row is None:
            source_stmt = select(Source).where(
                Source.id == source_id,
                or_(
                    Source.tenant_id == tenant_id,
                    Source.tenant_id == SYSTEM_TENANT_ID,
                ),
            )
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
        if not settings.scheduler_enabled:
            # Prod mode: the scheduler lives in the worker container, so this
            # process has no APScheduler job list. Report counts from the worker
            # heartbeat instead; the per-job list is empty cross-process.
            payload, _read_error = await self._read_worker_heartbeat()
            if worker_heartbeat_is_fresh(payload):
                total = payload.get("jobs_total", 0)
                running_count = payload.get("jobs_running", 0)
                paused_count = payload.get("jobs_paused", 0)
            else:
                # Stale/missing heartbeat: the worker is down, so no live jobs.
                total = running_count = paused_count = 0
            return {
                "total_jobs": total,
                "running_jobs": [],
                "paused_jobs": [],
                "all_jobs": [],
                "running_jobs_count": running_count,
                "paused_jobs_count": paused_count,
                "last_heartbeat": (payload or {}).get("timestamp"),
            }

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
            "running_jobs_count": len(running_jobs),
            "paused_jobs_count": len(paused_jobs),
            "last_heartbeat": None,
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

    async def _events_pushed_window(self) -> int:
        """SSE push events emitted over the sliding 60-minute window ending now.

        Sums the per-minute buckets written by SSEEventRouter.push_event
        (core/sse_router.py::_record_push_event_count,
        dashboard:events_pushed:minute:{minute}). Degraded Redis (no client, or
        a read error) and missing/corrupt bucket values count as 0; never raises.
        """
        if self.redis is None:
            return 0
        try:
            now_minute = int(time.time()) // 60
            keys = [RedisKeys.events_pushed_minute_key(now_minute - offset) for offset in range(60)]
            values = await self.redis.mget(keys)
        except Exception as e:
            logger.warning(f"Failed to read SSE events-pushed buckets from Redis: {e}")
            return 0

        total = 0
        for value in values or []:
            if value is None:
                continue
            try:
                total += int(value)
            except (TypeError, ValueError):
                continue
        return total

    async def get_business_metrics(self) -> dict:
        """Five business metrics for GET /dashboard/business-metrics (admin only).

        Scope: **system-wide (all tenants)**, not per-tenant. Every dashboard
        endpoint is admin-only and reports the aggregate operational picture, so
        these counters deliberately omit tenant_id filters (per-tenant isolation
        belongs to the feed endpoints). contract: dashboard-tab.md §3.5.

        Metrics:
          active_users_24h       distinct user_id with an sse_connections row
                                 connected within the last 24 hours
          items_today            items.created_at >= today 00:00 UTC
          category_distribution  items grouped by category_id JOIN
                                 categories.name, [{"category_name", count}],
                                 sorted descending by count
          watchlist_total        row count of watchlist_items
          events_pushed_1h       sliding 60-minute sum of the SSE push-event
                                 minute buckets (see _events_pushed_window)

        The assembled payload is cached in Redis for BUSINESS_METRICS_TTL seconds
        to keep the JOIN/COUNT queries off the hot request path. Any single
        sub-query failing degrades that metric to 0 / empty list instead of
        failing the endpoint. Never raises for degraded Redis.
        """
        cache_key = RedisKeys.BUSINESS_METRICS
        try:
            cached = await redis_get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Failed to read business metrics cache: {e}")

        active_users_24h = 0
        items_today = 0
        category_distribution: list[dict] = []
        watchlist_total = 0

        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_ago = now - timedelta(hours=24)

        try:
            result = await self.db.execute(
                select(func.count(func.distinct(SSEConnectionModel.user_id))).where(
                    SSEConnectionModel.connected_at >= day_ago
                )
            )
            active_users_24h = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Failed to compute active users metric: {e}")

        try:
            result = await self.db.execute(select(func.count()).select_from(Item).where(Item.created_at >= day_start))
            items_today = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Failed to compute items-today metric: {e}")

        try:
            result = await self.db.execute(
                select(Category.name, func.count(Item.id))
                .select_from(Item)
                .join(Category, Item.category_id == Category.id)
                .group_by(Category.name)
                .order_by(func.count(Item.id).desc())
            )
            category_distribution = [{"category_name": name, "count": count} for name, count in result.all()]
        except Exception as e:
            logger.warning(f"Failed to compute category distribution metric: {e}")

        try:
            result = await self.db.execute(select(func.count()).select_from(WatchlistItem))
            watchlist_total = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Failed to compute watchlist-total metric: {e}")

        events_pushed_1h = await self._events_pushed_window()

        metrics = {
            "active_users_24h": active_users_24h,
            "items_today": items_today,
            "category_distribution": category_distribution,
            "watchlist_total": watchlist_total,
            "events_pushed_1h": events_pushed_1h,
        }

        try:
            await redis_set(cache_key, json.dumps(metrics), ex=RedisKeys.BUSINESS_METRICS_TTL)
        except Exception as e:
            logger.warning(f"Failed to cache business metrics: {e}")

        return metrics

    def _db_pool_utilization(self) -> tuple[int | None, int | None]:
        """Best-effort (checked_out, pool_size) snapshot of the DB connection pool.

        Prefers the live queue-pool API (pool.checkedout() / pool.size()); the
        status()-object path mirrors get_system_info() so mock sessions work too.
        Returns (None, None) when neither exposes usable ints and the
        db_pool_exhausted alert is then simply skipped.
        """
        try:
            pool = self.db.get_bind().pool
            checked_out: int | None = None
            pool_size: int | None = None
            if callable(getattr(pool, "checkedout", None)) and callable(getattr(pool, "size", None)):
                raw_checked_out, raw_pool_size = pool.checkedout(), pool.size()
                if isinstance(raw_checked_out, int) and isinstance(raw_pool_size, int):
                    return raw_checked_out, raw_pool_size
            pool_status = pool.status()
            raw_checked_out = pool_status.checked_out() if hasattr(pool_status, "checked_out") else None
            raw_pool_size = pool_status.size() if hasattr(pool_status, "size") else None
            if isinstance(raw_checked_out, int):
                checked_out = raw_checked_out
            if isinstance(raw_pool_size, int):
                pool_size = raw_pool_size
            return checked_out, pool_size
        except Exception as e:
            logger.warning(f"Failed to read DB connection pool status: {e}")
            return None, None

    async def _collect_alerts(self, cpu_usage: float | None) -> tuple[bool, list[dict]]:
        """Evaluate the threshold alerts against freshly collected metrics.

        Updates the module-level alert state (dedupe by code while active, removal
        on recovery) and returns (changed, alerts) where `changed` is True when the
        active alert list differs from the previous evaluation — the caller then
        ships the whole list in the incremental SSE payload.
        """
        global _alerts_signature

        # cpu_high (skipped when psutil is unavailable and cpu_usage is None).
        if isinstance(cpu_usage, int | float):
            if cpu_usage > ALERT_THRESHOLDS["cpu_usage_percent"]:
                message = f"CPU usage above {ALERT_THRESHOLDS['cpu_usage_percent']:.0f}% threshold"
                if _collection_degraded:
                    message += f" (collection interval degraded to {COLLECT_INTERVAL_DEGRADED_SECONDS}s)"
                _set_alert("cpu_high", message)
            else:
                _clear_alert("cpu_high")

        # redis_memory_high: used_memory / maxmemory > 0.8; skipped when maxmemory
        # is unset/0 (no limit configured).
        if self.redis is not None:
            try:
                info = await self.redis.info("memory")
                used_memory = float(info.get("used_memory") or 0)
                max_memory = float(info.get("maxmemory") or 0)
                if max_memory > 0:
                    if used_memory / max_memory > ALERT_THRESHOLDS["redis_memory_ratio"]:
                        _set_alert(
                            "redis_memory_high",
                            f"Redis memory usage above {ALERT_THRESHOLDS['redis_memory_ratio']:.0%} of maxmemory",
                        )
                    else:
                        _clear_alert("redis_memory_high")
            except Exception as e:
                logger.warning(f"Failed to evaluate Redis memory alert: {e}")

        # db_pool_exhausted: checked_out connections reached the pool size.
        checked_out, pool_size = self._db_pool_utilization()
        if checked_out is not None and pool_size is not None and pool_size > 0:
            if checked_out >= pool_size:
                _set_alert("db_pool_exhausted", "DB connection pool exhausted (checked_out reached pool size)")
            else:
                _clear_alert("db_pool_exhausted")

        # sse_connections_high: active connections registered in THIS process
        # (multi-process caveat: each api worker only counts its own registry).
        # The isinstance guard keeps non-numeric values (mocked routers in tests)
        # from raising on the comparison — the check is simply skipped then.
        try:
            sse_active = event_router.get_stats().get("total_connections", 0)
        except Exception as e:
            logger.warning(f"Failed to evaluate SSE connection alert: {e}")
            sse_active = None
        if isinstance(sse_active, int):
            if sse_active > ALERT_THRESHOLDS["sse_connection_count"]:
                _set_alert(
                    "sse_connections_high",
                    f"Active SSE connections above {ALERT_THRESHOLDS['sse_connection_count']}",
                )
            else:
                _clear_alert("sse_connections_high")

        alerts = get_active_alerts()
        signature = tuple((alert["code"], alert["message"]) for alert in alerts)
        changed = signature != _alerts_signature
        _alerts_signature = signature
        return changed, alerts

    # Fix: default tenant changed from the dubious "system" string to SYSTEM_TENANT_ID
    # (kept as str for SSE/Redis JSON serialization).
    async def collect_and_push_metrics(self, start_time: datetime, tenant_id: str = str(SYSTEM_TENANT_ID)) -> None:
        global _cpu_high_streak, _collection_degraded

        if psutil is None:
            # Degraded mode: psutil-backed metrics report None below; DB/Redis/SSE
            # alerts are still evaluated and pushed (dashboard-tab.md §5).
            cpu_usage = None
            memory_usage_percent = None
            disk_usage_percent = None
            net_data = sample_network_rates()
            disk_io = sample_disk_rates()
        else:
            cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 0.5)
            mem = await asyncio.to_thread(psutil.virtual_memory)
            disk = await asyncio.to_thread(psutil.disk_usage, "/")
            memory_usage_percent = mem.percent
            disk_usage_percent = disk.percent
            net_data = await asyncio.to_thread(sample_network_rates)
            disk_io = await asyncio.to_thread(sample_disk_rates)

        # Load-adaptive interval: three consecutive samples above the cpu_high
        # threshold degrade the loop to 60s; the first sample back at or below the
        # threshold restores 30s. Without psutil the state is left untouched.
        if isinstance(cpu_usage, int | float):
            if cpu_usage > ALERT_THRESHOLDS["cpu_usage_percent"]:
                _cpu_high_streak += 1
            else:
                _cpu_high_streak = 0
            if not _collection_degraded and _cpu_high_streak >= HIGH_CPU_STREAK_THRESHOLD:
                _collection_degraded = True
                logger.warning(
                    f"High CPU load for {_cpu_high_streak} consecutive samples: "
                    f"dashboard collection interval degraded to {COLLECT_INTERVAL_DEGRADED_SECONDS}s"
                )
            elif _collection_degraded and _cpu_high_streak == 0:
                _collection_degraded = False
                logger.info(
                    "CPU load recovered: dashboard collection interval restored to %ss", COLLECT_INTERVAL_SECONDS
                )

        alerts_changed, alerts = await self._collect_alerts(cpu_usage)

        current_metrics = {
            "cpu_usage_percent": cpu_usage,
            "memory_usage_percent": memory_usage_percent,
            "disk_usage_percent": disk_usage_percent,
            "network_in_kbps": net_data["network_in_kbps"],
            "network_out_kbps": net_data["network_out_kbps"],
            "network_bytes_sent": net_data["network_bytes_sent"],
            "network_bytes_recv": net_data["network_bytes_recv"],
            "disk_read_mbps": disk_io["disk_read_mbps"],
            "disk_write_mbps": disk_io["disk_write_mbps"],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        should_push = False
        incremental_data = {}

        if _last_metrics:
            # None-aware diffs: a metric that appears/disappears (psutil
            # degradation transitions) is pushed exactly like a value change.
            if _metric_changed(
                current_metrics["cpu_usage_percent"],
                _last_metrics.get("cpu_usage_percent"),
                METRIC_THRESHOLDS["cpu_change_percent"],
            ):
                should_push = True
                incremental_data["cpu_usage_percent"] = current_metrics["cpu_usage_percent"]

            if _metric_changed(
                current_metrics["memory_usage_percent"],
                _last_metrics.get("memory_usage_percent"),
                METRIC_THRESHOLDS["memory_change_percent"],
            ):
                should_push = True
                incremental_data["memory_usage_percent"] = current_metrics["memory_usage_percent"]

            # The frontend merges the SSE payload into its system-info state and renders
            # rates (network_in_kbps / network_out_kbps), so push rate fields, not the
            # cumulative byte counters it cannot display.
            if current_metrics["network_in_kbps"] != _last_metrics.get("network_in_kbps"):
                should_push = True
                incremental_data["network_in_kbps"] = current_metrics["network_in_kbps"]

            if current_metrics["network_out_kbps"] != _last_metrics.get("network_out_kbps"):
                should_push = True
                incremental_data["network_out_kbps"] = current_metrics["network_out_kbps"]

            # Disk I/O rates follow the network-rate rule, not the 5%
            # METRIC_THRESHOLDS one (which only applies to CPU/memory percents):
            # rates fluctuate around small values where a relative threshold would
            # either suppress real activity or push noise every cycle, so any
            # change is pushed and the 30s cadence keeps the volume bounded.
            if current_metrics["disk_read_mbps"] != _last_metrics.get("disk_read_mbps"):
                should_push = True
                incremental_data["disk_read_mbps"] = current_metrics["disk_read_mbps"]

            if current_metrics["disk_write_mbps"] != _last_metrics.get("disk_write_mbps"):
                should_push = True
                incremental_data["disk_write_mbps"] = current_metrics["disk_write_mbps"]
        else:
            should_push = True
            incremental_data = current_metrics

        # Ship the whole alert list whenever it changed (triggered, recovered or
        # re-messaged); unchanged alerts never ride along.
        if alerts_changed:
            should_push = True
            incremental_data["alerts"] = alerts

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
        if psutil is None:
            # Degraded mode: psutil-backed columns are nullable, store None.
            mem_total = None
            mem_used = None
            disk_used = None
            disk_total = None
        else:
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

        if settings.scheduler_enabled:
            jobs_status = await scheduler_manager.get_jobs_status()
            active_jobs = len([j for j in jobs_status if not j.get("pending", False)])
        else:
            # Prod mode: the in-process scheduler has no jobs (they live in the
            # worker container). Reading the local singleton would always store 0
            # and pollute the snapshot; use the worker heartbeat's running count.
            payload, _read_error = await self._read_worker_heartbeat()
            active_jobs = payload.get("jobs_running", 0) if worker_heartbeat_is_fresh(payload) else 0

        # Nullable columns: degraded mode stores None instead of crashing on int(None).
        snapshot = DashboardSnapshot(
            tenant_id=tenant_id,
            cpu_usage_percent=cpu_usage,
            memory_used_mb=int(mem_used / (1024 * 1024)) if mem_used is not None else None,
            memory_total_mb=int(mem_total / (1024 * 1024)) if mem_total is not None else None,
            disk_used_gb=round(disk_used / (1024**3), 2) if disk_used is not None else None,
            disk_total_gb=round(disk_total / (1024**3), 2) if disk_total is not None else None,
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

        archive_interval = 300
        last_archive_time = time.monotonic()
        # Snapshot retention throttle: None = never run, so the first loop
        # iteration purges stale rows immediately (dashboard-tab.md §3.9.3).
        last_cleanup_time: float | None = None

        while True:
            try:
                # Re-read every cycle: sustained high CPU degrades the interval
                # from 30s to 60s and recovery restores it (get_collect_interval).
                await asyncio.sleep(get_collect_interval())

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

                    if snapshot_cleanup_due(last_cleanup_time, now):
                        try:
                            deleted = await cleanup_old_snapshots(session)
                            last_cleanup_time = now
                            logger.info(
                                f"Dashboard snapshot cleanup: removed {deleted} rows older than {SNAPSHOT_RETENTION_DAYS} days"
                            )
                        except Exception as e:
                            # Non-fatal: retention failures must never interrupt the
                            # metrics collection loop (dashboard-tab.md §3.9.3).
                            logger.warning(f"Dashboard snapshot cleanup failed: {e}")

            except asyncio.CancelledError:
                logger.info("Dashboard metrics collection cancelled")
                break
            except Exception as e:
                logger.error(f"Dashboard metrics collection error: {e}")
                await asyncio.sleep(5)

    global _metrics_collection_task
    _metrics_collection_task = asyncio.create_task(_periodic_loop())
    logger.info("Dashboard metrics collection started (interval=30s, archive=5min, snapshot-cleanup=24h)")
    return _metrics_collection_task


def stop_metrics_collection():
    global _metrics_collection_task
    if _metrics_collection_task and not _metrics_collection_task.done():
        _metrics_collection_task.cancel()
        _metrics_collection_task = None
        logger.info("Dashboard metrics collection stopped")
