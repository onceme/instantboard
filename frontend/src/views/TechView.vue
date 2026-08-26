<script setup lang="ts">
import { onMounted, onUnmounted, computed } from "vue";
import { useTechStore, SEARCH_PAGE_SIZE } from "@/stores/tech";
import TechSubNav from "@/components/tech/TechSubNav.vue";
import CategoryPanel from "@/components/tech/CategoryPanel.vue";
import NewsFeed from "@/components/tech/NewsFeed.vue";
import NewsCard from "@/components/tech/NewsCard.vue";
import TopicFilter from "@/components/tech/TopicFilter.vue";
import HotTopics from "@/components/tech/HotTopics.vue";
import ErrorAlert from "@/components/common/ErrorAlert.vue";
import SearchBar from "@/components/common/SearchBar.vue";
import Pagination from "@/components/common/Pagination.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import LoadingSpinner from "@/components/common/LoadingSpinner.vue";

const techStore = useTechStore();

const domains = ["robotics", "ai", "embedded", "space"];
const isFeedMode = computed(() => techStore.isFeedMode);
const searchTotalPages = computed(() =>
  Math.max(1, Math.ceil(techStore.searchTotal / SEARCH_PAGE_SIZE)),
);

onMounted(() => {
  techStore.init();
});

onUnmounted(() => {
  techStore.cleanup();
});

// Retry button for the error alert: refetch data (news and topics share the error field, so both need refetching)
function retryFetch() {
  techStore.fetchNews();
  techStore.fetchTopics();
}

function switchToFeedMode() {
  techStore.setFeedMode(true);
}

function switchToGridMode() {
  techStore.setFeedMode(false);
}

function onSearch(query: string) {
  void techStore.search(query);
}
</script>

<template>
  <div class="tech-view">
    <TechSubNav />
    <SearchBar
      v-model="techStore.searchQuery"
      :loading="techStore.searchLoading"
      placeholder="搜索科技新闻…"
      @search="onSearch"
    />
    <TopicFilter />
    <HotTopics />

    <ErrorAlert
      v-if="techStore.error"
      :message="techStore.error"
      retryable
      @retry="retryFetch"
    />

    <!-- Search mode: a non-blank query swaps the whole content area from the
         panel/feed views to the paged search results (GET /tech/search). -->
    <div v-if="techStore.isSearchActive" class="search-results">
      <div class="search-summary">
        <span class="search-summary-text">
          搜索 “{{ techStore.searchQuery.trim() }}” ·
          {{ techStore.searchTotal }} 条结果
        </span>
        <button
          type="button"
          class="search-reset-btn"
          @click="techStore.clearSearch()"
        >
          清除
        </button>
      </div>

      <ErrorAlert
        v-if="techStore.searchError"
        :message="techStore.searchError"
        retryable
        @retry="techStore.retrySearch()"
      />

      <LoadingSpinner
        v-else-if="
          techStore.searchLoading && techStore.searchResults.length === 0
        "
      />

      <template v-else>
        <EmptyState
          v-if="techStore.searchResults.length === 0"
          title="未找到相关新闻"
          description="换个关键词试试"
          icon="news"
        />

        <div v-else class="search-list">
          <NewsCard
            v-for="item in techStore.searchResults"
            :key="item.id"
            :item="item"
          />
        </div>

        <Pagination
          v-if="searchTotalPages > 1"
          class="search-pagination"
          :page="techStore.searchPage"
          :page-size="SEARCH_PAGE_SIZE"
          :total="techStore.searchTotal"
          @update:page="techStore.setSearchPage"
        />
      </template>
    </div>

    <template v-else>
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
        <CategoryPanel
          v-for="domain in domains"
          :key="domain"
          :domain="domain"
        />
      </div>

      <div v-else class="feed-mode">
        <NewsFeed />
      </div>
    </template>
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
  /* minmax(0, 1fr) instead of bare 1fr: grid items default to min-width auto,
     so long titles could otherwise widen the track past the container and
     create page-level horizontal overflow */
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.search-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.search-summary-text {
  font-size: 13px;
  color: var(--text-secondary);
}

.search-reset-btn {
  padding: 4px 10px;
  font-size: 12px;
  color: var(--text-secondary);
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
}

.search-reset-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.search-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.search-pagination {
  justify-content: center;
}

@media (max-width: 767px) {
  .category-grid {
    grid-template-columns: 1fr;
  }
}
</style>
