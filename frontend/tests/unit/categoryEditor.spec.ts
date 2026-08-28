/**
 * CategoryEditor regressions: mutation failures (409 duplicate / 400 quota /
 * 403 permission) surface as visible Chinese error text instead of being
 * swallowed; the top "add" form and the inline row editor keep separate state
 * so editing a row no longer leaks its values into the add inputs; a failed
 * add keeps the user's input plus the error, a successful add clears it.
 * Full-form coverage: icon/color/interval/keywords/slug controls render with
 * defaults, filled values land in the create/update payloads, blank optional
 * fields are omitted (backend defaults / stored values win), intervals below
 * the backend minimum are rejected client-side, and edit mode prefills the
 * extended fields.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { apiClient } from "@/utils/api";
import CategoryEditor from "@/components/settings/CategoryEditor.vue";
import type { Category } from "@/types";

function makeCategory(overrides: Partial<Category> = {}): Category {
  return {
    id: "cat-1",
    name: "机器人",
    slug: "robotics",
    description: "机器人相关资讯",
    type: "custom",
    refresh_interval_seconds: 300,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
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

function method(config: InternalAxiosRequestConfig): string {
  return (config.method ?? "").toLowerCase();
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete (apiClient.defaults as { adapter?: unknown }).adapter;
});

describe("add error paths", () => {
  it("409 duplicate: shows the backend message inline and keeps the input", async () => {
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [] });
      }
      if (method(config) === "post" && config.url === "/categories") {
        throw errorResponse(409, config, {
          detail: {
            error: {
              code: "DUPLICATE_CATEGORY",
              message: "分类名称已存在：科技",
            },
          },
        });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    const nameInput = wrapper.find(".add-section .input-name");
    await nameInput.setValue("科技");
    await wrapper.find(".add-btn").trigger("click");
    await flushPromises();

    const error = wrapper.find(".add-section .error-text");
    expect(error.exists()).toBe(true);
    expect(error.text()).toBe("分类名称已存在：科技");
    // Failed add must not lose the user's input
    expect((nameInput.element as HTMLInputElement).value).toBe("科技");
  });

  it("successful add clears the inputs and refreshes the list", async () => {
    let listCalls = 0;
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        listCalls += 1;
        return okResponse(config, {
          success: true,
          data: listCalls === 1 ? [] : [makeCategory({ name: "新分类" })],
        });
      }
      if (method(config) === "post" && config.url === "/categories") {
        return okResponse(config, {
          success: true,
          data: makeCategory({ name: "新分类" }),
        });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    const nameInput = wrapper.find(".add-section .input-name");
    await nameInput.setValue("新分类");
    await wrapper.find(".add-btn").trigger("click");
    await flushPromises();

    expect((nameInput.element as HTMLInputElement).value).toBe("");
    expect(wrapper.find(".add-section .error-text").exists()).toBe(false);
    // Initial load + refresh after the add
    expect(listCalls).toBe(2);
    expect(wrapper.text()).toContain("新分类");
  });
});

describe("add/edit state separation", () => {
  it("starting a row edit fills only the edit row, never the add form", async () => {
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [makeCategory()] });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".edit-btn").trigger("click");
    await flushPromises();

    const editInputs = wrapper.findAll(".edit-row input");
    expect((editInputs[0].element as HTMLInputElement).value).toBe("机器人");
    expect((editInputs[1].element as HTMLInputElement).value).toBe(
      "机器人相关资讯",
    );

    // Regression: startEdit used to pour the row values into the add inputs
    const addName = wrapper.find(".add-section .input-name");
    const addDesc = wrapper.find(".add-section .input-desc");
    expect((addName.element as HTMLInputElement).value).toBe("");
    expect((addDesc.element as HTMLInputElement).value).toBe("");

    await wrapper.find(".cancel-btn").trigger("click");
    await flushPromises();
    expect(wrapper.find(".edit-row").exists()).toBe(false);
    // Add form still untouched after cancel
    expect(
      (wrapper.find(".add-section .input-name").element as HTMLInputElement)
        .value,
    ).toBe("");
  });
});

describe("delete error path", () => {
  it("surfaces delete failures (e.g. category still in use) via the error alert", async () => {
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [makeCategory()] });
      }
      if (method(config) === "delete" && config.url === "/categories/cat-1") {
        throw errorResponse(400, config, {
          detail: {
            error: {
              code: "CATEGORY_IN_USE",
              message: "该分类下仍有数据源，无法删除",
            },
          },
        });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".delete-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".error-alert").text()).toContain(
      "该分类下仍有数据源，无法删除",
    );
    // The category is still there (no silent removal)
    expect(wrapper.text()).toContain("机器人");
  });
});

function overlay(): HTMLElement | null {
  return document.body.querySelector(".dialog-overlay");
}

describe("reclassify action", () => {
  // ConfirmationDialog teleports into <body>; keep it out of other specs
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders the button on custom rows only, never on predefined ones", async () => {
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, {
          success: true,
          data: [
            makeCategory({
              id: "cat-fin",
              name: "财经",
              slug: "finance",
              type: "finance",
            }),
            makeCategory(),
          ],
        });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    expect(wrapper.find(".category-item.custom .reclassify-btn").exists()).toBe(
      true,
    );
    expect(
      wrapper.find(".category-item.predefined .reclassify-btn").exists(),
    ).toBe(false);
  });

  it("opens the confirmation dialog; cancelling sends no request", async () => {
    const requests: Array<{ method: string; url: string }> = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push({ method: method(config), url: config.url ?? "" });
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [makeCategory()] });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".reclassify-btn").trigger("click");
    await flushPromises();

    const root = overlay();
    expect(root).not.toBeNull();
    expect(root!.textContent).toContain("重新分类");
    expect(root!.textContent).toContain("机器人");

    (document.body.querySelector(".btn-cancel") as HTMLButtonElement).click();
    await flushPromises();

    // Only the initial GET happened — the reclassify POST was never sent
    expect(requests.some((r) => r.method === "post")).toBe(false);
  });

  it("confirm posts to /categories/{id}/reclassify and echoes the counts", async () => {
    let reclassifyCalls = 0;
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [makeCategory()] });
      }
      if (
        method(config) === "post" &&
        config.url === "/categories/cat-1/reclassify"
      ) {
        reclassifyCalls += 1;
        return okResponse(config, {
          success: true,
          data: { scanned: 42, updated: 7 },
        });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".reclassify-btn").trigger("click");
    await flushPromises();
    (document.body.querySelector(".btn-confirm") as HTMLButtonElement).click();
    await flushPromises();

    expect(reclassifyCalls).toBe(1);
    expect(wrapper.find(".reclassify-message").text()).toBe(
      "已扫描 42 条，更新 7 条",
    );
  });

  it("surfaces reclassify failures via the error alert", async () => {
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [makeCategory()] });
      }
      if (
        method(config) === "post" &&
        config.url === "/categories/cat-1/reclassify"
      ) {
        throw errorResponse(500, config, {
          detail: {
            error: {
              code: "INTERNAL_ERROR",
              message: "重打标失败，请稍后重试",
            },
          },
        });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".reclassify-btn").trigger("click");
    await flushPromises();
    (document.body.querySelector(".btn-confirm") as HTMLButtonElement).click();
    await flushPromises();

    expect(wrapper.find(".error-alert").text()).toContain(
      "重打标失败，请稍后重试",
    );
    expect(wrapper.find(".reclassify-message").exists()).toBe(false);
  });
});

// Captures the JSON body of a POST /categories or PUT /categories/{id} call.
// config.data is already a JSON string by the time the adapter runs (axios
// request transformers have executed).
function requestBody(
  config: InternalAxiosRequestConfig,
): Record<string, unknown> {
  return JSON.parse(config.data as string) as Record<string, unknown>;
}

describe("full category form", () => {
  it("renders slug/interval/keywords/color/icon controls with defaults", async () => {
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [] });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    const section = wrapper.find(".add-section");
    expect(section.find(".input-slug").exists()).toBe(true);
    expect(section.find(".input-interval").exists()).toBe(true);
    expect(section.find(".input-keywords").exists()).toBe(true);
    const colorInput = section.find('input[type="color"]');
    expect(colorInput.exists()).toBe(true);
    expect((colorInput.element as HTMLInputElement).value.toLowerCase()).toBe(
      "#3b82f6",
    );
    // 15 curated icon options, "folder" preselected
    expect(section.findAll(".icon-option")).toHaveLength(15);
    expect(section.find(".icon-option.selected").attributes("data-icon")).toBe(
      "folder",
    );
  });

  it("submits icon/color/interval/keywords/slug as filled by the user", async () => {
    let posted: Record<string, unknown> | null = null;
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [] });
      }
      if (method(config) === "post" && config.url === "/categories") {
        posted = requestBody(config);
        return okResponse(config, { success: true, data: makeCategory() });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".add-section .input-name").setValue("体育");
    await wrapper.find(".add-section .input-slug").setValue("sports");
    await wrapper.find(".add-section .input-interval").setValue("120");
    await wrapper
      .find(".add-section .input-keywords")
      .setValue("AI, 机器人 ,, 新闻 ");
    await wrapper
      .find('.add-section .icon-option[data-icon="rocket"]')
      .trigger("click");
    await wrapper.find('.add-section input[type="color"]').setValue("#FF0000");

    // Selection follows the click before submission
    expect(
      wrapper
        .find(".add-section .icon-option.selected")
        .attributes("data-icon"),
    ).toBe("rocket");

    await wrapper.find(".add-btn").trigger("click");
    await flushPromises();

    expect(posted).not.toBeNull();
    expect(posted!.name).toBe("体育");
    expect(posted!.type).toBe("custom");
    expect(posted!.slug).toBe("sports");
    expect(posted!.icon).toBe("rocket");
    expect((posted!.color as string).toLowerCase()).toBe("#ff0000");
    expect(posted!.refresh_interval_seconds).toBe(120);
    // Blank entries between commas are filtered out
    expect(posted!.keywords_filter).toEqual(["AI", "机器人", "新闻"]);
  });

  it("leaving slug/interval/keywords blank omits them; icon/color fall back to defaults", async () => {
    let posted: Record<string, unknown> | null = null;
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [] });
      }
      if (method(config) === "post" && config.url === "/categories") {
        posted = requestBody(config);
        return okResponse(config, { success: true, data: makeCategory() });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".add-section .input-name").setValue("体育");
    await wrapper.find(".add-btn").trigger("click");
    await flushPromises();

    // No slug / refresh_interval_seconds / keywords_filter keys — the backend
    // slugifies the name, applies the 300s default and stores no filter
    expect(posted).toEqual({
      name: "体育",
      description: "",
      type: "custom",
      icon: "folder",
      color: "#3B82F6",
    });
  });

  it("rejects a refresh interval below the backend minimum without sending a request", async () => {
    const mutations: string[] = [];
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [] });
      }
      mutations.push(`${method(config)} ${config.url}`);
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".add-section .input-name").setValue("体育");
    await wrapper.find(".add-section .input-interval").setValue("5");
    await wrapper.find(".add-btn").trigger("click");
    await flushPromises();

    expect(wrapper.find(".add-section .error-text").text()).toContain(
      "刷新频率",
    );
    expect(mutations).toHaveLength(0);
    // The input is kept so the user can correct it
    expect(
      (wrapper.find(".add-section .input-name").element as HTMLInputElement)
        .value,
    ).toBe("体育");
  });
});

describe("full form in edit mode", () => {
  it("prefills slug/icon/color/interval and submits the modified fields", async () => {
    let updated: Record<string, unknown> | null = null;
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, {
          success: true,
          data: [
            makeCategory({
              icon: "trophy",
              color: "#22C55E",
              refresh_interval_seconds: 60,
            }),
          ],
        });
      }
      if (method(config) === "put" && config.url === "/categories/cat-1") {
        updated = requestBody(config);
        return okResponse(config, { success: true, data: makeCategory() });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".edit-btn").trigger("click");
    await flushPromises();

    const row = wrapper.find(".edit-row");
    expect((row.find(".input-name").element as HTMLInputElement).value).toBe(
      "机器人",
    );
    expect((row.find(".input-slug").element as HTMLInputElement).value).toBe(
      "robotics",
    );
    expect(
      (row.find(".input-interval").element as HTMLInputElement).value,
    ).toBe("60");
    expect(
      (
        row.find('input[type="color"]').element as HTMLInputElement
      ).value.toLowerCase(),
    ).toBe("#22c55e");
    expect(row.find(".icon-option.selected").attributes("data-icon")).toBe(
      "trophy",
    );
    // Responses carry no keywords_filter, so there is nothing to echo
    expect(
      (row.find(".input-keywords").element as HTMLInputElement).value,
    ).toBe("");

    await row.find(".input-keywords").setValue("人形, Optimus");
    await row.find('.icon-option[data-icon="star"]').trigger("click");
    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();

    expect(updated).not.toBeNull();
    expect(updated!.name).toBe("机器人");
    expect(updated!.slug).toBe("robotics");
    expect(updated!.icon).toBe("star");
    expect(updated!.refresh_interval_seconds).toBe(60);
    expect((updated!.color as string).toLowerCase()).toBe("#22c55e");
    expect(updated!.keywords_filter).toEqual(["人形", "Optimus"]);
  });

  it("omits blank keywords and a cleared interval on save so stored values survive", async () => {
    let updated: Record<string, unknown> | null = null;
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, { success: true, data: [makeCategory()] });
      }
      if (method(config) === "put" && config.url === "/categories/cat-1") {
        updated = requestBody(config);
        return okResponse(config, { success: true, data: makeCategory() });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".edit-btn").trigger("click");
    await flushPromises();

    // Explicitly clear the prefilled interval; leave keywords untouched
    await wrapper.find(".edit-row .input-interval").setValue("");
    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();

    expect(updated).not.toBeNull();
    expect(Object.keys(updated!)).not.toContain("keywords_filter");
    expect(Object.keys(updated!)).not.toContain("refresh_interval_seconds");
  });

  it("keeps an out-of-list stored icon (e.g. cpu) selectable while editing", async () => {
    let updated: Record<string, unknown> | null = null;
    apiClient.defaults.adapter = async (config) => {
      if (method(config) === "get" && config.url === "/categories") {
        return okResponse(config, {
          success: true,
          data: [makeCategory({ icon: "cpu" })],
        });
      }
      if (method(config) === "put" && config.url === "/categories/cat-1") {
        updated = requestBody(config);
        return okResponse(config, { success: true, data: makeCategory() });
      }
      throw errorResponse(500, config, {});
    };

    const wrapper = mount(CategoryEditor);
    await flushPromises();

    await wrapper.find(".edit-btn").trigger("click");
    await flushPromises();

    const row = wrapper.find(".edit-row");
    expect(row.findAll(".icon-option")).toHaveLength(16);
    expect(row.find(".icon-option.selected").attributes("data-icon")).toBe(
      "cpu",
    );

    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();

    // Saving without touching the icon must not drop it
    expect(updated!.icon).toBe("cpu");
  });
});
