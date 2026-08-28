import { apiGet } from "@/utils/api";
import type {
  BusinessMetrics,
  DashboardSystemInfo,
  ServiceHealth,
  DataSourceHealthSummary,
  DataSourceHealthDetailResponse,
  SchedulerStatusResponse,
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
  scheduler: () => apiGet<SchedulerStatusResponse>("/dashboard/scheduler"),
  sseStats: () => apiGet<SSEStats>("/dashboard/sse-stats"),
  businessMetrics: () => apiGet<BusinessMetrics>("/dashboard/business-metrics"),
};
