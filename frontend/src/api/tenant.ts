import { apiGet, apiPut } from "@/utils/api";
import type { TenantSettings, TenantSettingsUpdate } from "@/types";

// Tenant-level category overrides (refresh cadence + display color), see
// content-categories.md §3.4.4. GET is readable by any tenant member; PUT is
// admin-only and replaces each provided override map wholesale.
export function getTenantSettings() {
  return apiGet<TenantSettings>("/tenant/settings");
}

export function updateTenantSettings(payload: TenantSettingsUpdate) {
  return apiPut<TenantSettings>("/tenant/settings", payload);
}
