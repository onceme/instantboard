<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import { useFinanceStore } from "@/stores/finance";
import MarketTicker from "@/components/finance/MarketTicker.vue";
import FinanceSubNav from "@/components/finance/FinanceSubNav.vue";
import FinanceGrid from "@/components/finance/FinanceGrid.vue";

const financeStore = useFinanceStore();

onMounted(() => {
  financeStore.init();
});

onUnmounted(() => {
  financeStore.cleanup();
});
</script>

<template>
  <div class="finance-view">
    <MarketTicker />
    <FinanceSubNav />
    <FinanceGrid />
  </div>
</template>

<style scoped>
.finance-view {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 0;
}
</style>
