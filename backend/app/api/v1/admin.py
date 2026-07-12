import logging
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import Forbidden, ValidationError
from app.dependencies import get_current_user, get_db
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.admin import (
    TenantCreate,
    TenantResponse,
    TenantStatsResponse,
    TenantUpdate,
)
from app.schemas.base import PaginatedMeta, PaginatedResponse, SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def require_admin(user: dict):
    if user.get("role") != "admin":
        raise Forbidden(message="Admin access required")


def _tenant_to_response(tenant: Tenant) -> TenantResponse:
    return TenantResponse(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan,
        settings=tenant.settings or {},
        max_users=tenant.max_users,
        max_categories=tenant.max_categories,
        max_sources=tenant.max_sources,
        is_active=tenant.is_active,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@router.post("/tenants", response_model=SuccessResponse[TenantResponse], status_code=201)
async def create_tenant(
    request: TenantCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)

    dup_stmt = select(Tenant).where(Tenant.slug == request.slug)
    existing = (await db.execute(dup_stmt)).scalar_one_or_none()
    if existing is not None:
        raise ValidationError(
            message=f"Tenant with slug '{request.slug}' already exists",
            details=[{"field": "slug", "message": "Slug must be unique"}],
        )

    tenant = Tenant(
        name=request.name,
        slug=request.slug,
        plan=request.plan,
        settings=request.settings or {},
        max_users=request.max_users,
        max_categories=request.max_categories,
        max_sources=request.max_sources,
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    return SuccessResponse(success=True, data=_tenant_to_response(tenant))


@router.get("/tenants", response_model=PaginatedResponse[TenantResponse])
async def list_tenants(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)

    count_stmt = select(func.count()).select_from(Tenant)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(Tenant)
        .order_by(Tenant.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    tenants = [_tenant_to_response(t) for t in rows]

    return PaginatedResponse(
        success=True,
        data=tenants,
        meta=PaginatedMeta(total=total, page=page, page_size=page_size),
    )


@router.get("/tenants/{tenant_id}", response_model=SuccessResponse[TenantResponse])
async def get_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)

    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise ValidationError(message=f"Invalid tenant ID: {tenant_id}") from None

    stmt = select(Tenant).where(Tenant.id == tid)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if tenant is None:
        from fastapi import status

        from app.core.exceptions import AppException
        from app.schemas.base import ErrorCode

        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.VALIDATION_ERROR,
            message=f"Tenant not found: {tenant_id}",
        )

    return SuccessResponse(success=True, data=_tenant_to_response(tenant))


@router.put("/tenants/{tenant_id}", response_model=SuccessResponse[TenantResponse])
async def update_tenant(
    tenant_id: str,
    request: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)

    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise ValidationError(message=f"Invalid tenant ID: {tenant_id}") from None

    stmt = select(Tenant).where(Tenant.id == tid)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if tenant is None:
        from fastapi import status

        from app.core.exceptions import AppException
        from app.schemas.base import ErrorCode

        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.VALIDATION_ERROR,
            message=f"Tenant not found: {tenant_id}",
        )

    update_data = request.model_dump(exclude_unset=True)

    if "slug" in update_data and update_data["slug"]:
        new_slug = update_data["slug"]
        dup_stmt = select(Tenant).where(Tenant.slug == new_slug, Tenant.id != tid)
        existing = (await db.execute(dup_stmt)).scalar_one_or_none()
        if existing is not None:
            raise ValidationError(
                message=f"Tenant with slug '{new_slug}' already exists",
                details=[{"field": "slug", "message": "Slug must be unique"}],
            )

    for field, value in update_data.items():
        if value is not None:
            setattr(tenant, field, value)

    await db.flush()
    await db.refresh(tenant)

    return SuccessResponse(success=True, data=_tenant_to_response(tenant))


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)

    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise ValidationError(message=f"Invalid tenant ID: {tenant_id}") from None

    stmt = select(Tenant).where(Tenant.id == tid)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if tenant is None:
        from fastapi import status

        from app.core.exceptions import AppException
        from app.schemas.base import ErrorCode

        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.VALIDATION_ERROR,
            message=f"Tenant not found: {tenant_id}",
        )

    if tenant.slug == "system":
        raise Forbidden(message="Cannot delete the system tenant")

    await db.delete(tenant)
    await db.flush()

    return Response(status_code=204)


@router.get("/tenants/{tenant_id}/stats", response_model=SuccessResponse[TenantStatsResponse])
async def get_tenant_stats(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)

    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise ValidationError(message=f"Invalid tenant ID: {tenant_id}") from None

    stmt = select(Tenant).where(Tenant.id == tid)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if tenant is None:
        from fastapi import status

        from app.core.exceptions import AppException
        from app.schemas.base import ErrorCode

        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.VALIDATION_ERROR,
            message=f"Tenant not found: {tenant_id}",
        )

    user_count_stmt = select(func.count()).select_from(User).where(User.tenant_id == tid)
    user_count = (await db.execute(user_count_stmt)).scalar() or 0

    category_count_stmt = select(func.count()).select_from(Category).where(Category.tenant_id == tid)
    category_count = (await db.execute(category_count_stmt)).scalar() or 0

    source_count_stmt = select(func.count()).select_from(Source).where(Source.tenant_id == tid)
    source_count = (await db.execute(source_count_stmt)).scalar() or 0

    item_count_stmt = select(func.count()).select_from(Item).where(Item.tenant_id == tid)
    item_count = (await db.execute(item_count_stmt)).scalar() or 0

    from app.core.sse_router import event_router

    sse_stats = event_router.get_stats()
    active_sse = sse_stats.get("total_connections", 0)

    stats = TenantStatsResponse(
        tenant_id=str(tid),
        user_count=user_count,
        category_count=category_count,
        source_count=source_count,
        item_count=item_count,
        active_sse_connections=active_sse,
    )

    return SuccessResponse(success=True, data=stats)
