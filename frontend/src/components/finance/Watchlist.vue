<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { formatCurrency, formatPercent, getChangeClass } from "@/utils/format";
import { getApiErrorMessage } from "@/utils/api";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  Bell,
  Check,
  ChevronDown,
  ChevronUp,
  GripVertical,
  Star,
  X,
} from "lucide-vue-next";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorAlert from "@/components/common/ErrorAlert.vue";
import type { FundNAVIntraday, WatchlistItem } from "@/types";

const financeStore = useFinanceStore();

// Null-safe formatters for the intraday NAV estimate cell (fund-intraday-nav.md
// §9.2). They accept the nullable estimate fields directly so the template never
// needs cross-call narrowing (item.nav's fields are `number | null`).
function fmtNavCurrency(v: number | null | undefined): string {
  return v != null ? formatCurrency(v, "CNY") : "--";
}
function fmtNavPercent(v: number | null | undefined): string {
  return v != null ? formatPercent(v) : "--";
}
function navChangeClass(v: number | null | undefined): string {
  return v != null ? changeClass(v) : "change-neutral";
}
function fmtCoverage(nav: FundNAVIntraday | null | undefined): string | null {
  return nav != null && nav.coverage_percent != null
    ? nav.coverage_percent.toFixed(1)
    : null;
}
function isDelayedNav(nav: FundNAVIntraday | null | undefined): boolean {
  return (
    nav != null &&
    (nav.delayed_markets.length > 0 ||
      nav.quote_status === "delayed" ||
      nav.quote_status === "mixed")
  );
}
// Delay-tier labels per market (fund-intraday-nav.md §1.2/§6.1 magnitudes):
// HK free feeds lag ~15-25min, US ~15min; A-shares are realtime and never
// appear in delayed_markets.
const DELAY_TIER_LABELS: Record<string, string> = {
  HK: "HK ≈15~25min",
  US: "US ≈15min",
};
function delayedMarketsLabel(nav: FundNAVIntraday | null | undefined): string {
  if (nav == null || nav.delayed_markets.length === 0) return "行情";
  return nav.delayed_markets
    .map((m) => DELAY_TIER_LABELS[m] ?? `${m} 延迟`)
    .join(" · ");
}

// Tooltip texts for the estimate badges (accuracy definition, unknown-position
// assumption and disclaimer / delayed-quote magnitude / stale disclosure).
const COVERAGE_BADGE_TOOLTIP =
  "精度口径：可得持仓权重之和占净值比例。基金未披露的仓位按盘中不变假设处理，精度越低估值偏差可能越大。估值仅供参考，不构成投资建议";
function delayedBadgeTooltip(markets: string[]): string {
  const tiers = markets
    .map((m) => DELAY_TIER_LABELS[m] ?? `${m} 延迟`)
    .join("、");
  return `含延迟行情成分（${tiers}），估值基于延迟行情计算，可能与实时价格存在偏差`;
}
const STALE_BADGE_TOOLTIP =
  "该基金持仓披露报告期已超过 120 天新鲜度阈值：持仓不足以代表当前组合，估值已转为指数外推或仅显示官方净值，偏差可能较大";

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
      // Intraday estimate (fund rows): the SSE-fed navEstimates wins; the
      // quote's fund_nav snapshot seeds the row until the first batch lands.
      nav:
        financeStore.navEstimates[item.symbol] ??
        financeStore.watchlistQuotes.get(item.symbol)?.fund_nav ??
        null,
      order: index + 1,
    }));
});

// --- 拖拽排序（finance-tab.md §3.2）---
// 桌面端走 HTML5 原生 DnD（只能从把手发起，避免干扰行内按钮）；移动端
// (<768px) HTML5 DnD 不可靠，降级为每行的上移/下移按钮——按钮所有视口都
// 渲染，桌面端弱化显示、移动端常显。
const dragArmed = ref(false); // 把手 mousedown 置位，否则 dragstart 被拒绝
const draggingId = ref<string | null>(null);
const dropTarget = ref<{ id: string; position: "before" | "after" } | null>(
  null,
);
const reorderError = ref<string | null>(null);

const canReorder = computed(() => sortedWatchlist.value.length >= 2);

// 按 display_order 排序的纯条目（不含行的 quote/order 附加字段），重排
// 计算的输入。
const orderedItems = computed(() =>
  [...financeStore.watchlist].sort((a, b) => a.display_order - b.display_order),
);

// 文档级 mouseup：松开把手但没发起拖拽时复位，避免误触发后续拖拽。
onMounted(() => document.addEventListener("mouseup", disarmDrag));
onBeforeUnmount(() => document.removeEventListener("mouseup", disarmDrag));

function armDrag() {
  dragArmed.value = true;
}

function disarmDrag() {
  dragArmed.value = false;
}

function onDragStart(event: DragEvent, item: { id: string }) {
  disarmDrag();
  if (!canReorder.value) {
    event.preventDefault();
    return;
  }
  draggingId.value = item.id;
  reorderError.value = null;
  cancelEdit(); // 拖拽期间禁用阈值编辑等行内交互，避免冲突
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    // Firefox 需要设置过数据才会真正开始拖拽
    event.dataTransfer.setData("text/plain", item.id);
  }
}

function onDragOver(event: DragEvent, item: { id: string }) {
  if (!draggingId.value || draggingId.value === item.id) return;
  event.preventDefault(); // 允许 drop
  const position = rectPosition(event);
  if (
    dropTarget.value?.id !== item.id ||
    dropTarget.value.position !== position
  ) {
    dropTarget.value = { id: item.id, position };
  }
}

function onDrop(event: DragEvent, item: { id: string }) {
  event.preventDefault();
  const position =
    dropTarget.value?.id === item.id
      ? dropTarget.value.position
      : rectPosition(event);
  finishDrop(item.id, position);
}

// 落在列表容器本身（如行间隙）时，视为移动到末尾
function onListDrop() {
  const items = orderedItems.value;
  const last = items[items.length - 1];
  if (last && draggingId.value && draggingId.value !== last.id) {
    finishDrop(last.id, "after");
  } else {
    clearDragState();
  }
}

function onListDragLeave(event: DragEvent) {
  const list = event.currentTarget as HTMLElement;
  const related = event.relatedTarget as Node | null;
  if (!related || !list.contains(related)) {
    dropTarget.value = null;
  }
}

function onDragEnd() {
  clearDragState();
}

function rectPosition(event: DragEvent): "before" | "after" {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  return event.clientY - rect.top < rect.height / 2 ? "before" : "after";
}

function finishDrop(targetId: string, position: "before" | "after" | null) {
  const sourceId = draggingId.value;
  clearDragState();
  if (!sourceId || sourceId === targetId || position === null) return;
  const list = [...orderedItems.value];
  const fromIndex = list.findIndex((item) => item.id === sourceId);
  if (fromIndex < 0) return;
  const [moved] = list.splice(fromIndex, 1);
  let insertAt = list.findIndex((item) => item.id === targetId);
  if (insertAt < 0) return;
  if (position === "after") insertAt += 1;
  list.splice(insertAt, 0, moved);
  if (list.every((item, index) => item.id === orderedItems.value[index].id)) {
    return; // 顺序未变，不发请求
  }
  void commitReorder(list);
}

function moveItem(item: { id: string }, delta: -1 | 1) {
  const list = [...orderedItems.value];
  const fromIndex = list.findIndex((entry) => entry.id === item.id);
  const toIndex = fromIndex + delta;
  if (fromIndex < 0 || toIndex < 0 || toIndex >= list.length) return;
  [list[fromIndex], list[toIndex]] = [list[toIndex], list[fromIndex]];
  void commitReorder(list);
}

// 乐观提交：先更新 store.watchlist 的顺序与 display_order，请求失败则回滚
// 到拖拽前快照并行内提示（可重新拖拽或刷新页面后重试）；成功静默——重排
// 结果已即时可见，不再额外弹提示。
async function commitReorder(newOrder: WatchlistItem[]) {
  const snapshot = financeStore.watchlist.map((item) => ({ ...item }));
  financeStore.watchlist = newOrder.map((item, index) => ({
    ...item,
    display_order: index,
  }));
  try {
    await financeStore.reorderWatchlist(newOrder);
  } catch (err) {
    financeStore.watchlist = snapshot;
    reorderError.value = getApiErrorMessage(
      err,
      "排序保存失败，已恢复原顺序，可重新拖拽或刷新页面后重试",
    );
  }
}

function isDropBefore(id: string) {
  return dropTarget.value?.id === id && dropTarget.value.position === "before";
}

function isDropAfter(id: string) {
  return dropTarget.value?.id === id && dropTarget.value.position === "after";
}

function clearDragState() {
  draggingId.value = null;
  dropTarget.value = null;
}

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
}

async function removeItem(itemId: string) {
  if (draggingId.value) return; // 拖拽期间禁用行内操作
  await financeStore.removeFromWatchlist(itemId);
}

function openEditor(item: WatchlistItem) {
  if (draggingId.value) return; // 拖拽期间禁用阈值编辑器，避免冲突
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

    <div
      v-else
      class="watchlist-list"
      @dragover.prevent
      @drop.prevent="onListDrop"
      @dragleave="onListDragLeave"
    >
      <div v-if="reorderError" class="reorder-error">{{ reorderError }}</div>
      <div
        v-for="item in sortedWatchlist"
        :key="item.id"
        class="watchlist-row"
        :class="{
          dragging: draggingId === item.id,
          'drop-before': isDropBefore(item.id),
          'drop-after': isDropAfter(item.id),
        }"
        :draggable="canReorder"
        @dragstart="onDragStart($event, item)"
        @dragover="onDragOver($event, item)"
        @drop.stop.prevent="onDrop($event, item)"
        @dragend="onDragEnd"
      >
        <div class="watchlist-item">
          <span
            class="drag-handle"
            :class="{ 'handle-disabled': !canReorder }"
            :title="canReorder ? '拖拽排序' : ''"
            @mousedown="armDrag"
          >
            <GripVertical :size="14" />
          </span>
          <span class="item-order">{{ item.order }}</span>
          <div class="item-symbol-name">
            <span class="item-symbol">{{ item.symbol }}</span>
            <span class="item-name text-truncate">{{
              item.name || item.symbol
            }}</span>
            <!-- Intraday NAV badges (fund rows only, fund-intraday-nav.md §9.2) -->
            <span
              v-if="fmtCoverage(item.nav) != null"
              class="fund-badge fund-badge-coverage"
              :title="COVERAGE_BADGE_TOOLTIP"
            >
              精度 {{ fmtCoverage(item.nav) }}%
            </span>
            <span
              v-if="isDelayedNav(item.nav)"
              class="fund-badge fund-badge-delayed"
              :title="delayedBadgeTooltip(item.nav?.delayed_markets ?? [])"
            >
              延迟·{{ delayedMarketsLabel(item.nav) }}
            </span>
            <span
              v-if="item.nav?.holdings_stale"
              class="fund-badge fund-badge-stale"
              :title="STALE_BADGE_TOOLTIP"
            >
              ⚠
            </span>
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
          <!-- Intraday estimate column (fund rows only): estimate NAV +
               estimated change vs. official NAV. Hidden below 768px. -->
          <div v-if="item.nav" class="item-nav">
            <span class="nav-value">{{
              fmtNavCurrency(item.nav.nav_estimate)
            }}</span>
            <span :class="navChangeClass(item.nav.estimate_change_percent)">
              {{ fmtNavPercent(item.nav.estimate_change_percent) }}
            </span>
          </div>
          <div class="row-move">
            <button
              class="move-btn"
              title="上移"
              :disabled="item.order <= 1"
              @click="moveItem(item, -1)"
            >
              <ChevronUp :size="14" />
            </button>
            <button
              class="move-btn"
              title="下移"
              :disabled="item.order >= sortedWatchlist.length"
              @click="moveItem(item, 1)"
            >
              <ChevronDown :size="14" />
            </button>
          </div>
          <button
            class="alert-btn"
            :class="{ 'has-alert': item.alert_threshold_percent != null }"
            :title="
              item.alert_threshold_percent != null
                ? `涨跌幅提醒：${item.alert_threshold_percent}%`
                : '设置涨跌幅提醒'
            "
            :disabled="draggingId !== null"
            @click="openEditor(item)"
          >
            <Bell :size="14" />
          </button>
          <button
            class="remove-btn"
            title="移除"
            :disabled="draggingId !== null"
            @click="removeItem(item.id)"
          >
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
  transition: opacity var(--transition-fast);
}

.watchlist-row:last-child {
  border-bottom: none;
}

/* 拖拽中的行半透明，插入点用 2px 高亮线指示（不占布局，无跳动） */
.watchlist-row.dragging {
  opacity: 0.4;
}

.watchlist-row.drop-before {
  box-shadow: 0 -2px 0 0 var(--accent);
}

.watchlist-row.drop-after {
  box-shadow: 0 2px 0 0 var(--accent);
}

.drag-handle {
  display: flex;
  align-items: center;
  color: var(--text-muted);
  cursor: grab;
  user-select: none;
  transition: color var(--transition-fast);
}

.drag-handle:hover {
  color: var(--text-secondary);
}

.drag-handle:active {
  cursor: grabbing;
}

.drag-handle.handle-disabled {
  opacity: 0.35;
  cursor: default;
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

/* Intraday NAV estimate column (fund rows, fund-intraday-nav.md §9.2) */
.item-nav {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0;
  font-size: 12px;
  font-weight: 500;
  text-align: right;
  min-width: 84px;
}

.item-nav .nav-value {
  color: var(--text-primary);
}

/* Fund estimate badges: accuracy / delayed quote / stale disclosure */
.fund-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  margin-left: 4px;
  font-size: 10px;
  line-height: 16px;
  border-radius: var(--radius-sm, 4px);
  white-space: nowrap;
  cursor: help;
  user-select: none;
}

.fund-badge-coverage {
  color: var(--accent);
  background-color: color-mix(in srgb, var(--accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
}

.fund-badge-delayed {
  color: var(--text-secondary);
  background-color: var(--bg-hover);
  border: 1px solid var(--border-color);
}

.fund-badge-stale {
  color: var(--warning, #f59e0b);
  background-color: color-mix(
    in srgb,
    var(--warning, #f59e0b) 12%,
    transparent
  );
  border: 1px solid color-mix(in srgb, var(--warning, #f59e0b) 35%, transparent);
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

.row-move {
  display: flex;
  gap: 2px;
}

/* 移动端 (<768px) HTML5 DnD 不可靠，降级为这两个按钮：所有视口都渲染，
   桌面端弱化显示（低不透明度），移动端常显（见下方媒体查询）。 */
.move-btn {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  opacity: 0.35;
  transition: all var(--transition-fast);
}

.move-btn:hover:not(:disabled) {
  color: var(--accent);
  background-color: var(--bg-hover);
  opacity: 1;
}

.move-btn:disabled {
  opacity: 0.2;
  cursor: default;
}

.reorder-error {
  font-size: 12px;
  color: var(--danger);
  padding: 4px 0;
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
  /* 移动端无可靠 HTML5 DnD：上移/下移按钮常显（桌面端仅弱化显示） */
  .move-btn {
    opacity: 0.8;
  }

  .move-btn:disabled {
    opacity: 0.25;
  }

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

  /* Row width budget: hide the estimate column and badges on small screens
     (price/change stay; the estimate surfaces via SSE as soon as it exists). */
  .item-nav {
    display: none;
  }

  .fund-badge {
    display: none;
  }
}
</style>
