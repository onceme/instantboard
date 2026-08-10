/**
 * ProfileSettings SSE status line regression: /settings keeps no stream open, so
 * DISCONNECTED there is reported neutrally ("当前页面无数据流…") instead of a red
 * "未连接"; on the SSE routes (/finance, /tech, /dashboard) a real disconnection
 * still reads "未连接", and the aggregated live state reads "已连接" once any
 * push channel is CONNECTED.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";

// Minimal vue-router stub: the component only needs the current route object
const routeState = vi.hoisted(() => ({ name: "settings", path: "/settings" }));
vi.mock("vue-router", () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: vi.fn() }),
}));

import ProfileSettings from "@/components/settings/ProfileSettings.vue";
import { useSSEStore } from "@/stores/sse";
import { SSEConnectionState } from "@/types";

function mountProfileSettings(path: string, name: string) {
  routeState.path = path;
  routeState.name = name;
  return mount(ProfileSettings);
}

// The SSE status value lives in the setting row labelled "SSE连接"
function sseStatusValue(wrapper: ReturnType<typeof mountProfileSettings>) {
  const row = wrapper
    .findAll(".setting-item")
    .find((item) => item.find(".setting-label").text() === "SSE连接");
  if (!row) {
    throw new Error('Could not find the "SSE连接" setting row');
  }
  return row.find(".setting-value");
}

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

describe("status line on routes without a stream", () => {
  it("/settings + DISCONNECTED shows the neutral no-stream hint, not a red 未连接", () => {
    const wrapper = mountProfileSettings("/settings", "settings");
    // Fresh SSE store: no finance/tech/dashboard channel is up yet
    expect(useSSEStore().overallState).toBe(SSEConnectionState.DISCONNECTED);

    const value = sseStatusValue(wrapper);
    expect(value.text()).toBe(
      "当前页面无数据流（在 财经/科技/仪表盘 页面自动连接）",
    );
    expect(value.classes()).toContain("sse-idle");
    expect(value.classes()).not.toContain("sse-disconnected");
    expect(value.text()).not.toBe("未连接");
  });
});

describe("status line on the SSE stream routes", () => {
  it("/finance + DISCONNECTED shows the red 未连接", () => {
    const wrapper = mountProfileSettings("/finance", "finance");

    const value = sseStatusValue(wrapper);
    expect(value.text()).toBe("未连接");
    expect(value.classes()).toContain("sse-disconnected");
  });

  it("shows 已连接 once any push channel is CONNECTED", async () => {
    const wrapper = mountProfileSettings("/finance", "finance");
    const sseStore = useSSEStore();
    sseStore.setFinanceState(SSEConnectionState.CONNECTED);
    await nextTick();

    expect(sseStore.overallState).toBe(SSEConnectionState.CONNECTED);
    const value = sseStatusValue(wrapper);
    expect(value.text()).toBe("已连接");
    expect(value.classes()).toContain("sse-connected");
  });
});
