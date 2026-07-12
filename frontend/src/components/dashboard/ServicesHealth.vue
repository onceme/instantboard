<script setup lang="ts">
import { useDashboardStore } from "@/stores/dashboard";
import { computed } from "vue";
import { CheckCircle2, AlertTriangle, XCircle } from "lucide-vue-next";

const dashboardStore = useDashboardStore();

const services = computed(() => dashboardStore.services);

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
</script>

<template>
  <div class="services-health">
    <h3 class="health-title">服务健康</h3>

    <div class="services-grid">
      <div
        v-for="service in services"
        :key="service.service"
        class="service-card card"
      >
        <div class="service-header">
          <span class="service-name">{{ service.service }}</span>
          <component
            :is="statusIcon(service.status)"
            :size="16"
            :style="{ color: statusColor(service.status) }"
          />
        </div>
        <div class="service-details">
          <div class="detail-item">
            <span class="detail-label">响应时间</span>
            <span class="detail-value">{{ service.response_time_ms }}ms</span>
          </div>
          <div v-if="service.connection_count" class="detail-item">
            <span class="detail-label">连接数</span>
            <span class="detail-value">{{ service.connection_count }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.services-health {
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

.services-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.service-card {
  padding: 12px;
}

.service-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.service-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.service-details {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-item {
  display: flex;
  justify-content: space-between;
}

.detail-label {
  font-size: 12px;
  color: var(--text-muted);
}

.detail-value {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
