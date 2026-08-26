/**
 * Tech store keyword search (GET /tech/search, tech-tab.md §3.7):
 * search(q) sends q/page/page_size, blank queries clear state without a
 * request, stale out-of-order responses are dropped, pagination reuses the
 * same query, and SSE item_update never touches the search result snapshot.
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

const captured = vi.hoisted(() => ({
  options: null as null | {
    category: string;
    eventHandlers?: Partial<Record<string, (data: unknown) => void>>;
  },
}));

vi.mock("@/utils/sse.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/sse.ts")>();
  class FakeSSEConnection {
    connect = vi.fn();
    disconnect = vi.fn();
    constructor(options: unknown) {
      captured.options = options as typeof captured.options;
    }
  }
  return { ...actual, SSEConnection: FakeSSEConnection };
});

import { apiGet } from "@/utils/api";
import { useTechStore, SEARCH_PAGE_SIZE } from "@/stores/tech";
import { SSEEventType } from "@/types";

const mockApiGet = vi.mocked(apiGet);

function okSearch(items: unknown[] = [], total = items.length, page = 1) {
  return {
    success: true,
    data: items,
    meta: { total, page, page_size: SEARCH_PAGE_SIZE },
  };
}

const searchCalls = () =>
  mockApiGet.mock.calls.filter(([url]) => url === "/tech/search");

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  captured.options = null;
});

describe("search triggers", () => {
  it("sends the trimmed query with page/page_size", async () => {
    mockApiGet.mockResolvedValue(okSearch([{ id: "1", title: "Robot arm" }]));
    const store = useTechStore();

    await store.search("  robot  ");

    expect(searchCalls()).toHaveLength(1);
    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/search",
      expect.objectContaining({
        q: "robot",
        page: 1,
        page_size: SEARCH_PAGE_SIZE,
      }),
    );
    expect(store.searchResults).toHaveLength(1);
    expect(store.searchTotal).toBe(1);
    expect(store.isSearchActive).toBe(true);
    expect(store.searchLoading).toBe(false);
  });

  it("blank query clears the search state without any request", async () => {
    const store = useTechStore();

    await store.search("   ");

    expect(searchCalls()).toHaveLength(0);
    expect(store.isSearchActive).toBe(false);
    expect(store.searchResults).toEqual([]);
    expect(store.searchTotal).toBe(0);
    expect(store.searchPage).toBe(1);
  });

  it("empty query after a previous search clears results without a request", async () => {
    mockApiGet.mockResolvedValue(okSearch([{ id: "1", title: "hit" }], 1));
    const store = useTechStore();
    await store.search("robot");
    expect(store.searchResults).toHaveLength(1);

    await store.search("");

    expect(searchCalls()).toHaveLength(1); // no second request
    expect(store.isSearchActive).toBe(false);
    expect(store.searchResults).toEqual([]);
  });

  it("writes searchError on failure", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("500 boom"));
    const store = useTechStore();

    await store.search("robot");

    expect(store.searchError).toBe("搜索科技新闻失败，请稍后重试。");
    expect(store.searchResults).toEqual([]);
    expect(store.searchLoading).toBe(false);
  });

  it("clears a previous searchError on the next successful search", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("boom"));
    const store = useTechStore();
    await store.search("robot");
    expect(store.searchError).not.toBeNull();

    mockApiGet.mockResolvedValueOnce(okSearch());
    await store.search("robot");

    expect(store.searchError).toBeNull();
  });
});

describe("search pagination", () => {
  it("setSearchPage refetches the same query with the new page", async () => {
    mockApiGet.mockResolvedValue(okSearch());
    const store = useTechStore();
    await store.search("orbit");

    store.setSearchPage(3);
    await mockApiGet.mock.results.at(-1)?.value;

    const lastParams = searchCalls().at(-1)?.[1] as Record<string, unknown>;
    expect(lastParams).toEqual(
      expect.objectContaining({ q: "orbit", page: 3 }),
    );
  });

  it("a new query restarts on page 1", async () => {
    mockApiGet.mockResolvedValue(okSearch());
    const store = useTechStore();
    await store.search("orbit");
    store.setSearchPage(3);
    await mockApiGet.mock.results.at(-1)?.value;
    expect(store.searchPage).toBe(3);

    await store.search("starlink");

    expect(store.searchPage).toBe(1);
    const lastParams = searchCalls().at(-1)?.[1] as Record<string, unknown>;
    expect(lastParams).toEqual(
      expect.objectContaining({ q: "starlink", page: 1 }),
    );
  });

  it("retrySearch keeps the current page", async () => {
    mockApiGet.mockResolvedValue(okSearch());
    const store = useTechStore();
    await store.search("orbit");
    store.setSearchPage(2);
    await mockApiGet.mock.results.at(-1)?.value;

    store.retrySearch();
    await mockApiGet.mock.results.at(-1)?.value;

    const lastParams = searchCalls().at(-1)?.[1] as Record<string, unknown>;
    expect(lastParams).toEqual(
      expect.objectContaining({ q: "orbit", page: 2 }),
    );
  });
});

describe("stale response guard", () => {
  it("drops an out-of-order older response", async () => {
    const store = useTechStore();

    let releaseFirst!: (value: unknown) => void;
    const first = new Promise((resolve) => {
      releaseFirst = resolve;
    });
    mockApiGet.mockReturnValueOnce(first as never);
    const p1 = store.search("ro");

    mockApiGet.mockResolvedValueOnce(
      okSearch([{ id: "2", title: "robot" }], 1),
    );
    await store.search("robot");
    expect(store.searchResults[0]?.title ?? store.searchResults[0]).toBe(
      "robot",
    );

    // The belated first response arrives last and must be ignored
    releaseFirst(okSearch([{ id: "1", title: "stale" }], 1));
    await p1;

    expect(store.searchResults).toHaveLength(1);
    expect((store.searchResults[0] as { id: string }).id).toBe("2");
  });

  it("clearing while a request is in flight discards its response", async () => {
    const store = useTechStore();

    let release!: (value: unknown) => void;
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    mockApiGet.mockReturnValueOnce(pending as never);
    const p = store.search("robot");

    await store.clearSearch();
    expect(store.isSearchActive).toBe(false);

    release(okSearch([{ id: "1", title: "late" }], 1));
    await p;

    expect(store.searchResults).toEqual([]);
    expect(store.searchQuery).toBe("");
  });
});

describe("SSE isolation", () => {
  it("item_update feeds newsItems only, never the search results", () => {
    const store = useTechStore();
    store.searchResults = [{ id: "s1", title: "result" } as never];

    store.connectSSE();
    const handlers = captured.options!.eventHandlers!;
    handlers[SSEEventType.ITEM_UPDATE]!({ id: "live1", title: "Live item" });

    expect(store.newsItems.map((item) => item.id)).toContain("live1");
    expect(store.searchResults).toEqual([{ id: "s1", title: "result" }]);
  });
});
