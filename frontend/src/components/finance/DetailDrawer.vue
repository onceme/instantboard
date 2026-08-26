<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { X } from "lucide-vue-next";
import axios from "axios";
import { useFinanceStore } from "@/stores/finance";
import { getApiErrorMessage } from "@/utils/api";
import {
  formatCurrency,
  formatLargeNumber,
  formatPercent,
  getChangeClass,
} from "@/utils/format";
import type { QuoteHistoryPoint } from "@/types";

const props = defineProps<{
  visible: boolean;
  symbol: string;
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
}>();

const financeStore = useFinanceStore();

// SSE linkage: no dedicated subscription needed — the store's quote_update
// handler (updateQuoteFromSSE) merges pushes into quotesCache keyed by
// symbol, so binding to the cached row keeps the open drawer's price and
// change live while FinanceView's finance SSE channel is connected.
const quote = computed(
  () => financeStore.quotesCache.get(props.symbol) ?? null,
);

const inWatchlist = computed(() =>
  financeStore.watchlist.some((item) => item.symbol === props.symbol),
);

const loading = ref(false);
const loadError = ref<string | null>(null);

type WatchlistState = "idle" | "loading" | "added" | "failed";
const watchlistState = ref<WatchlistState>("idle");
const watchlistError = ref<string | null>(null);

// Disabled once the symbol is already on the list (local state or backend
// 409 from another session) or while the add request is in flight.
const watchlistDisabled = computed(
  () =>
    inWatchlist.value ||
    watchlistState.value === "added" ||
    watchlistState.value === "loading",
);

const watchlistButtonText = computed(() => {
  if (inWatchlist.value || watchlistState.value === "added") {
    return "已在自选中";
  }
  if (watchlistState.value === "loading") return "加入中…";
  return "加入自选";
});

const watchlistHint = computed(() => {
  if (watchlistState.value === "added") return "已加入自选";
  if (watchlistState.value === "failed") return watchlistError.value ?? "";
  return "";
});

const changeClass = computed(() => {
  const cls = getChangeClass(quote.value?.change_percent ?? 0);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
});

// Sparkline: pure SVG instead of ChartWrapper — ChartWrapper is the
// dashboard's Chart.js wrapper (full chart options/plugins); a dependency-free
// inline polyline is the least invasive fit for a tiny sparkline.
const SPARK_W = 320;
const SPARK_H = 72;
const SPARK_PAD = 6;

const historyPoints = computed<QuoteHistoryPoint[]>(() => {
  const history = quote.value?.history ?? [];
  return history.filter(
    (p) => typeof p.close === "number" && Number.isFinite(p.close),
  );
});

const hasSparkline = computed(() => historyPoints.value.length >= 2);

const sparklinePoints = computed(() => {
  if (!hasSparkline.value) return "";
  const pts = historyPoints.value;
  const closes = pts.map((p) => p.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const stepX = (SPARK_W - SPARK_PAD * 2) / (pts.length - 1);
  return pts
    .map((p, i) => {
      const x = SPARK_PAD + i * stepX;
      const y =
        SPARK_PAD + (1 - (p.close - min) / span) * (SPARK_H - SPARK_PAD * 2);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
});

// Up/down coloring follows the series trend itself (first vs last close),
// not the intraday change_percent, since the series spans 5 sessions.
const sparklineTrendClass = computed(() => {
  const pts = historyPoints.value;
  if (pts.length < 2) return "";
  const diff = pts[pts.length - 1].close - pts[0].close;
  if (diff > 0) return "spark-up";
  if (diff < 0) return "spark-down";
  return "spark-flat";
});

function priceOrDash(value?: number | null): string {
  return value == null
    ? "--"
    : formatCurrency(value, quote.value?.currency || "USD");
}

function close() {
  emit("update:visible", false);
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && props.visible) {
    close();
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));

const closeBtn = ref<HTMLButtonElement | null>(null);

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      nextTick(() => closeBtn.value?.focus());
    }
  },
  { immediate: true },
);

// Refresh on every (re)open, including a symbol switch while open; keep any
// cached quote on screen during the fetch so the drawer never flashes empty.
watch(
  () => [props.visible, props.symbol] as const,
  async ([visible, symbol]) => {
    if (!visible || !symbol) return;
    loadError.value = null;
    watchlistState.value = "idle";
    watchlistError.value = null;
    loading.value = !financeStore.quotesCache.has(symbol);
    try {
      await financeStore.getQuote(symbol);
    } catch (err) {
      loadError.value = getApiErrorMessage(err, "加载行情失败，请稍后重试。");
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);

async function addToWatchlist() {
  if (watchlistDisabled.value) return;
  watchlistState.value = "loading";
  watchlistError.value = null;
  try {
    await financeStore.addToWatchlist(props.symbol);
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
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="visible" class="drawer-overlay" @click.self="close">
        <aside
          class="drawer"
          role="dialog"
          aria-modal="true"
          :aria-label="`行情详情 ${symbol}`"
        >
          <div class="drawer-header">
            <div class="drawer-title">
              <span class="drawer-symbol">{{ symbol }}</span>
              <span v-if="quote?.name" class="drawer-name text-truncate">{{
                quote.name
              }}</span>
            </div>
            <button
              ref="closeBtn"
              type="button"
              class="drawer-close"
              aria-label="关闭"
              @click="close"
            >
              <X :size="18" />
            </button>
          </div>

          <div v-if="loading" class="drawer-status">加载行情中…</div>
          <div v-else-if="loadError" class="drawer-status drawer-status-error">
            {{ loadError }}
          </div>

          <template v-else-if="quote">
            <div class="drawer-price">
              <span class="current-price">{{
                formatCurrency(quote.current_price, quote.currency || "USD")
              }}</span>
              <span :class="changeClass" class="change-percent">{{
                formatPercent(quote.change_percent)
              }}</span>
            </div>

            <div class="sparkline-block">
              <div class="section-label">近 5 日走势</div>
              <svg
                v-if="hasSparkline"
                class="sparkline"
                :viewBox="`0 0 ${SPARK_W} ${SPARK_H}`"
                preserveAspectRatio="none"
                role="img"
                aria-label="近 5 日收盘价走势"
              >
                <polyline
                  class="sparkline-line"
                  :class="sparklineTrendClass"
                  :points="sparklinePoints"
                  fill="none"
                  stroke-width="2"
                  stroke-linejoin="round"
                  stroke-linecap="round"
                />
              </svg>
              <div v-else class="sparkline-empty">暂无近 5 日走势数据</div>
            </div>

            <div class="drawer-details">
              <div class="detail-row">
                <span class="detail-label">今开</span>
                <span class="detail-value">{{ priceOrDash(quote.open) }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">最高</span>
                <span class="detail-value">{{ priceOrDash(quote.high) }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">最低</span>
                <span class="detail-value">{{ priceOrDash(quote.low) }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">昨收</span>
                <span class="detail-value">{{
                  priceOrDash(quote.close_previous)
                }}</span>
              </div>
              <div v-if="quote.market_cap" class="detail-row">
                <span class="detail-label">市值</span>
                <span class="detail-value">{{
                  formatLargeNumber(quote.market_cap)
                }}</span>
              </div>
              <div v-if="quote.pe_ratio" class="detail-row">
                <span class="detail-label">市盈率</span>
                <span class="detail-value">{{
                  quote.pe_ratio.toFixed(2)
                }}</span>
              </div>
            </div>

            <div class="drawer-actions">
              <button
                type="button"
                class="btn-watchlist"
                :disabled="watchlistDisabled"
                @click="addToWatchlist"
              >
                {{ watchlistButtonText }}
              </button>
            </div>
            <p
              v-if="watchlistHint"
              class="watchlist-hint"
              :class="
                watchlistState === 'failed' ? 'hint-error' : 'hint-success'
              "
            >
              {{ watchlistHint }}
            </p>
          </template>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  justify-content: flex-end;
  background-color: rgba(15, 23, 42, 0.45);
}

.drawer {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 400px;
  max-width: 100vw;
  height: 100%;
  padding: 20px;
  overflow-y: auto;
  background-color: var(--bg-card);
  border-left: 1px solid var(--border-color);
  box-shadow: var(--shadow-lg);
}

/* Full-width drawer on small screens */
@media (max-width: 480px) {
  .drawer {
    width: 100vw;
    border-left: none;
  }
}

.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}

.drawer-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.drawer-symbol {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}

.drawer-name {
  font-size: 13px;
  color: var(--text-secondary);
  max-width: 300px;
}

.drawer-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.drawer-close:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.drawer-status {
  padding: 24px 0;
  text-align: center;
  font-size: 14px;
  color: var(--text-muted);
}

.drawer-status-error {
  color: var(--danger);
}

.drawer-price {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.current-price {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}

.change-percent {
  font-size: 15px;
  font-weight: 600;
}

.section-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.sparkline {
  width: 100%;
  height: 72px;
}

.spark-up {
  stroke: var(--up-color);
}

.spark-down {
  stroke: var(--down-color);
}

.spark-flat {
  stroke: var(--text-muted);
}

.sparkline-empty {
  padding: 18px 0;
  text-align: center;
  font-size: 13px;
  color: var(--text-muted);
  background-color: var(--bg-secondary);
  border-radius: var(--radius-md);
}

.drawer-details {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-md);
}

.detail-row {
  display: flex;
  justify-content: space-between;
}

.detail-label {
  font-size: 13px;
  color: var(--text-muted);
}

.detail-value {
  font-size: 13px;
  color: var(--text-secondary);
}

.drawer-actions {
  margin-top: auto;
}

.btn-watchlist {
  width: 100%;
  padding: 10px 16px;
  font-size: 14px;
  font-weight: 500;
  color: #fff;
  background-color: var(--accent);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.btn-watchlist:hover:not(:disabled) {
  background-color: var(--accent-hover);
}

.btn-watchlist:disabled {
  background-color: var(--bg-secondary);
  color: var(--text-muted);
  cursor: not-allowed;
  border: 1px solid var(--border-color);
}

.watchlist-hint {
  font-size: 13px;
  text-align: center;
}

.hint-success {
  color: var(--text-muted);
}

.hint-error {
  color: var(--danger);
}

.drawer-enter-active,
.drawer-leave-active {
  transition: opacity var(--transition-normal);
}

.drawer-enter-active .drawer,
.drawer-leave-active .drawer {
  transition: transform var(--transition-normal);
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-from .drawer,
.drawer-leave-to .drawer {
  transform: translateX(100%);
}
</style>
