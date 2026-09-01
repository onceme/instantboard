<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { formatCurrency, formatPercent, getChangeClass } from "@/utils/format";
import { getFundStatusNote } from "@/utils/fundStatus";
import { computed, ref } from "vue";
import type { FundNAVIntraday } from "@/types";
import { Search } from "lucide-vue-next";

const financeStore = useFinanceStore();
const searchQuery = ref("");
// Search hits are identified by symbol/name; the estimate itself lives in the
// store's navEstimates (single source of truth, SSE-refreshed, §9.2).
const searchResults = ref<Array<{ symbol: string; name: string }>>([]);
const selectedSymbol = ref<string | null>(null);
// Reactive selected estimate: SSE nav_batch_update merges keep this view
// fresh without re-fetching (replaces the old snapshot-on-select logic).
const selectedFund = computed<FundNAVIntraday | null>(() =>
  selectedSymbol.value
    ? (financeStore.navEstimates[selectedSymbol.value] ?? null)
    : null,
);
const isSearching = ref(false);

async function searchFunds() {
  if (!searchQuery.value.trim()) {
    searchResults.value = [];
    return;
  }
  isSearching.value = true;
  try {
    await financeStore.searchSymbols(searchQuery.value, "fund");
    const fundResults = financeStore.searchResults.filter(
      (r) => r.type === "fund",
    );
    // One batch load seeds the estimates for every hit (rt cache wins,
    // misses degrade to latest_official server-side).
    await financeStore.fetchFundNAVBatch(fundResults.map((r) => r.symbol));
    searchResults.value = fundResults.map((r) => ({
      symbol: r.symbol,
      name: r.name,
    }));
  } finally {
    isSearching.value = false;
  }
}

function selectFund(fund: { symbol: string }) {
  selectedSymbol.value = fund.symbol;
  searchResults.value = [];
  searchQuery.value = "";
}

function clearSelection() {
  selectedSymbol.value = null;
}

let timer: ReturnType<typeof setTimeout> | null = null;

function onInput() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(searchFunds, 300);
}

const changeClass = computed(() => {
  const change = selectedFund.value?.estimate_change_percent;
  if (change == null) return "";
  const cls = getChangeClass(change);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
});

const METHOD_LABELS: Record<FundNAVIntraday["estimate_method"], string> = {
  holdings_weighted: "持仓加权",
  index_tracking: "指数外推",
  latest_official: "官方净值",
};

const QUOTE_STATUS_LABELS: Record<FundNAVIntraday["quote_status"], string> = {
  realtime: "实时行情",
  delayed: "延迟行情",
  mixed: "实时+延迟混合行情",
  frozen: "净值停更（非交易时段/无数据）",
};

function delayedMarketsText(fund: FundNAVIntraday): string {
  return fund.delayed_markets.length > 0
    ? `（${fund.delayed_markets.join("/")} 延迟）`
    : "";
}

// §9.4 status note for the selected fund (explains a missing live estimate
// instead of rendering bare "--" rows).
const statusNote = computed(() => getFundStatusNote(selectedFund.value));

// Concise reason for the degraded estimate methods (fund-intraday-nav.md
// §4.3): why the holdings-weighted path is not in effect.
function methodReason(fund: FundNAVIntraday): string | null {
  if (fund.estimate_method === "index_tracking") {
    if (fund.holdings_report_date == null) return "无可用持仓，改用指数外推";
    if (fund.holdings_stale) return "持仓报告期超过 120 天，改用指数外推";
    return "可得持仓精度不足，采用指数外推";
  }
  if (fund.estimate_method === "latest_official") {
    return fund.holdings_stale
      ? "持仓披露异常或过旧，仅显示官方净值"
      : "暂无可用持仓与指数绑定，仅显示官方净值";
  }
  return null;
}
</script>

<template>
  <div class="fund-nav">
    <h2 class="nav-title">基金NAV估值</h2>

    <div class="search-box">
      <Search :size="16" class="search-icon" />
      <input
        v-model="searchQuery"
        type="text"
        placeholder="输入基金代码或名称..."
        class="search-input"
        @input="onInput"
      />
    </div>

    <div v-if="searchResults.length > 0" class="search-results">
      <div
        v-for="fund in searchResults"
        :key="fund.symbol"
        class="result-item"
        @click="selectFund(fund)"
      >
        <span class="result-symbol">{{ fund.symbol }}</span>
        <span class="result-name">{{ fund.name }}</span>
      </div>
    </div>

    <div v-if="selectedFund" class="nav-detail">
      <div class="nav-header">
        <span class="fund-symbol">{{ selectedFund.symbol }}</span>
        <span class="fund-name">{{ selectedFund.name }}</span>
        <span
          v-if="selectedFund.coverage_percent != null"
          class="nav-coverage-badge"
          title="精度口径：可得持仓权重之和占净值比例。未披露仓位按盘中不变假设，精度越低偏差可能越大"
        >
          精度 {{ selectedFund.coverage_percent.toFixed(1) }}%
        </span>
        <button class="close-btn" @click="clearSelection">×</button>
      </div>

      <div class="nav-values">
        <div class="nav-row">
          <span class="nav-label">官方NAV</span>
          <span class="nav-official">{{
            selectedFund.nav_official != null
              ? formatCurrency(selectedFund.nav_official, "CNY")
              : "--"
          }}</span>
          <span class="nav-date">{{
            selectedFund.nav_official_date
              ? `基于 ${selectedFund.nav_official_date} 净值`
              : "官方净值待更新"
          }}</span>
        </div>

        <div class="nav-row">
          <span class="nav-label">估值NAV</span>
          <span class="nav-estimate">{{
            selectedFund.nav_estimate != null
              ? formatCurrency(selectedFund.nav_estimate, "CNY")
              : "--"
          }}</span>
          <span
            v-if="selectedFund.estimate_change_percent != null"
            :class="changeClass"
            class="nav-deviation"
          >
            {{ formatPercent(selectedFund.estimate_change_percent) }}
          </span>
        </div>

        <div class="nav-row">
          <span class="nav-label">估值方法</span>
          <span class="estimate-method">{{
            METHOD_LABELS[selectedFund.estimate_method]
          }}</span>
        </div>

        <div v-if="methodReason(selectedFund)" class="nav-row">
          <span class="nav-label">方法说明</span>
          <span class="method-reason">{{ methodReason(selectedFund) }}</span>
        </div>

        <div class="nav-row">
          <span class="nav-label">行情状态</span>
          <span class="quote-status">
            {{ QUOTE_STATUS_LABELS[selectedFund.quote_status]
            }}{{ delayedMarketsText(selectedFund) }}
          </span>
        </div>

        <div v-if="statusNote" class="nav-row">
          <span class="nav-label">估值状态</span>
          <span class="nav-status-note" :title="statusNote.tooltip">
            {{ statusNote.label }}
          </span>
        </div>

        <div v-if="selectedFund.holdings_report_date" class="nav-row">
          <span class="nav-label">持仓报告期</span>
          <span class="report-date">
            {{ selectedFund.holdings_report_date }}
            <span v-if="selectedFund.holdings_stale" class="report-stale">
              （披露较旧，估值偏差可能较大）
            </span>
          </span>
        </div>

        <div class="nav-disclaimer">
          估值仅供参考，不构成投资建议。未披露仓位按盘中不变假设处理。
        </div>
      </div>
    </div>

    <div v-else-if="!isSearching && searchQuery.trim()" class="nav-placeholder">
      请搜索基金代码查看NAV估值
    </div>
  </div>
</template>

<style scoped>
.fund-nav {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.nav-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--bg-secondary);
  margin-bottom: 8px;
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
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  margin-bottom: 12px;
  max-height: 200px;
  overflow-y: auto;
}

.result-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.result-item:hover {
  background-color: var(--bg-hover);
}

.result-symbol {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}

.result-name {
  color: var(--text-secondary);
  font-size: 13px;
}

.nav-detail {
  margin-top: 8px;
}

.nav-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.fund-symbol {
  font-weight: 700;
  color: var(--text-primary);
  font-size: 16px;
}

.fund-name {
  color: var(--text-secondary);
  font-size: 14px;
}

.close-btn {
  margin-left: auto;
  font-size: 18px;
  color: var(--text-muted);
  padding: 4px 8px;
  border-radius: var(--radius-sm);
}

.close-btn:hover {
  background-color: var(--bg-hover);
}

.nav-values {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}

.nav-label {
  font-size: 13px;
  color: var(--text-muted);
  min-width: 80px;
}

.nav-official {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.nav-date {
  font-size: 12px;
  color: var(--text-muted);
}

.nav-estimate {
  font-size: 14px;
  font-weight: 600;
  color: var(--accent);
}

.nav-deviation {
  font-size: 14px;
  font-weight: 600;
}

.index-info {
  font-size: 13px;
  color: var(--text-secondary);
}

.estimate-method {
  font-size: 13px;
  color: var(--text-secondary);
}

.method-reason {
  font-size: 12px;
  color: var(--text-muted);
}

.nav-coverage-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  color: var(--accent);
  background-color: color-mix(in srgb, var(--accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
  cursor: help;
  user-select: none;
}

/* §9.4 estimate status note: why this fund shows no live intraday value. */
.nav-status-note {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  color: var(--warning, #f59e0b);
  background-color: color-mix(
    in srgb,
    var(--warning, #f59e0b) 12%,
    transparent
  );
  border: 1px solid color-mix(in srgb, var(--warning, #f59e0b) 35%, transparent);
  cursor: help;
  user-select: none;
}

.quote-status {
  font-size: 13px;
  color: var(--text-secondary);
}

.report-date {
  font-size: 13px;
  color: var(--text-secondary);
}

.report-stale {
  color: var(--warning);
}

.nav-disclaimer {
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-muted);
  padding: 6px 8px;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-sm);
}

.nav-placeholder {
  text-align: center;
  padding: 24px;
  color: var(--text-muted);
  font-size: 14px;
}
</style>
