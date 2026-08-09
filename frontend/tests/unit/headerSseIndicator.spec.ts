/**
 * Header SSE indicator regression: the indicator only renders on routes that
 * actually maintain an SSE connection (/finance, /tech, /dashboard). On every
 * other route (e.g. /settings, /login) it is hidden instead of faking a red
 * "disconnected" dot; when shown, color and tooltip follow the live SSE state.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";

// Minimal vue-router stub: the component only needs the current route object
// plus a router for the user-menu navigation (not under test here).
const routeState = vi.hoisted(() => ({ name: "settings", path: "/settings" }));
vi.mock("vue-router", () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: vi.fn() }),
}));

import Header from "@/components/layout/Header.vue";
import { useFinanceStore } from "@/stores/finance";
import { useTechStore } from "@/stores/tech";
import { useDashboardStore } from "@/stores/dashboard";
import { SSEConnectionState } from "@/types";

function mountHeader(path: string, name: string) {
  routeState.path = path;
  routeState.name = name;
  return mount(Header);
}

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

describe("indicator visibility by route", () => {
  it("is hidden on /settings (no SSE connection on this route)", () => {
    const wrapper = mountHeader("/settings", "settings");
    expect(wrapper.find(".sse-indicator").exists()).toBe(false);
  });

  it("is hidden on login-ish routes", () => {
    expect(mountHeader("/login", "login").find(".sse-indicator").exists()).toBe(
      false,
    );
    expect(
      mountHeader("/ibadmin", "ibadmin").find(".sse-indicator").exists(),
    ).toBe(false);
  });

  it("is shown on the SSE routes", () => {
    expect(
      mountHeader("/finance", "finance").find(".sse-indicator").exists(),
    ).toBe(true);
    expect(mountHeader("/tech", "tech").find(".sse-indicator").exists()).toBe(
      true,
    );
    expect(
      mountHeader("/dashboard", "dashboard").find(".sse-indicator").exists(),
    ).toBe(true);
  });
});

describe("indicator reflects live SSE state", () => {
  it("/finance shows connected (green) with a connected tooltip", async () => {
    const wrapper = mountHeader("/finance", "finance");
    const financeStore = useFinanceStore();
    financeStore.sseState = SSEConnectionState.CONNECTED;
    await nextTick();

    const indicator = wrapper.find(".sse-indicator");
    expect(indicator.exists()).toBe(true);
    expect(indicator.classes()).toContain("sse-connected");
    expect(indicator.attributes("title")).toBe("实时推送：已连接");
    expect(indicator.attributes("aria-label")).toBe("实时推送：已连接");
  });

  it("/tech shows reconnecting (amber) while reconnecting", async () => {
    const wrapper = mountHeader("/tech", "tech");
    const techStore = useTechStore();
    techStore.sseState = SSEConnectionState.RECONNECTING;
    await nextTick();

    const indicator = wrapper.find(".sse-indicator");
    expect(indicator.classes()).toContain("sse-reconnecting");
    expect(indicator.attributes("title")).toBe("实时推送：重连中");
  });

  it("/dashboard shows disconnected (red) by default", () => {
    const wrapper = mountHeader("/dashboard", "dashboard");
    // Fresh dashboard store state is DISCONNECTED — no SSE connection yet
    expect(useDashboardStore().sseState).toBe(SSEConnectionState.DISCONNECTED);

    const indicator = wrapper.find(".sse-indicator");
    expect(indicator.classes()).toContain("sse-disconnected");
    expect(indicator.attributes("title")).toBe("实时推送：已断开");
  });

  it("reads the state from the matching route's store", async () => {
    const wrapper = mountHeader("/tech", "tech");
    const financeStore = useFinanceStore();
    // Finance being connected must NOT light up the indicator on /tech
    financeStore.sseState = SSEConnectionState.CONNECTED;
    await nextTick();

    const indicator = wrapper.find(".sse-indicator");
    expect(indicator.classes()).toContain("sse-disconnected");
  });
});
