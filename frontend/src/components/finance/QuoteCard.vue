<script setup lang="ts">
import type { FinanceQuote } from "@/types";
import {
  formatCurrency,
  formatPercent,
  formatVolume,
  getChangeClass,
} from "@/utils/format";
import { computed, ref, watch } from "vue";
import { Loader2, Star } from "lucide-vue-next";
import axios from "axios";
import { useFinanceStore } from "@/stores/finance";
import { getApiErrorMessage } from "@/utils/api";

const props = defineProps<{
  quote: FinanceQuote;
  showSparkline?: boolean;
}>();

const financeStore = useFinanceStore();

const changeColorClass = computed(() => {
  const cls = getChangeClass(props.quote.change_percent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
});

// Watchlist action mirrors DetailDrawer's contract (finance-tab.md §3.2):
// POST {symbol} text, backend resolves against finance_symbols; a 409 from
// another session is normalized to the same "already in watchlist" state.
type WatchlistState = "idle" | "loading" | "added" | "failed";
const watchlistState = ref<WatchlistState>("idle");
const watchlistError = ref<string | null>(null);

const inWatchlist = computed(() =>
  financeStore.watchlist.some((item) => item.symbol === props.quote.symbol),
);

const watchlistDisabled = computed(
  () =>
    inWatchlist.value ||
    watchlistState.value === "added" ||
    watchlistState.value === "loading",
);

const watchlistAdded = computed(
  () => inWatchlist.value || watchlistState.value === "added",
);

const watchlistAriaLabel = computed(() => {
  if (watchlistAdded.value) return "已在自选中";
  if (watchlistState.value === "loading") return "加入中…";
  return "加入自选";
});

const watchlistHint = computed(() => {
  if (watchlistState.value === "added") return "已加入自选";
  if (watchlistState.value === "failed") return watchlistError.value ?? "";
  return "";
});

// The card instance is reused across search selections (same component,
// new quote prop), so per-symbol action state must reset on symbol switch.
watch(
  () => props.quote.symbol,
  () => {
    watchlistState.value = "idle";
    watchlistError.value = null;
  },
);

async function addToWatchlist() {
  if (watchlistDisabled.value) return;
  watchlistState.value = "loading";
  watchlistError.value = null;
  try {
    await financeStore.addToWatchlist(props.quote.symbol);
    watchlistState.value = "added";
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 409) {
      watchlistState.value = "added";
    } else {
      watchlistState.value = "failed";
      watchlistError.value = getApiErrorMessage(
        err,
        "加入自选失败，请稍后重试。",
      );
    }
  }
}
</script>

<template>
  <div class="quote-card card">
    <div class="quote-header">
      <div class="symbol-name">
        <span class="symbol">{{ quote.symbol }}</span>
        <span class="name text-truncate">{{ quote.name }}</span>
      </div>
      <div class="header-actions">
        <div v-if="quote.type" class="quote-type">
          {{ quote.type }}
        </div>
        <button
          type="button"
          class="watchlist-btn"
          :class="{ 'watchlist-added': watchlistAdded }"
          :disabled="watchlistDisabled"
          :title="watchlistAriaLabel"
          :aria-label="watchlistAriaLabel"
          @click="addToWatchlist"
        >
          <Loader2
            v-if="watchlistState === 'loading'"
            :size="14"
            class="spinning"
          />
          <Star
            v-else
            :size="14"
            :fill="watchlistAdded ? 'currentColor' : 'none'"
          />
        </button>
      </div>
    </div>

    <p
      v-if="watchlistHint"
      class="watchlist-hint"
      :class="watchlistState === 'failed' ? 'hint-error' : 'hint-success'"
    >
      {{ watchlistHint }}
    </p>

    <div class="quote-price">
      <span class="current-price">{{
        formatCurrency(quote.current_price, quote.currency || "USD")
      }}</span>
      <span :class="changeColorClass" class="change-value">
        {{ formatCurrency(quote.change, quote.currency || "USD") }}
      </span>
      <span :class="changeColorClass" class="change-percent">
        {{ formatPercent(quote.change_percent) }}
      </span>
    </div>

    <div class="quote-details">
      <div class="detail-row">
        <div class="detail-item">
          <span class="detail-label">开盘</span>
          <span class="detail-value">{{
            quote.open
              ? formatCurrency(quote.open, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">最高</span>
          <span class="detail-value">{{
            quote.high
              ? formatCurrency(quote.high, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
      </div>
      <div class="detail-row">
        <div class="detail-item">
          <span class="detail-label">最低</span>
          <span class="detail-value">{{
            quote.low
              ? formatCurrency(quote.low, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">昨收</span>
          <span class="detail-value">{{
            quote.close_previous
              ? formatCurrency(quote.close_previous, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
      </div>
    </div>

    <div v-if="quote.volume" class="quote-volume">
      <span class="volume-label">成交量</span>
      <span class="volume-value">{{ formatVolume(quote.volume) }}</span>
    </div>
  </div>
</template>

<style scoped>
.quote-card {
  padding: 16px;
}

.quote-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.symbol-name {
  display: flex;
  align-items: center;
  gap: 8px;
}

.symbol {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
}

.name {
  font-size: 14px;
  color: var(--text-secondary);
  max-width: 200px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.quote-type {
  font-size: 12px;
  color: var(--text-muted);
  padding: 2px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

/* Same 24px icon-button convention as Watchlist row actions (alert-btn etc.) */
.watchlist-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.watchlist-btn:hover:not(:disabled) {
  color: var(--accent);
  background-color: var(--bg-hover);
}

.watchlist-btn.watchlist-added {
  color: var(--accent);
  cursor: not-allowed;
}

.watchlist-btn:disabled:not(.watchlist-added) {
  cursor: wait;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.watchlist-hint {
  margin-top: -6px;
  margin-bottom: 8px;
  font-size: 12px;
}

.hint-success {
  color: var(--text-muted);
}

.hint-error {
  color: var(--danger);
}

.quote-price {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.current-price {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
}

.change-value {
  font-size: 14px;
  font-weight: 500;
}

.change-percent {
  font-size: 14px;
  font-weight: 600;
}

.quote-details {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}

.detail-row {
  display: flex;
  gap: 24px;
}

.detail-item {
  display: flex;
  gap: 4px;
}

.detail-label {
  font-size: 12px;
  color: var(--text-muted);
}

.detail-value {
  font-size: 12px;
  color: var(--text-secondary);
}

.quote-volume {
  display: flex;
  gap: 4px;
}

.volume-label {
  font-size: 12px;
  color: var(--text-muted);
}

.volume-value {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
