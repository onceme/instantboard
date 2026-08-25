<script setup lang="ts">
import { computed } from "vue";
import { formatRelativeTime } from "@/utils/format";

const props = defineProps<{
  title: string;
  summary?: string;
  source?: string;
  timestamp?: string | number | Date;
  url?: string;
}>();

// Valid dates are shown as relative time; unparseable values are shown as-is.
const displayTime = computed(() => {
  const ts = props.timestamp;
  if (ts === undefined || ts === null || ts === "") return "";
  const parsed = new Date(ts);
  if (Number.isNaN(parsed.getTime())) return String(ts);
  return formatRelativeTime(parsed.toISOString());
});

const hasMeta = computed(
  () => Boolean(props.source) || displayTime.value !== "",
);
</script>

<template>
  <article class="message-card">
    <a
      v-if="url"
      :href="url"
      target="_blank"
      rel="noopener noreferrer"
      class="card-title"
    >
      {{ title }}
    </a>
    <h3 v-else class="card-title card-title-static">
      {{ title }}
    </h3>

    <p v-if="summary" class="card-summary line-clamp-3">
      {{ summary }}
    </p>

    <slot />

    <footer v-if="hasMeta || $slots.footer" class="card-footer">
      <div v-if="hasMeta" class="card-meta">
        <span v-if="source" class="card-source">{{ source }}</span>
        <span v-if="displayTime" class="card-time">{{ displayTime }}</span>
      </div>
      <div v-if="$slots.footer" class="card-actions">
        <slot name="footer" />
      </div>
    </footer>
  </article>
</template>

<style scoped>
.message-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  transition: box-shadow var(--transition-normal);
}

.message-card:hover {
  box-shadow: var(--shadow-sm);
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text-primary);
  text-decoration: none;
}

a.card-title:hover {
  color: var(--accent);
}

.card-summary {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 2px;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.card-source {
  font-size: 12px;
  color: var(--text-muted);
}

.card-time {
  font-size: 12px;
  color: var(--text-muted);
}

.card-actions {
  flex-shrink: 0;
}
</style>
