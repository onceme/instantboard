<script setup lang="ts">
import { onMounted } from "vue";
import { useFinanceStore } from "@/stores/finance";
import OverviewWatchlistSummary from "./OverviewWatchlistSummary.vue";
import MarketIndices from "./MarketIndices.vue";
import FinanceNewsPanel from "./FinanceNewsPanel.vue";

const financeStore = useFinanceStore();

// Hybrid Overview (finance-tab.md §3.6.2): watchlist summary + market indices
// + finance news. Watchlist list + quotes load once per session; re-activating
// the panel reuses the store data (ensureWatchlist is a no-op when loaded).
onMounted(() => {
  financeStore.ensureWatchlist();
});
</script>

<template>
  <div class="finance-overview">
    <OverviewWatchlistSummary />
    <MarketIndices />
    <FinanceNewsPanel />
  </div>
</template>

<style scoped>
.finance-overview {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
