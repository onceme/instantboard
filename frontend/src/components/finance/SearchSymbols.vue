<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { formatCurrency, formatPercent, getChangeClass } from "@/utils/format";
import { getApiErrorCode, getApiErrorMessage } from "@/utils/api";
import { reactive, ref, computed } from "vue";
import axios from "axios";
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

// The suggestion panel stays mounted for the whole lifetime of a query:
// loading and empty-match states render inside it instead of unmounting it,
// so a slow or superseded response can never make the list "jump away"
// (the store's sequence guard drops stale payloads).
const panelVisible = computed(
  () => searchQuery.value.trim().length > 0 && !selectedSymbol.value,
);

// Quick-watch (加入自选) per candidate row — no detour through the detail
// drawer required. State is keyed by candidate symbol; funds register at
// selection time through the existing add_to_watchlist registry fallback.
const watchPending = reactive(new Set<string>());
const watchedSession = reactive(new Set<string>());
const watchNotes = reactive<Record<string, string>>({});

function normalizeFundCode(symbol: string): string {
  return symbol.replace(/\.(SS|SZ|OF)$/i, "");
}

function isWatched(symbol: string): boolean {
  const code = normalizeFundCode(symbol);
  if (
    financeStore.watchlist.some(
      (item) => normalizeFundCode(item.symbol) === code,
    )
  ) {
    return true;
  }
  // 409s from another session / spelling variants are not in the local list
  // yet still mean "already followed".
  return watchedSession.has(symbol);
}

function watchLabel(symbol: string): string {
  if (watchPending.has(symbol)) return "关注中…";
  if (isWatched(symbol)) return "✓已关注";
  return "＋关注";
}

function setWatchNote(symbol: string, text: string) {
  watchNotes[symbol] = text;
  setTimeout(() => {
    if (watchNotes[symbol] === text) {
      delete watchNotes[symbol];
    }
  }, 2600);
}

async function quickWatch(result: SearchResult) {
  if (isWatched(result.symbol) || watchPending.has(result.symbol)) return;
  watchPending.add(result.symbol);
  try {
    await financeStore.addToWatchlist(result.symbol);
    watchedSession.add(result.symbol);
  } catch (err) {
    if (
      getApiErrorCode(err) === "DUPLICATE_WATCHLIST_ITEM" ||
      (axios.isAxiosError(err) && err.response?.status === 409)
    ) {
      watchedSession.add(result.symbol);
      setWatchNote(result.symbol, "已在自选");
    } else {
      setWatchNote(
        result.symbol,
        getApiErrorMessage(err, "加入自选失败，请稍后重试"),
      );
    }
  } finally {
    watchPending.delete(result.symbol);
  }
}

// UX choice: selecting a result opens DetailDrawer as the primary detail
// experience (sparkline + watchlist action, finance-tab.md §3.1). The inline
// QuoteCard below is kept as a lightweight quick preview so the panel is not
// empty after the drawer closes.
function selectResult(result: SearchResult) {
  selectedSymbol.value = result.symbol;
  // OTC fund candidates have no realtime quote source; the drawer renders its
  // no-quote fallback (watchlist action stays reachable) when the fetch fails.
  financeStore.getQuote(result.symbol).catch(() => {});
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

    <div v-if="panelVisible" class="search-results">
      <div v-if="financeStore.searchLoading" class="search-status">搜索中…</div>
      <div
        v-else-if="financeStore.searchResults.length === 0"
        class="search-status"
      >
        无匹配结果
      </div>
      <template v-else>
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
                result.change_percent
                  ? formatPercent(result.change_percent)
                  : ""
              }}
            </span>
          </div>
          <div class="result-actions">
            <span v-if="watchNotes[result.symbol]" class="watch-note">
              {{ watchNotes[result.symbol] }}
            </span>
            <button
              class="watch-quick-btn"
              :class="{ 'watch-added': isWatched(result.symbol) }"
              :title="isWatched(result.symbol) ? '已在自选' : '加入自选'"
              :disabled="
                isWatched(result.symbol) || watchPending.has(result.symbol)
              "
              @click.stop="quickWatch(result)"
            >
              {{ watchLabel(result.symbol) }}
            </button>
          </div>
        </div>
      </template>
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

/* Loading / empty-match states render inside the panel (it never unmounts
   mid-query), so late responses cannot collapse the suggestion list. */
.search-status {
  padding: 16px 12px;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;
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

.result-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.watch-quick-btn {
  flex-shrink: 0;
  font-size: 12px;
  padding: 3px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  background-color: var(--bg-secondary);
  color: var(--text-secondary);
  white-space: nowrap;
  transition: all var(--transition-fast);
}

.watch-quick-btn:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent);
  background-color: color-mix(in srgb, var(--accent) 8%, transparent);
}

.watch-quick-btn:disabled {
  cursor: default;
}

.watch-quick-btn.watch-added {
  color: var(--success, var(--down-color));
  border-color: color-mix(
    in srgb,
    var(--success, var(--down-color)) 45%,
    transparent
  );
  background-color: color-mix(
    in srgb,
    var(--success, var(--down-color)) 10%,
    transparent
  );
}

.watch-note {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
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
