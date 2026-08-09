from datetime import datetime

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    name: str = Field(max_length=100)
    category_id: str
    source_type: str = Field(pattern="^(rss|api|web_scrape|social)$")
    url: str
    config: dict | None = Field(default=None)
    refresh_interval_seconds: int | None = Field(default=None, ge=10)
    is_active: bool = Field(default=True)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    source_type: str | None = Field(default=None, pattern="^(rss|api|web_scrape|social)$")
    url: str | None = Field(default=None)
    config: dict | None = Field(default=None)
    refresh_interval_seconds: int | None = Field(default=None, ge=10)
    is_active: bool | None = Field(default=None)
    priority: int | None = Field(default=None, ge=1, le=10)


class SourceResponse(BaseModel):
    id: str
    name: str
    category_id: str
    source_type: str
    url: str
    config: dict
    refresh_interval_seconds: int | None = None
    is_active: bool
    priority: int
    # False when no collector can run for this source (no source_type match and no
    # config.library fallback) — the UI shows why such a source cannot be enabled
    collector_available: bool = False
    health_status: str | None = None
    last_fetch_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class SourceHealthResponse(BaseModel):
    source_id: str
    status: str
    success_rate_24h: float | None = None
    avg_response_time_ms: int | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int
    total_fetches_24h: int
    last_error: str | None = None


class HealthCheckResult(BaseModel):
    success: bool
    response_time_ms: int = Field(default=0)
    error_message: str | None = None


class SourceListParams(BaseModel):
    category_id: str | None = None
    source_type: str | None = Field(default=None, pattern="^(rss|api|web_scrape|social)$")
    status: str | None = Field(default=None, pattern="^(healthy|degraded|down)$")
    is_active: bool | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
