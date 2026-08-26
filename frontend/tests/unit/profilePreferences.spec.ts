/**
 * ProfileSettings 关注话题 (favorite_tags preferences feeding the tech relevance
 * re-rank, design tech-tab.md §3.5.1): server preferences echo into the
 * subcategory chips on load, chip toggles build the PUT payload, an empty
 * selection clears the list, invalid tags are sanitized client-side and never
 * submitted, and backend failures surface an inline error.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { createPinia, setActivePinia } from "pinia";
import { apiClient } from "@/utils/api";
import { sanitizeFavoriteTags } from "@/api/user";
import ProfileSettings from "@/components/settings/ProfileSettings.vue";

function okResponse(
  config: InternalAxiosRequestConfig,
  data: unknown,
): AxiosResponse {
  return {
    status: 200,
    statusText: "OK",
    data,
    headers: {},
    config,
  } as AxiosResponse;
}

function errorResponse(
  status: number,
  config: InternalAxiosRequestConfig,
  data: unknown,
): AxiosError {
  const response = {
    status,
    statusText: String(status),
    data,
    headers: {},
    config,
  } as AxiosResponse;
  return new AxiosError(
    `Request failed with status code ${status}`,
    String(status),
    config,
    null,
    response,
  );
}

function stubPrefsApi(storedTags: string[], opts: { failPut?: boolean } = {}) {
  const putRequests: Array<Record<string, unknown>> = [];
  apiClient.defaults.adapter = async (config) => {
    const method = (config.method ?? "").toLowerCase();
    if (method === "get" && config.url === "/users/me/preferences") {
      return okResponse(config, {
        success: true,
        data: { favorite_tags: storedTags },
      });
    }
    if (method === "put" && config.url === "/users/me/preferences") {
      const body = JSON.parse(config.data as string);
      putRequests.push(body);
      if (opts.failPut) {
        throw errorResponse(400, config, {
          detail: {
            success: false,
            error: {
              code: "VALIDATION_ERROR",
              message: "Invalid favorite_tags",
              details: [
                {
                  field: "favorite_tags",
                  message: "Tags must match ^[a-z0-9-]{1,32}$",
                },
              ],
            },
          },
        });
      }
      return okResponse(config, { success: true, data: body });
    }
    throw errorResponse(500, config, {});
  };
  return putRequests;
}

function tagChip(wrapper: ReturnType<typeof mount>, label: string) {
  const chip = wrapper
    .findAll(".favorite-tag")
    .find((el) => el.text() === label);
  if (!chip) throw new Error(`chip not found: ${label}`);
  return chip;
}

function saveButton(wrapper: ReturnType<typeof mount>) {
  const btn = wrapper
    .findAll(".toggle-btn")
    .find((el) => el.text().includes("保存"));
  if (!btn) throw new Error("save button not found");
  return btn;
}

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("sanitizeFavoriteTags", () => {
  it("lowercases and de-duplicates keeping first-seen order", () => {
    expect(sanitizeFavoriteTags(["LLM", "drone", "llm"])).toEqual([
      "llm",
      "drone",
    ]);
  });

  it("drops malformed entries so they are never submitted", () => {
    expect(
      sanitizeFavoriteTags(["llm", "bad tag", "under_score", "中文", ""]),
    ).toEqual(["llm"]);
  });
});

describe("ProfileSettings favorite tags", () => {
  it("renders all tech subcategories as selectable chips", async () => {
    stubPrefsApi([]);
    const wrapper = mount(ProfileSettings);
    await flushPromises();

    const labels = wrapper.findAll(".setting-label").map((el) => el.text());
    expect(labels).toContain("关注话题");
    // 24 tech subcategories across the four domains
    expect(wrapper.findAll(".favorite-tag")).toHaveLength(24);
  });

  it("echoes server preferences on load", async () => {
    stubPrefsApi(["llm", "drone"]);
    const wrapper = mount(ProfileSettings);
    await flushPromises();

    expect(tagChip(wrapper, "大语言模型").classes()).toContain("active");
    expect(tagChip(wrapper, "无人机").classes()).toContain("active");
    expect(tagChip(wrapper, "大语言模型").attributes("aria-pressed")).toBe(
      "true",
    );
    expect(tagChip(wrapper, "协作机器人").classes()).not.toContain("active");
  });

  it("sanitizes malformed stored tags while echoing", async () => {
    stubPrefsApi(["LLM", "bad tag", "llm"]);
    const wrapper = mount(ProfileSettings);
    await flushPromises();

    expect(tagChip(wrapper, "大语言模型").classes()).toContain("active");
    expect(
      wrapper.findAll(".favorite-tag.active"),
      "only the valid tag survives",
    ).toHaveLength(1);
  });

  it("toggles chips and saves the selection", async () => {
    const putRequests = stubPrefsApi([]);
    const wrapper = mount(ProfileSettings);
    await flushPromises();

    await tagChip(wrapper, "大语言模型").trigger("click");
    await tagChip(wrapper, "协作机器人").trigger("click");
    await tagChip(wrapper, "大语言模型").trigger("click");
    await tagChip(wrapper, "大语言模型").trigger("click");

    await saveButton(wrapper).trigger("click");
    await flushPromises();

    expect(putRequests).toEqual([{ favorite_tags: ["cobot", "llm"] }]);
    expect(wrapper.find(".favorite-tags-saved").exists()).toBe(true);
  });

  it("saves an empty selection as a clear", async () => {
    const putRequests = stubPrefsApi(["llm"]);
    const wrapper = mount(ProfileSettings);
    await flushPromises();

    // Deselect the echoed chip, then save
    await tagChip(wrapper, "大语言模型").trigger("click");
    await saveButton(wrapper).trigger("click");
    await flushPromises();

    expect(putRequests).toEqual([{ favorite_tags: [] }]);
    expect(wrapper.find(".favorite-tags-saved").exists()).toBe(true);
  });

  it("shows an inline error and no success hint when the save fails", async () => {
    stubPrefsApi([], { failPut: true });
    const wrapper = mount(ProfileSettings);
    await flushPromises();

    await tagChip(wrapper, "大语言模型").trigger("click");
    await saveButton(wrapper).trigger("click");
    await flushPromises();

    const error = wrapper.find(".favorite-tags-error");
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain("Invalid favorite_tags");
    expect(wrapper.find(".favorite-tags-saved").exists()).toBe(false);
  });
});
