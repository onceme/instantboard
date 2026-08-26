from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_db, require_admin
from app.schemas.base import SuccessResponse
from app.schemas.tenant import TenantSettingsResponse, TenantSettingsUpdate
from app.services.tenant import TenantSettingsService

router = APIRouter()


@router.get("/settings", response_model=SuccessResponse[TenantSettingsResponse])
async def get_tenant_settings(
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    # Readable by any member of the tenant: the values only affect display
    # colors and refresh cadence of categories the caller can already list.
    service = TenantSettingsService(db)
    return await service.get_overrides(tenant_id)


@router.put("/settings", response_model=SuccessResponse[TenantSettingsResponse])
async def update_tenant_settings(
    request: TenantSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    # require_admin enforces role=admin; the tenant comes from the same JWT
    # (a tenant admin can only ever reach their own tenant row — cross-tenant
    # writes are structurally impossible on this endpoint).
    service = TenantSettingsService(db)
    result = await service.update_overrides(user.get("tenant_id"), request)
    await db.commit()
    return result
