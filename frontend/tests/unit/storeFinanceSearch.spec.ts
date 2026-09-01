/**
 * Finance store symbol-search race guard: keystroke searches fire one request
 * per (debounced) input — a slow early response arriving after a newer one
 * must never overwrite the fresh results (the "候选列表跳走" symptom). Also
 * covers the loading flag lifecycle and the abort wiring.
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

import axios from "axios";
import { apiGet } from "@/utils/api";
import { useFinanceStore } from "@/stores/finance";
import type { ApiResponse, SearchResult } from "@/types";

const mockApiGet = vi.mocked(apiGet);

function row(symbol: string, name: string): SearchResult {
  return {
    symbol,
    name,
    type: "fund",
    market: "CN",
    exchange: "",
  };
}

function ok(data: SearchResult[]): ApiResponse<SearchResult[]> {
  return { success: true, data };
}

// A promise whose resolution the test controls — simulates a slow request.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("searchSymbols race guard", () => {
  it("a late first response never overwrites the newer query's results", async () => {
    const store = useFinanceStore();
    const first = deferred<ApiResponse<SearchResult[]>>();
    mockApiGet.mockReturnValueOnce(first.promise);
    const p1 = store.searchSymbols("0178");

    mockApiGet.mockResolvedValueOnce(
      ok([row("017811", "东方人工智能主题混合C")]),
    );
    const p2 = store.searchSymbols("017811");
    await p2;
    expect(store.searchResults.map((r) => r.symbol)).toEqual(["017811"]);

    // The slow first response finally lands — it must be dropped.
    first.resolve(ok([row("017801", "某旧基金")]));
    await p1;

    expect(store.searchResults.map((r) => r.symbol)).toEqual(["017811"]);
    expect(store.searchLoading).toBe(false);
  });

  it("aborts the previous in-flight request when a newer one starts", async () => {
    const store = useFinanceStore();
    const first = deferred<ApiResponse<SearchResult[]>>();
    mockApiGet.mockReturnValueOnce(first.promise);
    const p1 = store.searchSymbols("0178");

    const firstSignal = mockApiGet.mock.calls[0][2] as AbortSignal | undefined;
    expect(firstSignal).toBeInstanceOf(AbortSignal);
    expect(firstSignal?.aborted).toBe(false);

    mockApiGet.mockResolvedValueOnce(
      ok([row("017811", "东方人工智能主题混合C")]),
    );
    const p2 = store.searchSymbols("017811");

    expect(firstSignal?.aborted).toBe(true);
    await p2;
    // A cancellation rejection must not surface as an error nor clear results.
    first.reject(new axios.CanceledError("canceled"));
    await p1;
    expect(store.searchResults.map((r) => r.symbol)).toEqual(["017811"]);
  });

  it("toggles searchLoading around the latest request only", async () => {
    const store = useFinanceStore();
    const slow = deferred<ApiResponse<SearchResult[]>>();
    mockApiGet.mockReturnValueOnce(slow.promise);

    const p = store.searchSymbols("017811");
    expect(store.searchLoading).toBe(true);

    slow.resolve(ok([row("017811", "东方人工智能主题混合C")]));
    await p;
    expect(store.searchLoading).toBe(false);
  });

  it("a superseded request clearing loading does not hide the latest request's state", async () => {
    const store = useFinanceStore();
    const first = deferred<ApiResponse<SearchResult[]>>();
    mockApiGet.mockReturnValueOnce(first.promise);
    const p1 = store.searchSymbols("017");

    const second = deferred<ApiResponse<SearchResult[]>>();
    mockApiGet.mockReturnValueOnce(second.promise);
    const p2 = store.searchSymbols("017811");

    // The superseded request finishes first; loading must stay on for the
    // still in-flight latest request.
    first.resolve(ok([]));
    await p1;
    expect(store.searchLoading).toBe(true);

    second.resolve(ok([row("017811", "东方人工智能主题混合C")]));
    await p2;
    expect(store.searchLoading).toBe(false);
  });

  it("empty query clears results, aborts in-flight work and stops loading", async () => {
    const store = useFinanceStore();
    const slow = deferred<ApiResponse<SearchResult[]>>();
    mockApiGet.mockReturnValueOnce(slow.promise);
    const p1 = store.searchSymbols("017811");
    const signal = mockApiGet.mock.calls[0][2] as AbortSignal | undefined;
    expect(signal).toBeInstanceOf(AbortSignal);
    // Emulate axios: an aborted signal rejects the request with CanceledError.
    signal?.addEventListener("abort", () =>
      slow.reject(new axios.CanceledError("canceled")),
    );

    store.searchSymbols("");
    await p1;

    expect(signal?.aborted).toBe(true);
    expect(store.searchResults).toEqual([]);
    expect(store.searchLoading).toBe(false);
  });

  it("a genuine failure of the latest query yields an empty result set", async () => {
    const store = useFinanceStore();
    mockApiGet.mockRejectedValueOnce(new Error("503 upstream"));

    await store.searchSymbols("017811");

    expect(store.searchResults).toEqual([]);
    expect(store.searchLoading).toBe(false);
  });
});
