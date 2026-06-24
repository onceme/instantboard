<script setup lang="ts">
import { useFinanceStore } from '@/stores/finance'
import { formatCurrency, formatPercent, getChangeClass } from '@/utils/format'
import { computed, ref } from 'vue'
import { COMMODITY_GROUPS } from '@/types'
import { RefreshCw } from 'lucide-vue-next'

const financeStore = useFinanceStore()
const refreshing = ref(false)

const groupedCommodities = computed(() => {
  const groups: Record<string, { label: string; items: typeof financeStore.commodities }> = {}

  for (const [key, config] of Object.entries(COMMODITY_GROUPS)) {
    groups[key] = {
      label: config.label,
      items: financeStore.commodities.filter((c) => c.category === config.category),
    }
  }

  return groups
})

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent)
  if (cls === 'up') return 'change-up'
  if (cls === 'down') return 'change-down'
  return 'change-neutral'
}

async function refresh() {
  refreshing.value = true
  try {
    await financeStore.getCommodities()
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <div class="commodities">
    <div class="commodities-header">
      <h2 class="commodities-title">大宗商品</h2>
      <button class="refresh-btn" @click="refresh" :disabled="refreshing">
        <RefreshCw :size="16" :class="{ spinning: refreshing }" />
      </button>
    </div>

    <div v-for="[groupKey, group] in Object.entries(groupedCommodities)" :key="groupKey" class="commodity-group">
      <h3 class="group-label">{{ group.label }}</h3>
      <div class="commodity-list">
        <div v-for="item in group.items" :key="item.symbol" class="commodity-row">
          <div class="commodity-info">
            <span class="commodity-name">{{ item.name }}</span>
            <span class="commodity-unit">{{ item.unit }}</span>
          </div>
          <div class="commodity-data">
            <span class="commodity-price">{{ formatCurrency(item.value) }}</span>
            <span :class="changeClass(item.change_percent)" class="commodity-change">
              {{ formatPercent(item.change_percent) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.commodities {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.commodities-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.commodities-title {
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
  to { transform: rotate(360deg); }
}

.commodity-group {
  margin-bottom: 16px;
}

.commodity-group:last-child {
  margin-bottom: 0;
}

.group-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border-light);
}

.commodity-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.commodity-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
}

.commodity-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.commodity-name {
  font-size: 14px;
  color: var(--text-primary);
}

.commodity-unit {
  font-size: 11px;
  color: var(--text-muted);
}

.commodity-data {
  display: flex;
  align-items: center;
  gap: 8px;
}

.commodity-price {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.commodity-change {
  font-size: 13px;
  font-weight: 500;
}
</style>
