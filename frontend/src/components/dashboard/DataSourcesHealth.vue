<script setup lang="ts">
import { useDashboardStore } from '@/stores/dashboard'
import { formatRelativeTime } from '@/utils/format'
import { computed, ref } from 'vue'

import { CheckCircle2, AlertTriangle, XCircle } from 'lucide-vue-next'

const dashboardStore = useDashboardStore()

const summary = computed(() => dashboardStore.dataSources)

const expandedRow = ref<string | null>(null)

function statusIcon(status: string) {
  if (status === 'healthy') return CheckCircle2
  if (status === 'degraded') return AlertTriangle
  return XCircle
}

function statusColor(status: string): string {
  if (status === 'healthy') return 'var(--success)'
  if (status === 'degraded') return 'var(--warning)'
  return 'var(--danger)'
}

function toggleExpand(id: string) {
  expandedRow.value = expandedRow.value === id ? null : id
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

    <div class="sources-table" v-if="summary">
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
          <tr
            v-for="source in summary.sources"
            :key="source.id"
            :class="{ 'row-down': source.status === 'down', 'row-expanded': expandedRow === source.id }"
            @click="toggleExpand(source.id)"
          >
            <td class="cell-name">
              <component :is="statusIcon(source.status)" :size="14" :style="{ color: statusColor(source.status) }" />
              {{ source.name }}
            </td>
            <td>{{ source.id }}</td>
            <td>
              <span class="status-badge" :style="{ backgroundColor: statusColor(source.status), color: 'white' }">
                {{ source.status }}
              </span>
            </td>
            <td>{{ source.last_success_at ? formatRelativeTime(source.last_success_at) : '--' }}</td>
            <td>{{ source.last_failure_at ? formatRelativeTime(source.last_failure_at) : '--' }}</td>
            <td>{{ source.avg_response_time_ms }}ms</td>
          </tr>
        </tbody>
      </table>
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

tbody tr {
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

tbody tr:hover {
  background-color: var(--bg-hover);
}

.row-down {
  background-color: rgba(239, 68, 68, 0.05);
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
</style>
