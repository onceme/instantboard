/**
 * TechView keyword search wiring (tech-tab.md §3.7): the SearchBar above
 * TopicFilter drives GET /tech/search (300ms debounce from common/SearchBar),
 * an active query swaps the content area to paged search results with the
 * "搜索 “q” · N 条结果" summary; clearing or blank queries restore the feed
 * without a request, failures render a retryable ErrorAlert.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia } from "pinia";
import type { Pinia } from "pinia";

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

vi.mock("@/utils/sse.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/sse.ts")>();
  const constructed: { options: unknown }[] = [];
  class FakeSSEConnection {
    connect = vi.fn();
    disconnect = vi.fn();
    constructor(options: unknown) {
      constructed.push({ options });
    }
  }
  return { ...actual, SSEConnection: FakeSSEConnection };
});

import { apiGet } from "@/utils/api";
import TechView from "@/views/TechView.vue";
import SearchBar from "@/components/common/SearchBar.vue";
import Pagination from "@/components/common/Pagination.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import NewsCard from "@/components/tech/NewsCard.vue";

const mockApiGet = vi.mocked(apiGet);

const okFeed = {
  success: true,
  data: [],
  meta: { total: 0, page: 1, page_size: 20 },
};

function searchItem(seq: number, title = `Result ${seq}`) {
  return {
    id: `s${seq}`,
    title,
    summary: `Summary ${seq}`,
    url: `https://example.com/${seq}`,
    source_name: "Seed RSS",
    source_id: "src",
    category_id: "cat",
    topic_tags: ["tech"],
    published_at: "2026-08-26T10:00:00Z",
    fetched_at: "2026-08-26T10:00:00Z",
    priority: 5,
  };
}

function okSearch(items: unknown[], total = items.length, page = 1) {
  return {
    success: true,
    data: items,
    meta: { total, page, page_size: 20 },
  };
}

const searchCalls = () =>
  mockApiGet.mock.calls.filter(([url]) => url === "/tech/search");

let pinia: Pinia;

beforeEach(() => {
  vi.useFakeTimers();
  pinia = createPinia();
  vi.clearAllMocks();
  // init() issues news + topics requests on mount; let them resolve empty
  mockApiGet.mockResolvedValue(okFeed);
});

afterEach(() => {
  vi.useRealTimers();
});

async function mountTechView() {
  const wrapper = mount(TechView, { global: { plugins: [pinia] } });
  await flushPromises();
  return wrapper;
}

async function typeAndDebounce(
  wrapper: ReturnType<typeof mountTechView> extends Promise<infer W>
    ? W
    : never,
  text: string,
) {
  await wrapper.find(".search-input").setValue(text);
  vi.advanceTimersByTime(300);
  await flushPromises();
}

describe("search rendering", () => {
  it("mounts the SearchBar above the filters and starts in feed mode", async () => {
    const wrapper = await mountTechView();

    expect(wrapper.findComponent(SearchBar).exists()).toBe(true);
    expect(wrapper.find(".search-results").exists()).toBe(false);
    expect(wrapper.find(".mode-switch").exists()).toBe(true);
  });

  it("debounced input switches to the search result view", async () => {
    mockApiGet.mockImplementation(async (url: string) =>
      url === "/tech/search"
        ? okSearch([searchItem(1), searchItem(2)], 2)
        : okFeed,
    );
    const wrapper = await mountTechView();

    await typeAndDebounce(wrapper, "robot");

    expect(searchCalls()).toHaveLength(1);
    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/search",
      expect.objectContaining({ q: "robot", page: 1, page_size: 20 }),
    );
    const summary = wrapper.find(".search-summary-text");
    expect(summary.exists()).toBe(true);
    expect(summary.text()).toContain("搜索 “robot”");
    expect(summary.text()).toContain("2 条结果");
    expect(wrapper.findAllComponents(NewsCard)).toHaveLength(2);
    // Feed/grid views are replaced while the search is active
    expect(wrapper.find(".mode-switch").exists()).toBe(false);
  });

  it("shows the EmptyState when nothing matches", async () => {
    mockApiGet.mockImplementation(async (url: string) =>
      url === "/tech/search" ? okSearch([], 0) : okFeed,
    );
    const wrapper = await mountTechView();

    await typeAndDebounce(wrapper, "zzz");

    const empty = wrapper.findComponent(EmptyState);
    expect(empty.exists()).toBe(true);
    expect(empty.props("title")).toBe("未找到相关新闻");
    expect(wrapper.text()).toContain("0 条结果");
  });

  it("renders pagination for multi-page result sets and pages forward", async () => {
    const page = Array.from({ length: 20 }, (_, i) => searchItem(i));
    mockApiGet.mockImplementation(async (url: string) =>
      url === "/tech/search" ? okSearch(page, 45) : okFeed,
    );
    const wrapper = await mountTechView();
    await typeAndDebounce(wrapper, "orbit");

    const pagination = wrapper.findComponent(Pagination);
    expect(pagination.exists()).toBe(true);

    await pagination.vm.$emit("update:page", 2);
    await flushPromises();

    expect(wrapper.text()).toContain("45 条结果");
    const lastParams = searchCalls().at(-1)?.[1] as Record<string, unknown>;
    expect(lastParams).toEqual(
      expect.objectContaining({ q: "orbit", page: 2 }),
    );
  });

  it("hides pagination for a single page of results", async () => {
    mockApiGet.mockImplementation(async (url: string) =>
      url === "/tech/search" ? okSearch([searchItem(1)], 1) : okFeed,
    );
    const wrapper = await mountTechView();

    await typeAndDebounce(wrapper, "robot");

    expect(wrapper.findComponent(Pagination).exists()).toBe(false);
  });
});

describe("clearing and blank queries", () => {
  it("clearing restores the feed and sends no blank request", async () => {
    mockApiGet.mockImplementation(async (url: string) =>
      url === "/tech/search" ? okSearch([searchItem(1)], 1) : okFeed,
    );
    const wrapper = await mountTechView();
    await typeAndDebounce(wrapper, "robot");
    expect(wrapper.find(".search-results").exists()).toBe(true);

    await wrapper
      .findComponent(SearchBar)
      .find(".search-clear")
      .trigger("click");
    await flushPromises();

    expect(wrapper.find(".search-results").exists()).toBe(false);
    expect(wrapper.find(".mode-switch").exists()).toBe(true);
    // Still exactly one /tech/search request — the clear never hit the backend
    expect(searchCalls()).toHaveLength(1);
  });

  it("clear button in the result header also restores the feed", async () => {
    mockApiGet.mockImplementation(async (url: string) =>
      url === "/tech/search" ? okSearch([searchItem(1)], 1) : okFeed,
    );
    const wrapper = await mountTechView();
    await typeAndDebounce(wrapper, "robot");

    await wrapper.find(".search-reset-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".search-results").exists()).toBe(false);
    expect(searchCalls()).toHaveLength(1);
  });

  it("an empty query never issues a search request", async () => {
    const wrapper = await mountTechView();

    // Enter on an empty input emits search("") immediately
    await wrapper.find(".search-input").trigger("keydown.enter");
    vi.advanceTimersByTime(1000);
    await flushPromises();

    expect(searchCalls()).toHaveLength(0);
    expect(wrapper.find(".search-results").exists()).toBe(false);
  });
});

describe("search errors", () => {
  it("renders a retryable ErrorAlert on failure and retries on click", async () => {
    mockApiGet.mockImplementation(async (url: string) => {
      if (url === "/tech/search") {
        throw new Error("search down");
      }
      return okFeed;
    });
    const wrapper = await mountTechView();

    await typeAndDebounce(wrapper, "robot");

    expect(wrapper.text()).toContain("搜索科技新闻失败，请稍后重试。");
    expect(searchCalls()).toHaveLength(1);

    await wrapper.find(".alert-retry").trigger("click");
    await flushPromises();

    expect(searchCalls()).toHaveLength(2);
  });
});
