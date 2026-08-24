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

import { apiGet } from "@/utils/api";
import { useFinanceStore } from "@/stores/finance";

const mockApiGet = vi.mocked(apiGet);

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
