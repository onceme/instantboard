import { defineStore } from "pinia";
import { ref } from "vue";
import type { TechNewsItem, TechTopic, TechDomain, TechSort } from "@/types";
import { SSEEventType } from "@/types";
import { techApi } from "@/api/tech";
import type { TechNewsParams } from "@/api/tech";
import { getApiErrorMessage } from "@/utils/api";
import { SSEConnection, SSEConnectionState } from "@/utils/sse.ts";
import { useAuthStore } from "./auth";
import { useSSEStore } from "./sse";

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

  function addItemFromSSE(data: TechNewsItem) {
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
    setDomain,
    setSubcategory,
    setTag,
    setSort,
    setFeedMode,
    fetchNews,
    fetchTopics,
    loadMore,
    connectSSE,
    disconnectSSE,
    init,
    cleanup,
  };
});
