/**
 * HotTopics: renders the top 12 tags from /tech/topics ordered by count desc
 * with count badges; clicking a tag activates the hot-tag news filter
 * (fetchNews sends `tag`), clicking it again clears it; with no topic data
 * nothing renders (loading shows skeleton pills instead).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
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

import { apiGet } from "@/utils/api";
import { useTechStore } from "@/stores/tech";
import HotTopics from "@/components/tech/HotTopics.vue";
import type { TechTopic, TechNewsItem } from "@/types";

const mockApiGet = vi.mocked(apiGet);

function okNews(items: TechNewsItem[] = []) {
  return {
    success: true,
    data: items,
    meta: { total: items.length, page: 1, page_size: 20 },
  };
}

function topic(tag: string, count: number): TechTopic {
  return { tag, label: tag.toUpperCase(), count, last_active_at: null };
}

let pinia: Pinia;

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  vi.clearAllMocks();
  mockApiGet.mockResolvedValue(okNews());
});

function mountHotTopics() {
  return mount(HotTopics, { global: { plugins: [pinia] } });
}

function tagButtons(wrapper: ReturnType<typeof mountHotTopics>) {
  return wrapper.findAll(".hot-tag");
}

describe("top 12 rendering", () => {
  it("renders at most 12 tags sorted by count desc with count badges", async () => {
    const store = useTechStore();
    // 14 topics with shuffled counts; top 12 by count must win, ordered desc
    store.topics = [
      topic("t3", 30),
      topic("t14", 1),
      topic("t1", 100),
      topic("t9", 8),
      topic("t5", 50),
      topic("t12", 4),
      topic("t7", 20),
      topic("t2", 90),
      topic("t11", 5),
      topic("t4", 60),
      topic("t10", 6),
      topic("t6", 40),
      topic("t8", 10),
      topic("t13", 2),
    ];

    const wrapper = mountHotTopics();
    const buttons = tagButtons(wrapper);

    expect(buttons).toHaveLength(12);
    const counts = buttons.map((b) => Number(b.find(".tag-count").text()));
    expect(counts).toEqual([100, 90, 60, 50, 40, 30, 20, 10, 8, 6, 5, 4]);
    // Labels resolved from the topic payload; the raw tag is exposed as title
    expect(buttons[0].text()).toContain("T1");
    expect(buttons[0].attributes("title")).toBe("t1");
    // The two lowest-count tags are cut off
    expect(wrapper.text()).not.toContain("T13");
    expect(wrapper.text()).not.toContain("T14");
  });

  it("marks the active tag", () => {
    const store = useTechStore();
    store.topics = [topic("llm", 12), topic("drone", 3)];
    store.activeTag = "llm";

    const wrapper = mountHotTopics();
    const active = wrapper.find(".hot-tag.active");
    expect(active.exists()).toBe(true);
    expect(active.attributes("title")).toBe("llm");
    expect(active.attributes("aria-pressed")).toBe("true");
  });
});

describe("click to filter", () => {
  it("clicking a tag sends the news query with the tag param", async () => {
    const store = useTechStore();
    store.topics = [topic("llm", 42)];

    const wrapper = mountHotTopics();
    await wrapper.find(".hot-tag").trigger("click");
    await flushPromises();

    expect(store.activeTag).toBe("llm");
    expect(mockApiGet).toHaveBeenCalledWith(
      "/tech/news",
      expect.objectContaining({ tag: "llm" }),
    );
  });

  it("clicking the active tag again clears the filter", async () => {
    const store = useTechStore();
    store.topics = [topic("llm", 42)];

    const wrapper = mountHotTopics();
    await wrapper.find(".hot-tag").trigger("click");
    await flushPromises();
    expect(store.activeTag).toBe("llm");

    await wrapper.find(".hot-tag").trigger("click");
    await flushPromises();

    expect(store.activeTag).toBe("");
    const lastCall = mockApiGet.mock.calls.at(-1);
    expect(lastCall?.[0]).toBe("/tech/news");
    expect(lastCall?.[1]).not.toHaveProperty("tag");
  });
});

describe("empty and loading states", () => {
  it("renders nothing when there are no topics", () => {
    const store = useTechStore();
    store.topics = [];
    store.topicsLoading = false;

    const wrapper = mountHotTopics();
    // Only v-if comment placeholders may remain — no visible content
    expect(wrapper.find(".hot-topics").exists()).toBe(false);
    expect(wrapper.find(".hot-tag").exists()).toBe(false);
    expect(wrapper.find(".skeleton-tag").exists()).toBe(false);
    expect(wrapper.text()).toBe("");
  });

  it("shows skeleton pills while topics are loading", () => {
    const store = useTechStore();
    store.topics = [];
    store.topicsLoading = true;

    const wrapper = mountHotTopics();
    expect(wrapper.findAll(".skeleton-tag").length).toBeGreaterThan(0);
    expect(wrapper.find(".hot-tag").exists()).toBe(false);
  });
});
