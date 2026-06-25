<script setup lang="ts">
import { useFinanceStore } from '@/stores/finance'
import { formatCurrency, formatPercent, getChangeClass } from '@/utils/format'
import { computed, ref } from 'vue'
import type { FundNAV } from '@/types'
import { Search } from 'lucide-vue-next'

const financeStore = useFinanceStore()
const searchQuery = ref('')
const searchResults = ref<FundNAV[]>([])
const selectedFund = ref<FundNAV | null>(null)
const isSearching = ref(false)

async function searchFunds() {
  if (!searchQuery.value.trim()) {
    searchResults.value = []
    return
  }
  isSearching.value = true
  try {
    await financeStore.searchSymbols(searchQuery.value, 'fund')
    const fundResults = financeStore.searchResults.filter(r => r.type === 'fund')
    searchResults.value = []
    for (const r of fundResults) {
      const navData = await financeStore.getFundNAV(r.symbol)
      searchResults.value.push(navData)
    }
  } finally {
    isSearching.value = false
  }
}

function selectFund(fund: FundNAV) {
  selectedFund.value = fund
  searchResults.value = []
  searchQuery.value = ''
}

function clearSelection() {
  selectedFund.value = null
}

let timer: ReturnType<typeof setTimeout> | null = null

function onInput() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(searchFunds, 300)
}

const deviationClass = computed(() => {
  if (!selectedFund.value?.nav_estimate_deviation_percent) return ''
  const cls = getChangeClass(selectedFund.value.nav_estimate_deviation_percent)
  if (cls === 'up') return 'change-up'
  if (cls === 'down') return 'change-down'
  return 'change-neutral'
})
</script>

<template>
  <div class="fund-nav">
    <h2 class="nav-title">
      基金NAV估值
    </h2>

    <div class="search-box">
      <Search
        :size="16"
        class="search-icon"
      />
      <input
        v-model="searchQuery"
        type="text"
        placeholder="输入基金代码或名称..."
        class="search-input"
        @input="onInput"
      >
    </div>

    <div
      v-if="searchResults.length > 0"
      class="search-results"
    >
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

    <div
      v-if="selectedFund"
      class="nav-detail"
    >
      <div class="nav-header">
        <span class="fund-symbol">{{ selectedFund.symbol }}</span>
        <span class="fund-name">{{ selectedFund.name }}</span>
        <button
          class="close-btn"
          @click="clearSelection"
        >
          ×
        </button>
      </div>

      <div class="nav-values">
        <div class="nav-row">
          <span class="nav-label">官方NAV</span>
          <span class="nav-official">{{ formatCurrency(selectedFund.nav_official, 'CNY') }}</span>
          <span class="nav-date">{{ selectedFund.nav_official_date }}</span>
        </div>

        <div
          v-if="selectedFund.nav_estimate"
          class="nav-row"
        >
          <span class="nav-label">估值NAV</span>
          <span class="nav-estimate">{{ formatCurrency(selectedFund.nav_estimate, 'CNY') }}</span>
        </div>

        <div
          v-if="selectedFund.nav_estimate_deviation_percent"
          class="nav-row"
        >
          <span class="nav-label">估值偏差</span>
          <span
            :class="deviationClass"
            class="nav-deviation"
          >
            {{ formatPercent(selectedFund.nav_estimate_deviation_percent) }}
          </span>
        </div>

        <div
          v-if="selectedFund.underlying_index"
          class="nav-row"
        >
          <span class="nav-label">跟踪指数</span>
          <span class="index-info">
            {{ selectedFund.underlying_index.name }}
            {{ formatPercent(selectedFund.underlying_index.change_percent) }}
          </span>
        </div>

        <div
          v-if="selectedFund.estimate_method"
          class="nav-row"
        >
          <span class="nav-label">估值方法</span>
          <span class="estimate-method">{{ selectedFund.estimate_method === 'index_tracking' ? '指数跟踪法' : selectedFund.estimate_method }}</span>
        </div>

        <div class="nav-disclaimer">
          估值仅供参考，不构成投资建议
        </div>
      </div>
    </div>

    <div
      v-else-if="!isSearching && searchQuery.trim()"
      class="nav-placeholder"
    >
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
