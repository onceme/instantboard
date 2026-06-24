from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import Forbidden
from app.dependencies import get_current_user, get_db
from app.schemas.admin import (
    TenantCreate,
    TenantResponse,
    TenantStatsResponse,
    TenantUpdate,
)
from app.schemas.base import PaginatedResponse, PaginationParams, SuccessResponse

router = APIRouter()


def require_admin(user: dict):
    if user.get("role") != "admin":
        raise Forbidden(message="Admin access required")


@router.post("/tenants", response_model=SuccessResponse[TenantResponse], status_code=201)
async def create_tenant(
    request: TenantCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    pass


@router.get("/tenants", response_model=PaginatedResponse[TenantResponse])
async def list_tenants(
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    pass


@router.get("/tenants/{tenant_id}", response_model=SuccessResponse[TenantResponse])
async def get_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    pass


@router.put("/tenants/{tenant_id}", response_model=SuccessResponse[TenantResponse])
async def update_tenant(
    tenant_id: str,
    request: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    pass


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    pass


@router.get("/tenants/{tenant_id}/stats", response_model=SuccessResponse[TenantStatsResponse])
async def get_tenant_stats(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    pass
