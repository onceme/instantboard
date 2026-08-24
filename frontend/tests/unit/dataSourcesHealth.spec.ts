/**
 * DataSourcesHealth "类型" column regression: the column must show each data
 * source's type (source_type), not its UUID id — the cell used to be bound
 * to source.id.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import type { Pinia } from "pinia";
import { createPinia, setActivePinia } from "pinia";

import DataSourcesHealth from "@/components/dashboard/DataSourcesHealth.vue";
import { useDashboardStore } from "@/stores/dashboard";
import type { DataSourceHealthDetail } from "@/types";

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

let pinia: Pinia;

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

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
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
