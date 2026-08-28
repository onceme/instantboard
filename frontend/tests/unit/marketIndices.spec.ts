/**
 * MarketIndices panel holiday support (finance-tab.md §3.4.4): the status
 * pill renders "休市 · <holiday_name>" when the backend reports a
 * closed-by-holiday market, keeps the plain open/closed labels otherwise,
 * and marks the holiday pill with the index-status-holiday class.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import MarketIndices from "@/components/finance/MarketIndices.vue";
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
    timestamp: "2026-10-01T02:00:00Z",
    ...overrides,
  };
}

function mountPanel(indices: MarketIndex[]) {
  const store = useFinanceStore();
  store.marketIndices = indices;
  return mount(MarketIndices);
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("MarketIndices holiday hint", () => {
  it("shows the holiday name in the status pill when closed for a holiday", () => {
    const wrapper = mountPanel([
      makeIndex({
        market_status: "closed",
        market_status_reason: "holiday",
        holiday_name: "国庆节",
      }),
    ]);

    const status = wrapper.find(".index-status");
    expect(status.text()).toBe("休市 · 国庆节");
    expect(status.classes()).toContain("index-status-holiday");
  });

  it("keeps the plain closed label for weekend/off-hours closures", () => {
    const wrapper = mountPanel([
      makeIndex({ market_status: "closed", market_status_reason: "weekend" }),
      makeIndex({
        symbol: "^GSPC",
        name: "S&P 500",
        market_status: "closed",
        market_status_reason: "off_hours",
        region: "US",
      }),
    ]);

    const statuses = wrapper.findAll(".index-status");
    expect(statuses.map((s) => s.text())).toEqual(["休市", "休市"]);
    expect(
      statuses.some((s) => s.classes().includes("index-status-holiday")),
    ).toBe(false);
  });

  it("shows the open label while the market is open (reason null)", () => {
    const wrapper = mountPanel([
      makeIndex({ market_status: "open", market_status_reason: null }),
    ]);

    const status = wrapper.find(".index-status");
    expect(status.text()).toBe("开盘");
    expect(status.classes()).not.toContain("index-status-holiday");
  });

  it("tolerates legacy payloads without the closed-reason fields", () => {
    const legacy = makeIndex();
    delete (legacy as Partial<MarketIndex>).market_status_reason;
    delete (legacy as Partial<MarketIndex>).holiday_name;
    const wrapper = mountPanel([legacy]);

    expect(wrapper.find(".index-status").text()).toBe("休市");
  });
});
