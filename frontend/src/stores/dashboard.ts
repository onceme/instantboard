import { defineStore } from "pinia";
import { ref } from "vue";
import type {
  BusinessMetrics,
  DashboardSystemInfo,
  ServiceHealth,
  DataSourceHealthSummary,
  SchedulerStatusResponse,
  SSEStats,
  SourceHealthUpdateEvent,
} from "@/types";
import { SSEEventType } from "@/types";
import { apiGet } from "@/utils/api";
import { SSEConnection, SSEConnectionState } from "@/utils/sse.ts";
import { useAuthStore } from "./auth";
import { useSSEStore } from "./sse";

export const useDashboardStore = defineStore("dashboard", () => {
  const systemInfo = ref<DashboardSystemInfo | null>(null);
  const services = ref<ServiceHealth[]>([]);
  const dataSources = ref<DataSourceHealthSummary | null>(null);
  const scheduler = ref<SchedulerStatusResponse | null>(null);
  const sseStats = ref<SSEStats | null>(null);
  const businessMetrics = ref<BusinessMetrics | null>(null);
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
    const response =
      await apiGet<SchedulerStatusResponse>("/dashboard/scheduler");
    scheduler.value = response.data;
  }

  async function fetchSSEStats() {
    const response = await apiGet<SSEStats>("/dashboard/sse-stats");
    sseStats.value = response.data;
  }

  async function fetchBusinessMetrics() {
    // Admin-only endpoint; degrade silently (403 / network error keeps the
    // panel hidden instead of breaking the dashboard). Contract:
    // docs/dev-guide/design/dashboard-tab.md §3.5.
    try {
      const response = await apiGet<BusinessMetrics>(
        "/dashboard/business-metrics",
      );
      businessMetrics.value = response.data;
    } catch {
      businessMetrics.value = null;
    }
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

  function updateSourceHealthFromSSE(data: SourceHealthUpdateEvent) {
    // Contract: docs/dev-guide/design/data-flow.md §3.5.4. The backend publishes the
    // full source_health row keyed by source_id (the old payload only had
    // source_id/status and this code matched on data.id, so rows never
    // refreshed). Merge the mutable health fields into the existing row and
    // recompute the summary counters; never overwrite identity columns.
    if (!dataSources.value) return;

    const index = dataSources.value.sources.findIndex(
      (s) => s.id === data.source_id,
    );
    if (index < 0) return;

    const row = dataSources.value.sources[index];
    dataSources.value.sources[index] = {
      ...row,
      status: data.status,
      last_error: data.last_error ?? undefined,
      last_success_at: data.last_success_at ?? row.last_success_at,
      last_failure_at: data.last_failure_at ?? row.last_failure_at,
      avg_response_time_ms:
        data.avg_response_time_ms ?? row.avg_response_time_ms,
      consecutive_failures:
        data.consecutive_failures ?? row.consecutive_failures,
      total_fetches_24h: data.total_fetches_24h ?? row.total_fetches_24h,
      success_rate_24h: data.success_rate_24h ?? row.success_rate_24h,
    };

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

  function connectSSE() {
    const authStore = useAuthStore();
    const sseStore = useSSEStore();
    if (sseConnection.value) {
      sseConnection.value.disconnect();
    }

    sseConnection.value = new SSEConnection({
      category: "dashboard",
      token: authStore.token,
      onStateChange: (state) => {
        sseState.value = state;
        sseStore.setDashboardState(state);
      },
      eventHandlers: {
        [SSEEventType.SYSTEM_METRIC_UPDATE]: (data) =>
          updateSystemMetricFromSSE(data as never),
        [SSEEventType.SOURCE_HEALTH_UPDATE]: (data) =>
          updateSourceHealthFromSSE(data as SourceHealthUpdateEvent),
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
    fetchBusinessMetrics();
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
    businessMetrics,
    sseState,
    cpuHistory,
    memoryHistory,
    fetchSystemInfo,
    fetchServices,
    fetchDataSources,
    fetchScheduler,
    fetchSSEStats,
    fetchBusinessMetrics,
    updateSystemMetricFromSSE,
    updateSourceHealthFromSSE,
    connectSSE,
    disconnectSSE,
    init,
    cleanup,
  };
});
