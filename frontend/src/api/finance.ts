import { apiGet, apiPost, apiDelete, apiPut } from "@/utils/api";
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
  removeFromWatchlist: (itemId: string) =>
    apiDelete(`/finance/watchlist/${itemId}`),
  reorderWatchlist: (itemIds: string[]) =>
    apiPut("/finance/watchlist/reorder", { item_ids: itemIds }),
};
