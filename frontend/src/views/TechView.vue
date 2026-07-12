<script setup lang="ts">
import { onMounted, onUnmounted, computed } from "vue";
import { useTechStore } from "@/stores/tech";
import TechSubNav from "@/components/tech/TechSubNav.vue";
import CategoryPanel from "@/components/tech/CategoryPanel.vue";
import NewsFeed from "@/components/tech/NewsFeed.vue";
import TopicFilter from "@/components/tech/TopicFilter.vue";

const techStore = useTechStore();

const domains = ["robotics", "ai", "embedded", "space"];
const isFeedMode = computed(() => techStore.isFeedMode);

onMounted(() => {
  techStore.init();
});

onUnmounted(() => {
  techStore.cleanup();
});

function switchToFeedMode() {
  techStore.setFeedMode(true);
}

function switchToGridMode() {
  techStore.setFeedMode(false);
}
</script>

<template>
  <div class="tech-view">
    <TechSubNav />
    <TopicFilter />

    <div class="mode-switch">
      <button
        class="mode-btn"
        :class="{ active: !isFeedMode }"
        @click="switchToGridMode"
      >
        面板模式
      </button>
      <button
        class="mode-btn"
        :class="{ active: isFeedMode }"
        @click="switchToFeedMode"
      >
        合并流
      </button>
    </div>

    <div v-if="!isFeedMode" class="category-grid">
      <CategoryPanel v-for="domain in domains" :key="domain" :domain="domain" />
    </div>

    <div v-else class="feed-mode">
      <NewsFeed />
    </div>
  </div>
</template>

<style scoped>
.tech-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mode-switch {
  display: flex;
  gap: 6px;
}

.mode-btn {
  padding: 6px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--text-secondary);
  background-color: var(--bg-secondary);
  border: 1px solid transparent;
  transition: all var(--transition-fast);
}

.mode-btn.active {
  color: var(--accent);
  border-color: var(--accent);
  background-color: var(--bg-card);
}

.category-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

@media (max-width: 767px) {
  .category-grid {
    grid-template-columns: 1fr;
  }
}
</style>
