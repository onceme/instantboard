/**
 * Watchlist drag & drop reorder (finance-tab.md §3.2): the desktop path is
 * native HTML5 DnD gated on the drag handle (handle mousedown → dragstart →
 * dragover → drop) with optimistic reorder, rollback + inline error on save
 * failure; the mobile fallback is the per-row move-up/move-down buttons.
 * Reordering is only enabled with >= 2 entries.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import type { ComponentPublicInstance } from "vue";

import Watchlist from "@/components/finance/Watchlist.vue";
import { useFinanceStore } from "@/stores/finance";
import type { WatchlistItem } from "@/types";

type Wrapper = VueWrapper<ComponentPublicInstance>;

// Firefox-style requirement: the component only calls setData when present;
// a plain stub keeps the tests independent from happy-dom's DataTransfer.
const dataTransfer = { effectAllowed: "", setData: () => {} };

function makeItem(overrides: Partial<WatchlistItem> = {}): WatchlistItem {
  return {
    id: "w1",
    symbol: "AAPL",
    name: "Apple Inc",
    display_order: 0,
    created_at: "2026-08-26T00:00:00Z",
    ...overrides,
  };
}

function mountWatchlist(items: WatchlistItem[]) {
  const store = useFinanceStore();
  store.watchlist = items.map((item) => ({ ...item }));
  store.reorderWatchlist = vi.fn().mockResolvedValue({ message: "ok" });
  store.removeFromWatchlist = vi.fn().mockResolvedValue(undefined);
  const wrapper = mount(Watchlist);
  return { store, wrapper };
}

// jsdom/happy-dom report an all-zero rect; give the target row a real one so
// the before/after midpoint check is deterministic.
function mockRowRect(row: Wrapper, top: number, height = 40) {
  row.element.getBoundingClientRect = () =>
    ({
      top,
      bottom: top + height,
      height,
      left: 0,
      right: 200,
      width: 200,
      x: 0,
      y: top,
      toJSON: () => "",
    }) as DOMRect;
}

async function startDragFrom(wrapper: Wrapper, fromIndex: number) {
  const rows = wrapper.findAll(".watchlist-row");
  await rows[fromIndex].find(".drag-handle").trigger("mousedown");
  await rows[fromIndex].trigger("dragstart", { dataTransfer });
}

function reorderCallIds(store: ReturnType<typeof useFinanceStore>) {
  const mock = store.reorderWatchlist as ReturnType<typeof vi.fn>;
  return mock.mock.calls.map((call) =>
    (call[0] as WatchlistItem[]).map((item) => item.id),
  );
}

function rowSymbols(wrapper: Wrapper) {
  return wrapper.findAll(".item-symbol").map((node) => node.text());
}

describe("Watchlist drag & drop reorder", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("drags from the handle and drops below a row: optimistic reorder + store call", async () => {
    const { store, wrapper } = mountWatchlist([
      makeItem({ id: "w1", symbol: "AAPL", display_order: 0 }),
      makeItem({ id: "w2", symbol: "TSLA", display_order: 1 }),
      makeItem({ id: "w3", symbol: "MSFT", display_order: 2 }),
    ]);

    await startDragFrom(wrapper, 0);
    const rows = wrapper.findAll(".watchlist-row");
    // Row being dragged is marked; no drop indicator yet
    expect(rows[0].classes()).toContain("dragging");

    mockRowRect(rows[2], 80); // row C spans y 80-120, midpoint 100
    await rows[2].trigger("dragover", { clientY: 110 }); // lower half → after
    expect(wrapper.findAll(".watchlist-row")[2].classes()).toContain(
      "drop-after",
    );

    await rows[2].trigger("drop", { clientY: 110 });
    await flushPromises();

    expect(store.reorderWatchlist).toHaveBeenCalledTimes(1);
    expect(reorderCallIds(store)).toEqual([["w2", "w3", "w1"]]);
    // Optimistic update applied and renumbered 0-based
    expect(store.watchlist.map((item) => item.id)).toEqual(["w2", "w3", "w1"]);
    expect(store.watchlist.map((item) => item.display_order)).toEqual([
      0, 1, 2,
    ]);
    expect(rowSymbols(wrapper)).toEqual(["TSLA", "MSFT", "AAPL"]);
    // Drag state cleared after the drop
    expect(wrapper.find(".watchlist-row.dragging").exists()).toBe(false);
    expect(wrapper.find(".drop-after").exists()).toBe(false);
    expect(wrapper.find(".reorder-error").exists()).toBe(false);
  });

  it("dropping onto the upper half inserts before the target row", async () => {
    const { store, wrapper } = mountWatchlist([
      makeItem({ id: "w1", symbol: "AAPL", display_order: 0 }),
      makeItem({ id: "w2", symbol: "TSLA", display_order: 1 }),
      makeItem({ id: "w3", symbol: "MSFT", display_order: 2 }),
    ]);

    await startDragFrom(wrapper, 2); // drag C
    const rows = wrapper.findAll(".watchlist-row");
    mockRowRect(rows[0], 0); // row A spans y 0-40, midpoint 20
    await rows[0].trigger("dragover", { clientY: 5 }); // upper half → before
    expect(wrapper.findAll(".watchlist-row")[0].classes()).toContain(
      "drop-before",
    );
    await rows[0].trigger("drop", { clientY: 5 });
    await flushPromises();

    expect(reorderCallIds(store)).toEqual([["w3", "w1", "w2"]]);
    expect(rowSymbols(wrapper)).toEqual(["MSFT", "AAPL", "TSLA"]);
  });

  it("a drop that keeps the original order issues no request", async () => {
    const { store, wrapper } = mountWatchlist([
      makeItem({ id: "w1", symbol: "AAPL", display_order: 0 }),
      makeItem({ id: "w2", symbol: "TSLA", display_order: 1 }),
    ]);

    await startDragFrom(wrapper, 0); // drag A ...
    const rows = wrapper.findAll(".watchlist-row");
    mockRowRect(rows[1], 40);
    await rows[1].trigger("dragover", { clientY: 45 }); // ... above B's midpoint
    await rows[1].trigger("drop", { clientY: 45 }); // → still [A, B]
    await flushPromises();

    expect(store.reorderWatchlist).not.toHaveBeenCalled();
    expect(wrapper.find(".reorder-error").exists()).toBe(false);
  });

  it("rolls back to the pre-drag snapshot and shows an inline error on save failure", async () => {
    const { store, wrapper } = mountWatchlist([
      makeItem({ id: "w1", symbol: "AAPL", display_order: 0 }),
      makeItem({ id: "w2", symbol: "TSLA", display_order: 1 }),
      makeItem({ id: "w3", symbol: "MSFT", display_order: 2 }),
    ]);
    (store.reorderWatchlist as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("503 upstream"),
    );

    await startDragFrom(wrapper, 0);
    const rows = wrapper.findAll(".watchlist-row");
    mockRowRect(rows[2], 80);
    await rows[2].trigger("dragover", { clientY: 110 });
    await rows[2].trigger("drop", { clientY: 110 });
    await flushPromises();

    // Rolled back to the pre-drag order (list and rendered rows)
    expect(store.watchlist.map((item) => item.id)).toEqual(["w1", "w2", "w3"]);
    expect(rowSymbols(wrapper)).toEqual(["AAPL", "TSLA", "MSFT"]);
    // Inline hint offers retry-by-dragging-again or refreshing
    expect(wrapper.find(".reorder-error").text()).toContain("排序保存失败");
    expect(wrapper.find(".reorder-error").text()).toContain("刷新页面");
  });

  it("does not enable drag or move actions with a single item", async () => {
    const { store, wrapper } = mountWatchlist([makeItem()]);

    const row = wrapper.find(".watchlist-row");
    expect(row.attributes("draggable")).toBe("false");
    expect(wrapper.find(".drag-handle").classes()).toContain(
      "handle-disabled",
    );

    const moveButtons = wrapper.findAll(".move-btn");
    expect(moveButtons).toHaveLength(2);
    for (const button of moveButtons) {
      expect(button.attributes("disabled")).toBeDefined();
    }
    // Even a forced click (bypassing the disabled state) must not reorder
    await moveButtons[1].trigger("click");
    expect(store.reorderWatchlist).not.toHaveBeenCalled();
  });

  it("move-up/move-down buttons reorder rows (mobile fallback)", async () => {
    const { store, wrapper } = mountWatchlist([
      makeItem({ id: "w1", symbol: "AAPL", display_order: 0 }),
      makeItem({ id: "w2", symbol: "TSLA", display_order: 1 }),
      makeItem({ id: "w3", symbol: "MSFT", display_order: 2 }),
    ]);

    const firstRowButtons = wrapper
      .findAll(".watchlist-row")[0]
      .findAll(".move-btn");
    expect(firstRowButtons[0].attributes("disabled")).toBeDefined(); // 上移
    expect(firstRowButtons[1].attributes("disabled")).toBeUndefined(); // 下移

    await firstRowButtons[1].trigger("click"); // A down → [B, A, C]
    await flushPromises();
    expect(reorderCallIds(store)).toEqual([["w2", "w1", "w3"]]);
    expect(rowSymbols(wrapper)).toEqual(["TSLA", "AAPL", "MSFT"]);
    expect(store.watchlist.map((item) => item.display_order)).toEqual([
      0, 1, 2,
    ]);

    // The last row's 下移 is disabled; its 上移 moves C into the middle
    const lastRowButtons = wrapper
      .findAll(".watchlist-row")[2]
      .findAll(".move-btn");
    expect(lastRowButtons[1].attributes("disabled")).toBeDefined();
    await lastRowButtons[0].trigger("click"); // C up → [B, C, A]
    await flushPromises();
    expect(reorderCallIds(store)).toEqual([
      ["w2", "w1", "w3"],
      ["w2", "w3", "w1"],
    ]);
    expect(rowSymbols(wrapper)).toEqual(["TSLA", "MSFT", "AAPL"]);
  });

  it("disables bell/remove row actions during a drag and restores them after dragend", async () => {
    const { store, wrapper } = mountWatchlist([
      makeItem({ id: "w1", symbol: "AAPL", display_order: 0 }),
      makeItem({ id: "w2", symbol: "TSLA", display_order: 1 }),
    ]);

    await startDragFrom(wrapper, 0);

    expect(wrapper.find(".alert-btn").attributes("disabled")).toBeDefined();
    expect(wrapper.find(".remove-btn").attributes("disabled")).toBeDefined();
    await wrapper.find(".alert-btn").trigger("click");
    expect(wrapper.find(".threshold-editor").exists()).toBe(false);
    await wrapper.find(".remove-btn").trigger("click");
    expect(store.removeFromWatchlist).not.toHaveBeenCalled();

    await wrapper.find(".watchlist-row").trigger("dragend");
    expect(wrapper.find(".alert-btn").attributes("disabled")).toBeUndefined();
    await wrapper.find(".alert-btn").trigger("click");
    expect(wrapper.find(".threshold-editor").exists()).toBe(true);
  });
});
