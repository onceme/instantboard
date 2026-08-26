<script setup lang="ts">
import { computed } from "vue";
import { useDashboardStore } from "@/stores/dashboard";
import { useAuthStore } from "@/stores/auth";
import type { BusinessCategoryCount } from "@/types";

const dashboardStore = useDashboardStore();
const authStore = useAuthStore();

const metrics = computed(() => dashboardStore.businessMetrics);

// Admin-only data (GET /dashboard/business-metrics); the panel is hidden for
// non-admin sessions so a 403 never renders a broken card.
const isAdmin = computed(() => authStore.isAdmin);

const distribution = computed<BusinessCategoryCount[]>(
  () => metrics.value?.category_distribution ?? [],
);

// Largest bucket count; distribution bars are scaled relative to it. Guarded so
// an empty list yields a 0 denominator-safe width.
const maxCategoryCount = computed(() =>
  distribution.value.reduce((max, row) => Math.max(max, row.count), 0),
);

function barWidth(count: number): string {
  if (maxCategoryCount.value <= 0) return "0%";
  return `${Math.round((count / maxCategoryCount.value) * 100)}%`;
}
</script>

<template>
  <div v-if="isAdmin" class="business-metrics">
    <h3 class="metrics-title">业务指标</h3>

    <div class="stats-grid">
      <div class="stat-card">
        <span class="stat-number">{{ metrics?.active_users_24h ?? "--" }}</span>
        <span class="stat-label">活跃用户(24h)</span>
      </div>

      <div class="stat-card">
        <span class="stat-number">{{ metrics?.items_today ?? "--" }}</span>
        <span class="stat-label">今日新增条目</span>
      </div>

      <div class="stat-card">
        <span class="stat-number">{{ metrics?.watchlist_total ?? "--" }}</span>
        <span class="stat-label">自选列表总条目</span>
      </div>

      <div class="stat-card">
        <span class="stat-number">{{ metrics?.events_pushed_1h ?? "--" }}</span>
        <span class="stat-label">SSE推送事件(1h)</span>
      </div>
    </div>

    <div class="distribution-section">
      <h4 class="distribution-title">各分类数据量分布</h4>
      <div v-if="distribution.length" class="distribution-list">
        <div
          v-for="row in distribution"
          :key="row.category_name"
          class="distribution-row"
        >
          <span class="distribution-name" :title="row.category_name">{{
            row.category_name
          }}</span>
          <div class="distribution-track">
            <div
              class="distribution-bar"
              :style="{ width: barWidth(row.count) }"
            />
          </div>
          <span class="distribution-count">{{ row.count }}</span>
        </div>
      </div>
      <div v-else class="distribution-empty">暂无分类数据</div>
    </div>
  </div>
</template>

<style scoped>
.business-metrics {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.metrics-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.stats-grid {
  display: grid;
  /* minmax(0, 1fr): prevents wide numbers/labels from widening tracks
     beyond the card and causing page-level horizontal overflow */
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-md);
}

.stat-number {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
}

.stat-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 2px;
  text-align: center;
}

.distribution-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.distribution-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.distribution-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.distribution-name {
  flex: 0 0 90px;
  font-size: 12px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.distribution-track {
  flex: 1;
  min-width: 0;
  height: 8px;
  border-radius: var(--radius-md);
  background-color: var(--bg-secondary);
  overflow: hidden;
}

.distribution-bar {
  height: 100%;
  border-radius: var(--radius-md);
  background-color: var(--accent);
}

.distribution-count {
  flex: 0 0 auto;
  min-width: 32px;
  text-align: right;
  font-size: 12px;
  color: var(--text-muted);
}

.distribution-empty {
  font-size: 12px;
  color: var(--text-muted);
  padding: 8px 0;
}

@media (max-width: 767px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
