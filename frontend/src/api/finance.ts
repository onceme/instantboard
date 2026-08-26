import { apiGet, apiPost, apiDelete, apiPatch, apiPut } from "@/utils/api";
import type {
  FinanceQuote,
  MarketIndex,
  Commodity,
  FundNAV,
  SearchResult,
  WatchlistItem,
  WatchlistQuote,
} from "@/types";

export const financeApi = {
  search: (q: string, type?: string, market?: string) =>
    apiGet<SearchResult[]>("/finance/search", { q, type, market }),
  quote: (symbol: string, detailLevel?: string) =>
    apiGet<FinanceQuote>(`/finance/quote/${symbol}`, {
      detail_level: detailLevel,
    }),
  marketIndices: () => apiGet<MarketIndex[]>("/finance/market-indices"),
  commodities: () => apiGet<Commodity[]>("/finance/commodities"),
  fundNAV: (symbol: string, estimateType?: string) =>
    apiGet<FundNAV>(`/finance/fund/${symbol}/nav`, {
      estimate_type: estimateType,
    }),
  watchlist: () => apiGet<WatchlistItem[]>("/finance/watchlist"),
  watchlistQuotes: () => apiGet<WatchlistQuote[]>("/finance/watchlist/quotes"),
  addToWatchlist: (symbol: string) =>
    apiPost<WatchlistItem>("/finance/watchlist", { symbol }),
  // PATCH /finance/watchlist/{item_id}: null clears the alert threshold
  // (disables the alert); the backend validates the 0.5-50 range (400).
  updateWatchlistItem: (
    itemId: string,
    payload: { alert_threshold_percent: number | null },
  ) =>
    apiPatch<WatchlistItem>(
      `/finance/watchlist/${itemId}`,
      payload as Record<string, unknown>,
    ),
  removeFromWatchlist: (itemId: string) =>
    apiDelete(`/finance/watchlist/${itemId}`),
  reorderWatchlist: (itemIds: string[]) =>
    apiPut("/finance/watchlist/reorder", { item_ids: itemIds }),
};
