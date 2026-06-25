<script setup lang="ts">
import { useDashboardStore } from '@/stores/dashboard'
import { computed } from 'vue'
import { CheckCircle2, AlertTriangle, XCircle } from 'lucide-vue-next'

const dashboardStore = useDashboardStore()

const overallStatus = computed(() => {
  const services = dashboardStore.services
  if (services.length === 0) return 'unknown'

  const hasDown = services.some(s => s.status === 'down')
  const hasDegraded = services.some(s => s.status === 'degraded')

  if (hasDown) return 'down'
  if (hasDegraded) return 'degraded'
  return 'healthy'
})

const statusLabel = computed(() => {
  switch (overallStatus.value) {
    case 'healthy': return '系统正常运行'
    case 'degraded': return '部分服务降级'
    case 'down': return '系统异常'
    default: return '未知'
  }
})

const activeConnections = computed(() => dashboardStore.sseStats?.total_connections ?? 0)
const healthySources = computed(() => dashboardStore.dataSources?.healthy ?? 0)
const totalSources = computed(() => dashboardStore.dataSources?.total_sources ?? 0)

function statusIcon() {
  if (overallStatus.value === 'healthy') return CheckCircle2
  if (overallStatus.value === 'degraded') return AlertTriangle
  return XCircle
}

function statusColor(): string {
  if (overallStatus.value === 'healthy') return 'var(--success)'
  if (overallStatus.value === 'degraded') return 'var(--warning)'
  return 'var(--danger)'
}
</script>

<template>
  <div class="health-panel">
    <div
      class="overall-status"
      :style="{ '--status-color': statusColor() }"
    >
      <component
        :is="statusIcon()"
        :size="28"
        :style="{ color: statusColor() }"
      />
      <span class="overall-label">{{ statusLabel }}</span>
    </div>

    <div class="key-numbers">
      <div class="key-card">
        <span class="key-value">{{ activeConnections }}</span>
        <span class="key-label">活跃连接</span>
      </div>
      <div class="key-card">
        <span class="key-value">{{ healthySources }}/{{ totalSources }}</span>
        <span class="key-label">数据源</span>
      </div>
      <div class="key-card">
        <span class="key-value">{{ dashboardStore.sseStats?.events_pushed_24h ?? '--' }}</span>
        <span class="key-label">24h事件</span>
      </div>
    </div>

    <div
      class="status-light"
      :style="{ backgroundColor: statusColor() }"
    />
  </div>
</template>

<style scoped>
.health-panel {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.overall-status {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.overall-label {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.key-numbers {
  display: flex;
  gap: 16px;
  flex: 1;
}

.key-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 16px;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-md);
}

.key-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
}

.key-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 2px;
}

.status-light {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
}

@media (max-width: 767px) {
  .health-panel {
    flex-direction: column;
  }

  .key-numbers {
    flex-direction: column;
  }
}
</style>
