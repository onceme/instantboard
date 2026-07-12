<script setup lang="ts">
import { useTechStore } from "@/stores/tech";
import { DOMAIN_CONFIG, SUBCATEGORY_MAP } from "@/types";
import { computed } from "vue";

const techStore = useTechStore();

const allTags = computed(() => {
  const tags: Array<{ tag: string; domain: string; label: string }> = [];

  for (const [domainKey, subcats] of Object.entries(SUBCATEGORY_MAP)) {
    for (const sub of subcats) {
      tags.push({
        tag: sub.slug,
        domain: domainKey,
        label: sub.label,
      });
    }
  }

  return tags;
});

function toggleTag(tag: string) {
  if (techStore.currentSubcategory === tag) {
    techStore.setSubcategory("");
  } else {
    techStore.setSubcategory(tag);
  }
}

function clearAll() {
  techStore.setDomain("all");
  techStore.setSubcategory("");
}

function tagColor(domain: string): string {
  if (DOMAIN_CONFIG[domain]) {
    return `var(--domain-${domain})`;
  }
  return "var(--accent)";
}
</script>

<template>
  <div class="topic-filter">
    <button class="filter-btn clear-btn" @click="clearAll">全部</button>

    <div class="filter-scroll">
      <button
        v-for="t in allTags"
        :key="t.tag"
        class="filter-btn"
        :class="{ active: techStore.currentSubcategory === t.tag }"
        :style="{ '--tag-color': tagColor(t.domain) }"
        @click="toggleTag(t.tag)"
      >
        {{ t.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.topic-filter {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}

.clear-btn {
  padding: 4px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--text-secondary);
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  transition: all var(--transition-fast);
  white-space: nowrap;
  flex-shrink: 0;
}

.clear-btn:hover {
  color: var(--text-primary);
}

.filter-scroll {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  flex: 1;
}

.filter-btn {
  padding: 4px 10px;
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--text-secondary);
  background-color: var(--bg-secondary);
  border: 1px solid transparent;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.filter-btn:hover {
  color: var(--text-primary);
}

.filter-btn.active {
  color: white;
  background-color: var(--tag-color);
  border-color: var(--tag-color);
}
</style>
