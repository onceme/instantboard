/**
 * ProfileSettings regression after removing the SSE status row: the live SSE
 * state is now shown only by the header indicator, so the settings page must
 * keep rendering its user info, theme and color-scheme rows without any SSE
 * row sneaking back in.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { createPinia, setActivePinia } from "pinia";
import { apiClient } from "@/utils/api";

import ProfileSettings from "@/components/settings/ProfileSettings.vue";

// The component fetches GET /users/me/preferences on mount; stub the adapter
// so the regression assertion never depends on a live request.
function stubPrefsAdapter() {
  apiClient.defaults.adapter = async (
    config: InternalAxiosRequestConfig,
  ): Promise<AxiosResponse> =>
    ({
      status: 200,
      statusText: "OK",
      data: { success: true, data: { favorite_tags: [] } },
      headers: {},
      config,
    }) as AxiosResponse;
}

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
  stubPrefsAdapter();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("ProfileSettings", () => {
  it("renders theme and color-scheme rows but no SSE status row", async () => {
    const wrapper = mount(ProfileSettings);
    await flushPromises();

    const labels = wrapper.findAll(".setting-label").map((el) => el.text());
    expect(labels).toContain("主题");
    expect(labels).toContain("涨跌配色");
    expect(labels).not.toContain("SSE连接");
  });
});
