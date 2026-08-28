<script setup lang="ts">
import { useDashboardStore } from "@/stores/dashboard";
import { formatLargeNumber, formatPercent, formatUptime } from "@/utils/format";
import { computed } from "vue";

const dashboardStore = useDashboardStore();

const systemInfo = computed(() => dashboardStore.systemInfo);

const cpuPercent = computed(() => systemInfo.value?.cpu_usage_percent ?? 0);
const memoryPercent = computed(() => {
  if (!systemInfo.value) return 0;
  return (
    (systemInfo.value.memory_used_mb / systemInfo.value.memory_total_mb) * 100
  );
});
const diskPercent = computed(() => {
  if (!systemInfo.value) return 0;
  return (systemInfo.value.disk_used_gb / systemInfo.value.disk_total_gb) * 100;
});
// API request stats group (QPS / latency / error rates); absent on older
// backends, all-zero on fresh start or Redis downgrade.
const apiStats = computed(() => systemInfo.value?.api ?? null);
</script>

<template>
  <div class="system-status">
    <h3 class="status-title">系统状态</h3>

    <div class="status-item">
      <div class="status-label-row">
        <span class="status-label">CPU</span>
        <span class="status-value">{{ formatPercent(cpuPercent) }}</span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: cpuPercent + '%' }" />
      </div>
    </div>

    <div class="status-item">
      <div class="status-label-row">
        <span class="status-label">内存</span>
        <span class="status-value">{{ formatPercent(memoryPercent) }}</span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: memoryPercent + '%' }" />
      </div>
    </div>

    <div class="status-item">
      <div class="status-label-row">
        <span class="status-label">磁盘</span>
        <span class="status-value">{{ formatPercent(diskPercent) }}</span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: diskPercent + '%' }" />
      </div>
    </div>

    <div v-if="systemInfo" class="disk-io-row">
      <div class="network-item">
        <span class="network-label">磁盘读</span>
        <span class="network-value">{{
          systemInfo.disk_read_mbps ? `${systemInfo.disk_read_mbps} MB/s` : "--"
        }}</span>
      </div>
      <div class="network-item">
        <span class="network-label">磁盘写</span>
        <span class="network-value">{{
          systemInfo.disk_write_mbps
            ? `${systemInfo.disk_write_mbps} MB/s`
            : "--"
        }}</span>
      </div>
    </div>

    <div v-if="systemInfo" class="network-row">
      <div class="network-item">
        <span class="network-label">发送</span>
        <span class="network-value">{{
          systemInfo.network_out_kbps
            ? `${systemInfo.network_out_kbps} KB/s`
            : "--"
        }}</span>
      </div>
      <div class="network-item">
        <span class="network-label">接收</span>
        <span class="network-value">{{
          systemInfo.network_in_kbps
            ? `${systemInfo.network_in_kbps} KB/s`
            : "--"
        }}</span>
      </div>
    </div>

    <div v-if="apiStats" class="api-row">
      <div class="api-item">
        <span class="api-label">QPS</span>
        <span class="api-value">{{ apiStats.qps.toFixed(2) }}</span>
      </div>
      <div class="api-item">
        <span class="api-label">平均响应</span>
        <span class="api-value"
          >{{ apiStats.avg_response_ms.toFixed(1) }} ms</span
        >
      </div>
      <div class="api-item">
        <span class="api-label">4xx错误率</span>
        <span class="api-value"
          >{{ ((apiStats.error_rate_4xx || 0) * 100).toFixed(2) }}%</span
        >
      </div>
      <div class="api-item">
        <span class="api-label">5xx错误率</span>
        <span class="api-value"
          >{{ ((apiStats.error_rate_5xx || 0) * 100).toFixed(2) }}%</span
        >
      </div>
      <div class="api-item">
        <span class="api-label">累计请求</span>
        <span class="api-value">{{
          formatLargeNumber(apiStats.requests_total || 0)
        }}</span>
      </div>
    </div>

    <div v-if="systemInfo" class="system-meta">
      <div class="meta-item">
        <span class="meta-label">API版本</span>
        <span class="meta-value">{{
          systemInfo.api_version || systemInfo.version
        }}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">运行时长</span>
        <span class="meta-value">{{
          formatUptime(systemInfo.uptime_seconds)
        }}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">环境</span>
        <span class="meta-value">{{ systemInfo.environment }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.system-status {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.status-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.status-item {
  margin-bottom: 12px;
}

.status-label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.status-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.status-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.progress-bar {
  height: 6px;
  background-color: var(--bg-secondary);
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background-color: var(--accent);
  border-radius: 3px;
  transition: width var(--transition-normal);
}

.network-row {
  display: flex;
  gap: 16px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-light);
}

.disk-io-row {
  display: flex;
  gap: 16px;
  margin-bottom: 12px;
}

.network-item {
  display: flex;
  gap: 6px;
}

.network-label {
  font-size: 12px;
  color: var(--text-muted);
}

.network-value {
  font-size: 12px;
  color: var(--text-secondary);
}

.api-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-light);
}

.api-item {
  display: flex;
  gap: 6px;
}

.api-label {
  font-size: 12px;
  color: var(--text-muted);
}

.api-value {
  font-size: 12px;
  color: var(--text-secondary);
}

.system-meta {
  display: flex;
  gap: 16px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-light);
}

.meta-item {
  display: flex;
  gap: 6px;
}

.meta-label {
  font-size: 12px;
  color: var(--text-muted);
}

.meta-value {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
