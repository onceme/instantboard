<script setup lang="ts">
import { useTechStore } from "@/stores/tech";
import { computed } from "vue";

const techStore = useTechStore();

const HOT_TAGS_LIMIT = 12;

// Backend /tech/topics already orders by count DESC; re-sort defensively and
// cap at the top N so the row stays compact.
const hotTags = computed(() =>
  [...techStore.topics]
    .sort((a, b) => b.count - a.count)
    .slice(0, HOT_TAGS_LIMIT),
);
</script>

<template>
  <!-- No topic data → render nothing (empty state is hidden by design) -->
  <div
    v-if="techStore.topicsLoading && hotTags.length === 0"
    class="hot-topics"
    aria-busy="true"
  >
    <span v-for="n in 6" :key="n" class="skeleton-tag" />
  </div>

  <div v-else-if="hotTags.length > 0" class="hot-topics">
    <span class="hot-topics-title">热门话题</span>
    <button
      v-for="topic in hotTags"
      :key="topic.tag"
      class="hot-tag"
      :class="{ active: techStore.activeTag === topic.tag }"
      :aria-pressed="techStore.activeTag === topic.tag"
      :title="topic.tag"
      @click="techStore.setTag(topic.tag)"
    >
      {{ topic.label }}
      <span class="tag-count">{{ topic.count }}</span>
    </button>
  </div>
</template>

<style scoped>
.hot-topics {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.hot-topics-title {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.hot-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--text-secondary);
  background-color: var(--bg-secondary);
  border: 1px solid transparent;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.hot-tag:hover {
  color: var(--text-primary);
}

.hot-tag.active {
  color: white;
  background-color: var(--accent);
  border-color: var(--accent);
}

.hot-tag.active .tag-count {
  background-color: rgb(255 255 255 / 25%);
  color: white;
}

.tag-count {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  line-height: 14px;
  color: var(--text-muted);
  background-color: var(--bg-hover);
}

.skeleton-tag {
  width: 64px;
  height: 22px;
  border-radius: var(--radius-md);
  background-color: var(--bg-secondary);
  animation: hot-tag-pulse 1.2s ease-in-out infinite;
}

@keyframes hot-tag-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}
</style>
