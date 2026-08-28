<script setup lang="ts">
import { onMounted, ref } from "vue";
import { categoriesApi } from "@/api/categories";
import { useSettingsStore } from "@/stores/settings";
import { getApiErrorMessage } from "@/utils/api";
import { formatRelativeTime } from "@/utils/format";
import type { TechNewsItem } from "@/types";
import ErrorAlert from "@/components/common/ErrorAlert.vue";

const TOP_NEWS_COUNT = 5;

const settingsStore = useSettingsStore();

const items = ref<TechNewsItem[]>([]);
const isLoading = ref(false);
const error = ref<string | null>(null);

async function resolveFinanceCategoryId(): Promise<string | null> {
  if (settingsStore.categories.length === 0) {
    await settingsStore.fetchCategories();
  }
  const category = settingsStore.categories.find((c) => c.slug === "finance");
  return category?.id ?? null;
}

async function fetchNews() {
  isLoading.value = true;
  error.value = null;
  try {
    const categoryId = await resolveFinanceCategoryId();
    if (!categoryId) {
      items.value = [];
      return;
    }
    const response = await categoriesApi.listItems(categoryId, {
      sort: "time",
      page_size: TOP_NEWS_COUNT,
    });
    items.value = response.data;
  } catch (err) {
    error.value = getApiErrorMessage(err, "加载财经要闻失败，请稍后重试。");
  } finally {
    isLoading.value = false;
  }
}

onMounted(fetchNews);
</script>

<template>
  <!-- Empty choice: after a successful load with zero items the whole panel
       renders nothing (same convention as HotTopics'「无数据不渲染」), so the
       right column never shows a dead "暂无要闻" card. Errors keep the panel
       visible so the inline retry stays reachable. -->
  <div v-if="error || isLoading || items.length > 0" class="finance-news">
    <div class="news-header">
      <h3 class="news-title">财经要闻</h3>
    </div>

    <ErrorAlert v-if="error" :message="error" retryable @retry="fetchNews" />

    <div v-else-if="isLoading" class="news-list" aria-busy="true">
      <div
        v-for="n in TOP_NEWS_COUNT"
        :key="n"
        class="news-item news-item-skeleton"
      >
        <span class="skeleton-line skeleton-title" />
        <span class="skeleton-line skeleton-meta" />
      </div>
    </div>

    <div v-else class="news-list">
      <div v-for="item in items" :key="item.id" class="news-item">
        <a
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="news-link"
          :title="item.title"
        >
          {{ item.title }}
        </a>
        <div class="news-meta">
          <span class="news-source">{{ item.source_name ?? "未知来源" }}</span>
          <span class="news-time">{{
            formatRelativeTime(item.published_at)
          }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.finance-news {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 12px;
}

.news-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.news-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.news-list {
  display: flex;
  flex-direction: column;
}

.news-item {
  padding: 6px 0;
  border-bottom: 1px solid var(--border-light);
}

.news-item:last-child {
  border-bottom: none;
}

.news-link {
  display: -webkit-box;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  color: var(--text-primary);
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  transition: color var(--transition-fast);
}

.news-link:hover {
  color: var(--accent);
}

.news-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-muted);
}

.news-source {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.news-time {
  flex-shrink: 0;
}

.news-item-skeleton {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skeleton-line {
  border-radius: var(--radius-sm);
  background-color: var(--bg-secondary);
  animation: finance-news-pulse 1.2s ease-in-out infinite;
}

.skeleton-title {
  height: 14px;
  width: 90%;
}

.skeleton-meta {
  height: 10px;
  width: 50%;
}

@keyframes finance-news-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}
</style>
