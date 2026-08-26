/**
 * Finance store regression: per-endpoint error state is written on failure
 * (so panels show an ErrorAlert instead of a silent empty list) and cleared
 * again on a successful retry. Only the data-loading methods are exercised —
 * init()/connectSSE() are never triggered.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    apiGet: vi.fn(),
    apiPost: vi.fn(),
    apiPut: vi.fn(),
    apiDelete: vi.fn(),
  };
});

import { apiGet, apiPut } from "@/utils/api";
import { useFinanceStore } from "@/stores/finance";
import type { WatchlistItem } from "@/types";

const mockApiGet = vi.mocked(apiGet);
const mockApiPut = vi.mocked(apiPut);

function makeWatchlistItem(
  id: string,
  symbol: string,
  displayOrder: number,
): WatchlistItem {
  return {
    id,
    symbol,
    display_order: displayOrder,
    created_at: "2026-08-26T00:00:00Z",
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("getMarketIndices error state", () => {
  it("writes marketIndicesError on failure and keeps stale data empty", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("503 upstream"));
    const store = useFinanceStore();

    await store.getMarketIndices();

    expect(store.marketIndicesError).toBe("加载市场指数失败，请稍后重试。");
    expect(store.marketIndices).toEqual([]);
    expect(mockApiGet).toHaveBeenCalledWith("/finance/market-indices");
  });

  it("clears the previous error on a successful retry", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("boom"));
    const store = useFinanceStore();
    await store.getMarketIndices();
    expect(store.marketIndicesError).not.toBeNull();

    mockApiGet.mockResolvedValueOnce({
      success: true,
      data: [{ symbol: "000001.SS", current_price: 3500 }],
    });
    await store.getMarketIndices();

    expect(store.marketIndicesError).toBeNull();
    expect(store.marketIndices).toHaveLength(1);
    expect(store.marketIndices[0].symbol).toBe("000001.SS");
  });
});

describe("getCommodities error state", () => {
  it("writes commoditiesError on failure", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("502"));
    const store = useFinanceStore();

    await store.getCommodities();

    expect(store.commoditiesError).toBe("加载大宗商品失败，请稍后重试。");
    expect(store.commodities).toEqual([]);
  });

  it("clears commoditiesError on a successful retry", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("boom"));
    const store = useFinanceStore();
    await store.getCommodities();
    expect(store.commoditiesError).not.toBeNull();

    mockApiGet.mockResolvedValueOnce({
      success: true,
      data: [{ symbol: "GC=F", current_price: 2000 }],
    });
    await store.getCommodities();

    expect(store.commoditiesError).toBeNull();
    expect(store.commodities[0].symbol).toBe("GC=F");
  });
});

describe("fetchWatchlist error state", () => {
  it("writes watchlistError when the watchlist request fails", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("network down"));
    const store = useFinanceStore();

    await store.fetchWatchlist();

    expect(store.watchlistError).toBe("加载自选股失败，请稍后重试。");
    expect(store.watchlist).toEqual([]);
  });

  it("loads items and quotes, clearing a previous error", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("boom"));
    const store = useFinanceStore();
    await store.fetchWatchlist();
    expect(store.watchlistError).not.toBeNull();

    const item = { id: "w1", symbol: "AAPL", display_order: 1 };
    const quote = { symbol: "AAPL", current_price: 150 };
    mockApiGet
      .mockResolvedValueOnce({ success: true, data: [item] })
      .mockResolvedValueOnce({ success: true, data: [quote] });

    await store.fetchWatchlist();

    expect(store.watchlistError).toBeNull();
    expect(store.watchlist).toEqual([item]);
    expect(store.watchlistQuotes.get("AAPL")).toEqual(quote);
    expect(mockApiGet).toHaveBeenCalledWith("/finance/watchlist");
    expect(mockApiGet).toHaveBeenCalledWith("/finance/watchlist/quotes");
  });

  it("clears its error at the start of every attempt", async () => {
    const store = useFinanceStore();
    // Simulate a stale error from an earlier round
    mockApiGet.mockRejectedValueOnce(new Error("first failure"));
    await store.fetchWatchlist();
    expect(store.watchlistError).not.toBeNull();

    // Second attempt fails again: error is rewritten, not duplicated/stuck
    mockApiGet.mockRejectedValueOnce(new Error("second failure"));
    await store.fetchWatchlist();
    expect(store.watchlistError).toBe("加载自选股失败，请稍后重试。");
  });
});

describe("reorderWatchlist contract", () => {
  it("sends the WatchlistReorderRequest body ({items} with 0-based display_order) and applies the order", async () => {
    const store = useFinanceStore();
    const a = makeWatchlistItem("w1", "AAPL", 0);
    const b = makeWatchlistItem("w2", "TSLA", 1);
    const c = makeWatchlistItem("w3", "MSFT", 2);
    store.watchlist = [a, b, c];
    mockApiPut.mockResolvedValueOnce({
      success: true,
      data: { message: "Watchlist order updated" },
    });

    const data = await store.reorderWatchlist([c, a, b]);

    expect(mockApiPut).toHaveBeenCalledWith("/finance/watchlist/reorder", {
      items: [
        { item_id: "w3", display_order: 0 },
        { item_id: "w1", display_order: 1 },
        { item_id: "w2", display_order: 2 },
      ],
    });
    expect(data).toEqual({ message: "Watchlist order updated" });
    expect(store.watchlist.map((item) => item.id)).toEqual(["w3", "w1", "w2"]);
    expect(store.watchlist.map((item) => item.display_order)).toEqual([
      0, 1, 2,
    ]);
  });

  it("rethrows failures without mutating the list so the caller can roll back", async () => {
    const store = useFinanceStore();
    const a = makeWatchlistItem("w1", "AAPL", 0);
    const b = makeWatchlistItem("w2", "TSLA", 1);
    store.watchlist = [a, b];
    mockApiPut.mockRejectedValueOnce(new Error("503 upstream"));

    await expect(store.reorderWatchlist([b, a])).rejects.toThrow();

    // The request went out with the correct contract before failing
    expect(mockApiPut).toHaveBeenCalledWith("/finance/watchlist/reorder", {
      items: [
        { item_id: "w2", display_order: 0 },
        { item_id: "w1", display_order: 1 },
      ],
    });
    // ...and the local list keeps the pre-reorder order for the rollback
    expect(store.watchlist.map((item) => item.id)).toEqual(["w1", "w2"]);
  });
});

describe("updateMarketIndexFromSSE", () => {
  it("replaces the whole list when the payload is an array", () => {
    const store = useFinanceStore();
    // Stale entry from an earlier round, not present in the new payload
    store.updateMarketIndexFromSSE({
      symbol: "^GSPC",
      name: "S&P 500",
      value: 4900,
      change: -5,
      change_percent: -0.1,
      market_status: "closed",
      region: "US",
      timestamp: "2026-01-01T00:00:00Z",
    });

    store.updateMarketIndexFromSSE([
      {
        symbol: "^GSPC",
        name: "S&P 500",
        value: 5000,
        change: 10,
        change_percent: 0.2,
        market_status: "open",
        region: "US",
        timestamp: "2026-01-02T00:00:00Z",
      },
      {
        symbol: "000001.SS",
        name: "上证综合指数",
        value: 3100,
        change: -8,
        change_percent: -0.26,
        market_status: "closed",
        region: "CN",
        timestamp: "2026-01-02T00:00:00Z",
      },
    ]);

    expect(store.marketIndices).toEqual([
      {
        symbol: "^GSPC",
        name: "S&P 500",
        value: 5000,
        change: 10,
        change_percent: 0.2,
        market_status: "open",
        // absent from the payload → normalized to null by the SSE merge
        market_status_reason: null,
        holiday_name: null,
        region: "US",
        timestamp: "2026-01-02T00:00:00Z",
      },
      {
        symbol: "000001.SS",
        name: "上证综合指数",
        value: 3100,
        change: -8,
        change_percent: -0.26,
        market_status: "closed",
        market_status_reason: null,
        holiday_name: null,
        region: "CN",
        timestamp: "2026-01-02T00:00:00Z",
      },
    ]);
  });

  it("keeps market_status_reason and holiday_name from the array payload", () => {
    const store = useFinanceStore();
    store.updateMarketIndexFromSSE([
      {
        symbol: "000001.SS",
        name: "上证综合指数",
        value: 3100,
        change: 0,
        change_percent: 0,
        market_status: "closed",
        market_status_reason: "holiday",
        holiday_name: "国庆节",
        region: "CN",
        timestamp: "2026-10-01T02:00:00Z",
      },
      {
        symbol: "^GSPC",
        name: "S&P 500",
        value: 5000,
        change: 10,
        change_percent: 0.2,
        market_status: "closed",
        market_status_reason: "off_hours",
        holiday_name: null,
        region: "US",
        timestamp: "2026-10-01T02:00:00Z",
      },
    ]);

    expect(store.marketIndices[0].market_status_reason).toBe("holiday");
    expect(store.marketIndices[0].holiday_name).toBe("国庆节");
    expect(store.marketIndices[1].market_status_reason).toBe("off_hours");
    expect(store.marketIndices[1].holiday_name).toBeNull();
  });

  it("updates the matching entry in place when the payload is a single object", () => {
    const store = useFinanceStore();
    store.updateMarketIndexFromSSE([
      {
        symbol: "^GSPC",
        name: "S&P 500",
        value: 5000,
        change: 10,
        change_percent: 0.2,
        market_status: "open",
        region: "US",
        timestamp: "t1",
      },
      {
        symbol: "^HSI",
        name: "恒生指数",
        value: 17000,
        change: 100,
        change_percent: 0.59,
        market_status: "open",
        region: "HK",
        timestamp: "t1",
      },
    ]);

    store.updateMarketIndexFromSSE({
      symbol: "^GSPC",
      name: "S&P 500",
      value: 5010,
      change: 20,
      change_percent: 0.4,
      market_status: "open",
      region: "US",
      timestamp: "t2",
    });

    expect(store.marketIndices).toHaveLength(2);
    expect(store.marketIndices[0].value).toBe(5010);
    expect(store.marketIndices[0].timestamp).toBe("t2");
    expect(store.marketIndices[1].value).toBe(17000);
  });
});

describe("updateCommodityFromSSE", () => {
  it("replaces the whole list when the payload is an array", () => {
    const store = useFinanceStore();
    // Stale entry absent from the new payload must not survive a full-array push
    store.updateCommodityFromSSE({
      symbol: "ZC=F",
      name: "玉米期货",
      value: 450,
      change: 0,
      change_percent: 0,
      unit: "USD/bushel",
      category: "agriculture",
      timestamp: "2026-01-01T00:00:00Z",
    });

    store.updateCommodityFromSSE([
      {
        symbol: "GC=F",
        name: "黄金期货",
        value: 2400,
        change: 12,
        change_percent: 0.5,
        unit: "USD/oz",
        category: "precious_metal",
        timestamp: "2026-01-02T00:00:00Z",
      },
      {
        symbol: "BZ=F",
        name: "Brent原油期货",
        value: 85,
        change: -1,
        change_percent: -1.16,
        unit: "USD/bbl",
        category: "energy",
        timestamp: "2026-01-02T00:00:00Z",
      },
    ]);

    expect(store.commodities.map((c) => c.symbol)).toEqual(["GC=F", "BZ=F"]);
    expect(store.commodities[0]).toEqual({
      symbol: "GC=F",
      name: "黄金期货",
      value: 2400,
      change: 12,
      change_percent: 0.5,
      unit: "USD/oz",
      category: "precious_metal",
      timestamp: "2026-01-02T00:00:00Z",
    });
  });

  it("updates the matching entry in place when the payload is a single object", () => {
    const store = useFinanceStore();
    store.updateCommodityFromSSE([
      {
        symbol: "GC=F",
        name: "黄金期货",
        value: 2400,
        change: 12,
        change_percent: 0.5,
        unit: "USD/oz",
        category: "precious_metal",
        timestamp: "t1",
      },
      {
        symbol: "CL=F",
        name: "WTI原油期货",
        value: 80,
        change: 0.3,
        change_percent: 0.38,
        unit: "USD/bbl",
        category: "energy",
        timestamp: "t1",
      },
    ]);

    store.updateCommodityFromSSE({
      symbol: "GC=F",
      name: "黄金期货",
      value: 2410,
      change: 22,
      change_percent: 0.92,
      unit: "USD/oz",
      category: "precious_metal",
      timestamp: "t2",
    });

    expect(store.commodities).toHaveLength(2);
    expect(store.commodities[0].value).toBe(2410);
    expect(store.commodities[0].timestamp).toBe("t2");
    expect(store.commodities[1].value).toBe(80);
  });
});
