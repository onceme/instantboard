/**
 * Tech store regression: the error state is written on failure (distinct from
 * the "No news" empty state) and cleared on retry; fetchNews must send the
 * backend-aligned query params domain/subcategory/sort (the backend does not
 * recognize topic/subtopic/sort_by). init()/connectSSE() are never triggered.
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
import { useTechStore } from "@/stores/tech";

const mockApiGet = vi.mocked(apiGet);

function okNews(items: unknown[] = []) {
  return {
    success: true,
    data: items,
    meta: { total: items.length, page: 1, page_size: 20 },
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("fetchNews error state", () => {
  it("writes error on failure and keeps the list empty", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("503 upstream"));
    const store = useTechStore();

    await store.fetchNews();

    expect(store.error).toBe("加载科技新闻失败，请稍后重试。");
    expect(store.newsItems).toEqual([]);
    expect(store.isLoading).toBe(false);
  });

  it("clears a previous error on a successful retry", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("boom"));
    const store = useTechStore();
    await store.fetchNews();
    expect(store.error).not.toBeNull();

    mockApiGet.mockResolvedValueOnce(okNews([{ id: "n1", title: "Hi" }]));
    await store.fetchNews();

    expect(store.error).toBeNull();
    expect(store.newsItems).toHaveLength(1);
  });

  it("fetchNews error stays distinct from an empty success result", async () => {
    const store = useTechStore();
    mockApiGet.mockResolvedValueOnce(okNews([]));
    await store.fetchNews();

    // Empty list with no error → "No news" state, not an error alert
    expect(store.error).toBeNull();
    expect(store.newsItems).toEqual([]);
  });
});

describe("fetchNews topics error", () => {
  it("writes error when topic loading fails", async () => {
    mockApiGet.mockRejectedValueOnce(new Error("topics down"));
    const store = useTechStore();

    await store.fetchTopics();

    expect(store.error).toBe("加载话题失败，请稍后重试。");
  });
});

describe("query params are backend-aligned", () => {
  it("sends page/page_size/sort and omits domain when it is 'all'", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();

    await store.fetchNews();

    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({
        page: 1,
        page_size: 20,
        sort: "hot",
      }),
    );
    const params = mockApiGet.mock.calls[0][1] as Record<string, unknown>;
    expect(params).not.toHaveProperty("domain");
    expect(params).not.toHaveProperty("subcategory");
  });

  it("sends domain and subcategory once selected", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();
    store.currentDomain = "ai";
    store.currentSubcategory = "llm";

    await store.fetchNews();

    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({
        domain: "ai",
        subcategory: "llm",
        sort: "hot",
      }),
    );
  });

  it("reflects the selected sort order", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();
    store.currentSort = "time";

    await store.fetchNews();

    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({ sort: "time" }),
    );
  });

  it("setDomain resets subcategory and page before fetching", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();
    store.currentSubcategory = "llm";
    store.currentPage = 4;

    store.setDomain("robotics");
    await mockApiGet.mock.results[0]?.value;

    expect(store.currentSubcategory).toBe("");
    expect(store.currentPage).toBe(1);
    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({ domain: "robotics", page: 1 }),
    );
  });
});
