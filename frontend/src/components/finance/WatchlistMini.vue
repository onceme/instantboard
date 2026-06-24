<script setup lang="ts">
import { useFinanceStore } from '@/stores/finance'
import { formatCurrency, formatPercent, getChangeClass } from '@/utils/format'
import { computed } from 'vue'

const financeStore = useFinanceStore()

const items = computed(() => financeStore.watchlistTop5)

function goToWatchlist() {
  financeStore.setCurrentPanel('watchlist')
}

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent)
  if (cls === 'up') return 'change-up'
  if (cls === 'down') return 'change-down'
  return 'change-neutral'
}
</script>

<template>
  <div class="watchlist-mini">
    <div class="mini-header">
      <h3 class="mini-title">自选列表</h3>
    </div>

    <div v-if="items.length === 0" class="mini-empty">
      <p class="empty-text">添加自选</p>
    </div>

    <div v-else class="mini-list">
      <div v-for="item in items" :key="item.id" class="mini-item">
        <div class="item-symbol">{{ item.symbol }}</div>
        <div class="item-name text-truncate">{{ item.name || item.symbol }}</div>
        <div class="item-price">
          {{ item.quote ? formatCurrency(item.quote.current_price) : '--' }}
        </div>
        <div class="item-change" :class="item.quote ? changeClass(item.quote.change_percent) : ''">
          {{ item.quote ? formatPercent(item.quote.change_percent) : '--' }}
        </div>
      </div>
    </div>

    <button class="view-all-btn" @click="goToWatchlist">
      查看全部
    </button>
  </div>
</template>

<style scoped>
.watchlist-mini {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 12px;
}

.mini-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.mini-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.mini-empty {
  padding: 16px;
  text-align: center;
}

.empty-text {
  color: var(--text-muted);
  font-size: 13px;
}

.mini-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.mini-item {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  gap: 8px;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--border-light);
}

.mini-item:last-child {
  border-bottom: none;
}

.item-symbol {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.item-name {
  font-size: 12px;
  color: var(--text-secondary);
  max-width: 100px;
}

.item-price {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  text-align: right;
}

.item-change {
  font-size: 12px;
  font-weight: 500;
  text-align: right;
}

.view-all-btn {
  margin-top: 8px;
  width: 100%;
  padding: 6px;
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.view-all-btn:hover {
  background-color: var(--accent);
  color: white;
}
</style>
