import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type {
  FinanceQuote,
  MarketIndex,
  Commodity,
  FundNAV,
  WatchlistItem,
  WatchlistQuote,
  SearchResult,
  FinancePanel,
  SSEEventType,
} from "@/types";
import { apiGet, apiPost, apiDelete, apiPut } from "@/utils/api";
import { SSEConnection, SSEConnectionState } from "@/utils/sse.ts";
import { useAuthStore } from "./auth";

export const useFinanceStore = defineStore("finance", () => {
  const watchlist = ref<WatchlistItem[]>([]);
  const watchlistQuotes = ref<Map<string, WatchlistQuote>>(new Map());
  const marketIndices = ref<MarketIndex[]>([]);
  const commodities = ref<Commodity[]>([]);
  const searchResults = ref<SearchResult[]>([]);
  const quotesCache = ref<Map<string, FinanceQuote>>(new Map());
  const navData = ref<Map<string, FundNAV>>(new Map());

  const currentPanel = ref<FinancePanel>("overview");
  const searchQuery = ref("");
  const sseConnection = ref<SSEConnection | null>(null);
  const sseState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);

  const watchlistTop5 = computed(() => {
    const sorted = [...watchlist.value].sort(
      (a, b) => a.display_order - b.display_order,
    );
    return sorted.slice(0, 5).map((item) => ({
      ...item,
      quote: watchlistQuotes.value.get(item.symbol),
    }));
  });

  function setCurrentPanel(panel: FinancePanel) {
    currentPanel.value = panel;
  }

  async function searchSymbols(query: string, type: string = "all") {
    if (!query.trim()) {
      searchResults.value = [];
      return;
    }
    searchQuery.value = query;
    const response = await apiGet<SearchResult[]>("/finance/search", {
      q: query,
      type,
    });
    searchResults.value = response.data;
  }

  async function getQuote(symbol: string) {
    const response = await apiGet<FinanceQuote>(`/finance/quote/${symbol}`);
    quotesCache.value.set(symbol, response.data);
    return response.data;
  }

  async function getMarketIndices() {
    const response = await apiGet<MarketIndex[]>("/finance/market-indices");
    marketIndices.value = response.data;
  }

  async function getCommodities() {
    const response = await apiGet<Commodity[]>("/finance/commodities");
    commodities.value = response.data;
  }

  async function getFundNAV(symbol: string) {
    const response = await apiGet<FundNAV>(`/finance/fund/${symbol}/nav`);
    navData.value.set(symbol, response.data);
    return response.data;
  }

  async function fetchWatchlist() {
    const response = await apiGet<WatchlistItem[]>("/finance/watchlist");
    watchlist.value = response.data;

    const quotesResponse = await apiGet<WatchlistQuote[]>(
      "/finance/watchlist/quotes",
    );
    const quotesMap = new Map<string, WatchlistQuote>();
    for (const q of quotesResponse.data) {
      quotesMap.set(q.symbol, q);
    }
    watchlistQuotes.value = quotesMap;
  }

  async function addToWatchlist(symbol: string) {
    const response = await apiPost<WatchlistItem>("/finance/watchlist", {
      symbol,
    });
    watchlist.value.push(response.data);
  }

  async function removeFromWatchlist(itemId: string) {
    await apiDelete(`/finance/watchlist/${itemId}`);
    watchlist.value = watchlist.value.filter((item) => item.id !== itemId);
  }

  async function reorderWatchlist(reorderedIds: string[]) {
    await apiPut("/finance/watchlist/reorder", { item_ids: reorderedIds });
    const reordered = reorderedIds
      .map((id) => watchlist.value.find((item) => item.id === id))
      .filter(Boolean) as WatchlistItem[];
    watchlist.value = reordered;
  }

  function updateQuoteFromSSE(data: {
    symbol: string;
    current_price: number;
    change: number;
    change_percent: number;
    volume?: number;
    timestamp: string;
  }) {
    const existing = watchlistQuotes.value.get(data.symbol);
    if (existing) {
      watchlistQuotes.value.set(data.symbol, {
        ...existing,
        current_price: data.current_price,
        change: data.change,
        change_percent: data.change_percent,
        volume: data.volume,
        timestamp: data.timestamp,
      });
    }

    const cached = quotesCache.value.get(data.symbol);
    if (cached) {
      quotesCache.value.set(data.symbol, {
        ...cached,
        current_price: data.current_price,
        change: data.change,
        change_percent: data.change_percent,
        volume: data.volume,
        timestamp: data.timestamp,
      });
    }
  }

  function updateMarketIndexFromSSE(data: MarketIndex) {
    const index = marketIndices.value.findIndex(
      (i) => i.symbol === data.symbol,
    );
    if (index >= 0) {
      marketIndices.value[index] = data;
    } else {
      marketIndices.value.push(data);
    }
  }

  function updateCommodityFromSSE(data: Commodity) {
    const index = commodities.value.findIndex((c) => c.symbol === data.symbol);
    if (index >= 0) {
      commodities.value[index] = data;
    } else {
      commodities.value.push(data);
    }
  }

  function updateNAVFromSSE(data: FundNAV & { symbol: string }) {
    navData.value.set(data.symbol, data);
  }

  function connectSSE() {
    const authStore = useAuthStore();
    if (sseConnection.value) {
      sseConnection.value.disconnect();
    }

    sseConnection.value = new SSEConnection({
      category: "finance",
      token: authStore.token,
      onStateChange: (state) => {
        sseState.value = state;
      },
      eventHandlers: {
        [SSEEventType.QUOTE_UPDATE]: (data) =>
          updateQuoteFromSSE(data as never),
        [SSEEventType.MARKET_INDEX_UPDATE]: (data) =>
          updateMarketIndexFromSSE(data as never),
        [SSEEventType.COMMODITY_UPDATE]: (data) =>
          updateCommodityFromSSE(data as never),
        [SSEEventType.NAV_ESTIMATE_UPDATE]: (data) =>
          updateNAVFromSSE(data as never),
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

  function init() {
    fetchWatchlist();
    getMarketIndices();
    getCommodities();
    connectSSE();
  }

  function cleanup() {
    disconnectSSE();
  }

  return {
    watchlist,
    watchlistQuotes,
    marketIndices,
    commodities,
    searchResults,
    quotesCache,
    navData,
    currentPanel,
    searchQuery,
    sseState,
    watchlistTop5,
    setCurrentPanel,
    searchSymbols,
    getQuote,
    getMarketIndices,
    getCommodities,
    getFundNAV,
    fetchWatchlist,
    addToWatchlist,
    removeFromWatchlist,
    reorderWatchlist,
    connectSSE,
    disconnectSSE,
    init,
    cleanup,
  };
});
