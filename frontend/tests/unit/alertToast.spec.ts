/**
 * AlertToast (finance-tab.md §3.2): renders the newest watchlist alert as a
 * floating hint (direction arrow + symbol + change% + threshold), follows the
 * change-up/change-down color scheme and auto-hides after 8 seconds; a newer
 * alert restarts the visibility window.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import AlertToast from "@/components/finance/AlertToast.vue";
import type { FinanceAlert } from "@/types";

function makeAlert(overrides: Partial<FinanceAlert> = {}): FinanceAlert {
  return {
    symbol: "AAPL",
    name: "Apple Inc",
    price: 231.5,
    change_percent: 2.4,
    threshold_percent: 2,
    direction: "up",
    triggered_at: "2026-08-26T10:00:00Z",
    ...overrides,
  };
}

describe("AlertToast rendering", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders direction arrow, symbol, change percent and threshold", () => {
    const wrapper = mount(AlertToast, { props: { alert: makeAlert() } });

    expect(wrapper.find(".toast-direction").text()).toBe("▲");
    expect(wrapper.find(".toast-title").text()).toContain("AAPL");
    expect(wrapper.find(".toast-change").text()).toBe("+2.40%");
    expect(wrapper.find(".toast-detail").text()).toContain("阈值 2%");
    expect(wrapper.find(".toast-detail").text()).toContain("Apple Inc");
    expect(wrapper.find(".toast-detail").text()).toContain("$231.50");
  });

  it("renders a down alert with the down arrow and minus sign", () => {
    const wrapper = mount(AlertToast, {
      props: {
        alert: makeAlert({
          symbol: "600519.SS",
          name: null,
          direction: "down",
          change_percent: -3.2,
          price: null,
        }),
      },
    });

    expect(wrapper.find(".toast-direction").text()).toBe("▼");
    expect(wrapper.find(".toast-change").text()).toBe("-3.20%");
    // Null name falls back to the symbol; null price hides the 现价 segment
    expect(wrapper.find(".toast-detail").text()).toContain("600519.SS");
    expect(wrapper.find(".toast-detail").text()).not.toContain("现价");
  });

  it("uses change-up/change-down color scheme by direction", () => {
    const up = mount(AlertToast, { props: { alert: makeAlert() } });
    expect(up.find(".alert-toast").classes()).toContain("toast-up");
    expect(up.find(".toast-change").classes()).toContain("change-up");

    const down = mount(AlertToast, {
      props: { alert: makeAlert({ direction: "down", change_percent: -1 }) },
    });
    expect(down.find(".alert-toast").classes()).toContain("toast-down");
    expect(down.find(".toast-change").classes()).toContain("change-down");
  });
});

describe("AlertToast auto-dismiss", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("hides automatically after 8 seconds", async () => {
    const wrapper = mount(AlertToast, { props: { alert: makeAlert() } });
    expect(wrapper.find(".alert-toast").exists()).toBe(true);

    vi.advanceTimersByTime(8000);
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".alert-toast").exists()).toBe(false);
  });

  it("restarts the 8s window when a newer alert arrives", async () => {
    const wrapper = mount(AlertToast, { props: { alert: makeAlert() } });

    vi.advanceTimersByTime(5000);
    await wrapper.setProps({
      alert: makeAlert({ symbol: "TSLA", triggered_at: "t2" }),
    });

    // 5s after the swap the toast is still visible (timer restarted)
    vi.advanceTimersByTime(5000);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".alert-toast").exists()).toBe(true);
    expect(wrapper.text()).toContain("TSLA");

    // ...and hides 8s after the swap
    vi.advanceTimersByTime(3000);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".alert-toast").exists()).toBe(false);
  });

  it("clears the pending timer on unmount", () => {
    const wrapper = mount(AlertToast, { props: { alert: makeAlert() } });
    const clearSpy = vi.spyOn(globalThis, "clearTimeout");

    wrapper.unmount();

    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });
});
