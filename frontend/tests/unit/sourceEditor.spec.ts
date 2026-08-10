/**
 * SourceEditor regressions: a source with collector_available === false can no
 * longer be enabled (toggle disabled + "无可用采集器" hint), while legacy
 * payloads without the field stay toggleable; PUT /sources/{id} failures
 * (400 NO_COLLECTOR_AVAILABLE / 403 FORBIDDEN) are surfaced via the error
 * alert instead of being swallowed silently.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { apiClient } from "@/utils/api";
import SourceEditor from "@/components/settings/SourceEditor.vue";
import type { Source } from "@/types";

function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    id: "src-1",
    name: "测试源",
    category_id: "cat-1",
    source_type: "social",
    url: "https://example.com/feed",
    refresh_interval_seconds: 300,
    is_active: false,
    health_status: "healthy",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    collector_available: true,
    ...overrides,
  };
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

function method(config: InternalAxiosRequestConfig): string {
  return (config.method ?? "").toLowerCase();
}

// Adapter that serves the given source list and records PUT calls
function installAdapter(
  sourceList: Source[],
  putHandler?: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>,
) {
  apiClient.defaults.adapter = async (config) => {
    if (method(config) === "get" && config.url === "/sources") {
      return okResponse(config, { success: true, data: sourceList });
    }
    if (method(config) === "get" && config.url === "/categories") {
      return okResponse(config, { success: true, data: [] });
    }
    if (method(config) === "put" && config.url === "/sources/src-1") {
      if (putHandler) return putHandler(config);
      return okResponse(config, { success: true, data: sourceList[0] });
    }
    throw errorResponse(500, config, {});
  };
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("collector_available gating", () => {
  it("disables the enable toggle and explains why when no collector exists", async () => {
    installAdapter([makeSource({ collector_available: false })]);

    const wrapper = mount(SourceEditor);
    await flushPromises();

    const toggle = wrapper.find(".toggle-btn");
    expect(toggle.attributes("disabled")).toBeDefined();
    expect(toggle.attributes("title")).toBe("无可用采集器，无法启用");
    // Reason is visible on the row itself, not only in a tooltip
    expect(wrapper.find(".collector-badge").text()).toBe("无可用采集器");
  });

  it("keeps the toggle enabled for legacy sources without collector_available", async () => {
    const legacy = makeSource();
    delete legacy.collector_available;
    installAdapter([legacy]);

    const wrapper = mount(SourceEditor);
    await flushPromises();

    const toggle = wrapper.find(".toggle-btn");
    expect(toggle.attributes("disabled")).toBeUndefined();
    expect(wrapper.find(".collector-badge").exists()).toBe(false);
  });
});

describe("toggle error paths", () => {
  it("shows the backend message when enabling fails (NO_COLLECTOR_AVAILABLE)", async () => {
    installAdapter([makeSource()], async (config) => {
      throw errorResponse(400, config, {
        detail: {
          error: {
            code: "NO_COLLECTOR_AVAILABLE",
            message: "该类型数据源暂无可用采集器，无法启用",
          },
        },
      });
    });

    const wrapper = mount(SourceEditor);
    await flushPromises();

    await wrapper.find(".toggle-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".error-alert").text()).toContain(
      "该类型数据源暂无可用采集器，无法启用",
    );
  });

  it("falls back to the admin-only message on a bare 403", async () => {
    installAdapter([makeSource()], async (config) => {
      throw errorResponse(403, config, {
        detail: { error: { code: "FORBIDDEN" } },
      });
    });

    const wrapper = mount(SourceEditor);
    await flushPromises();

    await wrapper.find(".toggle-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".error-alert").text()).toContain(
      "仅管理员可修改系统数据源",
    );
  });
});
