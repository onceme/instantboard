/**
 * MarketTicker top quote strip (finance-tab.md §3.4.3): renders every market
 * index as name / value / change% items, duplicates the content for the
 * seamless CSS loop (translateX 0 → -50%), renders nothing without indices,
 * switches to the Indices sub-panel on item click and tags change% with the
 * global change-up/change-down/change-neutral classes.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import MarketTicker from "@/components/finance/MarketTicker.vue";
import { useFinanceStore } from "@/stores/finance";
import type { MarketIndex } from "@/types";

function makeIndex(overrides: Partial<MarketIndex> = {}): MarketIndex {
  return {
    symbol: "000001.SS",
    name: "上证综合指数",
    value: 3100.25,
    change: -8,
    change_percent: -0.26,
    market_status: "closed",
    market_status_reason: null,
    holiday_name: null,
    region: "CN",
    timestamp: "2026-08-26T00:00:00Z",
    ...overrides,
  };
}

function mountTicker(indices: MarketIndex[]) {
  const store = useFinanceStore();
  store.marketIndices = indices;
  return { store, wrapper: mount(MarketTicker) };
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("MarketTicker rendering", () => {
  it("renders name, value and change percent for every index", () => {
    const { wrapper } = mountTicker([
      makeIndex(),
      makeIndex({
        symbol: "^GSPC",
        name: "S&P 500",
        value: 5650.5,
        change: 12.4,
        change_percent: 1.25,
        market_status: "open",
        region: "US",
      }),
    ]);

    // Content is duplicated for the seamless loop: 2 indices → 4 items
    const items = wrapper.findAll(".ticker-item");
    expect(items).toHaveLength(4);

    expect(items[0].find(".item-name").text()).toBe("上证综合指数");
    expect(items[0].find(".item-value").text()).toBe("3,100.25");
    expect(items[0].find(".item-change").text()).toBe("-0.26%");

    expect(items[1].find(".item-name").text()).toBe("S&P 500");
    expect(items[1].find(".item-value").text()).toBe("5,650.50");
    expect(items[1].find(".item-change").text()).toBe("+1.25%");
  });

  it("marks the duplicated loop copy as aria-hidden", () => {
    const { wrapper } = mountTicker([makeIndex()]);

    const groups = wrapper.findAll(".ticker-group");
    expect(groups).toHaveLength(2);
    expect(groups[0].attributes("aria-hidden")).toBeUndefined();
    expect(groups[1].attributes("aria-hidden")).toBe("true");
  });

  it("scales the animation duration with the number of items", () => {
    const { wrapper } = mountTicker([
      makeIndex(),
      makeIndex({ symbol: "^GSPC", name: "S&P 500", region: "US" }),
      makeIndex({ symbol: "^HSI", name: "恒生指数", region: "HK" }),
    ]);

    expect(wrapper.find(".ticker-track").attributes("style")).toContain(
      "animation-duration: 18s",
    );
  });

  it("renders nothing when there are no indices", () => {
    const { wrapper } = mountTicker([]);

    expect(wrapper.find(".market-ticker").exists()).toBe(false);
    expect(wrapper.text()).toBe("");
  });
});

describe("MarketTicker change colors", () => {
  it("uses the global change-up/change-down/change-neutral classes", () => {
    const { wrapper } = mountTicker([
      makeIndex({ symbol: "UP", change_percent: 1.5 }),
      makeIndex({ symbol: "DOWN", change_percent: -2 }),
      makeIndex({ symbol: "FLAT", change_percent: 0 }),
    ]);

    const changes = wrapper.findAll(".ticker-item .item-change");
    expect(changes[0].classes()).toContain("change-up");
    expect(changes[1].classes()).toContain("change-down");
    expect(changes[2].classes()).toContain("change-neutral");
  });
});

describe("MarketTicker panel switching", () => {
  it("switches to the Indices sub-panel when an item is clicked", async () => {
    const { store, wrapper } = mountTicker([makeIndex()]);
    expect(store.currentPanel).toBe("overview");

    await wrapper.find(".ticker-item").trigger("click");

    expect(store.currentPanel).toBe("indices");
  });
});
