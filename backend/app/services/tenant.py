import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SYSTEM_TENANT_ID
from app.core.exceptions import ValidationError
from app.models.category import Category
from app.models.tenant import Tenant
from app.schemas.base import SuccessResponse
from app.schemas.tenant import (
    REFRESH_OVERRIDE_MAX_SECONDS,
    REFRESH_OVERRIDE_MIN_SECONDS,
    TenantSettingsResponse,
    TenantSettingsUpdate,
)

logger = logging.getLogger(__name__)

COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def extract_overrides(settings: dict | None) -> tuple[dict[str, int], dict[str, str]]:
    """Pull the two override maps out of a tenants.settings dict.

    Tolerant on purpose: malformed stored values (wrong types, non-dict
    settings) are dropped instead of raising, so reading settings can never
    break the callers (API endpoints, scheduler, category color merge).
    """
    settings = settings or {}
    refresh_raw = settings.get("refresh_overrides")
    color_raw = settings.get("color_overrides")

    refresh: dict[str, int] = {}
    if isinstance(refresh_raw, dict):
        for slug, value in refresh_raw.items():
            if isinstance(slug, str) and isinstance(value, int) and not isinstance(value, bool):
                refresh[slug] = value

    color: dict[str, str] = {}
    if isinstance(color_raw, dict):
        for slug, value in color_raw.items():
            if isinstance(slug, str) and isinstance(value, str):
                color[slug] = value

    return refresh, color


async def load_tenant_settings(session: AsyncSession, tenant_id: str) -> dict:
    """Load one tenant's settings dict; degrade to {} on any error.

    Used by the scheduler (worker rebuild + source events): a settings read
    failure must never block scheduling — interval resolution falls back to
    the source/type defaults.
    """
    try:
        result = await session.execute(select(Tenant.settings).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none() or {}
    except Exception as exc:
        logger.warning(f"Failed to load settings for tenant {tenant_id}, using defaults: {exc}")
        return {}


async def load_all_tenant_settings(session: AsyncSession) -> dict[str, dict]:
    """Load settings for every tenant as {str(tenant_id): settings}.

    Single query consumed by the scheduler's full rebuild so N active sources
    across M tenants cost one settings read instead of N. Degrades to {} on
    any error (scheduling continues with source/type default intervals).
    """
    try:
        result = await session.execute(select(Tenant.id, Tenant.settings))
        return {str(tenant_id): (settings or {}) for tenant_id, settings in result.all()}
    except Exception as exc:
        logger.warning(f"Failed to load tenant settings for scheduler rebuild, using defaults: {exc}")
        return {}


class TenantSettingsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_overrides(self, tenant_id: str) -> SuccessResponse[TenantSettingsResponse]:
        tenant = await self._get_tenant_or_fail(tenant_id)
        refresh, color = extract_overrides(tenant.settings)
        return SuccessResponse(
            success=True,
            data=TenantSettingsResponse(refresh_overrides=refresh, color_overrides=color),
        )

    async def update_overrides(
        self,
        tenant_id: str,
        data: TenantSettingsUpdate,
    ) -> SuccessResponse[TenantSettingsResponse]:
        tenant = await self._get_tenant_or_fail(tenant_id)

        refresh_overrides = data.refresh_overrides or {}
        color_overrides = data.color_overrides or {}
        await self._validate_overrides(tenant_id, refresh_overrides, color_overrides)

        # Replace only the two override keys; every other settings key stays
        # untouched. Reassign a fresh dict so SQLAlchemy detects the JSONB
        # change without needing flag_modified.
        new_settings = dict(tenant.settings or {})
        new_settings["refresh_overrides"] = refresh_overrides
        new_settings["color_overrides"] = color_overrides
        tenant.settings = new_settings

        await self.db.flush()
        await self.db.refresh(tenant)

        refresh, color = extract_overrides(tenant.settings)
        return SuccessResponse(
            success=True,
            data=TenantSettingsResponse(refresh_overrides=refresh, color_overrides=color),
        )

    async def _get_tenant_or_fail(self, tenant_id: str) -> Tenant:
        result = await self.db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise ValidationError(message=f"Tenant not found: {tenant_id}")
        return tenant

    async def _valid_category_slugs(self, tenant_id: str) -> set[str]:
        # System (predefined) categories plus the tenant's own categories are
        # both legal override targets — exactly the set GET /categories can
        # return for this tenant.
        stmt = select(Category.slug).where(or_(Category.tenant_id == tenant_id, Category.tenant_id == SYSTEM_TENANT_ID))
        rows = (await self.db.execute(stmt)).scalars().all()
        return set(rows)

    async def _validate_overrides(
        self,
        tenant_id: str,
        refresh_overrides: dict,
        color_overrides: dict,
    ) -> None:
        details: list[dict[str, str]] = []

        valid_slugs = await self._valid_category_slugs(tenant_id)

        for slug, value in refresh_overrides.items():
            if slug not in valid_slugs:
                details.append(
                    {
                        "field": f"refresh_overrides.{slug}",
                        "message": f"Unknown category slug '{slug}' (must be a system or own category)",
                    }
                )
            if isinstance(value, bool) or not isinstance(value, int):
                details.append(
                    {
                        "field": f"refresh_overrides.{slug}",
                        "message": "Refresh override must be an integer number of seconds",
                    }
                )
            elif not (REFRESH_OVERRIDE_MIN_SECONDS <= value <= REFRESH_OVERRIDE_MAX_SECONDS):
                details.append(
                    {
                        "field": f"refresh_overrides.{slug}",
                        "message": (
                            f"Refresh override must be between {REFRESH_OVERRIDE_MIN_SECONDS} "
                            f"and {REFRESH_OVERRIDE_MAX_SECONDS} seconds"
                        ),
                    }
                )

        for slug, value in color_overrides.items():
            if slug not in valid_slugs:
                details.append(
                    {
                        "field": f"color_overrides.{slug}",
                        "message": f"Unknown category slug '{slug}' (must be a system or own category)",
                    }
                )
            if not isinstance(value, str) or not COLOR_PATTERN.match(value):
                details.append(
                    {
                        "field": f"color_overrides.{slug}",
                        "message": "Color override must match #RRGGBB (e.g. #FF0000)",
                    }
                )

        if details:
            raise ValidationError(message="Tenant settings validation failed", details=details)
