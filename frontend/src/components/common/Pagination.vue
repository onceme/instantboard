<script setup lang="ts">
import { computed } from "vue";
import { ChevronLeft, ChevronRight } from "lucide-vue-next";

const props = defineProps<{
  page: number;
  pageSize: number;
  total: number;
}>();

defineEmits<{
  "update:page": [page: number];
}>();

type PageItem = number | "left-ellipsis" | "right-ellipsis";

const totalPages = computed(() => {
  if (props.pageSize <= 0) return 1;
  return Math.max(1, Math.ceil(props.total / props.pageSize));
});

// First and last pages are always rendered; when there are more than 7 pages
// the middle collapses into ellipses around a 3-page window of the current page.
const pageItems = computed<PageItem[]>(() => {
  const tp = totalPages.value;
  if (tp <= 7) {
    return Array.from({ length: tp }, (_, i) => i + 1);
  }

  const current = props.page;
  let start = current - 1;
  let end = current + 1;
  if (start < 2) {
    start = 2;
    end = 4;
  }
  if (end > tp - 1) {
    end = tp - 1;
    start = tp - 3;
  }
  start = Math.max(2, start);
  end = Math.min(tp - 1, end);

  const items: PageItem[] = [1];
  if (start > 3) {
    items.push("left-ellipsis");
  } else {
    for (let p = 2; p < start; p++) items.push(p);
  }
  for (let p = start; p <= end; p++) items.push(p);
  if (end < tp - 2) {
    items.push("right-ellipsis");
  } else {
    for (let p = end + 1; p < tp; p++) items.push(p);
  }
  items.push(tp);
  return items;
});

defineExpose({ totalPages, pageItems });
</script>

<template>
  <nav class="pagination" aria-label="分页">
    <button
      class="nav-btn nav-prev"
      :disabled="page <= 1"
      aria-label="上一页"
      @click="$emit('update:page', page - 1)"
    >
      <ChevronLeft :size="16" />
    </button>

    <template v-for="item in pageItems" :key="item">
      <button
        v-if="typeof item === 'number'"
        class="page-btn"
        :class="{ active: item === page }"
        :disabled="item === page"
        :aria-current="item === page ? 'page' : undefined"
        @click="$emit('update:page', item)"
      >
        {{ item }}
      </button>
      <span v-else class="page-ellipsis">…</span>
    </template>

    <button
      class="nav-btn nav-next"
      :disabled="page >= totalPages"
      aria-label="下一页"
      @click="$emit('update:page', page + 1)"
    >
      <ChevronRight :size="16" />
    </button>
  </nav>
</template>

<style scoped>
.pagination {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.page-btn,
.nav-btn {
  min-width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 6px;
  font-size: 13px;
  color: var(--text-secondary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  transition:
    color var(--transition-fast),
    border-color var(--transition-fast),
    background-color var(--transition-fast);
}

.page-btn:hover:not(:disabled),
.nav-btn:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent);
}

.page-btn.active {
  color: #fff;
  background-color: var(--accent);
  border-color: var(--accent);
  cursor: default;
}

.page-btn:disabled:not(.active),
.nav-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.page-ellipsis {
  min-width: 24px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: var(--text-muted);
  user-select: none;
}
</style>
