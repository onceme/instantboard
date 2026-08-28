/**
 * FinanceGrid Overview hybrid view (finance-tab.md §3.6.2): the Overview
 * sub-panel stacks three sections top → bottom — watchlist summary (top 5
 * quotes with symbol/price/change%), the existing MarketIndices panel and the
 * reused FinanceNewsPanel. An empty watchlist hides the summary section,
 * 「查看全部」switches to the Watchlist sub-panel, and activating Overview
 * loads the watchlist list + quotes exactly once (re-activation reuses the
 * store data instead of re-requesting).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import type { AxiosResponse } from "axios";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/utils/api";
import { useFinanceStore } from "@/stores/finance";
import type {
  Category,
  MarketIndex,
  TechNewsItem,
  WatchlistItem,
  WatchlistQuote,
} from "@/types";
import FinanceGrid from "@/components/finance/FinanceGrid.vue";

const FINANCE_CATEGORY: Category = {
  id: "c-fin",
  name: "财经",
  slug: "finance",
  type: "finance",
  refresh_interval_seconds: 30,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function makeWatchlistItem(seq: number, symbol: string): WatchlistItem {
  return {
    id: `w-${seq}`,
    symbol,
    name: `股票${seq}`,
    display_order: seq,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeQuote(
  symbol: string,
  price: number,
  changePercent: number,
): WatchlistQuote {
  return {
    symbol,
    name: symbol,
    current_price: price,
    change: Number(((price * changePercent) / 100).toFixed(2)),
    change_percent: changePercent,
    timestamp: "2026-08-26T00:00:00Z",
  };
}

function makeIndex(overrides: Partial<MarketIndex> = {}): MarketIndex {
  return {
    symbol: "000001.SS",
    name: "上证综合指数",
    value: 3100.25,
    change: -8,
    change_percent: -0.26,
    market_status: "closed",
    market_status_reason: null,
    holiday_name: null,
    region: "CN",
    timestamp: "2026-08-26T00:00:00Z",
    ...overrides,
  };
}

function makeNewsItem(seq: number): TechNewsItem {
  const publishedAt = new Date(Date.now() - seq * 30 * 60 * 1000).toISOString();
  return {
    id: `item-${seq}`,
    title: `财经要闻 ${seq}`,
    summary: `Summary ${seq}`,
    url: `https://example.com/finance/${seq}`,
    source_name: `财经源${seq}`,
    source_id: "src-1",
    category_id: "c-fin",
    topic_tags: [],
    published_at: publishedAt,
    fetched_at: publishedAt,
    priority: 5,
  };
}

interface StubOptions {
  watchlist?: WatchlistItem[];
  quotes?: WatchlistQuote[];
  newsItems?: TechNewsItem[];
}

interface CapturedRequest {
  url: string;
}

// Route stub responses by endpoint and capture every request so tests can
// assert the watchlist endpoints are hit exactly once per session load.
function stubApi(options: StubOptions = {}): CapturedRequest[] {
  const requests: CapturedRequest[] = [];
  apiClient.defaults.adapter = async (config) => {
    const url = config.url || "";
    requests.push({ url });

    const ok = (data: unknown): AxiosResponse =>
      ({
        status: 200,
        statusText: "OK",
        data: {
          success: true,
          data,
          meta: {
            total: Array.isArray(data) ? data.length : 1,
            page: 1,
            page_size: 20,
          },
        },
        headers: {},
        config,
      }) as AxiosResponse;

    if (url.includes("/finance/watchlist/quotes")) {
      return ok(options.quotes ?? []);
    }
    if (url.includes("/finance/watchlist")) {
      return ok(options.watchlist ?? []);
    }
    if (url.includes("/items")) {
      return ok(options.newsItems ?? []);
    }
    return ok([FINANCE_CATEGORY]);
  };
  return requests;
}

function setInnerWidth(width: number) {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
}

function mountGrid() {
  return mount(FinanceGrid);
}

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
  // lg viewport: keeps the ≥1440px right panel out of the rendered tree so
  // the spec stays focused on the Overview main column
  setInnerWidth(1280);
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("Overview hybrid view — three sections", () => {
  it("renders watchlist summary, market indices and finance news top to bottom", async () => {
    stubApi({
      watchlist: [makeWatchlistItem(1, "AAPL"), makeWatchlistItem(2, "TSLA")],
      quotes: [makeQuote("AAPL", 150, 1.25), makeQuote("TSLA", 240, -0.8)],
      newsItems: [makeNewsItem(1)],
    });
    const store = useFinanceStore();
    store.marketIndices = [makeIndex()];

    const wrapper = mountGrid();
    await flushPromises();

    const overview = wrapper.find(".finance-overview");
    expect(overview.exists()).toBe(true);

    // Section order: summary → indices → news
    const sectionClasses = [...overview.element.children].map(
      (el) => [...el.classList][0],
    );
    expect(sectionClasses).toEqual([
      "overview-watchlist",
      "market-indices",
      "finance-news",
    ]);

    const summaryItems = wrapper.findAll(".summary-item");
    expect(summaryItems).toHaveLength(2);
    expect(summaryItems[0].find(".item-symbol").text()).toBe("AAPL");
    expect(summaryItems[0].find(".item-price").text()).toBe("$150.00");
    expect(summaryItems[0].find(".item-change").text()).toBe("+1.25%");
    expect(summaryItems[0].find(".item-change").classes()).toContain(
      "change-up",
    );
    expect(summaryItems[1].find(".item-change").classes()).toContain(
      "change-down",
    );

    expect(wrapper.find(".indices-title").text()).toBe("市场指数");
    expect(wrapper.find(".news-title").text()).toBe("财经要闻");
  });

  it("shows at most 5 watchlist quotes in the summary", async () => {
    stubApi({
      watchlist: [
        makeWatchlistItem(1, "S1"),
        makeWatchlistItem(2, "S2"),
        makeWatchlistItem(3, "S3"),
        makeWatchlistItem(4, "S4"),
        makeWatchlistItem(5, "S5"),
        makeWatchlistItem(6, "S6"),
      ],
      quotes: [],
      newsItems: [],
    });

    const wrapper = mountGrid();
    await flushPromises();

    const symbols = wrapper
      .findAll(".summary-item .item-symbol")
      .map((el) => el.text());
    expect(symbols).toEqual(["S1", "S2", "S3", "S4", "S5"]);
  });
});

describe("Overview hybrid view — empty watchlist", () => {
  it("renders only market indices and finance news when the watchlist is empty", async () => {
    stubApi({ watchlist: [], quotes: [], newsItems: [makeNewsItem(1)] });

    const wrapper = mountGrid();
    await flushPromises();

    expect(wrapper.find(".overview-watchlist").exists()).toBe(false);
    expect(wrapper.find(".market-indices").exists()).toBe(true);
    expect(wrapper.find(".finance-news").exists()).toBe(true);
  });
});

describe("Overview hybrid view — 查看全部", () => {
  it("switches to the Watchlist sub-panel", async () => {
    stubApi({
      watchlist: [makeWatchlistItem(1, "AAPL")],
      quotes: [makeQuote("AAPL", 150, 1.25)],
      newsItems: [],
    });
    const store = useFinanceStore();

    const wrapper = mountGrid();
    await flushPromises();

    await wrapper.find(".view-all-link").trigger("click");

    expect(store.currentPanel).toBe("watchlist");
    expect(wrapper.find(".panel-watchlist").exists()).toBe(true);
    expect(wrapper.find(".finance-overview").exists()).toBe(false);
    expect(wrapper.find(".watchlist-title").text()).toBe("我的自选");
  });
});

describe("Overview data loading", () => {
  it("loads watchlist list + quotes once; re-activation does not refetch", async () => {
    const requests = stubApi({
      watchlist: [makeWatchlistItem(1, "AAPL")],
      quotes: [makeQuote("AAPL", 150, 1.25)],
      newsItems: [],
    });

    const wrapper = mountGrid();
    await flushPromises();

    const watchlistCalls = () =>
      requests.filter((r) => r.url === "/finance/watchlist").length;
    const quoteCalls = () =>
      requests.filter((r) => r.url === "/finance/watchlist/quotes").length;
    expect(watchlistCalls()).toBe(1);
    expect(quoteCalls()).toBe(1);
    expect(useFinanceStore().watchlistLoaded).toBe(true);

    // Leave Overview and come back: the remounted panel reuses store data
    const store = useFinanceStore();
    store.setCurrentPanel("indices");
    await wrapper.vm.$nextTick();
    store.setCurrentPanel("overview");
    await wrapper.vm.$nextTick();
    await flushPromises();

    expect(watchlistCalls()).toBe(1);
    expect(quoteCalls()).toBe(1);
    expect(wrapper.find(".overview-watchlist").exists()).toBe(true);
  });

  it("keeps the summary hidden on load failure so the panel degrades to indices + news", async () => {
    apiClient.defaults.adapter = async (config) => {
      const url = config.url || "";
      if (url.includes("/finance/watchlist")) {
        throw Object.assign(new Error("request failed"), {
          isAxiosError: true,
          config,
          response: {
            status: 503,
            statusText: "Service Unavailable",
            data: { detail: "行情服务暂不可用" },
            headers: {},
            config,
          },
        });
      }
      return {
        status: 200,
        statusText: "OK",
        data: {
          success: true,
          data: url.includes("/items") ? [makeNewsItem(1)] : [FINANCE_CATEGORY],
          meta: { total: 1, page: 1, page_size: 20 },
        },
        headers: {},
        config,
      } as AxiosResponse;
    };

    const wrapper = mountGrid();
    await flushPromises();

    expect(wrapper.find(".overview-watchlist").exists()).toBe(false);
    expect(wrapper.find(".market-indices").exists()).toBe(true);
    expect(wrapper.find(".finance-news").exists()).toBe(true);
    expect(useFinanceStore().watchlistLoaded).toBe(false);
  });
});
