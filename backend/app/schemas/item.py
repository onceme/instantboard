from datetime import datetime

from pydantic import BaseModel


class TagUpdateRequest(BaseModel):
    tag: str


class ItemTagsResponse(BaseModel):
    item_id: str
    topic_tags: list[str]


class ItemResponse(BaseModel):
    id: str
    title: str
    summary: str | None = None
    url: str
    image_url: str | None = None
    source_name: str | None = None
    source_id: str
    category_id: str
    topic_tags: list[str]
    published_at: datetime
    fetched_at: datetime
    priority: int
