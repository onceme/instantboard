import { defineStore } from "pinia";
import { ref } from "vue";
import type {
  TechNewsItem,
  TechTopic,
  TechDomain,
  TechSort,
  SSEEventType,
} from "@/types";
import { apiGet } from "@/utils/api";
import { SSEConnection, SSEConnectionState } from "@/utils/sse.ts";
import { useAuthStore } from "./auth";

export const useTechStore = defineStore("tech", () => {
  const newsItems = ref<TechNewsItem[]>([]);
  const topics = ref<TechTopic[]>([]);
  const currentDomain = ref<TechDomain>("all");
  const currentSort = ref<TechSort>("hot");
  const currentSubcategory = ref<string>("");
  const isFeedMode = ref(false);
  const sseConnection = ref<SSEConnection | null>(null);
  const sseState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const currentPage = ref(1);
  const totalPages = ref(0);
  const isLoading = ref(false);

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
    try {
      const params: Record<string, unknown> = {
        page: currentPage.value,
        page_size: 20,
      };

      if (currentDomain.value !== "all") {
        params.topic = currentDomain.value;
      }
      if (currentSubcategory.value) {
        params.subtopic = currentSubcategory.value;
      }
      if (currentSort.value === "time") {
        params.sort_by = "published_at";
      } else if (currentSort.value === "hot") {
        params.sort_by = "priority";
      }

      const response = await apiGet<TechNewsItem[]>("/tech/news", params);
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
    } finally {
      isLoading.value = false;
    }
  }

  async function fetchTopics() {
    const response = await apiGet<TechTopic[]>("/tech/topics");
    topics.value = response.data;
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

  function connectSSE() {
    const authStore = useAuthStore();
    if (sseConnection.value) {
      sseConnection.value.disconnect();
    }

    sseConnection.value = new SSEConnection({
      category: "tech",
      token: authStore.token,
      onStateChange: (state) => {
        sseState.value = state;
      },
      eventHandlers: {
        [SSEEventType.ITEM_UPDATE]: (data) => addItemFromSSE(data as never),
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
    isFeedMode,
    sseState,
    isLoading,
    currentPage,
    totalPages,
    setDomain,
    setSubcategory,
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
