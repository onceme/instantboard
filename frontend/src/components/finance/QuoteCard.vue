<script setup lang="ts">
import type { FinanceQuote } from "@/types";
import {
  formatCurrency,
  formatPercent,
  formatVolume,
  getChangeClass,
} from "@/utils/format";
import { computed } from "vue";

const props = defineProps<{
  quote: FinanceQuote;
  showSparkline?: boolean;
}>();

const changeColorClass = computed(() => {
  const cls = getChangeClass(props.quote.change_percent);
  if (cls === "up") return "change-up";
  if (cls === "down") return "change-down";
  return "change-neutral";
});
</script>

<template>
  <div class="quote-card card">
    <div class="quote-header">
      <div class="symbol-name">
        <span class="symbol">{{ quote.symbol }}</span>
        <span class="name text-truncate">{{ quote.name }}</span>
      </div>
      <div v-if="quote.type" class="quote-type">
        {{ quote.type }}
      </div>
    </div>

    <div class="quote-price">
      <span class="current-price">{{
        formatCurrency(quote.current_price, quote.currency || "USD")
      }}</span>
      <span :class="changeColorClass" class="change-value">
        {{ formatCurrency(quote.change, quote.currency || "USD") }}
      </span>
      <span :class="changeColorClass" class="change-percent">
        {{ formatPercent(quote.change_percent) }}
      </span>
    </div>

    <div class="quote-details">
      <div class="detail-row">
        <div class="detail-item">
          <span class="detail-label">开盘</span>
          <span class="detail-value">{{
            quote.open
              ? formatCurrency(quote.open, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">最高</span>
          <span class="detail-value">{{
            quote.high
              ? formatCurrency(quote.high, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
      </div>
      <div class="detail-row">
        <div class="detail-item">
          <span class="detail-label">最低</span>
          <span class="detail-value">{{
            quote.low
              ? formatCurrency(quote.low, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">昨收</span>
          <span class="detail-value">{{
            quote.close_previous
              ? formatCurrency(quote.close_previous, quote.currency || "USD")
              : "--"
          }}</span>
        </div>
      </div>
    </div>

    <div v-if="quote.volume" class="quote-volume">
      <span class="volume-label">成交量</span>
      <span class="volume-value">{{ formatVolume(quote.volume) }}</span>
    </div>
  </div>
</template>

<style scoped>
.quote-card {
  padding: 16px;
}

.quote-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.symbol-name {
  display: flex;
  align-items: center;
  gap: 8px;
}

.symbol {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
}

.name {
  font-size: 14px;
  color: var(--text-secondary);
  max-width: 200px;
}

.quote-type {
  font-size: 12px;
  color: var(--text-muted);
  padding: 2px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

.quote-price {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.current-price {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
}

.change-value {
  font-size: 14px;
  font-weight: 500;
}

.change-percent {
  font-size: 14px;
  font-weight: 600;
}

.quote-details {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}

.detail-row {
  display: flex;
  gap: 24px;
}

.detail-item {
  display: flex;
  gap: 4px;
}

.detail-label {
  font-size: 12px;
  color: var(--text-muted);
}

.detail-value {
  font-size: 12px;
  color: var(--text-secondary);
}

.quote-volume {
  display: flex;
  gap: 4px;
}

.volume-label {
  font-size: 12px;
  color: var(--text-muted);
}

.volume-value {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
