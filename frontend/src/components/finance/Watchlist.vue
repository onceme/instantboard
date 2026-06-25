<script setup lang="ts">
import { useFinanceStore } from '@/stores/finance'
import { formatCurrency, formatPercent, getChangeClass } from '@/utils/format'
import { computed } from 'vue'
import { X, Star } from 'lucide-vue-next'
import EmptyState from '@/components/common/EmptyState.vue'

const financeStore = useFinanceStore()

const sortedWatchlist = computed(() => {
  return [...financeStore.watchlist]
    .sort((a, b) => a.display_order - b.display_order)
    .map((item, index) => ({
      ...item,
      quote: financeStore.watchlistQuotes.get(item.symbol),
      order: index + 1,
    }))
})

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent)
  if (cls === 'up') return 'change-up'
  if (cls === 'down') return 'change-down'
  return 'change-neutral'
}

async function removeItem(itemId: string) {
  await financeStore.removeFromWatchlist(itemId)
}
</script>

<template>
  <div class="watchlist">
    <div class="watchlist-header">
      <h2 class="watchlist-title">
        我的自选
      </h2>
      <Star
        :size="16"
        class="header-icon"
      />
    </div>

    <EmptyState
      v-if="sortedWatchlist.length === 0"
      title="暂无自选"
      description="搜索并添加自选"
      icon="star"
    />

    <div
      v-else
      class="watchlist-list"
    >
      <div
        v-for="item in sortedWatchlist"
        :key="item.id"
        class="watchlist-item"
      >
        <span class="item-order">{{ item.order }}</span>
        <div class="item-symbol-name">
          <span class="item-symbol">{{ item.symbol }}</span>
          <span class="item-name text-truncate">{{ item.name || item.symbol }}</span>
        </div>
        <div
          v-if="item.quote"
          class="item-price"
        >
          {{ formatCurrency(item.quote.current_price) }}
        </div>
        <div
          v-if="item.quote"
          class="item-change"
        >
          <span :class="changeClass(item.quote.change_percent)">
            {{ formatCurrency(item.quote.change) }}
          </span>
          <span :class="changeClass(item.quote.change_percent)">
            {{ formatPercent(item.quote.change_percent) }}
          </span>
        </div>
        <div
          v-if="!item.quote"
          class="item-price"
        >
          --
        </div>
        <div
          v-if="!item.quote"
          class="item-change"
        >
          --
        </div>
        <button
          class="remove-btn"
          title="移除"
          @click="removeItem(item.id)"
        >
          <X :size="14" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.watchlist {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.watchlist-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.watchlist-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.header-icon {
  color: var(--accent);
}

.watchlist-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.watchlist-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-light);
}

.watchlist-item:last-child {
  border-bottom: none;
}

.item-order {
  font-size: 12px;
  color: var(--text-muted);
  min-width: 20px;
  text-align: center;
}

.item-symbol-name {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.item-symbol {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.item-name {
  font-size: 13px;
  color: var(--text-secondary);
  max-width: 150px;
}

.item-price {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  text-align: right;
  min-width: 80px;
}

.item-change {
  display: flex;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  text-align: right;
  min-width: 120px;
}

.remove-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: all var(--transition-fast);
}

.remove-btn:hover {
  color: var(--danger);
  background-color: rgba(239, 68, 68, 0.1);
}
</style>
