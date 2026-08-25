from fastapi import APIRouter, Depends, Query, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_db, get_redis
from app.schemas.base import PaginatedResponse, PaginationParams, SuccessResponse
from app.schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CategoryWithSourcesResponse,
    SubCategoryResponse,
)
from app.services.category import CategoryService

router = APIRouter()


def _get_category_service(db: AsyncSession, redis: Redis) -> CategoryService:
    return CategoryService(db=db, redis=redis)


@router.get("", response_model=PaginatedResponse[CategoryResponse])
async def list_categories(
    type: str | None = Query(default=None, description="Filter by category type"),  # noqa: A002
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    result = await service.list_categories(
        tenant_id=tenant_id,
        type_filter=type,
        page=pagination.page,
        page_size=pagination.page_size,
        sort_by=pagination.sort_by,
        sort_order=pagination.sort_order,
    )
    return result


@router.post("", response_model=SuccessResponse[CategoryResponse], status_code=201)
async def create_category(
    request: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    result = await service.create_category(data=request, tenant_id=tenant_id)
    await db.commit()
    return result


@router.get("/predefined", response_model=SuccessResponse[list[CategoryResponse]])
async def get_predefined_categories(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    result = await service.get_predefined_categories(tenant_id=tenant_id)
    return result


@router.get("/{category_id}", response_model=SuccessResponse[CategoryResponse])
async def get_category(
    category_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    result = await service.get_category(category_id=category_id, tenant_id=tenant_id)
    return result


@router.put("/{category_id}", response_model=SuccessResponse[CategoryResponse])
async def update_category(
    category_id: str,
    request: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    result = await service.update_category(
        category_id=category_id,
        data=request,
        tenant_id=tenant_id,
    )
    await db.commit()
    return result


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    await service.delete_category(category_id=category_id, tenant_id=tenant_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/{category_id}/sources", response_model=SuccessResponse[CategoryWithSourcesResponse])
async def get_category_with_sources(
    category_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    result = await service.get_category_with_sources(
        category_id=category_id,
        tenant_id=tenant_id,
    )
    return result


@router.get("/{category_id}/subcategories", response_model=SuccessResponse[list[SubCategoryResponse]])
async def list_subcategories(
    category_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_category_service(db, redis)
    result = await service.list_subcategories(
        category_id=category_id,
        tenant_id=tenant_id,
    )
    return result
