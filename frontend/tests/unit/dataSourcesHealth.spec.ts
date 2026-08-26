/**
 * DataSourcesHealth table: the "类型" column must show each data source's
 * source_type (never its UUID); the status/type/name filters apply
 * individually and stacked; pagination serves 20 rows per page and resets to
 * page 1 when any filter changes; expanding a row lazily fetches
 * GET /dashboard/data-sources/{id} once (cached for re-expand) and renders
 * the detail panel, with inline error + retry on failure.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import type { Pinia } from "pinia";
import { createPinia, setActivePinia } from "pinia";

import DataSourcesHealth from "@/components/dashboard/DataSourcesHealth.vue";
import { useDashboardStore } from "@/stores/dashboard";
import { apiClient } from "@/utils/api";
import type {
  DataSourceHealthDetail,
  DataSourceHealthDetailResponse,
} from "@/types";

const SOURCE_UUID = "3f2c9a1e-0b6d-4e8f-9a12-b7c5d4e3f2a1";

function makeDetail(
  overrides: Partial<DataSourceHealthDetail> = {},
): DataSourceHealthDetail {
  return {
    id: SOURCE_UUID,
    name: "Hacker News",
    source_type: "rss",
    status: "healthy",
    success_rate_24h: 99.5,
    avg_response_time_ms: 250,
    last_success_at: "2026-08-11T09:00:00Z",
    last_failure_at: "2026-08-10T09:00:00Z",
    consecutive_failures: 0,
    total_fetches_24h: 288,
    ...overrides,
  };
}

function makeDetailResponse(
  overrides: Partial<DataSourceHealthDetailResponse> = {},
): DataSourceHealthDetailResponse {
  return {
    source_id: SOURCE_UUID,
    name: "Hacker News",
    source_type: "rss",
    status: "degraded",
    success_rate_24h: 0.995,
    avg_response_time_ms: 250,
    last_success_at: "2026-08-11T09:00:00Z",
    last_failure_at: "2026-08-11T08:30:00Z",
    consecutive_failures: 3,
    total_fetches_24h: 288,
    last_error: "upstream timeout",
    health_history: [
      {
        status: "degraded",
        last_success_at: "2026-08-11T09:00:00Z",
        last_failure_at: "2026-08-11T08:30:00Z",
        total_fetches_24h: 288,
        last_error_message: "upstream timeout",
      },
    ],
    response_time_trend: [
      { ts: "2026-08-11T08:00:00Z", ms: 200 },
      { ts: "2026-08-11T08:30:00Z", ms: 400 },
    ],
    ...overrides,
  };
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

let pinia: Pinia;
let detailRequests: InternalAxiosRequestConfig[];

function mountWithSources(sources: DataSourceHealthDetail[]) {
  const store = useDashboardStore();
  store.dataSources = {
    total_sources: sources.length,
    healthy: sources.filter((s) => s.status === "healthy").length,
    degraded: sources.filter((s) => s.status === "degraded").length,
    down: sources.filter((s) => s.status === "down").length,
    sources,
  };
  return mount(DataSourcesHealth, { global: { plugins: [pinia] } });
}

// Serve GET /dashboard/data-sources/{sourceId} and count the calls
function installDetailAdapter(
  sourceId: string,
  handler?: () => { data?: DataSourceHealthDetailResponse },
) {
  apiClient.defaults.adapter = async (config) => {
    if (config.url === `/dashboard/data-sources/${sourceId}`) {
      detailRequests.push(config);
      const payload = handler ? handler() : { data: makeDetailResponse() };
      if (!payload.data) {
        throw errorResponse(500, config, {});
      }
      return okResponse(config, { success: true, data: payload.data });
    }
    throw errorResponse(404, config, {});
  };
}

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  detailRequests = [];
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("type column", () => {
  it("renders each row's source_type and never the UUID id", () => {
    const wrapper = mountWithSources([
      makeDetail(),
      makeDetail({
        id: "8d0a1b2c-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        name: "Eastmoney",
        source_type: "web_scrape",
        status: "down",
      }),
    ]);

    const rows = wrapper.findAll("tbody tr");
    expect(rows).toHaveLength(2);

    const typeCells = rows.map((row) => row.findAll("td")[1].text());
    expect(typeCells).toEqual(["rss", "web_scrape"]);

    expect(wrapper.text()).not.toContain(SOURCE_UUID);
  });

  it("shows -- when the source has no source_type", () => {
    const wrapper = mountWithSources([makeDetail({ source_type: null })]);

    expect(wrapper.findAll("tbody tr td")[1].text()).toBe("--");
  });
});

function makeManySources(count: number): DataSourceHealthDetail[] {
  const statuses = ["healthy", "degraded", "down"] as const;
  const types = ["rss", "api", "web_scrape"] as const;
  return Array.from({ length: count }, (_, i) =>
    makeDetail({
      id: `src-${i}`,
      name: `Source ${i}`,
      status: statuses[i % statuses.length],
      source_type: types[i % types.length],
    }),
  );
}

describe("filters", () => {
  const threeSources = [
    makeDetail({ id: "s1", name: "Alpha Feed", source_type: "rss", status: "healthy" }),
    makeDetail({ id: "s2", name: "Beta API", source_type: "api", status: "degraded" }),
    makeDetail({ id: "s3", name: "Gamma Scrape", source_type: "rss", status: "down" }),
  ];

  it("filters by status", async () => {
    const wrapper = mountWithSources(threeSources);
    expect(wrapper.findAll(".source-row")).toHaveLength(3);

    await wrapper.find(".filter-status").setValue("down");

    const rows = wrapper.findAll(".source-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("Gamma Scrape");
  });

  it("filters by type using the distinct source_type values", async () => {
    const wrapper = mountWithSources(threeSources);

    const options = wrapper
      .find(".filter-type")
      .findAll("option")
      .map((o) => o.element.value);
    expect(options).toEqual(["", "api", "rss"]);

    await wrapper.find(".filter-type").setValue("rss");

    const rows = wrapper.findAll(".source-row");
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.text())).toEqual(
      expect.arrayContaining([
        expect.stringContaining("Alpha Feed"),
        expect.stringContaining("Gamma Scrape"),
      ]),
    );
  });

  it("filters by name keyword (case-insensitive)", async () => {
    const wrapper = mountWithSources(threeSources);

    await wrapper.find(".filter-search").setValue("beta");

    const rows = wrapper.findAll(".source-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("Beta API");
  });

  it("stacks status + type + keyword filters", async () => {
    const wrapper = mountWithSources([
      ...threeSources,
      makeDetail({ id: "s4", name: "Delta Feed", source_type: "rss", status: "healthy" }),
    ]);

    await wrapper.find(".filter-status").setValue("healthy");
    await wrapper.find(".filter-type").setValue("rss");
    await wrapper.find(".filter-search").setValue("alpha");

    const rows = wrapper.findAll(".source-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("Alpha Feed");
  });

  it("shows the EmptyState when nothing matches", async () => {
    const wrapper = mountWithSources(threeSources);

    await wrapper.find(".filter-search").setValue("no-such-source");

    expect(wrapper.find(".source-row").exists()).toBe(false);
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.find(".empty-state").text()).toContain("没有匹配的数据源");
  });
});

describe("pagination", () => {
  it("shows at most 20 rows per page and switches pages", async () => {
    const wrapper = mountWithSources(makeManySources(25));

    expect(wrapper.findAll(".source-row")).toHaveLength(20);
    expect(wrapper.find(".footer-count").text()).toContain("25");

    await wrapper.find(".nav-next").trigger("click");
    await flushPromises();

    const rows = wrapper.findAll(".source-row");
    expect(rows).toHaveLength(5);
    expect(rows[0].text()).toContain("Source 20");
  });

  it("resets to page 1 when a filter changes", async () => {
    // 65 sources, statuses rotate healthy/degraded/down -> 22 healthy rows
    // stay spread over 2 pages after filtering
    const wrapper = mountWithSources(makeManySources(65));
    await wrapper.find(".nav-next").trigger("click");
    await flushPromises();
    expect(wrapper.find(".source-row").text()).toContain("Source 20");

    await wrapper.find(".filter-status").setValue("healthy");
    await flushPromises();

    expect(wrapper.findAll(".source-row")).toHaveLength(20);
    expect(wrapper.find(".nav-prev").attributes("disabled")).toBeDefined();
    expect(wrapper.find(".source-row").text()).toContain("Source 0");
  });
});

describe("row expansion", () => {
  it("fetches the detail endpoint on first expand and renders the panel", async () => {
    installDetailAdapter(SOURCE_UUID);
    const wrapper = mountWithSources([makeDetail()]);

    await wrapper.find(".source-row").trigger("click");
    await flushPromises();

    expect(detailRequests).toHaveLength(1);
    expect(detailRequests[0].url).toBe(`/dashboard/data-sources/${SOURCE_UUID}`);

    const detail = wrapper.find(".detail-row");
    expect(detail.exists()).toBe(true);
    // 0..1 ratio rendered as percentage
    expect(detail.text()).toContain("99.5%");
    expect(detail.text()).toContain("288");
    expect(detail.text()).toContain("3");
    expect(detail.text()).toContain("upstream timeout");
    expect(detail.findAll(".history-item")).toHaveLength(1);
    expect(detail.findAll(".trend-bar")).toHaveLength(2);
  });

  it("collapses on second click and does not re-fetch on re-expand", async () => {
    installDetailAdapter(SOURCE_UUID);
    const wrapper = mountWithSources([makeDetail()]);

    await wrapper.find(".source-row").trigger("click");
    await flushPromises();
    expect(detailRequests).toHaveLength(1);

    await wrapper.find(".source-row").trigger("click");
    expect(wrapper.find(".detail-row").exists()).toBe(false);

    await wrapper.find(".source-row").trigger("click");
    await flushPromises();

    expect(wrapper.find(".detail-row").exists()).toBe(true);
    expect(detailRequests).toHaveLength(1);
  });

  it("shows an inline error on failure and re-requests via retry", async () => {
    let fail = true;
    installDetailAdapter(SOURCE_UUID, () => {
      if (fail) return {};
      return { data: makeDetailResponse() };
    });
    const wrapper = mountWithSources([makeDetail()]);

    await wrapper.find(".source-row").trigger("click");
    await flushPromises();

    expect(wrapper.find(".detail-row .detail-error").exists()).toBe(true);
    expect(wrapper.find(".detail-row").text()).toContain("加载详情失败");

    fail = false;
    await wrapper.find(".detail-retry").trigger("click");
    await flushPromises();

    expect(detailRequests).toHaveLength(2);
    expect(wrapper.find(".detail-row .detail-panel").exists()).toBe(true);
  });
});
