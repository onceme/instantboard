/**
 * Tech store regression: the error state is written on failure (distinct from
 * the "No news" empty state) and cleared on retry; fetchNews must send the
 * backend-aligned query params domain/subcategory/tag/sort (the backend does
 * not recognize topic/subtopic/sort_by). SSE suites below capture the handler
 * wiring via a fake SSEConnection; init() is never triggered.
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
import { useTechStore } from "@/stores/tech";
import { SSEEventType } from "@/types";
import type { TechTopic } from "@/types";

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
  captured.options = null;
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

describe("hot topic tag filter", () => {
  it("sends tag when activeTag is set and omits it when empty", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();
    store.activeTag = "llm";

    await store.fetchNews();

    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({ tag: "llm" }),
    );

    store.activeTag = "";
    await store.fetchNews();

    const lastParams = mockApiGet.mock.calls.at(-1)?.[1] as Record<
      string,
      unknown
    >;
    expect(lastParams).not.toHaveProperty("tag");
  });

  it("tag stacks with domain and subcategory in one request", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();
    store.currentDomain = "ai";
    store.currentSubcategory = "llm";
    store.activeTag = "gpt-5";

    await store.fetchNews();

    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({
        domain: "ai",
        subcategory: "llm",
        tag: "gpt-5",
      }),
    );
  });

  it("setTag activates the tag and refetches", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();
    store.currentPage = 3;

    store.setTag("llm");
    await mockApiGet.mock.results[0]?.value;

    expect(store.activeTag).toBe("llm");
    expect(store.currentPage).toBe(1);
    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({ tag: "llm", page: 1 }),
    );
  });

  it("setTag on the active tag clears the filter", async () => {
    mockApiGet.mockResolvedValue(okNews());
    const store = useTechStore();

    store.setTag("llm");
    await mockApiGet.mock.results[0]?.value;
    expect(store.activeTag).toBe("llm");

    store.setTag("llm");
    await mockApiGet.mock.results[1]?.value;

    expect(store.activeTag).toBe("");
    const lastParams = mockApiGet.mock.calls.at(-1)?.[1] as Record<
      string,
      unknown
    >;
    expect(lastParams).not.toHaveProperty("tag");
  });
});

describe("topic_stats_update SSE handler (tech-tab.md §3.8)", () => {
  function connectAndCaptureHandlers(store: ReturnType<typeof useTechStore>) {
    store.connectSSE();
    const options = captured.options;
    expect(
      options,
      "connectSSE must construct an SSEConnection",
    ).not.toBeNull();
    expect(options!.category).toBe("tech");
    return options!.eventHandlers!;
  }

  it("wires topic_stats_update alongside item_update on the tech channel", () => {
    const store = useTechStore();
    const handlers = connectAndCaptureHandlers(store);

    expect(handlers).toHaveProperty(SSEEventType.ITEM_UPDATE);
    expect(handlers).toHaveProperty(SSEEventType.TOPIC_STATS_UPDATE);
  });

  it("replaces the topics list wholesale when the event arrives", () => {
    const store = useTechStore();
    const pushPayload: TechTopic[] = [
      {
        tag: "ai",
        label: "人工智能",
        count: 42,
        last_active_at: "2026-08-26T08:00:00+00:00",
      },
      { tag: "llm", label: "大语言模型", count: 7 },
    ];
    store.topics = [{ tag: "stale", label: "Stale", count: 1 }];
    const handlers = connectAndCaptureHandlers(store);

    handlers[SSEEventType.TOPIC_STATS_UPDATE]!(pushPayload);

    // Wholesale replacement: the stale entry is gone, order/count come from the payload.
    expect(store.topics).toEqual(pushPayload);
    expect(store.topics.map((t) => t.tag)).toEqual(["ai", "llm"]);
  });

  it("applies an empty stats payload (clears hot topics)", () => {
    const store = useTechStore();
    store.topics = [{ tag: "ai", label: "AI", count: 9 }];
    const handlers = connectAndCaptureHandlers(store);

    handlers[SSEEventType.TOPIC_STATS_UPDATE]!([]);

    expect(store.topics).toEqual([]);
  });

  it("ignores malformed non-array payloads", () => {
    const store = useTechStore();
    store.topics = [{ tag: "keep", label: "Keep", count: 3 }];
    const handlers = connectAndCaptureHandlers(store);

    handlers[SSEEventType.TOPIC_STATS_UPDATE]!({ topics: [] });

    expect(store.topics).toEqual([{ tag: "keep", label: "Keep", count: 3 }]);
  });
});
