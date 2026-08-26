<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import { useDashboardStore } from "@/stores/dashboard";
import HealthPanel from "@/components/dashboard/HealthPanel.vue";
import SystemStatus from "@/components/dashboard/SystemStatus.vue";
import ServicesHealth from "@/components/dashboard/ServicesHealth.vue";
import DataSourcesHealth from "@/components/dashboard/DataSourcesHealth.vue";
import SchedulerPanel from "@/components/dashboard/SchedulerPanel.vue";
import SSEStats from "@/components/dashboard/SSEStats.vue";

const dashboardStore = useDashboardStore();

onMounted(() => {
  dashboardStore.init();
});

onUnmounted(() => {
  dashboardStore.cleanup();
});
</script>

<template>
  <div class="dashboard-view">
    <HealthPanel />

    <div class="dashboard-grid">
      <div class="grid-left">
        <SystemStatus />
        <DataSourcesHealth />
        <SchedulerPanel />
      </div>
      <div class="grid-right">
        <ServicesHealth />
        <SSEStats />
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dashboard-grid {
  display: flex;
  gap: 16px;
}

.grid-left {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.grid-right {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

@media (max-width: 767px) {
  .dashboard-grid {
    flex-direction: column;
  }
}
</style>
