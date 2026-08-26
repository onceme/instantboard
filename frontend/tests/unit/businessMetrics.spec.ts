/**
 * BusinessMetrics panel (dashboard-tab.md §3.5, P2-25): five system-wide
 * business metrics from GET /dashboard/business-metrics — four stat cards
 * (active users 24h, items today, watchlist total, SSE events 1h) plus a
 * category-distribution bar list. The panel is admin-only, hides entirely for
 * non-admin sessions, shows "--" placeholders while the metrics are loading,
 * and degrades to an empty-state line when the distribution has no rows.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import BusinessMetrics from "@/components/dashboard/BusinessMetrics.vue";
import { useAuthStore } from "@/stores/auth";
import { useDashboardStore } from "@/stores/dashboard";
import type { BusinessMetrics as BusinessMetricsData, User } from "@/types";

function makeUser(role: User["role"]): User {
  return {
    id: "u-1",
    email: "user@example.com",
    name: "用户",
    tenant_id: "t-1",
    role,
  };
}

function makeMetrics(
  overrides: Partial<BusinessMetricsData> = {},
): BusinessMetricsData {
  return {
    active_users_24h: 7,
    items_today: 15,
    watchlist_total: 3,
    events_pushed_1h: 35,
    category_distribution: [
      { category_name: "财经", count: 10 },
      { category_name: "科技", count: 5 },
    ],
    ...overrides,
  };
}

function mountPanel(role: User["role"], metrics: BusinessMetricsData | null) {
  useAuthStore().user = makeUser(role);
  useDashboardStore().businessMetrics = metrics;
  return mount(BusinessMetrics);
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("admin rendering", () => {
  it("renders the four metric cards with their values", () => {
    const wrapper = mountPanel("admin", makeMetrics());

    const numbers = wrapper.findAll(".stat-card .stat-number");
    expect(numbers).toHaveLength(4);
    expect(numbers[0].text()).toBe("7");
    expect(numbers[1].text()).toBe("15");
    expect(numbers[2].text()).toBe("3");
    expect(numbers[3].text()).toBe("35");

    const labels = wrapper.findAll(".stat-card .stat-label");
    expect(labels.map((l) => l.text())).toEqual([
      "活跃用户(24h)",
      "今日新增条目",
      "自选列表总条目",
      "SSE推送事件(1h)",
    ]);
  });

  it("renders the category distribution rows sorted as provided", () => {
    const wrapper = mountPanel("admin", makeMetrics());

    const rows = wrapper.findAll(".distribution-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].find(".distribution-name").text()).toBe("财经");
    expect(rows[0].find(".distribution-count").text()).toBe("10");
    expect(rows[1].find(".distribution-name").text()).toBe("科技");
    expect(rows[1].find(".distribution-count").text()).toBe("5");
  });

  it("scales distribution bars relative to the largest category", () => {
    const wrapper = mountPanel("admin", makeMetrics());

    const bars = wrapper.findAll(".distribution-bar");
    expect(bars[0].attributes("style")).toContain("width: 100%");
    expect(bars[1].attributes("style")).toContain("width: 50%");
  });

  it("shows the empty-state line when the distribution has no rows", () => {
    const wrapper = mountPanel(
      "admin",
      makeMetrics({ category_distribution: [] }),
    );

    expect(wrapper.find(".distribution-row").exists()).toBe(false);
    expect(wrapper.find(".distribution-empty").text()).toBe("暂无分类数据");
  });
});

describe("missing-data placeholder", () => {
  it("renders -- in every card while the metrics are loading", () => {
    const wrapper = mountPanel("admin", null);

    const numbers = wrapper.findAll(".stat-card .stat-number");
    expect(numbers).toHaveLength(4);
    expect(numbers.map((n) => n.text())).toEqual(["--", "--", "--", "--"]);
    // Cards stay visible, but the distribution falls back to the empty state.
    expect(wrapper.find(".distribution-empty").exists()).toBe(true);
  });
});

describe("access control", () => {
  it("hides the whole panel for non-admin sessions", () => {
    const wrapper = mountPanel("member", makeMetrics());
    expect(wrapper.find(".business-metrics").exists()).toBe(false);
  });
});
