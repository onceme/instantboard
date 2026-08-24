/**
 * ProfileSettings regression after removing the SSE status row: the live SSE
 * state is now shown only by the header indicator, so the settings page must
 * keep rendering its user info, theme and color-scheme rows without any SSE
 * row sneaking back in.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import ProfileSettings from "@/components/settings/ProfileSettings.vue";

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

describe("ProfileSettings", () => {
  it("renders theme and color-scheme rows but no SSE status row", () => {
    const wrapper = mount(ProfileSettings);

    const labels = wrapper.findAll(".setting-label").map((el) => el.text());
    expect(labels).toContain("主题");
    expect(labels).toContain("涨跌配色");
    expect(labels).not.toContain("SSE连接");
  });
});
