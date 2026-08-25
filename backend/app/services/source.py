import logging

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Fix: the local SYSTEM_TENANT_ID used to be the string "system", which asyncpg failed to
# encode when compared against a UUID column. Reuse the UUID constant from core.constants
# instead (keeping the original exported name so modules like dashboard still work).
from app.collectors import resolve_collector
from app.core.constants import SYSTEM_TENANT_ID
from app.core.exceptions import (
    CategoryNotFound,
    Forbidden,
    NoCollectorAvailable,
    SourceNotFound,
    ValidationError,
)
from app.core.pagination import apply_sort
from app.core.redis import RedisKeys, redis_delete, redis_publish, redis_set
from app.models.category import Category
from app.models.source import Source, SourceHealth
from app.models.tenant import Tenant
from app.schemas.base import PaginatedMeta, PaginatedResponse, SuccessResponse
from app.schemas.source import (
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

# Whitelist of columns GET /sources may order by (see apply_sort).
SOURCE_SORT_FIELDS = {"name", "created_at", "priority"}


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
        # Whether a collector can actually run for this source (source_type match or
        # config.library fallback). The UI uses this to explain why a source cannot
        # be enabled instead of failing silently.
        collector_available=resolve_collector(source.source_type, source.config) is not None,
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

    @staticmethod
    def _check_collector_available(source_type: str, config: dict) -> None:
        # Pre-flight check before activating a source: without a resolvable collector
        # the source would sit in is_active=True forever without collecting anything
        # (e.g. a bare web_scrape/social source with no config.library fallback).
        if resolve_collector(source_type, config) is None:
            raise NoCollectorAvailable(
                message=(
                    f"No collector available for source_type '{source_type}'. "
                    "Set config.library to a supported collector "
                    "(yfinance, alpha_vantage, eastmoney, finnhub, rss, hackernews, arxiv) "
                    "or keep the source inactive."
                )
            )

    async def list_sources(
        self,
        tenant_id: str,
        category_id: str | None = None,
        source_type: str | None = None,
        status_filter: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str | None = None,
        sort_order: str = "desc",
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
        stmt = stmt.where(and_(*conditions))
        if sort_by is not None:
            stmt = apply_sort(stmt, sort_by, SOURCE_SORT_FIELDS, Source, sort_order)
        else:
            stmt = stmt.order_by(Source.priority.asc(), Source.name.asc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        sources = (await self.db.execute(stmt)).scalars().all()

        return PaginatedResponse(
            success=True,
            data=[_source_to_response(s) for s in sources],
            meta=PaginatedMeta(total=total, page=page, page_size=page_size),
        )

    async def get_source(self, source_id: str, tenant_id: str) -> SuccessResponse[SourceResponse]:
        # tenant_id arrives as a str from the JWT while ORM attributes are UUID objects;
        # normalize before comparing so ownership checks work in both directions
        tenant_id = str(tenant_id)
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

        if str(source.tenant_id) != tenant_id and source.tenant_id != SYSTEM_TENANT_ID:
            raise SourceNotFound(message="Source not accessible for this tenant")

        return SuccessResponse(
            success=True,
            data=_source_to_response(source),
        )

    async def create_source(self, data: SourceCreate, tenant_id: str) -> SuccessResponse[SourceResponse]:
        tenant_id = str(tenant_id)
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

        # str() on both sides: category.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison would never match (bug)
        if str(category.tenant_id) != tenant_id and category.tenant_id != SYSTEM_TENANT_ID:
            raise ValidationError(message="Cannot add sources to categories from other tenants")

        config = data.config or {}
        self._validate_source_config(data.source_type, config)

        # Creating an active source without a resolvable collector would silently
        # never collect; reject it up front (same pre-flight as the enable path).
        if data.is_active:
            self._check_collector_available(data.source_type, config)

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
            total_fetches_24h=0,
            success_count_24h=0,
            avg_response_time_ms=0,
            consecutive_failures=0,
        )
        self.db.add(health)
        await self.db.flush()

        await self.db.refresh(source)

        redis_key = RedisKeys.source_health_key(str(source.id))
        # Same format and TTL as the collection path (collectors/base.py
        # record_health): a JSON string with ex=300. Writing a TTL-less hash here
        # mixed two formats for the same key, so readers (json.loads) could not
        # parse the create-time value and the key never expired.
        await redis_set(
            redis_key,
            {
                "status": "healthy",
                "consecutive_failures": 0,
                "total_fetches_24h": 0,
                "success_count_24h": 0,
                "avg_response_time_ms": 0,
            },
            ex=300,
        )
        await redis_publish(
            RedisKeys.channel_key("dashboard"),
            {
                "event": "source_created",
                "source_id": str(source.id),
                "category_id": str(source.category_id),
                "name": source.name,
                # Full source payload (same shape as source_enabled) so the worker can
                # schedule collection immediately without reading the DB — a DB read
                # here would race the still-uncommitted create transaction. Without
                # these fields the worker never picked the event up and freshly
                # created active sources only started collecting after a restart.
                "source": {
                    "id": str(source.id),
                    "tenant_id": str(source.tenant_id),
                    "category_id": str(source.category_id),
                    "category_slug": category.slug,
                    "name": source.name,
                    "source_type": source.source_type,
                    "url": source.url,
                    "config": source.config or {},
                    "refresh_interval_seconds": source.refresh_interval_seconds,
                    "is_active": source.is_active,
                    "priority": source.priority,
                },
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
        tenant_id = str(tenant_id)
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

        # str() on both sides: source.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison would reject every update.
        # System (seed) sources are editable by their owning tenant, i.e. the admin
        # session which lives in the system tenant itself (e.g. to enable seeded
        # sources); all other tenants are rejected by the ownership check.
        if str(source.tenant_id) != tenant_id:
            raise Forbidden(message="Cannot update sources from other tenants")

        update_data = data.model_dump(exclude_unset=True)

        if "source_type" in update_data and update_data["source_type"]:
            new_type = update_data["source_type"]
            config = update_data.get("config", source.config) or {}
            self._validate_source_config(new_type, config)

        # Enabling a source requires a collector that can actually run it. Evaluate
        # the effective type/config after this same request's changes (an update may
        # flip is_active and change source_type/config simultaneously). None values
        # are never written by the setattr loop below, so they keep the current state.
        if update_data.get("is_active") is True:
            effective_type = update_data.get("source_type") or source.source_type
            effective_config = update_data.get("config")
            if effective_config is None:
                effective_config = source.config
            self._check_collector_available(effective_type, effective_config or {})

        old_is_active = source.is_active

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

        # Runtime scheduling hook: the worker subscribes to the dashboard channel and
        # adds/removes the collection job without waiting for a restart. The payload
        # carries the full source data so the worker can build the job without reading
        # the DB (avoiding a race against the still-uncommitted transaction); the
        # worker's full rebuild from the DB on startup remains the fallback.
        if source.is_active != old_is_active:
            await redis_publish(
                RedisKeys.channel_key("dashboard"),
                {
                    "event": "source_enabled" if source.is_active else "source_disabled",
                    "source_id": str(source.id),
                    "source": {
                        "id": str(source.id),
                        "tenant_id": str(source.tenant_id),
                        "category_id": str(source.category_id),
                        "category_slug": source.category.slug if source.category else "",
                        "name": source.name,
                        "source_type": source.source_type,
                        "url": source.url,
                        "config": source.config or {},
                        "refresh_interval_seconds": source.refresh_interval_seconds,
                        "is_active": source.is_active,
                        "priority": source.priority,
                    },
                },
            )

        return SuccessResponse(
            success=True,
            data=_source_to_response(source),
        )

    async def delete_source(self, source_id: str, tenant_id: str) -> None:
        tenant_id = str(tenant_id)
        stmt = select(Source).where(Source.id == source_id).options(selectinload(Source.category))
        result = await self.db.execute(stmt)
        source = result.scalar_one_or_none()

        if source is None:
            raise SourceNotFound()

        if str(source.tenant_id) != tenant_id:
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
        tenant_id = str(tenant_id)
        stmt = select(SourceHealth).where(SourceHealth.source_id == source_id)
        result = await self.db.execute(stmt)
        health = result.scalar_one_or_none()

        if health is None:
            source_stmt = select(Source).where(Source.id == source_id)
            source = (await self.db.execute(source_stmt)).scalar_one_or_none()
            if source is None:
                raise SourceNotFound()
            if str(source.tenant_id) != tenant_id and source.tenant_id != SYSTEM_TENANT_ID:
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
        if str(source.tenant_id) != tenant_id and source.tenant_id != SYSTEM_TENANT_ID:
            raise SourceNotFound(message="Source not accessible for this tenant")

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
