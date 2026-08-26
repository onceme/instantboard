from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_current_user, get_db, get_redis
from app.schemas.base import PaginatedMeta, PaginatedResponse, SuccessResponse
from app.schemas.tech import TechNewsResponse, TechTopicResponse
from app.services.tech import TechService
from app.services.user import UserService

router = APIRouter()


async def _get_tech_service(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)) -> TechService:
    return TechService(db=db, redis=redis)


def build_news_response(result: dict) -> PaginatedResponse[TechNewsResponse]:
    """Map the TechService item envelope to the paginated response schema.

    Shared with the generic GET /categories/{id}/items endpoint, which reuses the
    same item query/response shape for any category.
    """
    data = []
    for item in result["data"]:
        published_at = item.get("published_at")
        if isinstance(published_at, str):
            try:
                published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                published_at = datetime.now(UTC)

        fetched_at = item.get("fetched_at")
        if isinstance(fetched_at, str):
            try:
                fetched_at = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                fetched_at = datetime.now(UTC)

        data.append(
            TechNewsResponse(
                id=item["id"],
                title=item["title"],
                summary=item.get("summary"),
                url=item["url"],
                source_name=item.get("source_name"),
                source_id=item["source_id"],
                category_id=item["category_id"],
                topic_tags=item.get("topic_tags", []),
                domain_tag=item.get("domain_tag"),
                published_at=published_at,
                fetched_at=fetched_at,
                image_url=item.get("image_url"),
                priority=item.get("priority", 5),
                extra_data=item.get("extra_data"),
                hot_score=item.get("hot_score"),
            )
        )

    return PaginatedResponse(
        data=data,
        meta=PaginatedMeta(**result["meta"]),
    )


@router.get("/news", response_model=PaginatedResponse[TechNewsResponse])
async def get_tech_news(
    domain: str | None = Query(default=None),
    subcategory: str | None = Query(default=None),
    sort: str = Query(default="hot", pattern="^(hot|time|relevance)$"),
    source_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: TechService = Depends(_get_tech_service),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    # Only the relevance mode consumes user preferences; skip the extra query for
    # hot/time. get_user_preferences fails soft (None) when the user row is gone,
    # so a stale token still gets the unpersonalized feed.
    user_preferences = None
    if sort == "relevance":
        user_preferences = await UserService(db).get_user_preferences(user["user_id"], user["tenant_id"])

    result = await service.get_news(
        tenant_id=tenant_id,
        domain=domain,
        subcategory=subcategory,
        sort=sort,
        page=page,
        page_size=page_size,
        source_id=source_id,
        since=since,
        user_preferences=user_preferences,
    )

    return build_news_response(result)


@router.get("/topics", response_model=SuccessResponse[list[TechTopicResponse]])
async def get_tech_topics(
    domain: str | None = Query(default=None),
    service: TechService = Depends(_get_tech_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    topics = await service.get_topics(tenant_id=tenant_id, domain=domain)

    data = []
    for topic in topics:
        last_active_at = topic.get("last_active_at")
        if isinstance(last_active_at, str):
            try:
                last_active_at = datetime.fromisoformat(last_active_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                last_active_at = None

        data.append(
            TechTopicResponse(
                tag=topic["tag"],
                label=topic.get("label", topic["tag"]),
                count=topic["count"],
                last_active_at=last_active_at,
            )
        )

    return SuccessResponse(data=data)
