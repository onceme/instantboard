import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type {
  FinanceAlert,
  FinanceQuote,
  MarketIndex,
  Commodity,
  FundNAV,
  FundNAVIntraday,
  WatchlistItem,
  WatchlistQuote,
  SearchResult,
  FinancePanel,
} from "@/types";
import { SSEEventType } from "@/types";
import { apiGet, apiPost, apiDelete, getApiErrorMessage } from "@/utils/api";
import { formatPercent } from "@/utils/format";
import { financeApi } from "@/api/finance";
import { SSEConnection, SSEConnectionState } from "@/utils/sse.ts";
import { useAuthStore } from "./auth";
import { useSSEStore } from "./sse";

// Keep only the most recent alerts around for the toast/history display.
const ALERT_HISTORY_LIMIT = 5;

export const useFinanceStore = defineStore("finance", () => {
  const watchlist = ref<WatchlistItem[]>([]);
  const watchlistQuotes = ref<Map<string, WatchlistQuote>>(new Map());
  const marketIndices = ref<MarketIndex[]>([]);
  const commodities = ref<Commodity[]>([]);
  const searchResults = ref<SearchResult[]>([]);
  const quotesCache = ref<Map<string, FinanceQuote>>(new Map());
  const navData = ref<Map<string, FundNAV>>(new Map());
  // Intraday NAV estimates keyed by normalized 6-digit fund code
  // (fund-intraday-nav.md §9): seeded from watchlist/quotes fund_nav fields,
  // refreshed whole-array via SSE nav_batch_update, and fetchable on demand
  // through GET /finance/fund-nav/batch. This is the single source of truth
  // for the Watchlist estimate column and the FundNAV panel.
  const navEstimates = ref<Record<string, FundNAVIntraday>>({});
  // Watchlist price alerts received via SSE alert_update, newest first
  // (finance-tab.md §3.2); capped at ALERT_HISTORY_LIMIT entries.
  const alerts = ref<FinanceAlert[]>([]);

  const currentPanel = ref<FinancePanel>("overview");
  const searchQuery = ref("");
  const sseConnection = ref<SSEConnection | null>(null);
  const sseState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);

  // Per-endpoint error state: on backend 5xx/503 the matching panel shows it via ErrorAlert instead of failing silently
  const marketIndicesError = ref<string | null>(null);
  const commoditiesError = ref<string | null>(null);
  const watchlistError = ref<string | null>(null);
  // Session-level loaded flag: watchlist list + quotes are fetched on demand
  // once; later consumers (Overview re-activation) reuse the data instead of
  // re-requesting. Errors keep it false so the next activation retries.
  const watchlistLoaded = ref(false);
  let watchlistFetchPromise: Promise<void> | null = null;

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
    marketIndicesError.value = null;
    try {
      const response = await apiGet<MarketIndex[]>("/finance/market-indices");
      marketIndices.value = response.data;
    } catch (err) {
      marketIndicesError.value = getApiErrorMessage(
        err,
        "加载市场指数失败，请稍后重试。",
      );
    }
  }

  async function getCommodities() {
    commoditiesError.value = null;
    try {
      const response = await apiGet<Commodity[]>("/finance/commodities");
      commodities.value = response.data;
    } catch (err) {
      commoditiesError.value = getApiErrorMessage(
        err,
        "加载大宗商品失败，请稍后重试。",
      );
    }
  }

  async function getFundNAV(symbol: string) {
    // Routed through the intraday batch endpoint (§9.1): fresh fund_nav_rt
    // values win server-side, misses degrade to latest_official, and missing
    // holdings trigger the lazy ingest hook — one code path for all funds.
    await fetchFundNAVBatch([symbol]);
    return navEstimates.value[symbol] ?? null;
  }

  // On-demand batch load (GET /finance/fund-nav/batch); entries carrying an
  // error marker are skipped so unknown codes never pollute the store.
  async function fetchFundNAVBatch(symbols: string[]) {
    const unique = [...new Set(symbols.filter(Boolean))];
    if (unique.length === 0) return;
    const response = await apiGet<FundNAVIntraday[]>(
      "/finance/fund-nav/batch",
      { symbols: unique.join(",") },
    );
    const next = { ...navEstimates.value };
    for (const entry of response.data ?? []) {
      if (!entry || entry.error) continue;
      next[entry.symbol] = entry;
    }
    navEstimates.value = next;
  }

  async function fetchWatchlist() {
    watchlistError.value = null;
    try {
      const response = await apiGet<WatchlistItem[]>("/finance/watchlist");
      watchlist.value = response.data;

      const quotesResponse = await apiGet<WatchlistQuote[]>(
        "/finance/watchlist/quotes",
      );
      const quotesMap = new Map<string, WatchlistQuote>();
      const estimateSeeds: Record<string, FundNAVIntraday> = {};
      for (const q of quotesResponse.data) {
        quotesMap.set(q.symbol, q);
        // Fund rows carry the intraday estimate snapshot (REST path; SSE
        // nav_batch_update keeps them fresh afterwards, §9.1).
        if (q.fund_nav && !q.fund_nav.error) {
          estimateSeeds[q.symbol] = q.fund_nav;
        }
      }
      watchlistQuotes.value = quotesMap;
      navEstimates.value = { ...navEstimates.value, ...estimateSeeds };
      watchlistLoaded.value = true;
    } catch (err) {
      watchlistLoaded.value = false;
      watchlistError.value = getApiErrorMessage(
        err,
        "加载自选股失败，请稍后重试。",
      );
    }
  }

  // Load watchlist list + quotes exactly once per session; share an in-flight
  // request so Overview activation and init() never double-fetch. Successful
  // loads make later calls no-ops (store data stays live via SSE quote_update
  // and local add/remove mutations).
  function ensureWatchlist(): Promise<void> {
    if (watchlistLoaded.value) {
      return Promise.resolve();
    }
    if (!watchlistFetchPromise) {
      const task = fetchWatchlist().then(() => {
        if (watchlistFetchPromise === task) {
          watchlistFetchPromise = null;
        }
      });
      watchlistFetchPromise = task;
    }
    return watchlistFetchPromise;
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

  // PUT /finance/watchlist/reorder expects WatchlistReorderRequest:
  // {items: [{item_id, display_order}]} — display_order is assigned 0-based
  // in the given order. The backend responds with a plain confirmation
  // message ({message}), so after success the new order is applied locally
  // with the same display_order values the backend persisted. Failures are
  // rethrown untouched so the caller can roll back its optimistic update
  // (finance-tab.md §3.2).
  async function reorderWatchlist(newOrder: WatchlistItem[]) {
    const items = newOrder.map((item, index) => ({
      item_id: item.id,
      display_order: index,
    }));
    const response = await financeApi.reorderWatchlist(items);
    watchlist.value = newOrder.map((item, index) => ({
      ...item,
      display_order: index,
    }));
    return response.data;
  }

  // PATCH the alert threshold of a watchlist entry; null disables the alert.
  // Errors are rethrown — the caller (Watchlist row editor) displays them.
  async function updateWatchlistAlert(
    itemId: string,
    threshold: number | null,
  ) {
    const response = await financeApi.updateWatchlistItem(itemId, {
      alert_threshold_percent: threshold,
    });
    const index = watchlist.value.findIndex((item) => item.id === itemId);
    if (index >= 0) {
      watchlist.value[index] = {
        ...watchlist.value[index],
        alert_threshold_percent: threshold ?? undefined,
      };
    }
    return response.data;
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

  // The backend pushes market_index_update / commodity_update as the WHOLE list
  // (services/finance.py push_event(formatted)), but single-object payloads are
  // accepted too for forward compatibility.
  function updateMarketIndexFromSSE(data: MarketIndex | MarketIndex[]) {
    if (Array.isArray(data)) {
      marketIndices.value = data.map((item) => ({
        symbol: item.symbol,
        name: item.name,
        value: item.value,
        change: item.change,
        change_percent: item.change_percent,
        market_status: item.market_status,
        market_status_reason: item.market_status_reason ?? null,
        holiday_name: item.holiday_name ?? null,
        region: item.region,
        timestamp: item.timestamp,
      }));
      return;
    }
    const index = marketIndices.value.findIndex(
      (i) => i.symbol === data.symbol,
    );
    if (index >= 0) {
      marketIndices.value[index] = data;
    } else {
      marketIndices.value.push(data);
    }
  }

  function updateCommodityFromSSE(data: Commodity | Commodity[]) {
    if (Array.isArray(data)) {
      commodities.value = data.map((item) => ({
        symbol: item.symbol,
        name: item.name,
        value: item.value,
        change: item.change,
        change_percent: item.change_percent,
        unit: item.unit,
        category: item.category,
        timestamp: item.timestamp,
      }));
      return;
    }
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

  // SSE nav_batch_update (fund-intraday-nav.md §8): the payload is the whole
  // tenant-filtered estimate array — merge each entry wholesale by symbol
  // (no field-level merging; each item is a complete state snapshot).
  function updateNAVBatchFromSSE(data: FundNAVIntraday | FundNAVIntraday[]) {
    const items = Array.isArray(data) ? data : [data];
    if (items.length === 0) return;
    const next = { ...navEstimates.value };
    for (const item of items) {
      if (item && item.symbol) {
        next[item.symbol] = item;
      }
    }
    navEstimates.value = next;
  }

  // alert_update payload: push to the front (newest first), cap the history,
  // and mirror the alert to a browser Notification when the tab is hidden.
  function updateAlertFromSSE(data: FinanceAlert) {
    alerts.value = [data, ...alerts.value].slice(0, ALERT_HISTORY_LIMIT);
    notifyBrowser(data);
  }

  function notifyBrowser(alert: FinanceAlert) {
    // Notify only when the tab is hidden AND the permission was already
    // granted. We deliberately never call Notification.requestPermission()
    // here — an unsolicited permission prompt is intrusive; granting stays an
    // explicit user/OS action (finance-tab.md §3.2).
    if (
      typeof document === "undefined" ||
      typeof Notification === "undefined"
    ) {
      return;
    }
    if (
      document.visibilityState !== "hidden" ||
      Notification.permission !== "granted"
    ) {
      return;
    }
    const directionLabel = alert.direction === "up" ? "涨" : "跌";
    try {
      new Notification(`自选${directionLabel}幅提醒 ${alert.symbol}`, {
        body: `${alert.name || alert.symbol} ${formatPercent(alert.change_percent)}（阈值 ${alert.threshold_percent}%）`,
        tag: `finance-alert-${alert.symbol}`,
      });
    } catch (e) {
      // Some environments require a service-worker registration for
      // notifications; the in-app toast already covers the display.
      console.debug("Browser notification failed:", e);
    }
  }

  function connectSSE() {
    const authStore = useAuthStore();
    const sseStore = useSSEStore();
    if (sseConnection.value) {
      sseConnection.value.disconnect();
    }

    sseConnection.value = new SSEConnection({
      category: "finance",
      token: authStore.token,
      onStateChange: (state) => {
        sseState.value = state;
        sseStore.setFinanceState(state);
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
        [SSEEventType.NAV_BATCH_UPDATE]: (data) =>
          updateNAVBatchFromSSE(data as never),
        [SSEEventType.ALERT_UPDATE]: (data) =>
          updateAlertFromSSE(data as never),
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
    // Shares the in-flight request when the Overview panel (child, mounted
    // before this view's onMounted) already triggered ensureWatchlist()
    ensureWatchlist();
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
    navEstimates,
    alerts,
    currentPanel,
    searchQuery,
    sseState,
    marketIndicesError,
    commoditiesError,
    watchlistError,
    watchlistLoaded,
    watchlistTop5,
    setCurrentPanel,
    searchSymbols,
    getQuote,
    getMarketIndices,
    getCommodities,
    getFundNAV,
    fetchWatchlist,
    ensureWatchlist,
    addToWatchlist,
    removeFromWatchlist,
    reorderWatchlist,
    updateWatchlistAlert,
    fetchFundNAVBatch,
    updateNAVBatchFromSSE,
    updateMarketIndexFromSSE,
    updateCommodityFromSSE,
    updateAlertFromSSE,
    connectSSE,
    disconnectSSE,
    init,
    cleanup,
  };
});
