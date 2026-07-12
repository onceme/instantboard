<script setup lang="ts">
import { useDashboardStore } from "@/stores/dashboard";
import { computed } from "vue";
import ChartWrapper from "./ChartWrapper.vue";

const dashboardStore = useDashboardStore();

const stats = computed(() => dashboardStore.sseStats);

const channelChartData = computed(() => {
  if (!stats.value) return null;

  const labels = Object.keys(stats.value.connections_by_channel);
  const data = Object.values(stats.value.connections_by_channel);

  return {
    labels,
    datasets: [
      {
        label: "连接数",
        data,
        backgroundColor: labels.map((_, i) => {
          const colors = [
            "#3B82F6",
            "#8B5CF6",
            "#10B981",
            "#F59E0B",
            "#EF4444",
          ];
          return colors[i % colors.length];
        }),
      },
    ],
  };
});

const channelChartOptions = computed(() => ({
  responsive: true,
  plugins: {
    legend: { display: false },
  },
  scales: {
    y: {
      beginAtZero: true,
      grid: { color: "var(--border-color)" },
      ticks: { color: "var(--text-muted)" },
    },
    x: {
      grid: { display: false },
      ticks: { color: "var(--text-muted)" },
    },
  },
}));
</script>

<template>
  <div class="sse-stats">
    <h3 class="stats-title">SSE连接统计</h3>

    <div class="stats-grid">
      <div class="stat-card">
        <span class="stat-number">{{ stats?.total_connections ?? "--" }}</span>
        <span class="stat-label">总连接数</span>
        <div class="live-indicator" />
      </div>

      <div class="stat-card">
        <span class="stat-number">{{
          stats?.peak_connections_24h ?? "--"
        }}</span>
        <span class="stat-label">今日峰值</span>
      </div>

      <div class="stat-card">
        <span class="stat-number">{{ stats?.events_pushed_24h ?? "--" }}</span>
        <span class="stat-label">事件推送</span>
      </div>
    </div>

    <div v-if="channelChartData" class="chart-section">
      <ChartWrapper
        type="bar"
        :data="channelChartData"
        :options="channelChartOptions"
      />
    </div>
  </div>
</template>

<style scoped>
.sse-stats {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.stats-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-md);
}

.stat-number {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
}

.stat-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 2px;
}

.live-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--success);
  margin-top: 4px;
  animation: blink 2s ease infinite;
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.chart-section {
  margin-top: 8px;
}

@media (max-width: 767px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>
