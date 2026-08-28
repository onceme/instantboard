/**
 * SchedulerPanel: renders GET /dashboard/scheduler (SchedulerStatusResponse)
 * from the dashboard store — summary cards (total / running / paused counts),
 * worker heartbeat freshness (fresh below the 45s backend TTL, ⚠️ 疑似掉线
 * when stale or missing), the running/paused tab switch, the job table
 * (adaptive interval shown as original → current ×multiplier, relative
 * last/next run, 24h counters) and the EmptyState when no jobs exist.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import type { Pinia } from "pinia";
import { createPinia, setActivePinia } from "pinia";

import SchedulerPanel from "@/components/dashboard/SchedulerPanel.vue";
import { useDashboardStore } from "@/stores/dashboard";
import type { SchedulerJobInfo, SchedulerStatusResponse } from "@/types";

function makeJob(overrides: Partial<SchedulerJobInfo> = {}): SchedulerJobInfo {
  return {
    job_id: "collect_src-1",
    source_id: "src-1",
    name: "Hacker News 采集",
    schedule: "interval[0:05:00]",
    original_interval: 300,
    current_interval: 300,
    adaptive_multiplier: 1,
    last_run: null,
    next_run: null,
    status: "active",
    success_count_24h: 0,
    failure_count_24h: 0,
    ...overrides,
  };
}

function makeStatus(
  overrides: Partial<SchedulerStatusResponse> = {},
): SchedulerStatusResponse {
  const base: SchedulerStatusResponse = {
    total_jobs: 0,
    running_jobs: [],
    paused_jobs: [],
    all_jobs: [],
    running_jobs_count: 0,
    paused_jobs_count: 0,
    last_heartbeat: null,
  };
  const merged = { ...base, ...overrides };
  if (!overrides.all_jobs && !overrides.total_jobs) {
    merged.all_jobs = [...merged.running_jobs, ...merged.paused_jobs];
    merged.total_jobs = merged.all_jobs.length;
  }
  if (
    !overrides.running_jobs_count &&
    !overrides.paused_jobs_count &&
    !overrides.total_jobs
  ) {
    merged.running_jobs_count = merged.running_jobs.length;
    merged.paused_jobs_count = merged.paused_jobs.length;
  }
  return merged;
}

let pinia: Pinia;

function mountWithScheduler(status: SchedulerStatusResponse | null) {
  const store = useDashboardStore();
  store.scheduler = status;
  return mount(SchedulerPanel, { global: { plugins: [pinia] } });
}

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
});

describe("summary cards", () => {
  it("renders total, running and paused counts from the response", () => {
    const wrapper = mountWithScheduler(
      makeStatus({
        total_jobs: 5,
        running_jobs_count: 3,
        paused_jobs_count: 2,
      }),
    );

    const cards = wrapper.findAll(".summary-card");
    expect(cards).toHaveLength(3);
    expect(cards[0].find(".summary-count").text()).toBe("5");
    expect(cards[0].find(".summary-label").text()).toBe("总任务");
    expect(cards[1].find(".summary-count").text()).toBe("3");
    expect(cards[1].find(".summary-label").text()).toBe("运行中");
    expect(cards[2].find(".summary-count").text()).toBe("2");
    expect(cards[2].find(".summary-label").text()).toBe("已暂停");
  });
});

describe("worker heartbeat freshness", () => {
  // Backend contract: worker.py writes a heartbeat every 15s with a 45s TTL;
  // anything older than 45s (or missing) means the worker is down.
  const NOW = new Date("2026-08-26T12:00:00Z");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a fresh heartbeat with its age when under 45s", () => {
    const wrapper = mountWithScheduler(
      makeStatus({ last_heartbeat: "2026-08-26T11:59:50Z" }),
    );

    const heartbeat = wrapper.find(".heartbeat");
    expect(heartbeat.classes()).toContain("heartbeat-fresh");
    expect(heartbeat.text()).toContain("心跳正常");
    expect(heartbeat.text()).toContain("10秒前");
    expect(heartbeat.text()).not.toContain("疑似掉线");
  });

  it("warns 疑似掉线 when the heartbeat is older than the 45s TTL", () => {
    const wrapper = mountWithScheduler(
      makeStatus({ last_heartbeat: "2026-08-26T11:59:00Z" }),
    );

    const heartbeat = wrapper.find(".heartbeat");
    expect(heartbeat.classes()).toContain("heartbeat-stale");
    expect(heartbeat.text()).toContain("⚠️ 疑似掉线");
    expect(heartbeat.text()).toContain("1分钟前");
  });

  it("warns 疑似掉线 when no heartbeat has ever been received", () => {
    const wrapper = mountWithScheduler(makeStatus({ last_heartbeat: null }));

    const heartbeat = wrapper.find(".heartbeat");
    expect(heartbeat.classes()).toContain("heartbeat-missing");
    expect(heartbeat.text()).toContain("⚠️ 疑似掉线");
    expect(heartbeat.text()).toContain("未收到");
  });
});

describe("job table", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-26T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders name, interval, relative runs, status badge and 24h counters", () => {
    const wrapper = mountWithScheduler(
      makeStatus({
        running_jobs: [
          makeJob({
            job_id: "collect_src-1",
            name: "Hacker News 采集",
            last_run: "2026-08-26T11:59:30Z",
            next_run: "2026-08-26T12:05:00Z",
            success_count_24h: 287,
            failure_count_24h: 1,
          }),
        ],
      }),
    );

    const cells = wrapper.find(".job-row").findAll("td");
    expect(cells[0].text()).toContain("Hacker News 采集");
    // 300s -> 5分钟 (multiplier = 1, plain interval)
    expect(cells[1].text()).toBe("5分钟");
    expect(cells[2].text()).toBe("30秒前");
    expect(cells[3].text()).toBe("5分钟后");
    expect(cells[4].find(".status-badge").text()).toBe("active");
    expect(cells[5].text()).toContain("287");
    expect(cells[5].text()).toContain("1");
    expect(cells[5].find(".count-failure").classes()).toContain("has-failures");
  });

  it("shows adaptive intervals as original → current with the multiplier", () => {
    const wrapper = mountWithScheduler(
      makeStatus({
        running_jobs: [
          makeJob({
            original_interval: 300,
            current_interval: 600,
            adaptive_multiplier: 2,
          }),
        ],
      }),
    );

    const intervalCell = wrapper.find(".job-row").findAll("td")[1];
    expect(intervalCell.find(".interval-original").text()).toBe("5分钟");
    expect(intervalCell.text()).toContain("→");
    expect(intervalCell.find(".interval-current").text()).toBe("10分钟");
    expect(intervalCell.find(".interval-multiplier").text()).toBe("×2");
  });

  it("renders -- placeholders for missing run times and counters", () => {
    const wrapper = mountWithScheduler(
      makeStatus({
        running_jobs: [
          makeJob({
            last_run: null,
            next_run: null,
            success_count_24h: null,
            failure_count_24h: null,
          }),
        ],
      }),
    );

    const cells = wrapper.find(".job-row").findAll("td");
    expect(cells[2].text()).toBe("--");
    expect(cells[3].text()).toBe("--");
    expect(cells[5].text().replace(/\s+/g, "")).toBe("--/--");
  });
});

describe("tab switching", () => {
  function mountWithBothTabs() {
    return mountWithScheduler(
      makeStatus({
        running_jobs: [makeJob({ name: "运行中任务" })],
        paused_jobs: [
          makeJob({
            job_id: "collect_src-2",
            name: "暂停任务",
            status: "paused",
          }),
        ],
      }),
    );
  }

  it("defaults to the running tab and switches to paused on click", async () => {
    const wrapper = mountWithBothTabs();

    let rows = wrapper.findAll(".job-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("运行中任务");

    const tabs = wrapper.findAll(".tab");
    expect(tabs[0].classes()).toContain("active");
    expect(tabs[0].text()).toContain("运行中 (1)");
    expect(tabs[1].text()).toContain("已暂停 (1)");

    await tabs[1].trigger("click");

    rows = wrapper.findAll(".job-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("暂停任务");
    expect(rows[0].text()).toContain("paused");
    expect(tabs[1].classes()).toContain("active");
  });

  it("shows the empty state on a tab with no jobs", async () => {
    const wrapper = mountWithScheduler(
      makeStatus({ running_jobs: [makeJob()] }),
    );

    await wrapper.findAll(".tab")[1].trigger("click");

    expect(wrapper.find(".job-row").exists()).toBe(false);
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.find(".empty-state").text()).toContain("暂无暂停的任务");
  });
});

describe("empty and prod modes", () => {
  it("shows the EmptyState when the scheduler has no jobs", () => {
    const wrapper = mountWithScheduler(makeStatus());

    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.find(".empty-state").text()).toContain("暂无调度任务");
    expect(wrapper.find(".tabs").exists()).toBe(false);
    expect(wrapper.find(".jobs-table").exists()).toBe(false);
  });

  it("explains when prod-mode counts exist but job details live in the worker", () => {
    // Prod mode: lists are empty, counts come from the worker heartbeat.
    const wrapper = mountWithScheduler(
      makeStatus({
        total_jobs: 3,
        running_jobs_count: 3,
        paused_jobs_count: 0,
        last_heartbeat: new Date().toISOString(),
      }),
    );

    expect(wrapper.find(".job-row").exists()).toBe(false);
    expect(wrapper.find(".prod-note").exists()).toBe(true);
    expect(wrapper.find(".prod-note").text()).toContain("worker");
  });
});
