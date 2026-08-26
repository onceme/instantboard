import { defineStore } from "pinia";
import { computed, ref } from "vue";
import type { TechNewsItem, TechTopic, TechDomain, TechSort } from "@/types";
import { SSEEventType } from "@/types";
import { techApi } from "@/api/tech";
import type { TechNewsParams } from "@/api/tech";
import { getApiErrorMessage } from "@/utils/api";
import { SSEConnection, SSEConnectionState } from "@/utils/sse.ts";
import { useAuthStore } from "./auth";
import { useSSEStore } from "./sse";

export const SEARCH_PAGE_SIZE = 20;

export const useTechStore = defineStore("tech", () => {
  const newsItems = ref<TechNewsItem[]>([]);
  const topics = ref<TechTopic[]>([]);
  const currentDomain = ref<TechDomain>("all");
  const currentSort = ref<TechSort>("hot");
  const currentSubcategory = ref<string>("");
  // Hot topic tag filter (HotTopics.vue): sent as the `tag` query param and
  // stacks with domain/subcategory (backend JSONB containment).
  const activeTag = ref<string>("");
  const isFeedMode = ref(false);
  const sseConnection = ref<SSEConnection | null>(null);
  const sseState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const currentPage = ref(1);
  const totalPages = ref(0);
  const isLoading = ref(false);
  const topicsLoading = ref(false);
  // Error message on request failure, rendered by views via ErrorAlert (distinct from the "No news" empty state)
  const error = ref<string | null>(null);

  // Keyword search state (GET /tech/search, tech-tab.md §3.7). searchQuery is
  // bound to common/SearchBar.vue via v-model; while it is non-blank the view
  // shows searchResults (paged with common/Pagination) instead of the feed.
  const searchQuery = ref("");
  const searchResults = ref<TechNewsItem[]>([]);
  const searchTotal = ref(0);
  const searchPage = ref(1);
  const searchLoading = ref(false);
  const searchError = ref<string | null>(null);
  // Guards against out-of-order responses: only the newest search() may write state.
  let searchSeq = 0;
  // Last query that actually ran a request; a different query restarts on page 1.
  let lastSearchedQuery = "";

  function setDomain(domain: TechDomain) {
    currentDomain.value = domain;
    currentSubcategory.value = "";
    currentPage.value = 1;
    fetchNews();
  }

  function setSubcategory(subcategory: string) {
    currentSubcategory.value = subcategory;
    currentPage.value = 1;
    fetchNews();
  }

  function setTag(tag: string) {
    // Clicking the active tag again clears the filter (toggle semantics)
    activeTag.value = activeTag.value === tag ? "" : tag;
    currentPage.value = 1;
    fetchNews();
  }

  function setSort(sort: TechSort) {
    currentSort.value = sort;
    currentPage.value = 1;
    fetchNews();
  }

  function setFeedMode(mode: boolean) {
    isFeedMode.value = mode;
    currentPage.value = 1;
    fetchNews();
  }

  async function fetchNews() {
    isLoading.value = true;
    // Clear the previous error on retry
    error.value = null;
    try {
      const params: TechNewsParams = {
        page: currentPage.value,
        page_size: 20,
        // Align with backend params: domain/subcategory/sort (the backend doesn't recognize topic/subtopic/sort_by)
        sort: currentSort.value,
      };

      if (currentDomain.value !== "all") {
        params.domain = currentDomain.value;
      }
      if (currentSubcategory.value) {
        params.subcategory = currentSubcategory.value;
      }
      if (activeTag.value) {
        params.tag = activeTag.value;
      }

      const response = await techApi.news(params);
      if (currentPage.value === 1) {
        newsItems.value = response.data;
      } else {
        newsItems.value.push(...response.data);
      }
      if (response.meta) {
        totalPages.value = Math.ceil(
          response.meta.total / response.meta.page_size,
        );
      }
    } catch (err) {
      // Record the error for display in views instead of silently showing an empty list on backend 5xx
      error.value = getApiErrorMessage(err, "加载科技新闻失败，请稍后重试。");
    } finally {
      isLoading.value = false;
    }
  }

  async function fetchTopics() {
    topicsLoading.value = true;
    try {
      const response = await techApi.topics();
      topics.value = response.data;
    } catch (err) {
      // Topic loading failures are also written to error for display in views
      error.value = getApiErrorMessage(err, "加载话题失败，请稍后重试。");
    } finally {
      topicsLoading.value = false;
    }
  }

  const isSearchActive = computed(() => searchQuery.value.trim() !== "");

  async function search(query: string) {
    const q = query.trim();
    searchQuery.value = query;

    if (!q) {
      // Blank query: cancel any in-flight request and back to the regular feed,
      // without sending a request (the backend would reject it with 400 anyway).
      searchSeq++;
      lastSearchedQuery = "";
      searchResults.value = [];
      searchTotal.value = 0;
      searchPage.value = 1;
      searchLoading.value = false;
      searchError.value = null;
      return;
    }

    // A fresh query restarts on page 1; re-running the same query (pagination,
    // retry) keeps the current page.
    if (q !== lastSearchedQuery) {
      searchPage.value = 1;
    }
    lastSearchedQuery = q;

    const seq = ++searchSeq;
    searchLoading.value = true;
    searchError.value = null;
    try {
      const response = await techApi.search({
        q,
        page: searchPage.value,
        page_size: SEARCH_PAGE_SIZE,
      });
      if (seq !== searchSeq) return;
      searchResults.value = response.data;
      searchTotal.value = response.meta?.total ?? 0;
    } catch (err) {
      if (seq !== searchSeq) return;
      searchError.value = getApiErrorMessage(
        err,
        "搜索科技新闻失败，请稍后重试。",
      );
    } finally {
      if (seq === searchSeq) {
        searchLoading.value = false;
      }
    }
  }

  function setSearchPage(page: number) {
    searchPage.value = page;
    void search(searchQuery.value);
  }

  function retrySearch() {
    void search(searchQuery.value);
  }

  function clearSearch() {
    searchQuery.value = "";
    void search("");
  }

  function addItemFromSSE(data: TechNewsItem) {
    // SSE items only ever feed newsItems; searchResults is a paged snapshot from
    // GET /tech/search and is never mutated here, so live items cannot leak into
    // an active search result set.
    const exists = newsItems.value.some((item) => item.id === data.id);
    if (!exists) {
      if (currentSort.value === "hot") {
        newsItems.value.unshift(data);
      } else {
        newsItems.value.push(data);
      }
    }
  }

  function applyTopicStats(data: unknown) {
    // topic_stats_update payload is the full topics array (same structure as the
    // GET /tech/topics `data` field); replace the local list wholesale so counts
    // and ordering match the backend without a REST round-trip.
    if (Array.isArray(data)) {
      topics.value = data as TechTopic[];
    }
  }

  function connectSSE() {
    const authStore = useAuthStore();
    const sseStore = useSSEStore();
    if (sseConnection.value) {
      sseConnection.value.disconnect();
    }

    sseConnection.value = new SSEConnection({
      category: "tech",
      token: authStore.token,
      onStateChange: (state) => {
        sseState.value = state;
        sseStore.setTechState(state);
      },
      eventHandlers: {
        [SSEEventType.ITEM_UPDATE]: (data) => addItemFromSSE(data as never),
        [SSEEventType.TOPIC_STATS_UPDATE]: (data) => applyTopicStats(data),
      },
    });

    sseConnection.value.connect();
  }

  function disconnectSSE() {
    if (sseConnection.value) {
      sseConnection.value.disconnect();
      sseConnection.value = null;
    }
  }

  function loadMore() {
    if (currentPage.value < totalPages.value) {
      currentPage.value++;
      fetchNews();
    }
  }

  function init() {
    fetchNews();
    fetchTopics();
    connectSSE();
  }

  function cleanup() {
    disconnectSSE();
  }

  return {
    newsItems,
    topics,
    currentDomain,
    currentSort,
    currentSubcategory,
    activeTag,
    isFeedMode,
    sseState,
    isLoading,
    topicsLoading,
    error,
    currentPage,
    totalPages,
    searchQuery,
    searchResults,
    searchTotal,
    searchPage,
    searchLoading,
    searchError,
    isSearchActive,
    setDomain,
    setSubcategory,
    setTag,
    setSort,
    setFeedMode,
    fetchNews,
    fetchTopics,
    search,
    setSearchPage,
    retrySearch,
    clearSearch,
    loadMore,
    connectSSE,
    disconnectSSE,
    init,
    cleanup,
  };
});
