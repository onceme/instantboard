from datetime import datetime

from pydantic import BaseModel, Field


class CpuMemoryInfo(BaseModel):
    cpu_usage_percent: float | None = None
    cpu_count: int | None = None
    memory_total_mb: int | None = None
    memory_used_mb: int | None = None
    memory_usage_percent: float | None = None


class DiskInfo(BaseModel):
    disk_total_gb: float | None = None
    disk_used_gb: float | None = None
    disk_usage_percent: float | None = None
    # I/O rates (MB/s) diff-sampled from psutil.disk_io_counters() by
    # services/dashboard.py sample_disk_rates(); 0 when psutil is unavailable.
    disk_read_mbps: float | None = None
    disk_write_mbps: float | None = None
    read_bytes: int | None = None
    write_bytes: int | None = None


class NetworkInfo(BaseModel):
    bytes_sent: int | None = None
    bytes_recv: int | None = None
    packets_sent: int | None = None
    packets_recv: int | None = None
    network_in_kbps: float | None = None
    network_out_kbps: float | None = None


class DatabaseStatus(BaseModel):
    postgres_connections: int | None = None
    postgres_active_queries: int | None = None
    redis_connected: bool | None = None
    redis_memory_used_mb: float | None = None


class SystemAlert(BaseModel):
    code: str
    message: str
    triggered_at: str


class ApiRequestStats(BaseModel):
    # Requests/s over a sliding 60s window (see DashboardService.get_api_request_stats).
    qps: float = 0.0
    # Average response time in ms over the same 60s window.
    avg_response_ms: float = 0.0
    # 4xx / 5xx share of windowed requests, 0..1 ratios.
    error_rate_4xx: float = 0.0
    error_rate_5xx: float = 0.0
    # Cumulative requests since the Redis totals key was created.
    requests_total: int = 0


class SystemInfoResponse(BaseModel):
    version: str = Field(default="1.0.0")
    uptime_seconds: int
    environment: str
    python_version: str
    # False when psutil is missing and system-level metrics degrade to None.
    psutil_available: bool = True
    # Currently active threshold alerts (empty when all metrics are within limits).
    alerts: list[SystemAlert] = Field(default_factory=list)
    cpu: CpuMemoryInfo | None = None
    memory: CpuMemoryInfo | None = None
    disk: DiskInfo | None = None
    network: NetworkInfo | None = None
    database: DatabaseStatus | None = None
    # API request stats; all zeros when the middleware has not written anything yet.
    api: ApiRequestStats = Field(default_factory=ApiRequestStats)
    cpu_count: int | None = None
    cpu_usage_percent: float | None = None
    memory_total_mb: int | None = None
    memory_used_mb: int | None = None
    disk_total_gb: float | None = None
    disk_used_gb: float | None = None
    # Disk I/O rates (MB/s) — same contract as the nested disk group above.
    disk_read_mbps: float | None = None
    disk_write_mbps: float | None = None
    network_in_kbps: float | None = None
    network_out_kbps: float | None = None


class ServiceHealthResponse(BaseModel):
    service: str
    status: str
    response_time_ms: int | None = None
    connection_count: int | None = None
    details: dict | None = None


class DataSourceHealthDetail(BaseModel):
    id: str
    name: str
    source_type: str | None = None
    category_id: str | None = None
    status: str
    success_rate_24h: float | None = None
    avg_response_time_ms: int | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int | None = None
    total_fetches_24h: int | None = None
    last_error: str | None = None


class DataSourceHealthSummary(BaseModel):
    total_sources: int
    healthy: int
    degraded: int
    down: int


class DataSourceHealthResponse(BaseModel):
    total_sources: int
    healthy: int
    degraded: int
    down: int
    sources: list[DataSourceHealthDetail]


class DataSourceHealthDetailResponse(BaseModel):
    source_id: str
    name: str
    source_type: str | None = None
    status: str
    success_rate_24h: float | None = None
    avg_response_time_ms: int | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int | None = None
    total_fetches_24h: int | None = None
    last_error: str | None = None
    health_history: list[dict] | None = None
    response_time_trend: list[dict] | None = None


class SchedulerJobInfo(BaseModel):
    job_id: str
    source_id: str | None = None
    name: str
    schedule: str | None = None
    original_interval: int | None = None
    current_interval: int | None = None
    adaptive_multiplier: float | None = None
    last_run: str | None = None
    next_run: str | None = None
    status: str
    success_count_24h: int | None = None
    failure_count_24h: int | None = None


class SchedulerStatusResponse(BaseModel):
    total_jobs: int
    running_jobs: list[SchedulerJobInfo]
    paused_jobs: list[SchedulerJobInfo]
    all_jobs: list[SchedulerJobInfo]
    # Job counts as plain integers. In dev (embedded scheduler) they mirror the
    # list lengths above; in prod the per-job list is empty (jobs live in the
    # worker container) and the counts come from the worker heartbeat instead.
    running_jobs_count: int = 0
    paused_jobs_count: int = 0
    last_heartbeat: str | None = None


class SSEStatsResponse(BaseModel):
    total_connections: int
    connections_by_channel: dict[str, int]
    peak_connections_24h: int
    peak_connections_today: int | None = None
    total_connections_today: int | None = None
    total_events_pushed: int | None = None
    average_events_per_minute: float | None = None
    avg_connection_duration_seconds: float | None = None


class BusinessCategoryCount(BaseModel):
    category_name: str
    count: int


class BusinessMetricsResponse(BaseModel):
    """GET /api/v1/dashboard/business-metrics (admin only, dashboard-tab.md §3.5).

    All counters are system-wide (all tenants); any unavailable sub-metric
    degrades to 0 / empty list rather than erroring the whole response.
    """

    # Distinct user_id that opened an SSE connection within the last 24 hours.
    active_users_24h: int = 0
    # Items with created_at >= today 00:00 UTC.
    items_today: int = 0
    # Items grouped by category (name JOIN), sorted descending by count.
    category_distribution: list[BusinessCategoryCount] = Field(default_factory=list)
    # Total rows in watchlist_items.
    watchlist_total: int = 0
    # SSE push events counted over the sliding 60-minute window ending now.
    events_pushed_1h: int = 0


class SystemMetricUpdate(BaseModel):
    cpu_usage_percent: float | None = None
    memory_usage_percent: float | None = None
    disk_usage_percent: float | None = None
    network_in_kbps: float | None = None
    network_out_kbps: float | None = None
    network_bytes_sent: int | None = None
    network_bytes_recv: int | None = None
    # Disk I/O rates (MB/s), pushed on any change like the network rates.
    disk_read_mbps: float | None = None
    disk_write_mbps: float | None = None
    timestamp: str | None = None
