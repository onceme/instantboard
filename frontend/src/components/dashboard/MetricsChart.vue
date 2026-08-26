<script setup lang="ts">
// Resource trend chart (docs/dev-guide/design/dashboard-tab.md §3.8.1): CPU% /
// memory% live line chart rendered from the dashboard store's rolling windows
// cpuHistory/memoryHistory ({ time, value } points). Each SSE
// system_metric_update push mutates those arrays, which reactively rebuilds
// chartData; vue-chartjs then updates the existing Chart.js instance in place
// (animation: false — §3.8.3 no-animation refresh). The store windows are
// capped at 60 points and the backend samples every 30s, so 60 points cover
// the last 30 minutes (hence the title).
import { computed } from "vue";
import { useDashboardStore } from "@/stores/dashboard";
import ChartWrapper from "./ChartWrapper.vue";

const dashboardStore = useDashboardStore();

const isEmpty = computed(
  () =>
    dashboardStore.cpuHistory.length === 0 &&
    dashboardStore.memoryHistory.length === 0,
);

function formatLabel(time: number): string {
  const d = new Date(time);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

const chartData = computed(() => {
  const cpu = dashboardStore.cpuHistory;
  const memory = dashboardStore.memoryHistory;
  const base = cpu.length >= memory.length ? cpu : memory;
  return {
    labels: base.map((point) => formatLabel(point.time)),
    datasets: [
      {
        label: "CPU",
        data: cpu.map((point) => point.value),
        borderColor: "#3b82f6", // --accent (light)
        backgroundColor: "rgba(59, 130, 246, 0.12)",
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      },
      {
        label: "内存",
        data: memory.map((point) => point.value),
        borderColor: "#8b5cf6", // accent violet (--domain-robotics, light)
        backgroundColor: "rgba(139, 92, 246, 0.12)",
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      },
    ],
  };
});

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  interaction: { mode: "index", intersect: false },
  plugins: {
    legend: { display: true, labels: { boxWidth: 12, boxHeight: 12 } },
  },
  scales: {
    x: {
      grid: { display: false },
      ticks: { maxTicksLimit: 6 },
    },
    y: {
      min: 0,
      max: 100,
      ticks: {
        maxTicksLimit: 5,
        callback: (value: number | string) => `${value}%`,
      },
    },
  },
}));
</script>

<template>
  <div class="metrics-chart">
    <h3 class="chart-title">资源趋势（最近 30 分钟）</h3>

    <p v-if="isEmpty" class="empty-hint">暂无趋势数据，等待指标采样…</p>
    <div v-else class="chart-section">
      <ChartWrapper type="line" :data="chartData" :options="chartOptions" />
    </div>
  </div>
</template>

<style scoped>
.metrics-chart {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.chart-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.chart-section {
  height: 220px;
}

.empty-hint {
  font-size: 13px;
  color: var(--text-muted);
  padding: 24px 0;
  text-align: center;
}
</style>
