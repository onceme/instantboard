<script setup lang="ts">
/**
 * Floating toast for watchlist price alerts (finance-tab.md §3.2).
 * Shows the newest store alert (direction arrow + symbol + change% + threshold)
 * and auto-hides 8s later; a newer alert restarts the timer. Color follows the
 * global change-up/change-down classes (--up-color/--down-color theme vars).
 * The alert history itself stays in financeStore.alerts — dismiss is visual only.
 */
import { onUnmounted, ref, watch } from "vue";
import type { FinanceAlert } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/format";

const AUTO_HIDE_MS = 8000;

const props = defineProps<{ alert: FinanceAlert }>();

const visible = ref(false);
let hideTimer: ReturnType<typeof setTimeout> | null = null;

function show() {
  visible.value = true;
  if (hideTimer) {
    clearTimeout(hideTimer);
  }
  hideTimer = setTimeout(() => {
    visible.value = false;
  }, AUTO_HIDE_MS);
}

watch(() => props.alert, show, { immediate: true });

onUnmounted(() => {
  if (hideTimer) {
    clearTimeout(hideTimer);
    hideTimer = null;
  }
});
</script>

<template>
  <div
    v-if="visible"
    class="alert-toast"
    :class="alert.direction === 'up' ? 'toast-up' : 'toast-down'"
    role="alert"
  >
    <span class="toast-direction" aria-hidden="true">
      {{ alert.direction === "up" ? "▲" : "▼" }}
    </span>
    <div class="toast-body">
      <span class="toast-title">
        涨跌幅提醒 {{ alert.symbol }}
        <span
          class="toast-change"
          :class="alert.direction === 'up' ? 'change-up' : 'change-down'"
        >
          {{ formatPercent(alert.change_percent) }}
        </span>
      </span>
      <span class="toast-detail">
        {{ alert.name || alert.symbol }}
        <template v-if="alert.price !== null">
          · 现价 {{ formatCurrency(alert.price) }}
        </template>
        · 阈值 {{ alert.threshold_percent }}%
      </span>
    </div>
  </div>
</template>

<style scoped>
.alert-toast {
  position: fixed;
  right: 20px;
  bottom: 24px;
  z-index: 1200;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 340px;
  padding: 12px 16px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-left: 4px solid var(--accent);
  border-radius: var(--radius-lg);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
  animation: alert-toast-in 0.25s ease-out;
}

.alert-toast.toast-up {
  border-left-color: var(--up-color);
}

.alert-toast.toast-down {
  border-left-color: var(--down-color);
}

.toast-direction {
  font-size: 14px;
  line-height: 20px;
}

.toast-up .toast-direction {
  color: var(--up-color);
}

.toast-down .toast-direction {
  color: var(--down-color);
}

.toast-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.toast-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.toast-change {
  font-weight: 600;
}

.toast-detail {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@keyframes alert-toast-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .alert-toast {
    animation: none;
  }
}
</style>
