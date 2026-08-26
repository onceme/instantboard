from pydantic import BaseModel, Field

# Tenant-level overrides for predefined/shared categories (design:
# docs/dev-guide/design/content-categories.md §3.4.4). refresh_overrides maps a
# category slug to the effective refresh interval in seconds; color_overrides
# maps a category slug to a display color. Both live inside tenants.settings
# and are validated by services/tenant.py (range/format/slug existence are
# checked there so failures surface as 400 VALIDATION_ERROR with details).
REFRESH_OVERRIDE_MIN_SECONDS = 10
REFRESH_OVERRIDE_MAX_SECONDS = 86400


class TenantSettingsUpdate(BaseModel):
    refresh_overrides: dict[str, int] | None = Field(default=None)
    color_overrides: dict[str, str] | None = Field(default=None)


class TenantSettingsResponse(BaseModel):
    refresh_overrides: dict[str, int] = Field(default_factory=dict)
    color_overrides: dict[str, str] = Field(default_factory=dict)
