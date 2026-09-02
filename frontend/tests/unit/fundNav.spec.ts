/**
 * FundNAV panel quick-watch (regression for the 017811 UX gap): the fund
 * panel's search results and the selected-fund detail both carry a 「＋关注」
 * entry point — behavior aligned with SearchSymbols (success → 「✓已关注」,
 * backend 409 duplicate → friendly 「已在自选」). Following a fund is what
 * pulls it into the intraday estimate loop, so the button is the panel's
 * canonical "make this fund show a live estimate" action.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import FundNAV from "@/components/finance/FundNAV.vue";
import { useFinanceStore } from "@/stores/finance";
import type { SearchResult, WatchlistItem } from "@/types";

function row(symbol: string, name: string): SearchResult {
  return { symbol, name, type: "fund", market: "CN", exchange: "" };
}

async function mountAndSearch(
  results: SearchResult[],
  watchlist: WatchlistItem[] = [],
) {
  const store = useFinanceStore();
  store.searchSymbols = vi.fn();
  store.fetchFundNAVBatch = vi.fn();
  store.addToWatchlist = vi.fn();
  store.watchlist = watchlist;
  store.searchResults = results;
  const wrapper = mount(FundNAV);
  await wrapper.find(".search-input").setValue("017811");
  // Debounced search: advance past the 300ms input timer.
  vi.advanceTimersByTime(350);
  await flushPromises();
  return { store, wrapper };
}

describe("FundNAV search results quick watch", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => vi.useRealTimers());

  it("renders a 「＋关注」 button on every search result row", async () => {
    const { wrapper } = await mountAndSearch([
      row("005844", "东方人工智能主题混合A"),
      row("017811", "东方人工智能主题混合C"),
    ]);
    const buttons = wrapper.findAll(".watch-quick-btn");
    expect(buttons).toHaveLength(2);
    expect(buttons[1].text()).toBe("＋关注");
  });

  it("adds to the watchlist and flips to 「✓已关注」 without selecting the fund", async () => {
    const { store, wrapper } = await mountAndSearch([
      row("017811", "东方人工智能主题混合C"),
    ]);
    (store.addToWatchlist as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: "w1",
      symbol: "017811",
      display_order: 0,
      created_at: "2026-09-01T07:00:00Z",
    });

    const btn = wrapper.find(".watch-quick-btn");
    await btn.trigger("click");
    await flushPromises();

    expect(store.addToWatchlist).toHaveBeenCalledWith("017811");
    const updated = wrapper.find(".watch-quick-btn");
    expect(updated.text()).toBe("✓已关注");
    expect(updated.attributes("disabled")).toBeDefined();
    // The click must not select the fund (row click opens the detail).
    expect(wrapper.find(".nav-detail").exists()).toBe(false);
  });

  it("treats a 409 duplicate as already watched with a 「已在自选」 note", async () => {
    const { store, wrapper } = await mountAndSearch([
      row("017811", "东方人工智能主题混合C"),
    ]);
    (store.addToWatchlist as ReturnType<typeof vi.fn>).mockRejectedValueOnce({
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          detail: {
            error: {
              code: "DUPLICATE_WATCHLIST_ITEM",
              message: "Watchlist item already exists",
            },
          },
        },
      },
    });

    await wrapper.find(".watch-quick-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".watch-quick-btn").text()).toBe("✓已关注");
    expect(wrapper.find(".watch-note").text()).toBe("已在自选");
  });

  it("shows rows already on the watchlist as watched without calling the API", async () => {
    const { store, wrapper } = await mountAndSearch(
      [row("017811", "东方人工智能主题混合C")],
      [
        {
          id: "w1",
          symbol: "017811",
          display_order: 0,
          created_at: "2026-09-01T07:00:00Z",
        } as WatchlistItem,
      ],
    );

    const btn = wrapper.find(".watch-quick-btn");
    expect(btn.text()).toBe("✓已关注");
    expect(btn.attributes("disabled")).toBeDefined();
    await btn.trigger("click");
    await flushPromises();
    expect(store.addToWatchlist).not.toHaveBeenCalled();
  });
});

describe("FundNAV selected-fund detail quick watch", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => vi.useRealTimers());

  it("carries a watch button in the detail header", async () => {
    const { store, wrapper } = await mountAndSearch([
      row("017811", "东方人工智能主题混合C"),
    ]);
    store.navEstimates["017811"] = {
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
      estimate_timestamp: "2026-09-01T07:00:00Z",
    };

    await wrapper.find(".result-item").trigger("click");
    await flushPromises();

    expect(wrapper.find(".nav-detail").exists()).toBe(true);
    const detailBtn = wrapper.find(".nav-header .watch-quick-btn");
    expect(detailBtn.exists()).toBe(true);
    expect(detailBtn.text()).toBe("＋关注");

    (store.addToWatchlist as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: "w1",
      symbol: "017811",
      display_order: 0,
      created_at: "2026-09-01T07:00:00Z",
    });
    await detailBtn.trigger("click");
    await flushPromises();

    expect(store.addToWatchlist).toHaveBeenCalledWith("017811");
    expect(wrapper.find(".nav-header .watch-quick-btn").text()).toBe("✓已关注");
  });
});
