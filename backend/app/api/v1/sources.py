from fastapi import APIRouter, Depends, Query, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_db, get_redis
from app.schemas.base import PaginatedResponse, SuccessResponse
from app.schemas.source import (
    SourceCreate,
    SourceHealthResponse,
    SourceResponse,
    SourceUpdate,
)
from app.services.source import SourceService

router = APIRouter()


def _get_source_service(db: AsyncSession, redis: Redis) -> SourceService:
    return SourceService(db=db, redis=redis)


@router.get("", response_model=PaginatedResponse[SourceResponse])
async def list_sources(
    category_id: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    status: str | None = Query(default=None, description="Filter by health status: healthy/degraded/down"),
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_source_service(db, redis)
    result = await service.list_sources(
        tenant_id=tenant_id,
        category_id=category_id,
        source_type=source_type,
        status_filter=status,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return result


@router.post("", response_model=SuccessResponse[SourceResponse], status_code=201)
async def create_source(
    request: SourceCreate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_source_service(db, redis)
    result = await service.create_source(data=request, tenant_id=tenant_id)
    await db.commit()
    return result


@router.get("/{source_id}", response_model=SuccessResponse[SourceResponse])
async def get_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_source_service(db, redis)
    result = await service.get_source(source_id=source_id, tenant_id=tenant_id)
    return result


@router.put("/{source_id}", response_model=SuccessResponse[SourceResponse])
async def update_source(
    source_id: str,
    request: SourceUpdate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_source_service(db, redis)
    result = await service.update_source(
        source_id=source_id,
        data=request,
        tenant_id=tenant_id,
    )
    await db.commit()
    return result


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_source_service(db, redis)
    await service.delete_source(source_id=source_id, tenant_id=tenant_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/{source_id}/health", response_model=SuccessResponse[SourceHealthResponse])
async def get_source_health(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_source_service(db, redis)
    result = await service.get_source_health(source_id=source_id, tenant_id=tenant_id)
    return result
