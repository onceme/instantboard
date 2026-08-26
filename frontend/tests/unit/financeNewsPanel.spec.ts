/**
 * FinanceNewsPanel (right column "Top Finance News"): resolves the predefined
 * finance category from the category list, fetches its latest 5 items via
 * GET /categories/{id}/items (sort=time&page_size=5) and renders compact
 * headline links. Empty feeds hide the panel entirely; failures show the
 * inline ErrorAlert with retry.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import type { AxiosResponse } from "axios";
import { createPinia } from "pinia";

import { apiClient } from "@/utils/api";
import type { Category, TechNewsItem } from "@/types";
import FinanceNewsPanel from "@/components/finance/FinanceNewsPanel.vue";

const FINANCE_CATEGORY: Category = {
  id: "c-fin",
  name: "财经",
  slug: "finance",
  type: "finance",
  refresh_interval_seconds: 30,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const TECH_CATEGORY: Category = {
  id: "c-tech",
  name: "科技",
  slug: "tech",
  type: "tech",
  refresh_interval_seconds: 30,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60 * 1000).toISOString();
}

function makeItem(seq: number, publishedAt: string): TechNewsItem {
  return {
    id: `item-${seq}`,
    title: `财经要闻 ${seq}`,
    summary: `Summary ${seq}`,
    url: `https://example.com/finance/${seq}`,
    source_name: `财经源${seq}`,
    source_id: "src-1",
    category_id: "c-fin",
    topic_tags: [],
    published_at: publishedAt,
    fetched_at: publishedAt,
    priority: 5,
  };
}

interface RequestOptions {
  categories: Category[];
  items?: TechNewsItem[];
  itemsError?: string;
}

interface CapturedRequest {
  url: string;
  params?: Record<string, unknown>;
}

// Route stub responses by endpoint (/categories vs /categories/{id}/items)
// and capture every request so tests can assert the query contract
function stubApi(options: RequestOptions) {
  const requests: CapturedRequest[] = [];
  apiClient.defaults.adapter = async (config) => {
    const url = config.url || "";
    requests.push({ url, params: config.params as Record<string, unknown> | undefined });

    if (url.includes("/items")) {
      if (options.itemsError) {
        throw Object.assign(new Error("request failed"), {
          isAxiosError: true,
          config,
          response: {
            status: 500,
            statusText: "Internal Server Error",
            data: { detail: options.itemsError },
            headers: {},
            config,
          },
        });
      }
      const items = options.items ?? [];
      return {
        status: 200,
        statusText: "OK",
        data: {
          success: true,
          data: items,
          meta: { total: items.length, page: 1, page_size: 5 },
        },
        headers: {},
        config,
      } as AxiosResponse;
    }

    return {
      status: 200,
      statusText: "OK",
      data: {
        success: true,
        data: options.categories,
        meta: { total: options.categories.length, page: 1, page_size: 20 },
      },
      headers: {},
      config,
    } as AxiosResponse;
  };
  return requests;
}

function mountPanel() {
  return mount(FinanceNewsPanel, { global: { plugins: [createPinia()] } });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("rendering", () => {
  it("renders the latest finance items as headline links with meta line", async () => {
    const items = [makeItem(1, minutesAgo(30)), makeItem(2, minutesAgo(120))];
    const requests = stubApi({
      categories: [FINANCE_CATEGORY, TECH_CATEGORY],
      items,
    });

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find(".news-title").text()).toBe("财经要闻");
    const entries = wrapper.findAll(".news-item");
    expect(entries).toHaveLength(2);

    const link = wrapper.find("a.news-link");
    expect(link.attributes("href")).toBe("https://example.com/finance/1");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.text()).toBe("财经要闻 1");

    expect(entries[0].find(".news-source").text()).toBe("财经源1");
    expect(entries[0].find(".news-time").text()).toBe("30分钟前");

    const itemRequest = requests.find((r) => r.url.includes("/items"));
    expect(itemRequest?.url).toBe("/categories/c-fin/items");
    expect(itemRequest?.params).toMatchObject({ sort: "time", page_size: 5 });
  });

  it("shows skeleton rows while loading", async () => {
    // Never resolves so the loading state stays observable
    apiClient.defaults.adapter = () => new Promise<AxiosResponse>(() => {});

    const wrapper = mountPanel();
    // isLoading flips synchronously in onMounted; render follows on next tick
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".finance-news").exists()).toBe(true);
    expect(wrapper.findAll(".news-item-skeleton")).toHaveLength(5);
    expect(wrapper.find("a.news-link").exists()).toBe(false);
  });
});

describe("hidden states", () => {
  it("renders nothing when the finance category has no items", async () => {
    stubApi({ categories: [FINANCE_CATEGORY], items: [] });

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find(".finance-news").exists()).toBe(false);
  });

  it("renders nothing and skips the items request without a finance category", async () => {
    const requests = stubApi({ categories: [TECH_CATEGORY], items: [] });

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find(".finance-news").exists()).toBe(false);
    expect(requests.some((r) => r.url.includes("/items"))).toBe(false);
  });
});

describe("failure state", () => {
  it("shows the inline error with retry and recovers on retry", async () => {
    stubApi({
      categories: [FINANCE_CATEGORY],
      itemsError: "财经数据暂不可用",
    });

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find(".finance-news").exists()).toBe(true);
    const alert = wrapper.find(".error-alert");
    expect(alert.exists()).toBe(true);
    expect(alert.text()).toContain("财经数据暂不可用");
    expect(wrapper.findAll("a.news-link")).toHaveLength(0);

    stubApi({
      categories: [FINANCE_CATEGORY],
      items: [makeItem(1, minutesAgo(10))],
    });
    await wrapper.find(".alert-retry").trigger("click");
    await flushPromises();

    expect(wrapper.find(".error-alert").exists()).toBe(false);
    expect(wrapper.findAll("a.news-link")).toHaveLength(1);
  });
});
