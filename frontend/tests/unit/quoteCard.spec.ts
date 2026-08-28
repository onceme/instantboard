/**
 * QuoteCard (finance-tab.md §3.2): the inline search preview card exposes an
 * "add to watchlist" icon action wired to financeStore.addToWatchlist. It
 * renders the star button, POSTs {symbol} on click, transitions to the added
 * (filled + disabled) state on success, treats a backend 409 (duplicate from
 * another session) the same as already-added, disables when the symbol is
 * already in store.watchlist (and stays reactive to it), and shows an inline
 * error hint on failure. Symbol switches reset the per-card action state.
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

import { apiPost } from "@/utils/api";
import QuoteCard from "@/components/finance/QuoteCard.vue";
import { useFinanceStore } from "@/stores/finance";
import type { FinanceQuote } from "@/types";

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
    timestamp: "2026-08-26T10:00:00Z",
    type: "stock",
    ...overrides,
  };
}

function watchlistItem(symbol: string, id = "w1") {
  return {
    id,
    symbol,
    symbol_id: `s-${symbol}`,
    display_order: 0,
    created_at: "2026-08-26T00:00:00Z",
  };
}

const wrappers: VueWrapper[] = [];

function mountCard(props: Record<string, unknown> = {}): VueWrapper {
  const wrapper = mount(QuoteCard, {
    props: { quote: makeQuote(), ...props },
  });
  wrappers.push(wrapper);
  return wrapper;
}

function btn(wrapper: VueWrapper): HTMLButtonElement {
  return wrapper.find(".watchlist-btn").element as HTMLButtonElement;
}

function starSvg(wrapper: VueWrapper): Element | null {
  return wrapper.find(".watchlist-btn svg").element;
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  // Default: adding to the watchlist succeeds.
  mockApiPost.mockResolvedValue({
    success: true,
    data: watchlistItem("AAPL"),
  });
});

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount());
  wrappers.length = 0;
});

describe("add-to-watchlist action", () => {
  it("renders the star button in the idle (not-added) state", () => {
    const wrapper = mountCard();

    const button = btn(wrapper);
    expect(button).not.toBeNull();
    expect(button.getAttribute("aria-label")).toBe("加入自选");
    expect(button.disabled).toBe(false);
    // Idle star is outlined, not filled.
    expect(starSvg(wrapper)!.getAttribute("fill")).toBe("none");
    expect(wrapper.find(".watchlist-hint").exists()).toBe(false);
  });

  it("POSTs {symbol} and switches to the filled/added state on success", async () => {
    const wrapper = mountCard();

    btn(wrapper).click();
    await flushPromises();

    expect(mockApiPost).toHaveBeenCalledWith("/finance/watchlist", {
      symbol: "AAPL",
    });
    const button = btn(wrapper);
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-label")).toBe("已在自选中");
    expect(starSvg(wrapper)!.getAttribute("fill")).toBe("currentColor");
    expect(wrapper.find(".watchlist-hint").text()).toBe("已加入自选");
  });

  it("is disabled and shows 已在自选中 when the symbol is already watched", async () => {
    const store = useFinanceStore();
    store.watchlist = [watchlistItem("AAPL")];

    const wrapper = mountCard();

    const button = btn(wrapper);
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-label")).toBe("已在自选中");
    expect(starSvg(wrapper)!.getAttribute("fill")).toBe("currentColor");

    button.click();
    await flushPromises();
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it("treats a backend 409 (duplicate) as already-in-watchlist", async () => {
    mockApiPost.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409 },
    });
    const wrapper = mountCard();

    btn(wrapper).click();
    await flushPromises();

    const button = btn(wrapper);
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-label")).toBe("已在自选中");
    expect(starSvg(wrapper)!.getAttribute("fill")).toBe("currentColor");
    expect(wrapper.find(".hint-error").exists()).toBe(false);
  });

  it("shows the inline error hint and re-enables the button on failure", async () => {
    mockApiPost.mockRejectedValue(new Error("network down"));
    const wrapper = mountCard();

    btn(wrapper).click();
    await flushPromises();

    const hint = wrapper.find(".watchlist-hint");
    expect(hint.classes()).toContain("hint-error");
    expect(hint.text()).toBe("加入自选失败，请稍后重试。");
    const button = btn(wrapper);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-label")).toBe("加入自选");
  });

  it("stays reactive to the store watchlist (added elsewhere disables it)", async () => {
    const store = useFinanceStore();
    const wrapper = mountCard();
    expect(btn(wrapper).disabled).toBe(false);

    // Simulate the symbol being added through another surface (DetailDrawer).
    store.watchlist = [watchlistItem("AAPL")];
    await flushPromises();

    const button = btn(wrapper);
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-label")).toBe("已在自选中");
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it("resets the action state when the previewed symbol changes", async () => {
    const wrapper = mountCard();
    btn(wrapper).click();
    await flushPromises();
    expect(btn(wrapper).getAttribute("aria-label")).toBe("已在自选中");

    await wrapper.setProps({
      quote: makeQuote({ symbol: "MSFT", name: "Microsoft" }),
    });
    await flushPromises();

    const button = btn(wrapper);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-label")).toBe("加入自选");
    expect(wrapper.find(".watchlist-hint").exists()).toBe(false);
  });
});
