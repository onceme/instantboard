/**
 * FundNAV panel §9.4 status notes: selecting a fund whose estimate snapshot
 * carries no live number shows the status note (label + tooltip) alongside
 * the existing method/status rows.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import FundNAV from "@/components/finance/FundNAV.vue";
import { useFinanceStore } from "@/stores/finance";
import type { FundNAVIntraday } from "@/types";

function nav(snapshot: Partial<FundNAVIntraday>): FundNAVIntraday {
  return {
    symbol: "017811",
    name: "东方人工智能主题混合C",
    nav_official: null,
    nav_official_date: null,
    nav_estimate: null,
    estimate_change_percent: null,
    estimate_method: "latest_official",
    coverage_percent: null,
    holdings_report_date: null,
    quote_status: "frozen",
    delayed_markets: [],
    holdings_stale: false,
    estimate_timestamp: "2026-09-01T06:50:00+00:00",
    ...snapshot,
  };
}

async function mountAndSelect(estimate: FundNAVIntraday) {
  const store = useFinanceStore();
  store.searchSymbols = vi.fn().mockResolvedValue(undefined);
  store.fetchFundNAVBatch = vi.fn().mockResolvedValue(undefined);
  // Seed what the (stubbed) search/batch would have produced.
  store.searchResults = [
    {
      symbol: estimate.symbol,
      name: estimate.name,
      type: "fund",
      market: "CN",
      exchange: "",
    },
  ];
  store.navEstimates = { [estimate.symbol]: estimate };

  const wrapper = mount(FundNAV);
  await wrapper.find(".search-input").setValue(estimate.symbol);
  // Debounce (300ms) before the search action fires.
  await new Promise((resolve) => setTimeout(resolve, 320));
  await flushPromises();

  const items = wrapper.findAll(".result-item");
  expect(items.length).toBeGreaterThan(0);
  await items[0].trigger("click");
  await flushPromises();
  return { store, wrapper };
}

describe("FundNAV status notes (§9.4)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows 官方净值待更新 for a frozen snapshot without an anchor", async () => {
    const { wrapper } = await mountAndSelect(nav({}));
    const note = wrapper.find(".nav-status-note");
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain("官方净值待更新");
    expect(note.attributes("title")).toContain("20:00");
  });

  it("shows 净值停更 when only the latest official NAV is available", async () => {
    const { wrapper } = await mountAndSelect(
      nav({
        nav_official: 1.66,
        nav_official_date: "2026-08-29",
        nav_estimate: 1.66,
      }),
    );
    expect(wrapper.find(".nav-status-note").text()).toContain("净值停更");
  });

  it("shows no status note for a healthy realtime estimate", async () => {
    const { wrapper } = await mountAndSelect(
      nav({
        estimate_method: "holdings_weighted",
        nav_official: 1.66,
        nav_estimate: 1.67,
        estimate_change_percent: 0.6,
        coverage_percent: 46.2,
        quote_status: "realtime",
        holdings_report_date: "2026-06-30",
      }),
    );
    expect(wrapper.find(".nav-detail").exists()).toBe(true);
    expect(wrapper.find(".nav-status-note").exists()).toBe(false);
  });
});
