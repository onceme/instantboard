<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { formatCurrency, formatPercent, getChangeClass } from "@/utils/format";
import { computed } from "vue";

const financeStore = useFinanceStore();

const items = computed(() => financeStore.watchlistTop5);

function goToWatchlist() {
  financeStore.setCurrentPanel("watchlist");
}

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
}
</script>

<template>
  <!-- Empty watchlist renders nothing: the Overview keeps only the index
       grid and the news panel (finance-tab.md §3.6.2) -->
  <div v-if="items.length > 0" class="overview-watchlist">
    <div class="summary-header">
      <h3 class="summary-title">我的自选</h3>
      <button class="view-all-link" type="button" @click="goToWatchlist">
        查看全部
      </button>
    </div>

    <div class="summary-list">
      <div v-for="item in items" :key="item.id" class="summary-item">
        <div class="item-symbol" :title="item.name || item.symbol">
          {{ item.symbol }}
        </div>
        <div class="item-price">
          {{ item.quote ? formatCurrency(item.quote.current_price) : "--" }}
        </div>
        <div
          class="item-change"
          :class="item.quote ? changeClass(item.quote.change_percent) : ''"
        >
          {{ item.quote ? formatPercent(item.quote.change_percent) : "--" }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overview-watchlist {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.summary-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.summary-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.view-all-link {
  padding: 2px 4px;
  font-size: 13px;
  color: var(--accent);
  background: none;
  border: none;
  cursor: pointer;
  transition: opacity var(--transition-fast);
}

.view-all-link:hover {
  opacity: 0.8;
}

.summary-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
}

.summary-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
}

.item-symbol {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.item-price {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  text-align: right;
}

.item-change {
  min-width: 60px;
  font-size: 12px;
  font-weight: 500;
  text-align: right;
}
</style>
