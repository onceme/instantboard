import { defineStore } from "pinia";
import { ref } from "vue";
import type {
  DashboardSystemInfo,
  ServiceHealth,
  DataSourceHealthSummary,
  DataSourceHealthDetail,
  SchedulerStatus,
  SSEStats,
  SSEEventType,
} from "@/types";
import { apiGet } from "@/utils/api";
import { SSEConnection, SSEConnectionState } from "@/utils/sse.ts";
import { useAuthStore } from "./auth";

export const useDashboardStore = defineStore("dashboard", () => {
  const systemInfo = ref<DashboardSystemInfo | null>(null);
  const services = ref<ServiceHealth[]>([]);
  const dataSources = ref<DataSourceHealthSummary | null>(null);
  const scheduler = ref<SchedulerStatus[]>([]);
  const sseStats = ref<SSEStats | null>(null);
  const sseConnection = ref<SSEConnection | null>(null);
  const sseState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);

  const cpuHistory = ref<Array<{ time: number; value: number }>>([]);
  const memoryHistory = ref<Array<{ time: number; value: number }>>([]);

  async function fetchSystemInfo() {
    const response = await apiGet<DashboardSystemInfo>("/dashboard/system");
    systemInfo.value = response.data;
  }

  async function fetchServices() {
    const response = await apiGet<ServiceHealth[]>("/dashboard/services");
    services.value = response.data;
  }

  async function fetchDataSources() {
    const response = await apiGet<DataSourceHealthSummary>(
      "/dashboard/data-sources",
    );
    dataSources.value = response.data;
  }

  async function fetchScheduler() {
    const response = await apiGet<SchedulerStatus[]>("/dashboard/scheduler");
    scheduler.value = response.data;
  }

  async function fetchSSEStats() {
    const response = await apiGet<SSEStats>("/dashboard/sse-stats");
    sseStats.value = response.data;
  }

  function updateSystemMetricFromSSE(data: Partial<DashboardSystemInfo>) {
    if (systemInfo.value) {
      Object.assign(systemInfo.value, data);

      if (data.cpu_usage_percent) {
        const now = Date.now();
        cpuHistory.value.push({ time: now, value: data.cpu_usage_percent });
        if (cpuHistory.value.length > 60) cpuHistory.value.shift();
      }

      const memPercent =
        data.memory_used_mb && data.memory_total_mb
          ? (data.memory_used_mb / data.memory_total_mb) * 100
          : undefined;
      if (memPercent) {
        const now = Date.now();
        memoryHistory.value.push({ time: now, value: memPercent });
        if (memoryHistory.value.length > 60) memoryHistory.value.shift();
      }
    }
  }

  function updateSourceHealthFromSSE(data: DataSourceHealthDetail) {
    if (dataSources.value) {
      const index = dataSources.value.sources.findIndex(
        (s) => s.id === data.id,
      );
      if (index >= 0) {
        dataSources.value.sources[index] = data;

        dataSources.value.healthy = dataSources.value.sources.filter(
          (s) => s.status === "healthy",
        ).length;
        dataSources.value.degraded = dataSources.value.sources.filter(
          (s) => s.status === "degraded",
        ).length;
        dataSources.value.down = dataSources.value.sources.filter(
          (s) => s.status === "down",
        ).length;
      }
    }
  }

  function connectSSE() {
    const authStore = useAuthStore();
    if (sseConnection.value) {
      sseConnection.value.disconnect();
    }

    sseConnection.value = new SSEConnection({
      category: "dashboard",
      token: authStore.token,
      onStateChange: (state) => {
        sseState.value = state;
      },
      eventHandlers: {
        [SSEEventType.SYSTEM_METRIC_UPDATE]: (data) =>
          updateSystemMetricFromSSE(data as never),
        [SSEEventType.SOURCE_HEALTH_UPDATE]: (data) =>
          updateSourceHealthFromSSE(data as never),
      },
    });

    sseConnection.value.connect();
  }

  function disconnectSSE() {
    if (sseConnection.value) {
      sseConnection.value.disconnect();
      sseConnection.value = null;
    }
  }

  function init() {
    fetchSystemInfo();
    fetchServices();
    fetchDataSources();
    fetchScheduler();
    fetchSSEStats();
    connectSSE();
  }

  function cleanup() {
    disconnectSSE();
  }

  return {
    systemInfo,
    services,
    dataSources,
    scheduler,
    sseStats,
    sseState,
    cpuHistory,
    memoryHistory,
    fetchSystemInfo,
    fetchServices,
    fetchDataSources,
    fetchScheduler,
    fetchSSEStats,
    connectSSE,
    disconnectSSE,
    init,
    cleanup,
  };
});
