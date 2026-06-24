from datetime import datetime

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    name: str = Field(max_length=100)
    slug: str = Field(max_length=50)
    plan: str = Field(default="free", pattern="^(free|pro|enterprise)$")
    settings: dict | None = Field(default=None)
    max_users: int = Field(default=5)
    max_categories: int = Field(default=10)
    max_sources: int = Field(default=50)


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    slug: str | None = Field(default=None, max_length=50)
    plan: str | None = Field(default=None, pattern="^(free|pro|enterprise)$")
    settings: dict | None = Field(default=None)
    max_users: int | None = Field(default=None)
    max_categories: int | None = Field(default=None)
    max_sources: int | None = Field(default=None)
    is_active: bool | None = Field(default=None)


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    settings: dict
    max_users: int
    max_categories: int
    max_sources: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TenantStatsResponse(BaseModel):
    tenant_id: str
    user_count: int
    category_count: int
    source_count: int
    item_count: int
    active_sse_connections: int
