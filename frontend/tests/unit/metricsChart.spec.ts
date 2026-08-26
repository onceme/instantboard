/**
 * MetricsChart (docs/dev-guide/design/dashboard-tab.md §3.8.1): live CPU /
 * memory trend chart bound to the dashboard store's 60-point rolling windows
 * (cpuHistory / memoryHistory). Chart.js canvas rendering is not supported by
 * happy-dom, so ChartWrapper is stubbed (same approach as the other dashboard
 * component specs) and assertions target the data/options handed to it —
 * dataset structure, 60-point window, HH:MM:SS labels, 0-100% y scale — plus
 * the reactive chain from updateSystemMetricFromSSE (SSE system_metric_update).
 */
import { beforeEach, describe, expect, it } from "vitest";
import { defineComponent, h, nextTick } from "vue";
import { mount } from "@vue/test-utils";
import type { Pinia } from "pinia";
import { createPinia, setActivePinia } from "pinia";

import MetricsChart from "@/components/dashboard/MetricsChart.vue";
import ChartWrapper from "@/components/dashboard/ChartWrapper.vue";
import { useDashboardStore } from "@/stores/dashboard";
import type { DashboardSystemInfo } from "@/types";

interface ChartDataShape {
  labels: string[];
  datasets: Array<{ label: string; data: number[]; borderColor: string }>;
}

interface ChartOptionsShape {
  animation: boolean;
  scales: { y: { min: number; max: number } };
}

const ChartStub = defineComponent({
  name: "ChartWrapper",
  props: {
    type: { type: String, required: true },
    data: { type: Object, required: true },
    options: { type: Object, default: () => ({}) },
  },
  setup(props) {
    return () => h("div", { class: "chart-stub", "data-type": props.type });
  },
});

function makeSystemInfo(
  overrides: Partial<DashboardSystemInfo> = {},
): DashboardSystemInfo {
  return {
    version: "1.0.0",
    uptime_seconds: 3600,
    environment: "development",
    python_version: "3.12.3",
    cpu_count: 4,
    cpu_usage_percent: 25,
    memory_total_mb: 8192,
    memory_used_mb: 4096,
    disk_total_gb: 100,
    disk_used_gb: 40,
    ...overrides,
  };
}

let pinia: Pinia;

function mountChart() {
  return mount(MetricsChart, {
    global: { plugins: [pinia], stubs: { ChartWrapper: ChartStub } },
  });
}

// findComponent resolves the stub through the original ChartWrapper reference.
function getChartStub(wrapper: ReturnType<typeof mountChart>) {
  return wrapper.findComponent(ChartWrapper);
}

function getChartData(wrapper: ReturnType<typeof mountChart>): ChartDataShape {
  return getChartStub(wrapper).props("data") as unknown as ChartDataShape;
}

function getChartOptions(
  wrapper: ReturnType<typeof mountChart>,
): ChartOptionsShape {
  return getChartStub(wrapper).props("options") as unknown as ChartOptionsShape;
}

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
});

describe("empty state", () => {
  it("shows the placeholder and no chart when both histories are empty", () => {
    const wrapper = mountChart();

    expect(wrapper.find(".chart-title").text()).toBe(
      "资源趋势（最近 30 分钟）",
    );
    expect(wrapper.find(".empty-hint").exists()).toBe(true);
    expect(wrapper.find(".empty-hint").text()).toContain("暂无趋势数据");
    expect(wrapper.findComponent(ChartWrapper).exists()).toBe(false);
  });
});

describe("dataset construction", () => {
  it("builds one dataset per metric with store values and HH:MM:SS labels", () => {
    const store = useDashboardStore();
    const base = Date.now();
    store.cpuHistory = [
      { time: base, value: 12 },
      { time: base + 30_000, value: 45 },
      { time: base + 60_000, value: 8 },
    ];
    store.memoryHistory = [
      { time: base, value: 50 },
      { time: base + 30_000, value: 55 },
      { time: base + 60_000, value: 60 },
    ];

    const wrapper = mountChart();
    const data = getChartData(wrapper);

    expect(getChartStub(wrapper).props("type")).toBe("line");
    expect(data.datasets).toHaveLength(2);
    expect(data.datasets[0].label).toBe("CPU");
    expect(data.datasets[0].data).toEqual([12, 45, 8]);
    expect(data.datasets[0].borderColor).toBe("#3b82f6");
    expect(data.datasets[1].label).toBe("内存");
    expect(data.datasets[1].data).toEqual([50, 55, 60]);
    expect(data.datasets[1].borderColor).toBe("#8b5cf6");

    expect(data.labels).toHaveLength(3);
    for (const label of data.labels) {
      expect(label).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    }
  });

  it("labels follow the longer series; shorter dataset simply ends earlier", () => {
    const store = useDashboardStore();
    const base = Date.now();
    store.cpuHistory = [
      { time: base, value: 10 },
      { time: base + 30_000, value: 20 },
      { time: base + 60_000, value: 30 },
    ];
    store.memoryHistory = [{ time: base, value: 50 }];

    const data = getChartData(mountChart());

    expect(data.labels).toHaveLength(3);
    expect(data.datasets[0].data).toHaveLength(3);
    expect(data.datasets[1].data).toHaveLength(1);
  });

  it("pins the y scale to 0-100 and disables animation (no-animation refresh)", () => {
    const store = useDashboardStore();
    store.cpuHistory = [{ time: Date.now(), value: 33 }];

    const options = getChartOptions(mountChart());

    expect(options.scales.y.min).toBe(0);
    expect(options.scales.y.max).toBe(100);
    expect(options.animation).toBe(false);
  });
});

describe("SSE reactive chain (system_metric_update)", () => {
  it("switches from placeholder to chart when the first point arrives", async () => {
    const store = useDashboardStore();
    const wrapper = mountChart();
    expect(wrapper.find(".empty-hint").exists()).toBe(true);

    store.systemInfo = makeSystemInfo();
    store.updateSystemMetricFromSSE({
      cpu_usage_percent: 42,
      memory_used_mb: 2048,
      memory_total_mb: 8192,
    });
    await nextTick();

    expect(wrapper.find(".empty-hint").exists()).toBe(false);
    const data = getChartData(wrapper);
    expect(data.datasets[0].data).toEqual([42]);
    expect(data.datasets[1].data).toEqual([25]); // 2048/8192 * 100
    expect(data.labels).toHaveLength(1);
  });

  it("keeps the chart capped at the 60-point rolling window", () => {
    const store = useDashboardStore();
    store.systemInfo = makeSystemInfo();
    for (let i = 1; i <= 65; i++) {
      store.updateSystemMetricFromSSE({ cpu_usage_percent: i });
    }

    const wrapper = mountChart();
    const data = getChartData(wrapper);

    expect(data.datasets[0].data).toHaveLength(60);
    expect(data.labels).toHaveLength(60);
    expect(data.datasets[0].data[59]).toBe(65);
    expect(data.datasets[0].data[0]).toBe(6);
  });
});
