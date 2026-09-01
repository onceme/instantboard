/**
 * SearchSymbols suggestion panel (UX regression pass for the 017811 search):
 * the panel stays mounted for the whole query lifetime — loading and
 * "no match" states render inside it instead of collapsing it — and every
 * candidate row carries a quick "＋关注" action (success → 「✓已关注」,
 * backend 409 duplicate → friendly 「已在自选」).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import SearchSymbols from "@/components/finance/SearchSymbols.vue";
import { useFinanceStore } from "@/stores/finance";
import type { SearchResult, WatchlistItem } from "@/types";

function row(symbol: string, name: string): SearchResult {
  return { symbol, name, type: "fund", market: "CN", exchange: "" };
}

async function mountSearch() {
  const store = useFinanceStore();
  // The store owns the race guard (covered in storeFinanceSearch.spec); here
  // the component's rendering/interaction is under test, so the search action
  // is stubbed and panel states are driven directly.
  store.searchSymbols = vi.fn();
  const wrapper = mount(SearchSymbols);
  await wrapper.find(".search-input").setValue("017811");
  return { store, wrapper };
}

describe("SearchSymbols suggestion panel stability", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows a loading row while the latest request is in flight", async () => {
    const { store, wrapper } = await mountSearch();
    store.searchLoading = true;
    store.searchResults = [];
    await flushPromises();

    const status = wrapper.find(".search-status");
    expect(status.exists()).toBe(true);
    expect(status.text()).toContain("搜索中");
    // Panel container stays rendered — nothing collapses mid-query.
    expect(wrapper.find(".search-results").exists()).toBe(true);
  });

  it("keeps the panel visible with a 无匹配结果 placeholder on empty results", async () => {
    const { store, wrapper } = await mountSearch();
    store.searchLoading = false;
    store.searchResults = [];
    await flushPromises();

    expect(wrapper.find(".search-results").exists()).toBe(true);
    const status = wrapper.find(".search-status");
    expect(status.exists()).toBe(true);
    expect(status.text()).toContain("无匹配结果");
  });

  it("renders candidate rows once results land", async () => {
    const { store, wrapper } = await mountSearch();
    store.searchLoading = false;
    store.searchResults = [
      row("005844", "东方人工智能主题混合A"),
      row("017811", "东方人工智能主题混合C"),
    ];
    await flushPromises();

    const items = wrapper.findAll(".result-item");
    expect(items).toHaveLength(2);
    expect(items[1].text()).toContain("017811");
    // Quick-watch entry point is discoverable on every row.
    expect(wrapper.findAll(".watch-quick-btn")).toHaveLength(2);
  });

  it("hides the panel when the query is cleared", async () => {
    const { wrapper } = await mountSearch();
    expect(wrapper.find(".search-results").exists()).toBe(true);

    await wrapper.find(".search-input").setValue("");
    await flushPromises();

    expect(wrapper.find(".search-results").exists()).toBe(false);
    expect(wrapper.find(".search-empty").exists()).toBe(true);
  });
});

describe("SearchSymbols quick watch action", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  async function mountWithResults(
    results: SearchResult[],
    watchlist: WatchlistItem[] = [],
  ) {
    const store = useFinanceStore();
    store.searchSymbols = vi.fn();
    store.watchlist = watchlist;
    store.addToWatchlist = vi.fn();
    const wrapper = mount(SearchSymbols);
    await wrapper.find(".search-input").setValue("017811");
    store.searchLoading = false;
    store.searchResults = results;
    await flushPromises();
    return { store, wrapper };
  }

  it("adds to the watchlist and flips the row to 「✓已关注」(disabled)", async () => {
    const { store, wrapper } = await mountWithResults([
      row("017811", "东方人工智能主题混合C"),
    ]);
    (store.addToWatchlist as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: "w1",
      symbol: "017811",
      display_order: 0,
      created_at: "2026-09-01T07:00:00Z",
    });

    const btn = wrapper.find(".watch-quick-btn");
    expect(btn.text()).toBe("＋关注");
    await btn.trigger("click");
    await flushPromises();

    expect(store.addToWatchlist).toHaveBeenCalledWith("017811");
    const updated = wrapper.find(".watch-quick-btn");
    expect(updated.text()).toBe("✓已关注");
    expect(updated.attributes("disabled")).toBeDefined();
    expect(updated.classes()).toContain("watch-added");
  });

  it("treats a 409 duplicate as already watched with a 「已在自选」 note", async () => {
    const { store, wrapper } = await mountWithResults([
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

    const btn = wrapper.find(".watch-quick-btn");
    expect(btn.text()).toBe("✓已关注");
    expect(btn.attributes("disabled")).toBeDefined();
    expect(wrapper.find(".watch-note").text()).toBe("已在自选");
  });

  it("shows rows already on the watchlist as watched without calling the API", async () => {
    const { store, wrapper } = await mountWithResults(
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

  it("surfaces a friendly inline error and stays clickable on other failures", async () => {
    const { store, wrapper } = await mountWithResults([
      row("017811", "东方人工智能主题混合C"),
    ]);
    (store.addToWatchlist as ReturnType<typeof vi.fn>).mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 503, data: {} },
    });

    await wrapper.find(".watch-quick-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".watch-note").text()).toContain("加入自选失败");
    const btn = wrapper.find(".watch-quick-btn");
    expect(btn.text()).toBe("＋关注");
    expect(btn.attributes("disabled")).toBeUndefined();
  });
});
