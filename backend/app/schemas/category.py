from datetime import datetime

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(max_length=50)
    slug: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    icon: str | None = Field(default="folder", max_length=50)
    color: str | None = Field(default="#3B82F6", max_length=7)
    type: str = Field(pattern="^(finance|tech|news|custom)$")
    refresh_interval_seconds: int = Field(default=300, ge=10)
    keywords_filter: list[str] | None = Field(default=None)
    is_active: bool = Field(default=True)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=50)
    slug: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    icon: str | None = Field(default=None, max_length=50)
    color: str | None = Field(default=None, max_length=7)
    type: str | None = Field(default=None, pattern="^(finance|tech|news|custom)$")
    refresh_interval_seconds: int | None = Field(default=None, ge=10)
    keywords_filter: list[str] | None = Field(default=None)
    priority_sort: bool | None = Field(default=None)
    is_active: bool | None = Field(default=None)


class CategoryResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    icon: str
    color: str
    type: str
    refresh_interval_seconds: int
    is_active: bool
    source_count: int = Field(default=0)
    created_at: datetime
    updated_at: datetime


class CategoryWithSourcesResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    icon: str
    color: str
    type: str
    refresh_interval_seconds: int
    is_active: bool
    source_count: int = Field(default=0)
    sources: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SubCategoryResponse(BaseModel):
    tag: str
    label: str | None = None
    count: int


class CategoryListParams(BaseModel):
    type: str | None = Field(default=None, pattern="^(finance|tech|news|custom)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ReclassifyResponse(BaseModel):
    scanned: int
    updated: int
