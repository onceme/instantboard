<script setup lang="ts">
import { useTechStore } from "@/stores/tech";
import { useInfiniteScroll } from "@/composables/useInfiniteScroll";
import { computed } from "vue";
import NewsCard from "./NewsCard.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import LoadingSpinner from "@/components/common/LoadingSpinner.vue";

const techStore = useTechStore();

const { containerRef, isLoading } = useInfiniteScroll(async () => {
  await techStore.loadMore();
});

const filteredItems = computed(() => {
  let items = techStore.newsItems;

  if (techStore.currentDomain !== "all") {
    items = items.filter((item) =>
      item.topic_tags.some(
        (tag) =>
          tag === techStore.currentDomain ||
          tag.startsWith(techStore.currentDomain),
      ),
    );
  }

  if (techStore.currentSubcategory) {
    items = items.filter((item) =>
      item.topic_tags.includes(techStore.currentSubcategory),
    );
  }

  return items;
});
</script>

<template>
  <div ref="containerRef" class="news-feed">
    <EmptyState
      v-if="filteredItems.length === 0 && !techStore.isLoading"
      title="暂无新闻"
      description="等待新消息推送"
      icon="news"
    />

    <div class="feed-list">
      <NewsCard v-for="item in filteredItems" :key="item.id" :item="item" />
    </div>

    <LoadingSpinner v-if="isLoading || techStore.isLoading" />

    <div
      v-if="
        filteredItems.length > 0 &&
        techStore.currentPage >= techStore.totalPages
      "
      class="feed-end"
    >
      已显示全部内容
    </div>
  </div>
</template>

<style scoped>
.news-feed {
  max-height: calc(100vh - 200px);
  overflow-y: auto;
}

.feed-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.feed-end {
  text-align: center;
  padding: 16px;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
