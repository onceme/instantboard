import ipaddress
import logging
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import Forbidden, ValidationError
from app.core.middleware import invalidate_ipblacklist_cache
from app.core.pagination import apply_sort
from app.core.redis import RedisKeys, redis_sadd, redis_smembers, redis_srem
from app.dependencies import get_current_user, get_db
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.admin import (
    IPBlacklistAddRequest,
    IPBlacklistEntry,
    IPBlacklistResponse,
    TenantCreate,
    TenantResponse,
    TenantStatsResponse,
    TenantUpdate,
)
from app.schemas.base import PaginatedMeta, PaginatedResponse, PaginationParams, SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Whitelist of columns GET /admin/tenants may order by (see apply_sort).
TENANT_SORT_FIELDS = {"name", "created_at"}


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
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)

    page, page_size = pagination.page, pagination.page_size

    count_stmt = select(func.count()).select_from(Tenant)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(Tenant)
    if pagination.sort_by is not None:
        stmt = apply_sort(stmt, pagination.sort_by, TENANT_SORT_FIELDS, Tenant, pagination.sort_order)
    else:
        stmt = stmt.order_by(Tenant.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
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


# ---------------------------------------------------------------------------
# IP blacklist management (security.md §3.3 layer 3)
# ---------------------------------------------------------------------------


def _parse_blacklist_ip(value: str) -> str:
    """Validate an IP literal and return its canonical (compressed) form.

    Canonicalization collapses equivalent spellings into one set member, e.g.
    "0:0:0:0:0:0:0:1" and "::1" are the same ban.
    """
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        raise ValidationError(
            message=f"Invalid IP address: {value}",
            details=[{"field": "ip", "message": "Must be a valid IPv4 or IPv6 address"}],
        ) from None


@router.get("/security/ip-blacklist", response_model=SuccessResponse[IPBlacklistResponse])
async def list_ip_blacklist(user: dict = Depends(get_current_user)):
    require_admin(user)

    ips = sorted(await redis_smembers(RedisKeys.IP_BLACKLIST))
    return SuccessResponse(success=True, data=IPBlacklistResponse(ips=ips))


@router.post("/security/ip-blacklist", response_model=SuccessResponse[IPBlacklistEntry])
async def add_to_ip_blacklist(request: IPBlacklistAddRequest, user: dict = Depends(get_current_user)):
    require_admin(user)

    ip = _parse_blacklist_ip(request.ip)
    # SADD is idempotent: banning an already-banned IP is a no-op that still
    # succeeds, so admins can retry without checking current state first.
    await redis_sadd(RedisKeys.IP_BLACKLIST, ip)
    invalidate_ipblacklist_cache()
    return SuccessResponse(success=True, data=IPBlacklistEntry(ip=ip))


@router.delete("/security/ip-blacklist/{ip}", response_model=SuccessResponse[IPBlacklistEntry])
async def remove_from_ip_blacklist(ip: str, user: dict = Depends(get_current_user)):
    require_admin(user)

    normalized = _parse_blacklist_ip(ip)
    # Idempotent delete (not 404): the admin intent is "ensure this IP is not
    # banned", so removing a missing entry succeeds too — retries and racing
    # deletes stay safe.
    await redis_srem(RedisKeys.IP_BLACKLIST, normalized)
    invalidate_ipblacklist_cache()
    return SuccessResponse(success=True, data=IPBlacklistEntry(ip=normalized))
