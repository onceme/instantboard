/**
 * CategoryView basic rendering: resolves the /c/:slug param against the
 * tenant's custom categories, fetches the generic category items feed and
 * renders it as NewsCards with a category header; unknown slugs and empty
 * feeds degrade to distinct EmptyStates.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import type { AxiosResponse } from "axios";
import { createPinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";

import { apiClient } from "@/utils/api";
import type { Category, TechNewsItem } from "@/types";
import CategoryView from "@/views/CategoryView.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import NewsCard from "@/components/tech/NewsCard.vue";

const CATEGORIES: Category[] = [
  {
    id: "c-fin",
    name: "财经",
    slug: "finance",
    icon: "chart-line",
    type: "finance",
    refresh_interval_seconds: 30,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "c-sports",
    name: "体育",
    slug: "sports",
    description: "赛事与球队动态",
    icon: "trophy",
    type: "custom",
    refresh_interval_seconds: 300,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

function makeItem(seq: number): TechNewsItem {
  return {
    id: `item-${seq}`,
    title: `Item ${seq}`,
    summary: `Summary ${seq}`,
    url: `https://example.com/${seq}`,
    source_name: "SportsWire",
    source_id: "src-1",
    category_id: "c-sports",
    topic_tags: ["sports"],
    published_at: "2026-08-25T10:00:00Z",
    fetched_at: "2026-08-25T10:05:00Z",
    priority: 5,
  };
}

// Route stub responses by endpoint: /categories serves the category list,
// /categories/{id}/items the paginated item feed
function stubApi(items: TechNewsItem[]) {
  apiClient.defaults.adapter = async (config) => {
    const url = config.url || "";
    const data = url.includes("/items")
      ? {
          success: true,
          data: items,
          meta: { total: items.length, page: 1, page_size: 20 },
        }
      : {
          success: true,
          data: CATEGORIES,
          meta: { total: CATEGORIES.length, page: 1, page_size: 20 },
        };
    return {
      status: 200,
      statusText: "OK",
      data,
      headers: {},
      config,
    } as AxiosResponse;
  };
}

async function mountAt(slug: string, items: TechNewsItem[]) {
  localStorage.clear();
  localStorage.setItem("access_token", "test-token");
  stubApi(items);

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/c/:slug", name: "category", component: CategoryView },
      { path: "/", name: "home", component: { template: "<div/>" } },
    ],
  });
  await router.push(`/c/${slug}`);
  await router.isReady();

  const wrapper = mount(CategoryView, {
    global: { plugins: [createPinia(), router] },
  });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("category feed rendering", () => {
  it("renders the category header and a NewsCard per item", async () => {
    const wrapper = await mountAt("sports", [makeItem(1), makeItem(2)]);

    expect(wrapper.find(".category-name").text()).toBe("体育");
    expect(wrapper.find(".category-desc").text()).toBe("赛事与球队动态");
    const cards = wrapper.findAllComponents(NewsCard);
    expect(cards).toHaveLength(2);
    expect(wrapper.text()).toContain("Item 1");
    expect(wrapper.text()).toContain("Item 2");
  });

  it("shows the feed-end marker when all pages are displayed", async () => {
    const wrapper = await mountAt("sports", [makeItem(1)]);

    expect(wrapper.find(".feed-end").text()).toBe("已显示全部内容");
  });
});

describe("degraded states", () => {
  it("shows the not-found EmptyState for an unknown slug", async () => {
    const wrapper = await mountAt("unknown-slug", []);

    const empty = wrapper.findComponent(EmptyState);
    expect(empty.exists()).toBe(true);
    expect(empty.props("title")).toBe("分类不存在");
    expect(wrapper.findAllComponents(NewsCard)).toHaveLength(0);
  });

  it("shows the no-content EmptyState for a known category without items", async () => {
    const wrapper = await mountAt("sports", []);

    expect(wrapper.find(".category-name").text()).toBe("体育");
    const empty = wrapper.findComponent(EmptyState);
    expect(empty.exists()).toBe(true);
    expect(empty.props("title")).toBe("暂无内容");
    expect(wrapper.find(".feed-end").exists()).toBe(false);
  });
});
