<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { formatCurrency, formatPercent, getChangeClass } from "@/utils/format";
import { getApiErrorMessage } from "@/utils/api";
import { computed, ref } from "vue";
import { Bell, Check, Star, X } from "lucide-vue-next";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorAlert from "@/components/common/ErrorAlert.vue";
import type { WatchlistItem } from "@/types";

const financeStore = useFinanceStore();

// Inline alert-threshold editor state (one editor open at a time).
// Mirror the backend 0.5-50 range client-side for immediate feedback; the
// PATCH endpoint stays authoritative (400 VALIDATION_ERROR out of range).
const THRESHOLD_MIN = 0.5;
const THRESHOLD_MAX = 50;
const editingId = ref<string | null>(null);
const editValue = ref("");
const saving = ref(false);
const saveError = ref<string | null>(null);
const successId = ref<string | null>(null);

const sortedWatchlist = computed(() => {
  return [...financeStore.watchlist]
    .sort((a, b) => a.display_order - b.display_order)
    .map((item, index) => ({
      ...item,
      quote: financeStore.watchlistQuotes.get(item.symbol),
      order: index + 1,
    }));
});

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
}

async function removeItem(itemId: string) {
  await financeStore.removeFromWatchlist(itemId);
}

function openEditor(item: WatchlistItem) {
  editingId.value = item.id;
  editValue.value =
    item.alert_threshold_percent != null
      ? String(item.alert_threshold_percent)
      : "";
  saveError.value = null;
}

function cancelEdit() {
  editingId.value = null;
  editValue.value = "";
  saveError.value = null;
}

async function saveThreshold(item: WatchlistItem) {
  // v-model on type="number" may deliver a number already — normalize to string.
  const raw = String(editValue.value ?? "").trim();
  // Empty input = disable the alert (PATCH null).
  const threshold = raw === "" ? null : Number(raw);
  if (
    threshold !== null &&
    (!Number.isFinite(threshold) ||
      threshold < THRESHOLD_MIN ||
      threshold > THRESHOLD_MAX)
  ) {
    saveError.value = `阈值需在 ${THRESHOLD_MIN}-${THRESHOLD_MAX} 之间`;
    return;
  }
  await submitThreshold(item, threshold);
}

async function disableThreshold(item: WatchlistItem) {
  await submitThreshold(item, null);
}

async function submitThreshold(item: WatchlistItem, threshold: number | null) {
  saving.value = true;
  saveError.value = null;
  try {
    await financeStore.updateWatchlistAlert(item.id, threshold);
    editingId.value = null;
    editValue.value = "";
    successId.value = item.id;
    setTimeout(() => {
      if (successId.value === item.id) {
        successId.value = null;
      }
    }, 2000);
  } catch (err) {
    saveError.value = getApiErrorMessage(err, "保存阈值失败，请稍后重试");
  } finally {
    saving.value = false;
  }
}

// ErrorAlert retry: refetch the watchlist
async function retryWatchlist() {
  await financeStore.fetchWatchlist();
}
</script>

<template>
  <div class="watchlist">
    <div class="watchlist-header">
      <h2 class="watchlist-title">我的自选</h2>
      <Star :size="16" class="header-icon" />
    </div>

    <ErrorAlert
      v-if="financeStore.watchlistError"
      :message="financeStore.watchlistError"
      retryable
      @retry="retryWatchlist"
    />

    <EmptyState
      v-else-if="sortedWatchlist.length === 0"
      title="暂无自选"
      description="搜索并添加自选"
      icon="star"
    />

    <div v-else class="watchlist-list">
      <div
        v-for="item in sortedWatchlist"
        :key="item.id"
        class="watchlist-row"
      >
        <div class="watchlist-item">
          <span class="item-order">{{ item.order }}</span>
          <div class="item-symbol-name">
            <span class="item-symbol">{{ item.symbol }}</span>
            <span class="item-name text-truncate">{{
              item.name || item.symbol
            }}</span>
          </div>
          <div v-if="item.quote" class="item-price">
            {{ formatCurrency(item.quote.current_price) }}
          </div>
          <div v-if="item.quote" class="item-change">
            <span :class="changeClass(item.quote.change_percent)">
              {{ formatCurrency(item.quote.change) }}
            </span>
            <span :class="changeClass(item.quote.change_percent)">
              {{ formatPercent(item.quote.change_percent) }}
            </span>
          </div>
          <div v-if="!item.quote" class="item-price">--</div>
          <div v-if="!item.quote" class="item-change">--</div>
          <button
            class="alert-btn"
            :class="{ 'has-alert': item.alert_threshold_percent != null }"
            :title="
              item.alert_threshold_percent != null
                ? `涨跌幅提醒：${item.alert_threshold_percent}%`
                : '设置涨跌幅提醒'
            "
            @click="openEditor(item)"
          >
            <Bell :size="14" />
          </button>
          <button class="remove-btn" title="移除" @click="removeItem(item.id)">
            <X :size="14" />
          </button>
        </div>
        <div v-if="editingId === item.id" class="threshold-editor">
          <input
            v-model="editValue"
            type="number"
            class="threshold-input"
            :min="THRESHOLD_MIN"
            :max="THRESHOLD_MAX"
            step="0.1"
            placeholder="阈值%（留空=关闭）"
            :disabled="saving"
            @keyup.enter="saveThreshold(item)"
            @keyup.esc="cancelEdit"
          />
          <button
            class="editor-btn"
            title="保存"
            :disabled="saving"
            @click="saveThreshold(item)"
          >
            <Check :size="14" />
          </button>
          <button
            class="editor-btn"
            title="取消"
            :disabled="saving"
            @click="cancelEdit"
          >
            <X :size="14" />
          </button>
          <button
            v-if="item.alert_threshold_percent != null"
            class="editor-disable"
            :disabled="saving"
            @click="disableThreshold(item)"
          >
            关闭提醒
          </button>
          <span v-if="saveError" class="editor-error">{{ saveError }}</span>
        </div>
        <div v-if="successId === item.id" class="threshold-success">
          <Check :size="12" /> 已保存
        </div>
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

.watchlist-row {
  border-bottom: 1px solid var(--border-light);
}

.watchlist-row:last-child {
  border-bottom: none;
}

.watchlist-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
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

.alert-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: all var(--transition-fast);
}

.alert-btn:hover {
  color: var(--accent);
  background-color: var(--bg-hover);
}

.alert-btn.has-alert {
  color: var(--accent);
}

.threshold-editor {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 0 8px 32px;
}

.threshold-input {
  width: 140px;
  padding: 4px 8px;
  font-size: 13px;
  color: var(--text-primary);
  background-color: var(--bg-input, var(--bg-card));
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

.threshold-input:focus {
  outline: none;
  border-color: var(--accent);
}

.editor-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: all var(--transition-fast);
}

.editor-btn:hover:not(:disabled) {
  color: var(--accent);
  background-color: var(--bg-hover);
}

.editor-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.editor-disable {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
}

.editor-disable:hover:not(:disabled) {
  color: var(--danger);
  background-color: rgba(239, 68, 68, 0.1);
}

.editor-error {
  font-size: 12px;
  color: var(--danger);
}

.threshold-success {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--success, var(--down-color));
  padding: 0 0 8px 32px;
}

/* <768px: row min-widths exceed ~360px viewports; stack the change column
   and shrink min-widths so symbol/price/change fit without overflowing */
@media (max-width: 767px) {
  .item-symbol-name {
    overflow: hidden;
  }

  .item-price {
    min-width: 64px;
  }

  .item-change {
    flex-direction: column;
    align-items: flex-end;
    gap: 0;
    min-width: 72px;
  }
}
</style>
