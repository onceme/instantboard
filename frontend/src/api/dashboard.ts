import { apiGet } from "@/utils/api";
import type {
  DashboardSystemInfo,
  ServiceHealth,
  DataSourceHealthSummary,
  DataSourceHealthDetailResponse,
  SchedulerStatus,
  SSEStats,
} from "@/types";

export const dashboardApi = {
  system: () => apiGet<DashboardSystemInfo>("/dashboard/system"),
  services: () => apiGet<ServiceHealth[]>("/dashboard/services"),
  dataSources: () => apiGet<DataSourceHealthSummary>("/dashboard/data-sources"),
  dataSourceDetail: (sourceId: string) =>
    apiGet<DataSourceHealthDetailResponse>(
      `/dashboard/data-sources/${sourceId}`,
    ),
  scheduler: () => apiGet<SchedulerStatus[]>("/dashboard/scheduler"),
  sseStats: () => apiGet<SSEStats>("/dashboard/sse-stats"),
};
