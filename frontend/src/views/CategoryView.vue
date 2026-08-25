<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { categoriesApi } from "@/api/categories";
import { useSettingsStore } from "@/stores/settings";
import { useInfiniteScroll } from "@/composables/useInfiniteScroll";
import { getApiErrorMessage } from "@/utils/api";
import type { Category, TechNewsItem } from "@/types";
import { resolveCategoryIcon } from "@/utils/categoryIcons";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorAlert from "@/components/common/ErrorAlert.vue";
import LoadingSpinner from "@/components/common/LoadingSpinner.vue";
// Card choice: NewsCard over MessageCard — the backend returns the exact tech
// news item shape and NewsCard's only tech coupling is DOMAIN_CONFIG-based
// stripe/tag coloring, which gracefully falls back to the accent color for
// unknown tags, so custom-category items render correctly without changes.
import NewsCard from "@/components/tech/NewsCard.vue";

const PAGE_SIZE = 20;

const route = useRoute();
const settingsStore = useSettingsStore();

const slug = computed(() => String(route.params.slug ?? ""));

// Navigation entries are only generated for custom categories, so resolve the
// slug against those only (predefined finance/tech have dedicated views)
const category = computed<Category | undefined>(() =>
  settingsStore.categories.find(
    (c) => c.type === "custom" && c.slug === slug.value,
  ),
);

const items = ref<TechNewsItem[]>([]);
const currentPage = ref(1);
const totalPages = ref(1);
const isLoading = ref(false);
const isResolving = ref(false);
const error = ref<string | null>(null);

async function loadCategories() {
  if (settingsStore.categories.length > 0) return;
  isResolving.value = true;
  try {
    await settingsStore.fetchCategories();
  } catch (err) {
    error.value = getApiErrorMessage(err, "加载分类列表失败，请稍后重试。");
  } finally {
    isResolving.value = false;
  }
}

async function fetchItems(page: number) {
  const current = category.value;
  if (!current) return;
  isLoading.value = true;
  error.value = null;
  try {
    const response = await categoriesApi.listItems(current.id, {
      page,
      page_size: PAGE_SIZE,
      sort: "time",
    });
    if (page === 1) {
      items.value = response.data;
    } else {
      items.value.push(...response.data);
    }
    currentPage.value = page;
    if (response.meta) {
      totalPages.value = Math.max(
        1,
        Math.ceil(response.meta.total / response.meta.page_size),
      );
    }
  } catch (err) {
    error.value = getApiErrorMessage(err, "加载分类内容失败，请稍后重试。");
  } finally {
    isLoading.value = false;
  }
}

async function init() {
  items.value = [];
  currentPage.value = 1;
  totalPages.value = 1;
  error.value = null;
  await loadCategories();
  if (category.value) {
    await fetchItems(1);
  }
}

async function loadMore() {
  if (!category.value) return;
  if (error.value) return;
  if (currentPage.value >= totalPages.value) return;
  await fetchItems(currentPage.value + 1);
}

// The scroll container div renders unconditionally so the listener attached in
// useInfiniteScroll's onMounted always finds it (it would miss a container that
// only appears after the async category resolution)
const { containerRef, isLoading: isAppending } = useInfiniteScroll(loadMore);

onMounted(init);

// The component instance is reused when switching between two /c/:slug routes
watch(slug, (next, prev) => {
  if (next !== prev) init();
});
</script>

<template>
  <div class="category-view">
    <div ref="containerRef" class="category-feed">
      <ErrorAlert v-if="error" :message="error" retryable @retry="init" />

      <LoadingSpinner v-if="isResolving || (isLoading && items.length === 0)" />

      <EmptyState
        v-else-if="!category"
        title="分类不存在"
        description="未找到该自定义分类，请检查设置中的分类配置"
        icon="folder"
      />

      <template v-else>
        <header class="category-header">
          <component
            :is="resolveCategoryIcon(category.icon)"
            :size="20"
            class="category-icon"
          />
          <div class="category-heading">
            <h2 class="category-name">
              {{ category.name }}
            </h2>
            <p v-if="category.description" class="category-desc">
              {{ category.description }}
            </p>
          </div>
        </header>

        <EmptyState
          v-if="items.length === 0 && !isLoading && !error"
          title="暂无内容"
          description="为该分类添加数据源后，采集到的内容将显示在这里"
          icon="inbox"
        />

        <div class="feed-list">
          <NewsCard v-for="item in items" :key="item.id" :item="item" />
        </div>

        <LoadingSpinner v-if="isAppending" />

        <div
          v-if="items.length > 0 && currentPage >= totalPages && !error"
          class="feed-end"
        >
          已显示全部内容
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.category-view {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.category-feed {
  max-height: calc(100vh - 200px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.category-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 2px 8px;
}

.category-icon {
  color: var(--accent);
  flex-shrink: 0;
  margin-top: 2px;
}

.category-heading {
  min-width: 0;
}

.category-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.category-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 2px;
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
