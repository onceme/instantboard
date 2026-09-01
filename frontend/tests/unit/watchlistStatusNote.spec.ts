/**
 * Watchlist fund-row §9.4 status notes: a fund without a live intraday
 * estimate explains itself (官方净值待更新 / 净值停更 …) instead of showing a
 * bare "--"; healthy realtime estimates render no note.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import Watchlist from "@/components/finance/Watchlist.vue";
import { useFinanceStore } from "@/stores/finance";
import type { FundNAVIntraday, WatchlistItem } from "@/types";

function makeItem(symbol: string, id: string): WatchlistItem {
  return {
    id,
    symbol,
    name: symbol,
    display_order: 0,
    created_at: "2026-09-01T00:00:00Z",
  } as WatchlistItem;
}

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

async function mountWatchlist(symbol: string, estimate: FundNAVIntraday) {
  const store = useFinanceStore();
  store.updateWatchlistAlert = vi.fn().mockResolvedValue({});
  store.watchlist = [makeItem(symbol, "w1")];
  store.navEstimates = { [symbol]: estimate };
  const wrapper = mount(Watchlist);
  return { store, wrapper };
}

describe("Watchlist fund status notes (§9.4)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows 官方净值待更新 for a freshly followed fund (frozen, no anchor)", async () => {
    const { wrapper } = await mountWatchlist("017811", nav({}));
    const note = wrapper.find(".nav-status-note");
    expect(note.exists()).toBe(true);
    expect(note.text()).toBe("官方净值待更新");
    expect(note.attributes("title")).toContain("20:00");
    // The note replaces the missing estimate — no lone "--" value.
    expect(wrapper.find(".item-nav .nav-value").exists()).toBe(false);
  });

  it("shows 净值停更 next to the official value when only the anchor exists", async () => {
    const { wrapper } = await mountWatchlist(
      "017811",
      nav({
        nav_official: 1.66,
        nav_official_date: "2026-08-29",
        nav_estimate: 1.66,
      }),
    );
    expect(wrapper.find(".item-nav .nav-value").text()).toContain("1.66");
    const note = wrapper.find(".nav-status-note");
    expect(note.exists()).toBe(true);
    expect(note.text()).toBe("净值停更");
  });

  it("shows the disclosure-anomaly note when holdings are stale and no estimate exists", async () => {
    const { wrapper } = await mountWatchlist(
      "017811",
      nav({ holdings_stale: true, holdings_report_date: "2022-06-30" }),
    );
    expect(wrapper.find(".nav-status-note").text()).toBe(
      "盘中估值不可用·持仓披露异常",
    );
  });

  it("renders no note for a healthy realtime estimate", async () => {
    const { wrapper } = await mountWatchlist(
      "017811",
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
    expect(wrapper.find(".nav-status-note").exists()).toBe(false);
    expect(wrapper.find(".item-nav .nav-value").exists()).toBe(true);
  });
});
