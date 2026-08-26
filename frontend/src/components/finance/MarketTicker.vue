<script setup lang="ts">
import { computed } from "vue";
import { useFinanceStore } from "@/stores/finance";
import { formatNumber, formatPercent, getChangeClass } from "@/utils/format";

const financeStore = useFinanceStore();

const indices = computed(() => financeStore.marketIndices);

// ~6s per index item keeps the scroll speed constant as the list changes
const scrollDuration = computed(
  () => `${Math.max(indices.value.length, 1) * 6}s`,
);

function changeClass(changePercent: number): string {
  const cls = getChangeClass(changePercent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
}

function goToIndices() {
  financeStore.setCurrentPanel("indices");
}
</script>

<template>
  <!-- No index data renders nothing, matching the other empty-state conventions -->
  <div v-if="indices.length > 0" class="market-ticker" aria-label="市场指数行情">
    <div class="ticker-track" :style="{ animationDuration: scrollDuration }">
      <!-- Two identical copies; translateX(0 → -50%) yields a seamless loop -->
      <div
        v-for="copy in [0, 1]"
        :key="copy"
        class="ticker-group"
        :aria-hidden="copy === 1 ? 'true' : undefined"
      >
        <button
          v-for="item in indices"
          :key="`${copy}-${item.symbol}`"
          type="button"
          class="ticker-item"
          :tabindex="copy === 1 ? -1 : undefined"
          :title="item.name"
          @click="goToIndices"
        >
          <span class="item-name">{{ item.name }}</span>
          <span class="item-value">{{ formatNumber(item.value, 2) }}</span>
          <span class="item-change" :class="changeClass(item.change_percent)">
            {{ formatPercent(item.change_percent) }}
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.market-ticker {
  height: 36px;
  overflow: hidden;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  margin-bottom: 12px;
}

.ticker-track {
  display: flex;
  width: max-content;
  height: 100%;
  animation-name: ticker-scroll;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
}

.market-ticker:hover .ticker-track {
  animation-play-state: paused;
}

.ticker-group {
  display: flex;
  align-items: center;
  height: 100%;
}

.ticker-item {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 100%;
  padding: 0 14px;
  font-size: 12px;
  white-space: nowrap;
  border: none;
  background: none;
  cursor: pointer;
}

.item-name {
  color: var(--text-secondary);
  transition: color var(--transition-fast);
}

.ticker-item:hover .item-name {
  color: var(--text-primary);
}

.item-value {
  font-weight: 600;
  color: var(--text-primary);
}

.item-change {
  font-weight: 500;
}

@keyframes ticker-scroll {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(-50%);
  }
}

/* Reduced motion: freeze the animation and allow manual horizontal scrolling */
@media (prefers-reduced-motion: reduce) {
  .ticker-track {
    animation: none;
  }

  .ticker-group[aria-hidden] {
    display: none;
  }

  .market-ticker {
    overflow-x: auto;
  }
}

/* Mobile keeps the strip but compresses fonts and spacing */
@media (max-width: 767px) {
  .market-ticker {
    height: 32px;
    margin-bottom: 8px;
  }

  .ticker-item {
    gap: 4px;
    padding: 0 8px;
    font-size: 11px;
  }
}
</style>
