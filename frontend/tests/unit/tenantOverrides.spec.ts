/**
 * TenantOverrides (tenant-level refresh/color overrides for categories,
 * design: content-categories.md §3.4.4): the settings tab is only rendered
 * for role=admin users regardless of session entry; existing overrides load
 * into the per-category inputs; saving builds the two maps wholesale — rows
 * left empty are omitted from the payload, which clears their overrides per
 * the PUT replace semantics; backend 400 validation failures surface the
 * first error.details[] message via ErrorAlert, 403 degrades to a permission
 * hint.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { createPinia, setActivePinia } from "pinia";
import { apiClient } from "@/utils/api";
import { useAuthStore } from "@/stores/auth";
import SettingsView from "@/views/SettingsView.vue";
import TenantOverrides from "@/components/settings/TenantOverrides.vue";
import type { Category, TenantSettings, User } from "@/types";

function makeCategory(overrides: Partial<Category> = {}): Category {
  return {
    id: "cat-1",
    name: "财经",
    slug: "finance",
    description: "财经资讯",
    type: "finance",
    refresh_interval_seconds: 30,
    color: "#FF0000",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const ALL_CATEGORIES: Category[] = [
  makeCategory(),
  makeCategory({
    id: "cat-2",
    name: "科技",
    slug: "tech",
    type: "tech",
    refresh_interval_seconds: 300,
    color: "#3B82F6",
  }),
  makeCategory({
    id: "cat-3",
    name: "体育",
    slug: "sports",
    type: "custom",
    refresh_interval_seconds: 300,
    color: "#10B981",
  }),
];

const STORED_SETTINGS: TenantSettings = {
  refresh_overrides: { finance: 60 },
  color_overrides: { finance: "#FF6B6B" },
};

function okResponse(
  config: InternalAxiosRequestConfig,
  data: unknown,
): AxiosResponse {
  return {
    status: 200,
    statusText: "OK",
    data,
    headers: {},
    config,
  } as AxiosResponse;
}

function errorResponse(
  status: number,
  config: InternalAxiosRequestConfig,
  data: unknown,
): AxiosError {
  const response = {
    status,
    statusText: String(status),
    data,
    headers: {},
    config,
  } as AxiosResponse;
  return new AxiosError(
    `Request failed with status code ${status}`,
    String(status),
    config,
    null,
    response,
  );
}

function method(config: InternalAxiosRequestConfig): string {
  return (config.method ?? "").toLowerCase();
}

// Default stub: list of ALL_CATEGORIES + the stored overrides above
function stubPanelApi(
  putHandler?: (config: InternalAxiosRequestConfig) => AxiosResponse,
) {
  apiClient.defaults.adapter = async (config) => {
    if (method(config) === "get" && config.url === "/categories") {
      return okResponse(config, { success: true, data: ALL_CATEGORIES });
    }
    if (method(config) === "get" && config.url === "/tenant/settings") {
      return okResponse(config, { success: true, data: STORED_SETTINGS });
    }
    if (method(config) === "put" && config.url === "/tenant/settings") {
      if (putHandler) return putHandler(config);
      return okResponse(config, {
        success: true,
        data: JSON.parse(config.data as string),
      });
    }
    throw errorResponse(500, config, {});
  };
}

function rowInput(
  wrapper: ReturnType<typeof mount>,
  slug: string,
  cls: string,
): HTMLInputElement {
  return wrapper.find(`.override-row[data-slug="${slug}"] ${cls}`)
    .element as HTMLInputElement;
}

function makeUser(role: User["role"]): User {
  return {
    id: "u-1",
    email: "user@example.com",
    name: "用户",
    tenant_id: "t-1",
    role,
  };
}

// Session entry decides the base layout; the tenant tab additionally follows
// role, so set both before mounting
function mountSettingsAs(entry: string, role: User["role"]) {
  localStorage.clear();
  localStorage.setItem("session_entry", entry);
  setActivePinia(createPinia());
  useAuthStore().user = makeUser(role);
  apiClient.defaults.adapter = async (config) => {
    if (config.url === "/tenant/settings") {
      return okResponse(config, {
        success: true,
        data: { refresh_overrides: {}, color_overrides: {} },
      });
    }
    return okResponse(config, { success: true, data: [] });
  };
  return mount(SettingsView);
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("settings tab visibility", () => {
  it("shows the 租户覆盖 tab and renders the panel for role=admin", async () => {
    const wrapper = mountSettingsAs("admin", "admin");

    const tabs = wrapper.findAll(".tab-btn");
    expect(tabs.map((el) => el.text())).toContain("租户覆盖");

    await tabs[tabs.length - 1].trigger("click");
    await flushPromises();

    expect(wrapper.findComponent(TenantOverrides).exists()).toBe(true);
  });

  it("shows the tab for an admin-role user even on an sso-entry session", () => {
    const wrapper = mountSettingsAs("sso", "admin");

    expect(wrapper.findAll(".tab-btn").map((el) => el.text())).toContain(
      "租户覆盖",
    );
  });

  it("hides the tab for non-admin roles", () => {
    const wrapper = mountSettingsAs("admin", "member");

    expect(wrapper.findAll(".tab-btn").map((el) => el.text())).not.toContain(
      "租户覆盖",
    );
    expect(wrapper.findComponent(TenantOverrides).exists()).toBe(false);
  });
});

describe("loading existing overrides", () => {
  it("prefills inputs with stored overrides and leaves the rest empty", async () => {
    stubPanelApi();
    const wrapper = mount(TenantOverrides);
    await flushPromises();

    expect(rowInput(wrapper, "finance", ".input-refresh").value).toBe("60");
    expect(rowInput(wrapper, "finance", ".input-color").value).toBe("#FF6B6B");
    expect(rowInput(wrapper, "tech", ".input-refresh").value).toBe("");
    expect(rowInput(wrapper, "tech", ".input-color").value).toBe("");
    expect(rowInput(wrapper, "sports", ".input-refresh").value).toBe("");
    expect(rowInput(wrapper, "sports", ".input-color").value).toBe("");
    // Defaults stay visible per row
    expect(wrapper.find('.override-row[data-slug="finance"]').text()).toContain(
      "30s",
    );
  });
});

describe("save payload", () => {
  it("sends only filled rows; blanking an existing override clears it", async () => {
    let sent: unknown = null;
    stubPanelApi((config) => {
      sent = JSON.parse(config.data as string);
      return okResponse(config, { success: true, data: sent });
    });

    const wrapper = mount(TenantOverrides);
    await flushPromises();

    // Clear the stored finance refresh override
    await wrapper
      .find('.override-row[data-slug="finance"] .input-refresh')
      .setValue("");
    // Keep the stored finance color override
    // Add a new refresh override for tech and a color override for sports
    await wrapper
      .find('.override-row[data-slug="tech"] .input-refresh')
      .setValue("120");
    await wrapper
      .find('.override-row[data-slug="sports"] .input-color')
      .setValue("#00FF00");

    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();

    expect(sent).toEqual({
      refresh_overrides: { tech: 120 },
      color_overrides: { finance: "#FF6B6B", sports: "#00FF00" },
    });
    expect(wrapper.find(".save-message").text()).toBe("已保存");
    // The cleared finance refresh input stayed empty after the echo re-sync
    expect(rowInput(wrapper, "finance", ".input-refresh").value).toBe("");
  });

  it("rejects a non-integer refresh value without sending a request", async () => {
    const requests: string[] = [];
    stubPanelApi((config) => {
      requests.push(method(config));
      return okResponse(config, { success: true, data: {} });
    });

    const wrapper = mount(TenantOverrides);
    await flushPromises();

    await wrapper
      .find('.override-row[data-slug="tech"] .input-refresh')
      .setValue("12.5");
    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".error-alert").text()).toContain(
      "「科技」的刷新频率必须是整数秒",
    );
    expect(requests).toEqual([]);
  });
});

describe("save error paths", () => {
  it("400: shows the first error.details[] message in the ErrorAlert", async () => {
    stubPanelApi((config) => {
      throw errorResponse(400, config, {
        detail: {
          error: {
            code: "VALIDATION_ERROR",
            message: "Tenant settings validation failed",
            details: [
              {
                field: "refresh_overrides.tech",
                message:
                  "Refresh override must be between 10 and 86400 seconds",
              },
              {
                field: "color_overrides.sports",
                message: "Color override must match #RRGGBB (e.g. #FF0000)",
              },
            ],
          },
        },
      });
    });

    const wrapper = mount(TenantOverrides);
    await flushPromises();

    await wrapper
      .find('.override-row[data-slug="tech"] .input-refresh')
      .setValue("5");
    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();

    const alert = wrapper.find(".error-alert");
    expect(alert.text()).toContain(
      "Refresh override must be between 10 and 86400 seconds",
    );
    // Only the first detail entry is shown
    expect(alert.text()).not.toContain("Color override must match");
    expect(wrapper.find(".save-message").exists()).toBe(false);
  });

  it("403: degrades to a permission hint", async () => {
    stubPanelApi((config) => {
      throw errorResponse(403, config, {
        detail: {
          error: { code: "FORBIDDEN", message: "Admin role required" },
        },
      });
    });

    const wrapper = mount(TenantOverrides);
    await flushPromises();

    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".error-alert").text()).toContain("无权限");
  });
});
