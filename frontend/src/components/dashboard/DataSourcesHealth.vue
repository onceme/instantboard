<script setup lang="ts">
import { useDashboardStore } from "@/stores/dashboard";
import { dashboardApi } from "@/api/dashboard";
import { formatRelativeTime } from "@/utils/format";
import { getApiErrorMessage } from "@/utils/api";
import { computed, ref, watch } from "vue";

import Pagination from "@/components/common/Pagination.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { CheckCircle2, AlertTriangle, XCircle } from "lucide-vue-next";
import type {
  DataSourceHealthDetail,
  DataSourceHealthDetailResponse,
} from "@/types";

const PAGE_SIZE = 20;

const dashboardStore = useDashboardStore();

const summary = computed(() => dashboardStore.dataSources);

const statusFilter = ref<"" | "healthy" | "degraded" | "down">("");
const typeFilter = ref("");
const searchQuery = ref("");
const page = ref(1);

const expandedRow = ref<string | null>(null);
const detailCache = ref<Record<string, DataSourceHealthDetailResponse>>({});
const detailLoading = ref<Record<string, boolean>>({});
const detailError = ref<Record<string, string>>({});
const detailInflight = new Set<string>();

const sourceTypes = computed(() => {
  const types = new Set<string>();
  for (const source of summary.value?.sources ?? []) {
    if (source.source_type) types.add(source.source_type);
  }
  return [...types].sort();
});

const filteredSources = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  return (summary.value?.sources ?? []).filter((source) => {
    if (statusFilter.value && source.status !== statusFilter.value)
      return false;
    if (typeFilter.value && source.source_type !== typeFilter.value)
      return false;
    if (query && !source.name.toLowerCase().includes(query)) return false;
    return true;
  });
});

const totalFiltered = computed(() => filteredSources.value.length);
const totalPages = computed(() =>
  Math.max(1, Math.ceil(totalFiltered.value / PAGE_SIZE)),
);
const safePage = computed(() => Math.min(page.value, totalPages.value));
const pagedSources = computed(() => {
  const start = (safePage.value - 1) * PAGE_SIZE;
  return filteredSources.value.slice(start, start + PAGE_SIZE);
});

watch([statusFilter, typeFilter, searchQuery], () => {
  page.value = 1;
});

function statusIcon(status: string) {
  if (status === "healthy") return CheckCircle2;
  if (status === "degraded") return AlertTriangle;
  return XCircle;
}

function statusColor(status: string): string {
  if (status === "healthy") return "var(--success)";
  if (status === "degraded") return "var(--warning)";
  return "var(--danger)";
}

function formatSuccessRate(rate: number | null | undefined): string {
  // Backend sends a 0..1 ratio (success_count_24h / total_fetches_24h)
  if (rate === null || rate === undefined) return "--";
  return `${(rate * 100).toFixed(1)}%`;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

interface TrendPoint {
  label: string;
  ms: number;
}

function trendPoints(detail: DataSourceHealthDetailResponse): TrendPoint[] {
  const entries = Array.isArray(detail.response_time_trend)
    ? detail.response_time_trend
    : [];
  return entries.map((entry) => ({
    ms:
      asNumber(entry.ms) ??
      asNumber(entry.response_time_ms) ??
      asNumber(entry.value) ??
      0,
    label: asString(entry.ts ?? entry.time ?? entry.timestamp),
  }));
}

function trendBarHeight(point: TrendPoint, points: TrendPoint[]): number {
  const max = Math.max(...points.map((p) => p.ms), 1);
  return Math.max(2, Math.round((point.ms / max) * 40));
}

function onPageChange(newPage: number) {
  page.value = newPage;
}

async function loadDetail(id: string, force = false) {
  if (!force && detailCache.value[id]) return;
  if (detailInflight.has(id)) return;
  detailInflight.add(id);
  detailLoading.value[id] = true;
  detailError.value[id] = "";
  try {
    const response = await dashboardApi.dataSourceDetail(id);
    detailCache.value[id] = response.data;
  } catch (err) {
    detailError.value[id] = getApiErrorMessage(err, "加载详情失败");
  } finally {
    detailLoading.value[id] = false;
    detailInflight.delete(id);
  }
}

function toggleExpand(source: DataSourceHealthDetail) {
  if (expandedRow.value === source.id) {
    expandedRow.value = null;
    return;
  }
  expandedRow.value = source.id;
  loadDetail(source.id);
}

function retryDetail(id: string) {
  loadDetail(id, true);
}
</script>

<template>
  <div class="data-sources-health">
    <h3 class="health-title">数据源健康</h3>

    <div v-if="summary" class="summary-cards">
      <div class="summary-card healthy">
        <span class="summary-count">{{ summary.healthy }}</span>
        <span class="summary-label">Healthy</span>
      </div>
      <div class="summary-card degraded">
        <span class="summary-count">{{ summary.degraded }}</span>
        <span class="summary-label">Degraded</span>
      </div>
      <div class="summary-card down">
        <span class="summary-count">{{ summary.down }}</span>
        <span class="summary-label">Down</span>
      </div>
    </div>

    <div v-if="summary" class="filters">
      <input
        v-model="searchQuery"
        class="filter-search"
        type="text"
        placeholder="按名称搜索…"
        aria-label="按名称搜索"
      />
      <select
        v-model="statusFilter"
        class="filter-status"
        aria-label="状态过滤"
      >
        <option value="">全部状态</option>
        <option value="healthy">healthy</option>
        <option value="degraded">degraded</option>
        <option value="down">down</option>
      </select>
      <select v-model="typeFilter" class="filter-type" aria-label="类型过滤">
        <option value="">全部类型</option>
        <option v-for="type in sourceTypes" :key="type" :value="type">
          {{ type }}
        </option>
      </select>
    </div>

    <div v-if="summary && totalFiltered === 0" class="empty-wrapper">
      <EmptyState
        title="没有匹配的数据源"
        description="尝试调整过滤条件或清空搜索关键字"
        icon="inbox"
      />
    </div>

    <div v-if="summary && totalFiltered > 0" class="sources-table">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>状态</th>
            <th>最后成功</th>
            <th>最后失败</th>
            <th>响应时间</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="source in pagedSources" :key="source.id">
            <tr
              class="source-row"
              :class="{
                'row-down': source.status === 'down',
                'row-expanded': expandedRow === source.id,
              }"
              @click="toggleExpand(source)"
            >
              <td class="cell-name">
                <component
                  :is="statusIcon(source.status)"
                  :size="14"
                  :style="{ color: statusColor(source.status) }"
                />
                {{ source.name }}
              </td>
              <td>{{ source.source_type || "--" }}</td>
              <td>
                <span
                  class="status-badge"
                  :style="{
                    backgroundColor: statusColor(source.status),
                    color: 'white',
                  }"
                >
                  {{ source.status }}
                </span>
              </td>
              <td>
                {{
                  source.last_success_at
                    ? formatRelativeTime(source.last_success_at)
                    : "--"
                }}
              </td>
              <td>
                {{
                  source.last_failure_at
                    ? formatRelativeTime(source.last_failure_at)
                    : "--"
                }}
              </td>
              <td>{{ source.avg_response_time_ms }}ms</td>
            </tr>
            <tr v-if="expandedRow === source.id" class="detail-row">
              <td :colspan="6">
                <div v-if="detailLoading[source.id]" class="detail-loading">
                  加载中…
                </div>
                <div v-else-if="detailError[source.id]" class="detail-error">
                  <span>{{ detailError[source.id] }}</span>
                  <button
                    type="button"
                    class="detail-retry"
                    @click.stop="retryDetail(source.id)"
                  >
                    重试
                  </button>
                </div>
                <div v-else-if="detailCache[source.id]" class="detail-panel">
                  <div class="detail-metrics">
                    <div class="detail-metric">
                      <span class="detail-label">成功率 (24h)</span>
                      <span class="detail-value">{{
                        formatSuccessRate(
                          detailCache[source.id].success_rate_24h,
                        )
                      }}</span>
                    </div>
                    <div class="detail-metric">
                      <span class="detail-label">采集总数 (24h)</span>
                      <span class="detail-value">{{
                        detailCache[source.id].total_fetches_24h ?? "--"
                      }}</span>
                    </div>
                    <div class="detail-metric">
                      <span class="detail-label">连续失败</span>
                      <span class="detail-value">{{
                        detailCache[source.id].consecutive_failures ?? "--"
                      }}</span>
                    </div>
                    <div class="detail-metric">
                      <span class="detail-label">最后错误</span>
                      <span class="detail-value detail-error-text">{{
                        detailCache[source.id].last_error || "无"
                      }}</span>
                    </div>
                  </div>

                  <div class="detail-section">
                    <h4 class="detail-section-title">健康历史</h4>
                    <ul
                      v-if="
                        (detailCache[source.id].health_history ?? []).length > 0
                      "
                      class="history-list"
                    >
                      <li
                        v-for="(entry, index) in detailCache[source.id]
                          .health_history ?? []"
                        :key="index"
                        class="history-item"
                      >
                        <span
                          class="status-badge"
                          :style="{
                            backgroundColor: statusColor(
                              asString(entry.status),
                            ),
                            color: 'white',
                          }"
                        >
                          {{ asString(entry.status) || "unknown" }}
                        </span>
                        <span v-if="asString(entry.last_success_at)">
                          成功:
                          {{
                            formatRelativeTime(asString(entry.last_success_at))
                          }}
                        </span>
                        <span v-if="asString(entry.last_failure_at)">
                          失败:
                          {{
                            formatRelativeTime(asString(entry.last_failure_at))
                          }}
                        </span>
                        <span v-if="asNumber(entry.total_fetches_24h) !== null">
                          采集: {{ entry.total_fetches_24h }}
                        </span>
                        <span
                          v-if="asString(entry.last_error_message)"
                          class="history-error"
                        >
                          {{ entry.last_error_message }}
                        </span>
                      </li>
                    </ul>
                    <p v-else class="detail-empty">暂无历史</p>
                  </div>

                  <div class="detail-section">
                    <h4 class="detail-section-title">响应时间趋势</h4>
                    <div
                      v-if="trendPoints(detailCache[source.id]).length > 0"
                      class="trend-bars"
                    >
                      <div
                        v-for="(point, index) in trendPoints(
                          detailCache[source.id],
                        )"
                        :key="index"
                        class="trend-bar"
                        :style="{
                          height: `${trendBarHeight(
                            point,
                            trendPoints(detailCache[source.id]),
                          )}px`,
                        }"
                        :title="
                          point.label
                            ? `${point.label}: ${point.ms}ms`
                            : `${point.ms}ms`
                        "
                      />
                    </div>
                    <p v-else class="detail-empty">暂无趋势</p>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div v-if="summary && totalFiltered > 0" class="table-footer">
      <span class="footer-count">共 {{ totalFiltered }} 个数据源</span>
      <Pagination
        v-if="totalPages > 1"
        :page="safePage"
        :page-size="PAGE_SIZE"
        :total="totalFiltered"
        @update:page="onPageChange"
      />
    </div>
  </div>
</template>

<style scoped>
.data-sources-health {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.health-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.summary-cards {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  min-width: 80px;
}

.summary-card.healthy {
  background-color: rgba(16, 185, 129, 0.1);
  color: var(--success);
}

.summary-card.degraded {
  background-color: rgba(245, 158, 11, 0.1);
  color: var(--warning);
}

.summary-card.down {
  background-color: rgba(239, 68, 68, 0.1);
  color: var(--danger);
}

.summary-count {
  font-size: 24px;
  font-weight: 700;
}

.summary-label {
  font-size: 12px;
  margin-top: 2px;
}

.filters {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.filter-search,
.filter-status,
.filter-type {
  height: 32px;
  font-size: 13px;
  color: var(--text-secondary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 0 8px;
}

.filter-search {
  flex: 1;
  min-width: 140px;
}

.empty-wrapper {
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
}

.sources-table {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

thead th {
  font-size: 12px;
  color: var(--text-muted);
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid var(--border-color);
}

tbody td {
  font-size: 13px;
  color: var(--text-secondary);
  padding: 8px;
  border-bottom: 1px solid var(--border-light);
}

tbody tr.source-row {
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

tbody tr.source-row:hover {
  background-color: var(--bg-hover);
}

.row-down {
  background-color: rgba(239, 68, 68, 0.05);
}

.row-expanded {
  background-color: var(--bg-hover);
}

.cell-name {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}

.detail-row td {
  background-color: var(--bg-hover);
  cursor: default;
}

.detail-loading {
  font-size: 13px;
  color: var(--text-muted);
  padding: 8px 0;
}

.detail-error {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--danger);
  padding: 8px 0;
}

.detail-retry {
  font-size: 12px;
  color: var(--accent);
  background: none;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  padding: 2px 10px;
  cursor: pointer;
}

.detail-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 0;
}

.detail-metrics {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}

.detail-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 96px;
}

.detail-label {
  font-size: 11px;
  color: var(--text-muted);
}

.detail-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.detail-error-text {
  font-weight: 400;
  max-width: 320px;
  overflow-wrap: anywhere;
}

.detail-section-title {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.history-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--text-secondary);
  flex-wrap: wrap;
}

.history-error {
  color: var(--danger);
  overflow-wrap: anywhere;
}

.detail-empty {
  font-size: 12px;
  color: var(--text-muted);
}

.trend-bars {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 44px;
}

.trend-bar {
  width: 10px;
  min-height: 2px;
  background-color: var(--accent);
  border-radius: 2px 2px 0 0;
  opacity: 0.85;
}

.table-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.footer-count {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
