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


class NetworkInfo(BaseModel):
    bytes_sent: int | None = None
    bytes_recv: int | None = None
    packets_sent: int | None = None
    packets_recv: int | None = None


class DatabaseStatus(BaseModel):
    postgres_connections: int | None = None
    postgres_active_queries: int | None = None
    redis_connected: bool | None = None
    redis_memory_used_mb: float | None = None


class SystemInfoResponse(BaseModel):
    version: str = Field(default="1.0.0")
    uptime_seconds: int
    environment: str
    python_version: str
    cpu: CpuMemoryInfo | None = None
    memory: CpuMemoryInfo | None = None
    disk: DiskInfo | None = None
    network: NetworkInfo | None = None
    database: DatabaseStatus | None = None
    cpu_count: int | None = None
    cpu_usage_percent: float | None = None
    memory_total_mb: int | None = None
    memory_used_mb: int | None = None
    disk_total_gb: float | None = None
    disk_used_gb: float | None = None


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


class SSEStatsResponse(BaseModel):
    total_connections: int
    connections_by_channel: dict[str, int]
    peak_connections_24h: int
    peak_connections_today: int | None = None
    total_connections_today: int | None = None
    total_events_pushed: int | None = None
    average_events_per_minute: float | None = None
    avg_connection_duration_seconds: float | None = None


class SystemMetricUpdate(BaseModel):
    cpu_usage_percent: float | None = None
    memory_usage_percent: float | None = None
    disk_usage_percent: float | None = None
    network_bytes_sent: int | None = None
    network_bytes_recv: int | None = None
    timestamp: str | None = None
