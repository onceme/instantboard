<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { formatCurrency, formatPercent, getChangeClass } from "@/utils/format";
import { ref, computed } from "vue";
import type { SearchResult } from "@/types";
import { Search } from "lucide-vue-next";
import QuoteCard from "./QuoteCard.vue";
import DetailDrawer from "./DetailDrawer.vue";

const financeStore = useFinanceStore();
const searchQuery = ref("");
const selectedSymbol = ref<string>("");
const selectedQuote = computed(() => {
  if (!selectedSymbol.value) return null;
  return financeStore.quotesCache.get(selectedSymbol.value) || null;
});

// Drawer state lives in this component (its only opener) rather than the
// store: the drawer is local to the Search panel experience.
const drawerSymbol = ref("");
const drawerVisible = ref(false);

let timer: ReturnType<typeof setTimeout> | null = null;

function onInput() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    financeStore.searchSymbols(searchQuery.value);
  }, 300);
}

// UX choice: selecting a result opens DetailDrawer as the primary detail
// experience (sparkline + watchlist action, finance-tab.md §3.1). The inline
// QuoteCard below is kept as a lightweight quick preview so the panel is not
// empty after the drawer closes.
function selectResult(result: SearchResult) {
  selectedSymbol.value = result.symbol;
  financeStore.getQuote(result.symbol);
  drawerSymbol.value = result.symbol;
  drawerVisible.value = true;
  searchQuery.value = "";
  financeStore.searchSymbols("");
}

function changeClass(changePercent?: number): string {
  if (!changePercent) return "";
  const cls = getChangeClass(changePercent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
}
</script>

<template>
  <div class="search-symbols">
    <div class="search-box">
      <Search :size="16" class="search-icon" />
      <input
        v-model="searchQuery"
        type="text"
        placeholder="搜索股票、基金、指数..."
        class="search-input"
        @input="onInput"
      />
    </div>

    <div
      v-if="financeStore.searchResults.length > 0 && !selectedSymbol"
      class="search-results"
    >
      <div
        v-for="result in financeStore.searchResults"
        :key="result.symbol"
        class="result-item"
        @click="selectResult(result)"
      >
        <div class="result-main">
          <span class="result-symbol">{{ result.symbol }}</span>
          <span class="result-name">{{ result.name }}</span>
        </div>
        <div class="result-meta">
          <span class="result-exchange">{{ result.exchange }}</span>
          <span class="result-type">{{ result.type }}</span>
        </div>
        <div v-if="result.current_price" class="result-price">
          <span class="price-value">{{
            formatCurrency(result.current_price)
          }}</span>
          <span
            :class="changeClass(result.change_percent)"
            class="price-change"
          >
            {{
              result.change_percent ? formatPercent(result.change_percent) : ""
            }}
          </span>
        </div>
      </div>
    </div>

    <div v-if="selectedQuote" class="selected-quote">
      <QuoteCard :quote="selectedQuote" />
    </div>

    <div v-if="!searchQuery.trim() && !selectedSymbol" class="search-empty">
      输入关键词搜索股票、基金或指数
    </div>

    <DetailDrawer v-model:visible="drawerVisible" :symbol="drawerSymbol" />
  </div>
</template>

<style scoped>
.search-symbols {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--bg-secondary);
  margin-bottom: 12px;
}

.search-icon {
  color: var(--text-muted);
}

.search-input {
  border: none;
  background: none;
  padding: 0;
  flex: 1;
  font-size: 14px;
}

.search-input:focus {
  border: none;
  box-shadow: none;
}

.search-results {
  max-height: 400px;
  overflow-y: auto;
}

.result-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--border-light);
  transition: background-color var(--transition-fast);
}

.result-item:hover {
  background-color: var(--bg-hover);
}

.result-item:last-child {
  border-bottom: none;
}

.result-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.result-symbol {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
}

.result-name {
  font-size: 13px;
  color: var(--text-secondary);
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 6px;
}

.result-exchange {
  font-size: 12px;
  color: var(--text-muted);
}

.result-type {
  font-size: 12px;
  color: var(--text-muted);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background-color: var(--bg-secondary);
}

.result-price {
  display: flex;
  align-items: center;
  gap: 6px;
}

.price-value {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.price-change {
  font-size: 12px;
  font-weight: 500;
}

.selected-quote {
  margin-top: 8px;
}

.search-empty {
  text-align: center;
  padding: 24px;
  color: var(--text-muted);
  font-size: 14px;
}
</style>
