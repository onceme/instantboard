from datetime import datetime

from pydantic import BaseModel, Field


class TechNewsResponse(BaseModel):
    id: str
    title: str
    summary: str | None = None
    url: str
    source_name: str | None = None
    source_id: str
    category_id: str
    topic_tags: list[str]
    domain_tag: str | None = None
    published_at: datetime
    fetched_at: datetime
    image_url: str | None = None
    priority: int
    extra_data: dict | None = None
    hot_score: float | None = None


class TechTopicResponse(BaseModel):
    tag: str
    label: str | None = None
    count: int
    last_active_at: datetime | None = None


class TechNewsParams(BaseModel):
    domain: str | None = Field(default=None)
    subcategory: str | None = Field(default=None)
    sort: str = Field(default="hot", pattern="^(hot|time|relevance)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
