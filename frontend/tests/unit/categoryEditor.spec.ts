/**
 * CategoryEditor regressions: mutation failures (409 duplicate / 400 quota /
 * 403 permission) surface as visible Chinese error text instead of being
 * swallowed; the top "add" form and the inline row editor keep separate state
 * so editing a row no longer leaks its values into the add inputs; a failed
 * add keeps the user's input plus the error, a successful add clears it.
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
            error: { code: "DUPLICATE_CATEGORY", message: "分类名称已存在：科技" },
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
