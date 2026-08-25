/**
 * Sidebar dynamic custom-category navigation: tenant custom categories
 * (type === "custom", active) render as extra nav entries below the fixed
 * ones, linking to the generic /c/:slug feed view; predefined/inactive
 * categories never appear; an empty or failed category fetch degrades to the
 * fixed entries only; admin-entry sessions keep their back-office menu.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import type { AxiosResponse } from "axios";
import { createPinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";

import { apiClient } from "@/utils/api";
import type { Category } from "@/types";
import Sidebar from "@/components/layout/Sidebar.vue";

const requests: string[] = [];

function stubApi(categories: Category[]) {
  apiClient.defaults.adapter = async (config) => {
    requests.push(config.url || "");
    return {
      status: 200,
      statusText: "OK",
      data: {
        success: true,
        data: categories,
        meta: { total: categories.length, page: 1, page_size: 20 },
      },
      headers: {},
      config,
    } as AxiosResponse;
  };
}

function makeCategory(overrides: Partial<Category>): Category {
  return {
    id: "c-1",
    name: "Sports",
    slug: "sports",
    icon: "trophy",
    color: "#3B82F6",
    type: "custom",
    refresh_interval_seconds: 300,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const PREDEFINED: Category[] = [
  makeCategory({
    id: "c-fin",
    name: "财经",
    slug: "finance",
    icon: "chart-line",
    type: "finance",
  }),
  makeCategory({
    id: "c-tech",
    name: "科技",
    slug: "tech",
    icon: "cpu",
    type: "tech",
  }),
];

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/finance", name: "finance", component: { template: "<div/>" } },
      { path: "/tech", name: "tech", component: { template: "<div/>" } },
      {
        path: "/dashboard",
        name: "dashboard",
        component: { template: "<div/>" },
      },
      {
        path: "/settings",
        name: "settings",
        component: { template: "<div/>" },
      },
      { path: "/c/:slug", name: "category", component: { template: "<div/>" } },
    ],
  });
}

async function mountSidebar(options: {
  categories: Category[];
  token?: boolean;
  sessionEntry?: string;
  path?: string;
}) {
  requests.length = 0;
  localStorage.clear();
  if (options.token !== false) {
    localStorage.setItem("access_token", "test-token");
  }
  if (options.sessionEntry) {
    localStorage.setItem("session_entry", options.sessionEntry);
  }

  stubApi(options.categories);
  const router = makeRouter();
  await router.push(options.path ?? "/finance");
  await router.isReady();

  const wrapper = mount(Sidebar, {
    props: { collapsed: false, mobileVisible: false },
    global: { plugins: [createPinia(), router] },
  });
  await flushPromises();
  return wrapper;
}

function navEntries(wrapper: ReturnType<typeof mountSidebar>) {
  return wrapper.findAll(".nav-item");
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("custom-category entries", () => {
  it("renders active custom categories below the fixed items", async () => {
    const wrapper = await mountSidebar({
      categories: [
        ...PREDEFINED,
        makeCategory({ id: "c-sports", name: "体育", slug: "sports" }),
        makeCategory({ id: "c-med", name: "医疗", slug: "medical" }),
      ],
    });

    const labels = navEntries(wrapper).map((el) => el.text());
    // Non-admin SSO session without admin role: dashboard entry is hidden
    expect(labels).toEqual(["财经", "科技", "设置", "体育", "医疗"]);
    expect(wrapper.find(".nav-section-label").text()).toBe("自定义分类");
  });

  it("links custom entries to /c/:slug", async () => {
    const wrapper = await mountSidebar({
      categories: [
        ...PREDEFINED,
        makeCategory({ id: "c-sports", name: "体育", slug: "sports" }),
      ],
    });

    const custom = navEntries(wrapper).filter((el) => el.text() === "体育")[0];
    expect(custom.attributes("href")).toBe("/c/sports");
  });

  it("skips predefined and inactive categories", async () => {
    const wrapper = await mountSidebar({
      categories: [
        ...PREDEFINED,
        makeCategory({
          id: "c-off",
          name: "已停用",
          slug: "offline",
          is_active: false,
        }),
      ],
    });

    const labels = navEntries(wrapper).map((el) => el.text());
    expect(labels).toEqual(["财经", "科技", "设置"]);
    expect(wrapper.find(".nav-section-label").exists()).toBe(false);
  });

  it("highlights the entry matching the current /c/:slug route", async () => {
    const wrapper = await mountSidebar({
      categories: [
        ...PREDEFINED,
        makeCategory({ id: "c-sports", name: "体育", slug: "sports" }),
      ],
      path: "/c/sports",
    });

    const active = wrapper.findAll(".nav-item.active");
    expect(active).toHaveLength(1);
    expect(active[0].text()).toBe("体育");
  });
});

describe("degraded states", () => {
  it("keeps only the fixed entries when the tenant has no custom categories", async () => {
    const wrapper = await mountSidebar({ categories: [...PREDEFINED] });

    const labels = navEntries(wrapper).map((el) => el.text());
    expect(labels).toEqual(["财经", "科技", "设置"]);
    expect(wrapper.find(".nav-section-label").exists()).toBe(false);
  });

  it("does not fetch categories without a stored token", async () => {
    await mountSidebar({ categories: [...PREDEFINED], token: false });
    expect(requests).toEqual([]);
  });

  it("keeps the fixed entries when the categories fetch fails", async () => {
    localStorage.setItem("access_token", "test-token");
    requests.length = 0;
    apiClient.defaults.adapter = async () => {
      throw new Error("network down");
    };
    const router = makeRouter();
    await router.push("/finance");
    await router.isReady();

    const wrapper = mount(Sidebar, {
      props: { collapsed: false, mobileVisible: false },
      global: { plugins: [createPinia(), router] },
    });
    await flushPromises();

    const labels = navEntries(wrapper).map((el) => el.text());
    expect(labels).toEqual(["财经", "科技", "设置"]);
  });
});

describe("admin-entry session", () => {
  it("keeps the back-office menu without custom entries", async () => {
    const wrapper = await mountSidebar({
      categories: [
        ...PREDEFINED,
        makeCategory({ id: "c-sports", name: "体育", slug: "sports" }),
      ],
      sessionEntry: "admin",
      path: "/settings",
    });

    const labels = navEntries(wrapper).map((el) => el.text());
    expect(labels).toEqual(["仪表盘", "设置"]);
    expect(wrapper.find(".nav-section-label").exists()).toBe(false);
  });
});
