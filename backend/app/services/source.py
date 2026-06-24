import logging
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import CategoryNotFound, Forbidden, SourceNotFound, ValidationError
from app.core.redis import RedisKeys, redis_delete, redis_hset, redis_publish
from app.models.category import Category
from app.models.source import Source, SourceHealth
from app.models.tenant import Tenant
from app.schemas.base import PaginatedMeta, PaginatedResponse, SuccessResponse
from app.schemas.source import (
    HealthCheckResult,
    SourceCreate,
    SourceHealthResponse,
    SourceResponse,
    SourceUpdate,
)

logger = logging.getLogger(__name__)

SOURCE_TYPE_CONFIG_RULES = {
    "rss": {"required_fields": ["url"]},
    "api": {"required_fields": ["url", "method"]},
    "web_scrape": {"required_fields": ["url", "selector"]},
    "social": {"required_fields": ["platform", "query"]},
}

SYSTEM_TENANT_ID = "system"


def _source_to_response(source: Source) -> SourceResponse:
    health_status = None
    last_fetch_at = None
    last_error = None
    if source.health is not None:
        health_status = source.health.status
        last_fetch_at = source.health.last_success_at
        last_error = source.health.last_error_message

    refresh_interval = source.refresh_interval_seconds
    if refresh_interval is None:
        refresh_interval = source.category.refresh_interval_seconds if source.category else 300

    return SourceResponse(
        id=str(source.id),
        name=source.name,
        category_id=str(source.category_id),
        source_type=source.source_type,
        url=source.url,
        config=source.config or {},
        refresh_interval_seconds=refresh_interval,
        is_active=source.is_active,
        priority=source.priority,
        health_status=health_status,
        last_fetch_at=last_fetch_at,
        last_error=last_error,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _health_to_response(health: SourceHealth) -> SourceHealthResponse:
    success_rate = None
    if health.total_fetches_24h > 0:
        success_rate = health.success_count_24h / health.total_fetches_24h

    return SourceHealthResponse(
        source_id=str(health.source_id),
        status=health.status,
        success_rate_24h=success_rate,
        avg_response_time_ms=health.avg_response_time_ms,
        last_success_at=health.last_success_at,
        last_failure_at=health.last_failure_at,
        consecutive_failures=health.consecutive_failures,
        total_fetches_24h=health.total_fetches_24h,
        last_error=health.last_error_message,
    )


class SourceService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    def _validate_source_config(self, source_type: str, config: dict) -> bool:
        if not config:
            return True

        rules = SOURCE_TYPE_CONFIG_RULES.get(source_type)
        if rules is None:
            return True

        required = rules["required_fields"]
        missing = [f for f in required if f not in config]
        if missing:
            raise ValidationError(
                message=f"Source config missing required fields for {source_type}: {', '.join(missing)}",
                details=[{"field": f, "message": f"Required for source_type '{source_type}'"} for f in missing],
            )

        return True

    async def list_sources(
        self,
        tenant_id: str,
        category_id: str | None = None,
        source_type: str | None = None,
        status_filter: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse[SourceResponse]:
        conditions = [
            or_(
                Source.tenant_id == tenant_id,
                Source.tenant_id == SYSTEM_TENANT_ID,
            )
        ]
        if category_id:
            conditions.append(Source.category_id == category_id)
        if source_type:
            conditions.append(Source.source_type == source_type)
        if is_active is not None:
            conditions.append(Source.is_active == is_active)

        if status_filter:
            health_join = True
            conditions.append(SourceHealth.status == status_filter)
        else:
            health_join = False

        count_stmt = select(func.count()).select_from(Source)
        if health_join:
            count_stmt = count_stmt.join(SourceHealth, Source.id == SourceHealth.source_id)
        count_stmt = count_stmt.where(and_(*conditions))
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = select(Source).options(selectinload(Source.health), selectinload(Source.category))
        if health_join:
            stmt = stmt.join(SourceHealth, Source.id == SourceHealth.source_id)
        stmt = stmt.where(and_(*conditions)).order_by(Source.priority.asc(), Source.name.asc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        sources = (await self.db.execute(stmt)).scalars().all()

        return PaginatedResponse(
            success=True,
            data=[_source_to_response(s) for s in sources],
            meta=PaginatedMeta(total=total, page=page, page_size=page_size),
        )

    async def get_source(self, source_id: str, tenant_id: str) -> SuccessResponse[SourceResponse]:
        stmt = (
            select(Source)
            .where(Source.id == source_id)
            .options(
                selectinload(Source.health),
                selectinload(Source.category),
            )
        )
        result = await self.db.execute(stmt)
        source = result.scalar_one_or_none()

        if source is None:
            raise SourceNotFound()

        if source.tenant_id != tenant_id and source.tenant_id != SYSTEM_TENANT_ID:
            raise SourceNotFound(message="Source not accessible for this tenant")

        return SuccessResponse(
            success=True,
            data=_source_to_response(source),
        )

    async def create_source(self, data: SourceCreate, tenant_id: str) -> SuccessResponse[SourceResponse]:
        tenant_stmt = select(Tenant).where(Tenant.id == tenant_id)
        tenant = (await self.db.execute(tenant_stmt)).scalar_one_or_none()
        if tenant is None:
            raise ValidationError(message="Tenant not found")

        current_count_stmt = select(func.count()).select_from(Source).where(Source.tenant_id == tenant_id)
        current_count = (await self.db.execute(current_count_stmt)).scalar() or 0
        if current_count >= tenant.max_sources:
            raise ValidationError(
                message=f"Source limit reached (max {tenant.max_sources})",
                details=[
                    {
                        "field": "name",
                        "message": f"Tenant already has {current_count} sources (max {tenant.max_sources})",
                    }
                ],
            )

        category_stmt = select(Category).where(Category.id == data.category_id)
        category = (await self.db.execute(category_stmt)).scalar_one_or_none()
        if category is None:
            raise CategoryNotFound(message="Referenced category not found")

        if category.tenant_id != tenant_id and category.tenant_id != SYSTEM_TENANT_ID:
            raise ValidationError(message="Cannot add sources to categories from other tenants")

        config = data.config or {}
        self._validate_source_config(data.source_type, config)

        refresh_interval = data.refresh_interval_seconds
        if refresh_interval is None:
            refresh_interval = category.refresh_interval_seconds

        source = Source(
            tenant_id=tenant_id,
            category_id=data.category_id,
            name=data.name,
            source_type=data.source_type,
            url=data.url,
            config=config,
            refresh_interval_seconds=refresh_interval,
            is_active=data.is_active,
            priority=5,
        )
        self.db.add(source)
        await self.db.flush()

        health = SourceHealth(
            source_id=source.id,
            status="healthy",
        )
        self.db.add(health)
        await self.db.flush()

        await self.db.refresh(source)

        redis_key = RedisKeys.source_health_key(str(source.id))
        await redis_hset(
            redis_key,
            mapping={
                "status": "healthy",
                "consecutive_failures": "0",
                "avg_response_time_ms": "0",
            },
        )
        await redis_publish(
            RedisKeys.channel_key("dashboard"),
            {
                "event": "source_created",
                "source_id": str(source.id),
                "category_id": str(source.category_id),
                "name": source.name,
            },
        )

        stmt = (
            select(Source)
            .where(Source.id == source.id)
            .options(
                selectinload(Source.health),
                selectinload(Source.category),
            )
        )
        source = (await self.db.execute(stmt)).scalar_one()

        return SuccessResponse(
            success=True,
            data=_source_to_response(source),
        )

    async def update_source(
        self,
        source_id: str,
        data: SourceUpdate,
        tenant_id: str,
    ) -> SuccessResponse[SourceResponse]:
        stmt = (
            select(Source)
            .where(Source.id == source_id)
            .options(
                selectinload(Source.health),
                selectinload(Source.category),
            )
        )
        result = await self.db.execute(stmt)
        source = result.scalar_one_or_none()

        if source is None:
            raise SourceNotFound()

        if source.tenant_id != tenant_id:
            raise Forbidden(message="Cannot update sources from other tenants")
        if source.tenant_id == SYSTEM_TENANT_ID:
            raise Forbidden(message="Cannot update system-level sources")

        update_data = data.model_dump(exclude_unset=True)

        if "source_type" in update_data and update_data["source_type"]:
            new_type = update_data["source_type"]
            config = update_data.get("config", source.config) or {}
            self._validate_source_config(new_type, config)

        for field, value in update_data.items():
            if value is not None and field != "category_id":
                setattr(source, field, value)

        await self.db.flush()
        await self.db.refresh(source)

        stmt = (
            select(Source)
            .where(Source.id == source_id)
            .options(
                selectinload(Source.health),
                selectinload(Source.category),
            )
        )
        source = (await self.db.execute(stmt)).scalar_one()

        return SuccessResponse(
            success=True,
            data=_source_to_response(source),
        )

    async def delete_source(self, source_id: str, tenant_id: str) -> None:
        stmt = select(Source).where(Source.id == source_id).options(selectinload(Source.category))
        result = await self.db.execute(stmt)
        source = result.scalar_one_or_none()

        if source is None:
            raise SourceNotFound()

        if source.tenant_id != tenant_id:
            raise Forbidden(message="Cannot delete sources from other tenants")
        if source.tenant_id == SYSTEM_TENANT_ID:
            raise Forbidden(message="Cannot delete system-level sources")

        redis_health_key = RedisKeys.source_health_key(str(source.id))
        await redis_delete(redis_health_key)

        category_slug = source.category.slug if source.category else "unknown"
        await redis_publish(
            RedisKeys.channel_key("dashboard"),
            {
                "event": "source_deleted",
                "source_id": str(source.id),
                "category_slug": category_slug,
            },
        )

        await self.db.delete(source)
        await self.db.flush()

    async def get_source_health(self, source_id: str, tenant_id: str) -> SuccessResponse[SourceHealthResponse]:
        stmt = select(SourceHealth).where(SourceHealth.source_id == source_id)
        result = await self.db.execute(stmt)
        health = result.scalar_one_or_none()

        if health is None:
            source_stmt = select(Source).where(Source.id == source_id)
            source = (await self.db.execute(source_stmt)).scalar_one_or_none()
            if source is None:
                raise SourceNotFound()
            if source.tenant_id != tenant_id and source.tenant_id != SYSTEM_TENANT_ID:
                raise SourceNotFound(message="Source not accessible for this tenant")

            return SuccessResponse(
                success=True,
                data=SourceHealthResponse(
                    source_id=str(source_id),
                    status="healthy",
                    consecutive_failures=0,
                    total_fetches_24h=0,
                    avg_response_time_ms=0,
                ),
            )

        source_stmt = select(Source).where(Source.id == source_id)
        source = (await self.db.execute(source_stmt)).scalar_one_or_none()
        if source is None:
            raise SourceNotFound()
        if source.tenant_id != tenant_id and source.tenant_id != SYSTEM_TENANT_ID:
            raise SourceNotFound(message="Source not accessible for this tenant")

        return SuccessResponse(
            success=True,
            data=_health_to_response(health),
        )

    async def update_source_health(
        self,
        source_id: str,
        result: HealthCheckResult,
    ) -> SuccessResponse[SourceHealthResponse]:
        stmt = select(SourceHealth).where(SourceHealth.source_id == source_id)
        health = (await self.db.execute(stmt)).scalar_one_or_none()

        if health is None:
            health = SourceHealth(
                source_id=source_id,
                status="healthy",
            )
            self.db.add(health)
            await self.db.flush()

        previous_status = health.status

        now = datetime.now(UTC)
        health.total_fetches_24h += 1

        if result.success:
            health.consecutive_failures = 0
            health.last_success_at = now
            health.success_count_24h += 1

            if result.response_time_ms > 0:
                current_avg = health.avg_response_time_ms
                total = health.total_fetches_24h
                health.avg_response_time_ms = int((current_avg * (total - 1) + result.response_time_ms) / total)

            if health.consecutive_failures == 0:
                if previous_status == "down" or previous_status == "degraded":
                    health.status = "degraded"
                elif previous_status != "healthy":
                    health.status = "healthy"
                else:
                    health.status = "healthy"
        else:
            health.consecutive_failures += 1
            health.last_failure_at = now
            health.last_error_message = result.error_message

            if health.consecutive_failures >= 10:
                health.status = "down"
            elif health.consecutive_failures >= 3:
                health.status = "degraded"
            else:
                health.status = previous_status

        health.updated_at = now
        await self.db.flush()

        redis_key = RedisKeys.source_health_key(str(source_id))
        await redis_hset(
            redis_key,
            mapping={
                "status": health.status,
                "consecutive_failures": str(health.consecutive_failures),
                "avg_response_time_ms": str(health.avg_response_time_ms),
                "last_error": health.last_error_message or "",
            },
        )

        if health.status != previous_status:
            source_stmt = select(Source).where(Source.id == source_id)
            source = (await self.db.execute(source_stmt)).scalar_one_or_none()
            category_slug = source.category.slug if source and source.category else "unknown"
            await redis_publish(
                RedisKeys.channel_key(category_slug),
                {
                    "event": "source_health_update",
                    "source_id": str(source_id),
                    "status": health.status,
                    "previous_status": previous_status,
                    "last_error": health.last_error_message,
                },
            )
            await redis_publish(
                RedisKeys.channel_key("dashboard"),
                {
                    "event": "source_health_update",
                    "source_id": str(source_id),
                    "status": health.status,
                    "previous_status": previous_status,
                },
            )

        return SuccessResponse(
            success=True,
            data=_health_to_response(health),
        )

    async def get_all_sources_health_summary(self, tenant_id: str) -> dict:
        stmt = (
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
        rows = (await self.db.execute(stmt)).all()

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
        for status, count in rows:
            summary[status] = count

        return summary
