<script setup lang="ts">
import { computed, onMounted, onUnmounted } from "vue";
import { useFinanceStore } from "@/stores/finance";
import MarketTicker from "@/components/finance/MarketTicker.vue";
import FinanceSubNav from "@/components/finance/FinanceSubNav.vue";
import FinanceGrid from "@/components/finance/FinanceGrid.vue";
import AlertToast from "@/components/finance/AlertToast.vue";

const financeStore = useFinanceStore();

// Newest alert drives the floating toast (auto-hides inside the component).
const latestAlert = computed(() => financeStore.alerts[0] ?? null);

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
    <AlertToast v-if="latestAlert" :alert="latestAlert" />
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
