/**
 * NewsCard manual tagging: "+" opens an inline input, Enter/confirm appends a
 * tag via POST /items/{id}/tags (server list replaces local tags), each tag's
 * "×" removes it via DELETE with rollback on failure; invalid input and API
 * errors surface an inline message.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { reactive } from "vue";
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";

import NewsCard from "@/components/tech/NewsCard.vue";
import TopicTag from "@/components/tech/TopicTag.vue";
import { apiClient } from "@/utils/api";
import type { TechNewsItem } from "@/types";

function makeItem(topic_tags = ["tech", "ai", "llm"]): TechNewsItem {
  return {
    id: "item-1",
    title: "GPT-5 released",
    summary: "A new model",
    url: "https://example.com/1",
    source_name: "HN",
    source_id: "src-1",
    category_id: "cat-1",
    topic_tags,
    published_at: "2026-08-25T10:00:00Z",
    fetched_at: "2026-08-25T10:05:00Z",
    priority: 5,
  };
}

interface Call {
  method: string;
  url: string;
  body?: unknown;
}

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
  message: string,
  code = "VALIDATION_ERROR",
) {
  const response = {
    status,
    statusText: String(status),
    data: { detail: { success: false, error: { code, message } } },
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

/** Stub: successful add/remove returning the authoritative updated tag list.
 * `base` mirrors the item's starting tags; the add tests keep the item at ≤2
 * starting tags so the added tag stays inside the visible slice (max 3). */
function stubApi(calls: Call[], base = ["tech", "ai"]) {
  apiClient.defaults.adapter = async (config) => {
    const method = (config.method || "").toUpperCase();
    const url = config.url || "";
    calls.push({ method, url });
    const matchAdd = url.match(/^\/items\/([^/]+)\/tags$/);
    const matchRemove = url.match(/^\/items\/([^/]+)\/tags\/([^/]+)$/);
    if (method === "POST" && matchAdd) {
      const body = JSON.parse((config.data as string) || "{}") as {
        tag?: string;
      };
      return okResponse(config, {
        success: true,
        data: { item_id: matchAdd[1], topic_tags: [...base, body.tag] },
      });
    }
    if (method === "DELETE" && matchRemove) {
      return okResponse(config, {
        success: true,
        data: {
          item_id: matchRemove[1],
          topic_tags: base.filter((t) => t !== matchRemove[2]),
        },
      });
    }
    throw errorResponse(404, config, "Item not found", "ITEM_NOT_FOUND");
  };
}

function stubFailingApi(calls: Call[], message: string) {
  apiClient.defaults.adapter = async (config) => {
    calls.push({
      method: (config.method || "").toUpperCase(),
      url: config.url || "",
    });
    throw errorResponse(400, config, message);
  };
}

async function mountCard(item: TechNewsItem) {
  // In production items come from a reactive store/feed array; wrap so that
  // mutating item.topic_tags re-runs the visibleTags computed.
  const wrapper = mount(NewsCard, { props: { item: reactive(item) } });
  await flushPromises();
  return wrapper;
}

function renderedTags(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper
    .findAllComponents(TopicTag)
    .map((t) => t.props("tag") as string);
}

beforeEach(() => localStorage.clear());

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("add tag flow", () => {
  // The card renders at most 3 tags (visibleTags slice), so the add tests
  // start from 2 tags — the added tag then lands inside the visible slice.
  it("opens an inline input on + and adds the tag on Enter", async () => {
    const calls: Call[] = [];
    stubApi(calls, ["tech", "ai"]);
    const item = makeItem(["tech", "ai"]);
    const wrapper = await mountCard(item);

    expect(wrapper.find(".tag-input").exists()).toBe(false);
    await wrapper.find(".tag-add").trigger("click");
    const input = wrapper.find(".tag-input");
    expect(input.exists()).toBe(true);

    await input.setValue("my-note");
    await input.trigger("keydown", { key: "Enter" });
    await flushPromises();

    expect(calls).toEqual([{ method: "POST", url: "/items/item-1/tags" }]);
    // Server list applied locally, order preserved (user tag appended last)
    expect(item.topic_tags).toEqual(["tech", "ai", "my-note"]);
    expect(renderedTags(wrapper)).toEqual(["tech", "ai", "my-note"]);
    // Input closes after success and no error is shown
    expect(wrapper.find(".tag-input").exists()).toBe(false);
    expect(wrapper.find(".tag-error").exists()).toBe(false);
  });

  it("submits via the confirm button (form submit)", async () => {
    const calls: Call[] = [];
    stubApi(calls, ["tech", "ai"]);
    const wrapper = await mountCard(makeItem(["tech", "ai"]));

    await wrapper.find(".tag-add").trigger("click");
    await wrapper.find(".tag-input").setValue("hot-topic");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(calls).toHaveLength(1);
    expect(renderedTags(wrapper)).toContain("hot-topic");
  });

  it("cancels the input on Escape", async () => {
    stubApi([]);
    const wrapper = await mountCard(makeItem());

    await wrapper.find(".tag-add").trigger("click");
    await wrapper.find(".tag-input").trigger("keydown", { key: "Escape" });

    expect(wrapper.find(".tag-input").exists()).toBe(false);
  });

  it("rejects invalid input client-side without calling the API", async () => {
    const calls: Call[] = [];
    stubApi(calls);
    const wrapper = await mountCard(makeItem());

    await wrapper.find(".tag-add").trigger("click");
    const input = wrapper.find(".tag-input");
    await input.setValue("Bad Tag!");
    await input.trigger("keydown", { key: "Enter" });
    await flushPromises();

    expect(calls).toEqual([]);
    expect(wrapper.find(".tag-error").text()).toContain("小写字母");
    expect(wrapper.find(".tag-input").exists()).toBe(true);
  });

  it("shows the backend error and keeps the input open on failure", async () => {
    const calls: Call[] = [];
    stubFailingApi(calls, "Tag limit reached (max 20 per item)");
    const item = makeItem();
    const wrapper = await mountCard(item);

    await wrapper.find(".tag-add").trigger("click");
    await wrapper.find(".tag-input").setValue("one-more");
    await wrapper.find(".tag-input").trigger("keydown", { key: "Enter" });
    await flushPromises();

    expect(calls).toHaveLength(1);
    expect(item.topic_tags).toEqual(["tech", "ai", "llm"]);
    expect(wrapper.find(".tag-error").text()).toBe(
      "Tag limit reached (max 20 per item)",
    );
    expect(wrapper.find(".tag-input").exists()).toBe(true);
  });
});

describe("remove tag flow", () => {
  it("removes a tag via × and applies the server list", async () => {
    const calls: Call[] = [];
    stubApi(calls, ["tech", "ai", "llm"]);
    const item = makeItem();
    const wrapper = await mountCard(item);

    const aiTag = wrapper
      .findAllComponents(TopicTag)
      .find((t) => t.props("tag") === "ai");
    expect(aiTag).toBeDefined();
    await aiTag!.find(".tag-remove").trigger("click");
    await flushPromises();

    expect(calls).toEqual([{ method: "DELETE", url: "/items/item-1/tags/ai" }]);
    expect(item.topic_tags).toEqual(["tech", "llm"]);
    expect(renderedTags(wrapper)).toEqual(["tech", "llm"]);
    expect(wrapper.find(".tag-error").exists()).toBe(false);
  });

  it("rolls back the optimistic removal on failure", async () => {
    const calls: Call[] = [];
    stubFailingApi(calls, "Tag not found on this item");
    const item = makeItem();
    const wrapper = await mountCard(item);

    const llmTag = wrapper
      .findAllComponents(TopicTag)
      .find((t) => t.props("tag") === "llm");
    await llmTag!.find(".tag-remove").trigger("click");
    await flushPromises();

    expect(calls).toHaveLength(1);
    // Rolled back: the tag is still there
    expect(item.topic_tags).toEqual(["tech", "ai", "llm"]);
    expect(renderedTags(wrapper)).toEqual(["tech", "ai", "llm"]);
    expect(wrapper.find(".tag-error").text()).toBe(
      "Tag not found on this item",
    );
  });
});
