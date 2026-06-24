from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_db, get_redis, require_admin
from app.schemas.base import SuccessResponse
from app.schemas.dashboard import (
    DataSourceHealthDetailResponse,
    DataSourceHealthResponse,
    SchedulerStatusResponse,
    ServiceHealthResponse,
    SSEStatsResponse,
    SystemInfoResponse,
)
from app.services.dashboard import DashboardService

router = APIRouter()


def _get_start_time(request: Request) -> datetime:
    from app.main import _start_time

    return _start_time or datetime.now(UTC)


@router.get("/system", response_model=SuccessResponse[SystemInfoResponse])
async def get_system_info(
    request: Request,
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
):
    start_time = _get_start_time(request)
    service = DashboardService(db, redis_client)
    data = await service.get_system_info(start_time)
    return SuccessResponse(success=True, data=data)


@router.get("/services", response_model=SuccessResponse[list[ServiceHealthResponse]])
async def get_services_health(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
):
    service = DashboardService(db, redis_client)
    data = await service.get_services_health()
    return SuccessResponse(success=True, data=data)


@router.get("/data-sources", response_model=SuccessResponse[DataSourceHealthResponse])
async def get_data_sources_health(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = DashboardService(db, redis_client)
    data = await service.get_data_sources_health_summary(tenant_id)
    return SuccessResponse(success=True, data=data)


@router.get("/data-sources/{source_id}", response_model=SuccessResponse[DataSourceHealthDetailResponse])
async def get_data_source_health_detail(
    source_id: str,
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = DashboardService(db, redis_client)
    data = await service.get_data_source_health_detail(tenant_id, source_id)
    if data is None:
        from app.core.exceptions import SourceNotFound

        raise SourceNotFound()
    return SuccessResponse(success=True, data=data)


@router.get("/scheduler", response_model=SuccessResponse[SchedulerStatusResponse])
async def get_scheduler_status(
    user: dict = Depends(require_admin),
):
    from app.core.redis import get_redis_client
    from app.db.session import async_session_factory
    from app.services.dashboard import DashboardService

    async with async_session_factory() as session:
        redis_client = await get_redis_client()
        service = DashboardService(session, redis_client)
        data = await service.get_scheduler_status()
    return SuccessResponse(success=True, data=data)


@router.get("/sse-stats", response_model=SuccessResponse[SSEStatsResponse])
async def get_sse_stats(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
):
    service = DashboardService(db, redis_client)
    data = await service.get_sse_stats()
    return SuccessResponse(success=True, data=data)
