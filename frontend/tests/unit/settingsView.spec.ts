/**
 * SettingsView session-entry decoupling: the settings page only exposes the
 * sections that belong to the current session entry. Admin-entry sessions see
 * the back-office sections (sources/categories) and land on the first admin
 * section; SSO-entry sessions see only their personal profile section; a
 * missing or unknown session entry falls back to the SSO layout (the same
 * rule the router guard applies via readStoredSessionEntry).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import type { AxiosResponse } from "axios";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/utils/api";
import SettingsView from "@/views/SettingsView.vue";
import ProfileSettings from "@/components/settings/ProfileSettings.vue";
import CategoryEditor from "@/components/settings/CategoryEditor.vue";
import SourceEditor from "@/components/settings/SourceEditor.vue";

// Admin sessions land on the sources tab, which fetches lists on mount; answer
// every request with an empty success envelope so nothing hits the network
function stubApiWithEmptyLists() {
  apiClient.defaults.adapter = async (config) =>
    ({
      status: 200,
      statusText: "OK",
      data: { success: true, data: [] },
      headers: {},
      config,
    }) as AxiosResponse;
}

// The auth store reads session_entry from localStorage on init, so set the
// storage BEFORE activating pinia to simulate a page load with that session
function mountWithSessionEntry(entry: string | null) {
  localStorage.clear();
  if (entry !== null) localStorage.setItem("session_entry", entry);
  setActivePinia(createPinia());
  stubApiWithEmptyLists();
  return mount(SettingsView);
}

function tabLabels(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll(".tab-btn").map((el) => el.text());
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("admin-entry session", () => {
  it("shows only the admin sections and hides the personal profile", () => {
    const wrapper = mountWithSessionEntry("admin");

    expect(tabLabels(wrapper)).toEqual(["数据源管理", "分类管理"]);
    expect(wrapper.findComponent(ProfileSettings).exists()).toBe(false);
  });

  it("lands on the first admin section (sources)", async () => {
    const wrapper = mountWithSessionEntry("admin");
    await flushPromises();

    expect(wrapper.find(".tab-btn.active").text()).toBe("数据源管理");
    expect(wrapper.findComponent(SourceEditor).exists()).toBe(true);
    expect(wrapper.findComponent(CategoryEditor).exists()).toBe(false);
  });

  it("switches between the admin sections", async () => {
    const wrapper = mountWithSessionEntry("admin");
    await flushPromises();

    await wrapper.findAll(".tab-btn")[1].trigger("click");

    expect(wrapper.find(".tab-btn.active").text()).toBe("分类管理");
    expect(wrapper.findComponent(CategoryEditor).exists()).toBe(true);
    expect(wrapper.findComponent(SourceEditor).exists()).toBe(false);
  });
});

describe("sso-entry session", () => {
  it("shows only the personal profile and hides all admin sections", () => {
    const wrapper = mountWithSessionEntry("sso");

    expect(tabLabels(wrapper)).toEqual(["个人设置"]);
    expect(wrapper.findComponent(ProfileSettings).exists()).toBe(true);
    expect(wrapper.findComponent(CategoryEditor).exists()).toBe(false);
    expect(wrapper.findComponent(SourceEditor).exists()).toBe(false);
  });
});

describe("missing or unknown session entry", () => {
  it("falls back to the sso layout when no entry is stored", () => {
    const wrapper = mountWithSessionEntry(null);

    expect(tabLabels(wrapper)).toEqual(["个人设置"]);
    expect(wrapper.findComponent(ProfileSettings).exists()).toBe(true);
  });

  it("falls back to the sso layout when the stored entry is unknown", () => {
    const wrapper = mountWithSessionEntry("something-else");

    expect(tabLabels(wrapper)).toEqual(["个人设置"]);
    expect(wrapper.findComponent(ProfileSettings).exists()).toBe(true);
  });
});
