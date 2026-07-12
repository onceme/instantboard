<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { useResponsive } from "@/composables/useResponsive";
import { computed } from "vue";
import WatchlistMini from "./WatchlistMini.vue";
import MarketIndices from "./MarketIndices.vue";
import Commodities from "./Commodities.vue";
import Watchlist from "./Watchlist.vue";
import SearchSymbols from "./SearchSymbols.vue";
import FundNAV from "./FundNAV.vue";

const financeStore = useFinanceStore();
const { showRightPanel } = useResponsive();

const currentPanel = computed(() => financeStore.currentPanel);
</script>

<template>
  <div class="finance-grid">
    <div class="finance-main">
      <div v-if="currentPanel === 'overview'" class="panel-overview">
        <MarketIndices />
      </div>

      <div v-if="currentPanel === 'watchlist'" class="panel-watchlist">
        <Watchlist />
      </div>

      <div v-if="currentPanel === 'search'" class="panel-search">
        <SearchSymbols />
      </div>

      <div v-if="currentPanel === 'indices'" class="panel-indices">
        <MarketIndices />
      </div>

      <div v-if="currentPanel === 'commodities'" class="panel-commodities">
        <Commodities />
      </div>
    </div>

    <div v-if="showRightPanel" class="finance-right">
      <WatchlistMini />
      <FundNAV />
    </div>
  </div>
</template>

<style scoped>
.finance-grid {
  display: flex;
  gap: 16px;
  min-height: 0;
}

.finance-main {
  flex: 1;
  min-width: 0;
}

.finance-right {
  width: var(--right-panel-width);
  display: flex;
  flex-direction: column;
  gap: 12px;
  flex-shrink: 0;
}

@media (max-width: 1365px) {
  .finance-right {
    display: none;
  }

  .finance-grid {
    flex-direction: column;
  }
}
</style>
