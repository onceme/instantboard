/**
 * DetailDrawer (finance-tab.md §3.1): renders quote fields with up/down
 * coloring, draws the 5-day SVG sparkline from quote.history (placeholder
 * below 2 points), drives the watchlist button through its states
 * (idle/added/already-in-watchlist/409/failure), refreshes live from
 * quotesCache (SSE merge path), and closes via close button, overlay click
 * and Escape.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    apiGet: vi.fn(),
    apiPost: vi.fn(),
    apiPut: vi.fn(),
    apiDelete: vi.fn(),
  };
});

import { apiGet, apiPost } from "@/utils/api";
import DetailDrawer from "@/components/finance/DetailDrawer.vue";
import { useFinanceStore } from "@/stores/finance";
import type { FinanceQuote } from "@/types";

const mockApiGet = vi.mocked(apiGet);
const mockApiPost = vi.mocked(apiPost);

function makeQuote(overrides: Partial<FinanceQuote> = {}): FinanceQuote {
  return {
    symbol: "AAPL",
    name: "Apple Inc",
    current_price: 180,
    open: 178,
    high: 182,
    low: 177.5,
    close_previous: 179,
    change: 1,
    change_percent: 0.56,
    market_cap: 2800000000000,
    pe_ratio: 28.5,
    timestamp: "2026-08-26T10:00:00Z",
    history: [
      { time: "2026-08-20T20:00:00Z", close: 175 },
      { time: "2026-08-21T20:00:00Z", close: 178 },
      { time: "2026-08-24T20:00:00Z", close: 180 },
    ],
    ...overrides,
  };
}

const wrappers: VueWrapper[] = [];

// The drawer fetches on open via an immediate watcher, so the apiGet mock
// must be armed BEFORE mounting.
function mountDrawer(
  options: {
    props?: Record<string, unknown>;
    quote?: FinanceQuote;
    quoteError?: unknown;
  } = {},
): VueWrapper {
  if (options.quoteError !== undefined) {
    mockApiGet.mockRejectedValue(options.quoteError);
  } else {
    mockApiGet.mockResolvedValue({
      success: true,
      data: options.quote ?? makeQuote(),
    });
  }
  const wrapper = mount(DetailDrawer, {
    props: { visible: true, symbol: "AAPL", ...options.props },
  });
  wrappers.push(wrapper);
  return wrapper;
}

function drawer(): HTMLElement | null {
  return document.body.querySelector(".drawer");
}

function overlay(): HTMLElement | null {
  return document.body.querySelector(".drawer-overlay");
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount());
  wrappers.length = 0;
  document.body.innerHTML = "";
});

describe("field rendering", () => {
  it("renders nothing while hidden", () => {
    mountDrawer({ props: { visible: false } });
    expect(overlay()).toBeNull();
  });

  it("renders symbol, name, price, change and day-range fields", async () => {
    mountDrawer();
    await flushPromises();

    const root = drawer()!;
    expect(root.textContent).toContain("AAPL");
    expect(root.textContent).toContain("Apple Inc");
    expect(root.textContent).toContain("$180.00");
    expect(root.textContent).toContain("+0.56%");
    expect(root.querySelector(".change-percent")!.classList).toContain(
      "change-up",
    );

    const rows = [...root.querySelectorAll(".detail-row")].map(
      (row) => row.textContent,
    );
    expect(rows).toEqual(
      expect.arrayContaining([
        expect.stringContaining("今开"),
        expect.stringContaining("最高"),
        expect.stringContaining("最低"),
        expect.stringContaining("昨收"),
        expect.stringContaining("市值"),
        expect.stringContaining("市盈率"),
      ]),
    );
    expect(root.textContent).toContain("2.80T");
    expect(root.textContent).toContain("28.50");
  });

  it("omits market cap and pe rows when absent", async () => {
    mountDrawer({
      quote: makeQuote({ market_cap: undefined, pe_ratio: undefined }),
    });
    await flushPromises();

    const labels = [...drawer()!.querySelectorAll(".detail-label")].map(
      (el) => el.textContent,
    );
    expect(labels).not.toContain("市值");
    expect(labels).not.toContain("市盈率");
  });

  it("uses change-down coloring for a falling quote", async () => {
    mountDrawer({ quote: makeQuote({ change_percent: -1.2, change: -2 }) });
    await flushPromises();

    expect(
      drawer()!.querySelector(".change-percent")!.classList,
    ).toContain("change-down");
  });

  it("shows the load error message when the quote fetch fails", async () => {
    mountDrawer({ quoteError: new Error("503 upstream") });
    await flushPromises();

    const status = drawer()!.querySelector(".drawer-status-error");
    expect(status).not.toBeNull();
    expect(status!.textContent).toContain("加载行情失败");
  });
});

describe("sparkline", () => {
  it("draws a polyline with trend coloring when history has >= 2 points", async () => {
    mountDrawer();
    await flushPromises();

    const line = drawer()!.querySelector(".sparkline-line");
    expect(line).not.toBeNull();
    expect(line!.getAttribute("points")).toMatch(/^\d+\.\d+,\d+\.\d+/);
    // rising series (175 → 180) gets the up color
    expect(line!.classList).toContain("spark-up");
    expect(drawer()!.querySelector(".sparkline-empty")).toBeNull();
  });

  it("colors a falling series with the down class", async () => {
    mountDrawer({
      quote: makeQuote({
        history: [
          { time: "2026-08-20T20:00:00Z", close: 180 },
          { time: "2026-08-21T20:00:00Z", close: 175 },
        ],
      }),
    });
    await flushPromises();

    const line = drawer()!.querySelector(".sparkline-line");
    expect(line!.classList).toContain("spark-down");
  });

  it("shows the placeholder when history has fewer than 2 points", async () => {
    mountDrawer({
      quote: makeQuote({
        history: [{ time: "2026-08-20T20:00:00Z", close: 175 }],
      }),
    });
    await flushPromises();

    expect(drawer()!.querySelector(".sparkline-line")).toBeNull();
    expect(drawer()!.querySelector(".sparkline-empty")!.textContent).toContain(
      "暂无近 5 日走势数据",
    );
  });

  it("shows the placeholder when history is missing entirely", async () => {
    mountDrawer({ quote: makeQuote({ history: [] }) });
    await flushPromises();

    expect(drawer()!.querySelector(".sparkline-empty")).not.toBeNull();
  });
});

describe("watchlist action", () => {
  it("calls addToWatchlist with {symbol} and switches to the added state", async () => {
    mockApiPost.mockResolvedValue({
      success: true,
      data: {
        id: "w1",
        symbol: "AAPL",
        symbol_id: "s1",
        display_order: 0,
      },
    });
    mountDrawer();
    await flushPromises();

    const btn = drawer()!.querySelector(
      ".btn-watchlist",
    ) as HTMLButtonElement;
    expect(btn.textContent).toBe("加入自选");
    btn.click();
    await flushPromises();

    expect(mockApiPost).toHaveBeenCalledWith("/finance/watchlist", {
      symbol: "AAPL",
    });
    expect(btn.textContent).toBe("已在自选中");
    expect(btn.disabled).toBe(true);
    expect(drawer()!.querySelector(".watchlist-hint")!.textContent).toBe(
      "已加入自选",
    );
  });

  it("renders a disabled button when the symbol is already in the watchlist", async () => {
    const store = useFinanceStore();
    store.watchlist = [
      {
        id: "w1",
        symbol: "AAPL",
        display_order: 0,
        created_at: "2026-08-26T00:00:00Z",
      },
    ];
    mountDrawer();
    await flushPromises();

    const btn = drawer()!.querySelector(
      ".btn-watchlist",
    ) as HTMLButtonElement;
    expect(btn.textContent).toBe("已在自选中");
    expect(btn.disabled).toBe(true);

    btn.click();
    await flushPromises();
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it("treats a backend 409 (duplicate) as already-in-watchlist", async () => {
    mockApiPost.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409 },
    });
    mountDrawer();
    await flushPromises();

    const btn = drawer()!.querySelector(
      ".btn-watchlist",
    ) as HTMLButtonElement;
    btn.click();
    await flushPromises();

    expect(btn.textContent).toBe("已在自选中");
    expect(btn.disabled).toBe(true);
    expect(drawer()!.querySelector(".hint-error")).toBeNull();
  });

  it("shows the error message and re-enables the button on failure", async () => {
    mockApiPost.mockRejectedValue(new Error("network down"));
    mountDrawer();
    await flushPromises();

    const btn = drawer()!.querySelector(
      ".btn-watchlist",
    ) as HTMLButtonElement;
    btn.click();
    await flushPromises();

    const hint = drawer()!.querySelector(".watchlist-hint")!;
    expect(hint.classList).toContain("hint-error");
    expect(hint.textContent).toBe("加入自选失败，请稍后重试。");
    expect(btn.textContent).toBe("加入自选");
    expect(btn.disabled).toBe(false);
  });
});

describe("SSE linkage via quotesCache", () => {
  it("reflects store quote_update merges while open", async () => {
    const store = useFinanceStore();
    mountDrawer();
    await flushPromises();
    expect(drawer()!.textContent).toContain("$180.00");

    // Same path the SSE quote_update handler takes (updateQuoteFromSSE)
    store.quotesCache.set("AAPL", {
      ...store.quotesCache.get("AAPL")!,
      current_price: 185.5,
      change: 6.5,
      change_percent: 3.63,
    });
    await flushPromises();

    expect(drawer()!.textContent).toContain("$185.50");
    expect(drawer()!.textContent).toContain("+3.63%");
  });
});

describe("close interactions", () => {
  it("emits update:visible false when the close button is clicked", async () => {
    const wrapper = mountDrawer();
    await flushPromises();

    (drawer()!.querySelector(".drawer-close") as HTMLButtonElement).click();
    await flushPromises();

    expect(wrapper.emitted("update:visible")).toEqual([[false]]);
  });

  it("closes on overlay click but not when clicking inside the drawer", async () => {
    const wrapper = mountDrawer();
    await flushPromises();

    drawer()!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("update:visible")).toBeUndefined();

    overlay()!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("update:visible")).toEqual([[false]]);
  });

  it("closes on Escape but ignores Escape while hidden", async () => {
    const wrapper = mountDrawer();
    await flushPromises();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(wrapper.emitted("update:visible")).toEqual([[false]]);

    const hidden = mountDrawer({ props: { visible: false } });
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(hidden.emitted("update:visible")).toBeUndefined();
  });

  it("focuses the close button when opened", async () => {
    mountDrawer();
    await flushPromises();

    expect(document.activeElement).toBe(
      document.body.querySelector(".drawer-close"),
    );
  });
});
