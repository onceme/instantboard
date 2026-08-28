/**
 * Watchlist per-row alert-threshold editor (finance-tab.md §3.2): the bell
 * opens an inline editor prefilled with the current threshold; saving PATCHes
 * the number via store.updateWatchlistAlert, an empty input disables the
 * alert (null), out-of-range values fail client-side without a call, backend
 * rejections surface inline, and 关闭提醒 sends null.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import Watchlist from "@/components/finance/Watchlist.vue";
import { useFinanceStore } from "@/stores/finance";
import type { WatchlistItem } from "@/types";

function makeItem(overrides: Partial<WatchlistItem> = {}): WatchlistItem {
  return {
    id: "w1",
    symbol: "AAPL",
    name: "Apple Inc",
    display_order: 0,
    alert_threshold_percent: undefined,
    created_at: "2026-08-26T00:00:00Z",
    ...overrides,
  };
}

async function mountWatchlist(items: WatchlistItem[]) {
  const store = useFinanceStore();
  store.watchlist = items;
  store.updateWatchlistAlert = vi.fn().mockResolvedValue({});
  const wrapper = mount(Watchlist);
  return { store, wrapper };
}

describe("Watchlist alert-threshold editor", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("renders a bell per row and marks rows that already have a threshold", async () => {
    const { wrapper } = await mountWatchlist([
      makeItem(),
      makeItem({
        id: "w2",
        symbol: "TSLA",
        display_order: 1,
        alert_threshold_percent: 3,
      }),
    ]);

    const bells = wrapper.findAll(".alert-btn");
    expect(bells).toHaveLength(2);
    expect(bells[0].classes()).not.toContain("has-alert");
    expect(bells[1].classes()).toContain("has-alert");
    expect(wrapper.find(".threshold-editor").exists()).toBe(false);
  });

  it("opens the editor on bell click and prefills the current threshold", async () => {
    const { wrapper } = await mountWatchlist([
      makeItem({ alert_threshold_percent: 3 }),
    ]);

    await wrapper.find(".alert-btn").trigger("click");

    const editor = wrapper.find(".threshold-editor");
    expect(editor.exists()).toBe(true);
    const input = editor.find(".threshold-input");
    expect((input.element as HTMLInputElement).value).toBe("3");
    // Existing threshold offers the disable shortcut
    expect(editor.find(".editor-disable").exists()).toBe(true);
  });

  it("saves a valid threshold, closes the editor and shows a success state", async () => {
    const { store, wrapper } = await mountWatchlist([makeItem()]);

    await wrapper.find(".alert-btn").trigger("click");
    await wrapper.find(".threshold-input").setValue("2.5");
    await wrapper.find('.editor-btn[title="保存"]').trigger("click");
    await flushPromises();

    expect(store.updateWatchlistAlert).toHaveBeenCalledWith("w1", 2.5);
    expect(wrapper.find(".threshold-editor").exists()).toBe(false);
    expect(wrapper.find(".threshold-success").text()).toContain("已保存");
  });

  it("saves null when the input is cleared (disables the alert)", async () => {
    const { store, wrapper } = await mountWatchlist([
      makeItem({ alert_threshold_percent: 3 }),
    ]);

    await wrapper.find(".alert-btn").trigger("click");
    await wrapper.find(".threshold-input").setValue("");
    await wrapper.find('.editor-btn[title="保存"]').trigger("click");
    await flushPromises();

    expect(store.updateWatchlistAlert).toHaveBeenCalledWith("w1", null);
    expect(wrapper.find(".threshold-editor").exists()).toBe(false);
  });

  it("rejects out-of-range values inline without calling the API", async () => {
    const { store, wrapper } = await mountWatchlist([makeItem()]);

    await wrapper.find(".alert-btn").trigger("click");
    await wrapper.find(".threshold-input").setValue("60");
    await wrapper.find('.editor-btn[title="保存"]').trigger("click");
    await flushPromises();

    expect(store.updateWatchlistAlert).not.toHaveBeenCalled();
    expect(wrapper.find(".editor-error").text()).toContain("0.5-50");
    // Editor stays open for correction
    expect(wrapper.find(".threshold-editor").exists()).toBe(true);
  });

  it("shows the backend error inline when the PATCH is rejected", async () => {
    const { store, wrapper } = await mountWatchlist([makeItem()]);
    (
      store.updateWatchlistAlert as ReturnType<typeof vi.fn>
    ).mockRejectedValueOnce(new Error("400"));

    await wrapper.find(".alert-btn").trigger("click");
    await wrapper.find(".threshold-input").setValue("2");
    await wrapper.find('.editor-btn[title="保存"]').trigger("click");
    await flushPromises();

    expect(wrapper.find(".editor-error").text()).toBe(
      "保存阈值失败，请稍后重试",
    );
    expect(wrapper.find(".threshold-editor").exists()).toBe(true);
  });

  it("disables the alert via 关闭提醒 with null", async () => {
    const { store, wrapper } = await mountWatchlist([
      makeItem({ alert_threshold_percent: 4 }),
    ]);

    await wrapper.find(".alert-btn").trigger("click");
    await wrapper.find(".editor-disable").trigger("click");
    await flushPromises();

    expect(store.updateWatchlistAlert).toHaveBeenCalledWith("w1", null);
    expect(wrapper.find(".threshold-editor").exists()).toBe(false);
  });

  it("closes the editor on cancel without saving", async () => {
    const { store, wrapper } = await mountWatchlist([makeItem()]);

    await wrapper.find(".alert-btn").trigger("click");
    await wrapper.find(".threshold-input").setValue("5");
    await wrapper.find('.editor-btn[title="取消"]').trigger("click");

    expect(store.updateWatchlistAlert).not.toHaveBeenCalled();
    expect(wrapper.find(".threshold-editor").exists()).toBe(false);
  });
});
