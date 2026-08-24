<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { formatNumber, formatPercent, getChangeClass } from "@/utils/format";
import { computed, ref } from "vue";
import { MARKET_REGION_GROUPS } from "@/types";
import { RefreshCw } from "lucide-vue-next";
import ErrorAlert from "@/components/common/ErrorAlert.vue";

const financeStore = useFinanceStore();
const refreshing = ref(false);

const groupedIndices = computed(() => {
  const groups: Record<
    string,
    Array<{
      symbol: string;
      name: string;
      value: number;
      change_percent: number;
      market_status: string;
      region: string;
    }>
  > = {};

  for (const [key, config] of Object.entries(MARKET_REGION_GROUPS)) {
    groups[key] = financeStore.marketIndices.filter((i) =>
      config.regions.includes(i.region),
    );
  }

  return groups;
});

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
}

function marketStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    open: "开盘",
    closed: "休市",
    pre_market: "盘前",
    post_market: "盘后",
  };
  return labels[status] || status;
}

async function refresh() {
  refreshing.value = true;
  try {
    await financeStore.getMarketIndices();
  } finally {
    refreshing.value = false;
  }
}
</script>

<template>
  <div class="market-indices">
    <div class="indices-header">
      <h2 class="indices-title">市场指数</h2>
      <button class="refresh-btn" :disabled="refreshing" @click="refresh">
        <RefreshCw :size="16" :class="{ spinning: refreshing }" />
      </button>
    </div>

    <ErrorAlert
      v-if="financeStore.marketIndicesError"
      :message="financeStore.marketIndicesError"
      retryable
      :retrying="refreshing"
      @retry="refresh"
    />

    <template v-if="!financeStore.marketIndicesError">
      <div
        v-for="[groupKey, group] in Object.entries(groupedIndices)"
        :key="groupKey"
        class="region-group"
      >
        <h3 class="region-label">
          {{ MARKET_REGION_GROUPS[groupKey]?.label || groupKey }}
        </h3>
        <div class="indices-list">
          <div v-for="index in group" :key="index.symbol" class="index-row">
            <div class="index-info">
              <span class="index-name">{{ index.name }}</span>
              <span class="index-status">{{
                marketStatusLabel(index.market_status)
              }}</span>
            </div>
            <div class="index-data">
              <span class="index-value">{{
                formatNumber(index.value, 2)
              }}</span>
              <span
                :class="changeClass(index.change_percent)"
                class="index-change"
              >
                {{ formatPercent(index.change_percent) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.market-indices {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.indices-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.indices-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.refresh-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  transition: background-color var(--transition-fast);
}

.refresh-btn:hover {
  background-color: var(--bg-hover);
}

.refresh-btn:disabled {
  opacity: 0.5;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.region-group {
  margin-bottom: 16px;
}

.region-group:last-child {
  margin-bottom: 0;
}

.region-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border-light);
}

.indices-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.index-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
}

.index-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.index-name {
  font-size: 14px;
  color: var(--text-primary);
}

.index-status {
  font-size: 11px;
  color: var(--text-muted);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background-color: var(--bg-secondary);
}

.index-data {
  display: flex;
  align-items: center;
  gap: 8px;
}

.index-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.index-change {
  font-size: 13px;
  font-weight: 500;
}
</style>
