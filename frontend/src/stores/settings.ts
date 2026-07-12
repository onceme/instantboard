import { defineStore } from "pinia";
import { ref } from "vue";
import type { Category, Source } from "@/types";
import { apiGet, apiPost, apiPut, apiDelete } from "@/utils/api";

export const useSettingsStore = defineStore("settings", () => {
  const categories = ref<Category[]>([]);
  const sources = ref<Source[]>([]);
  const isLoadingCategories = ref(false);
  const isLoadingSources = ref(false);

  async function fetchCategories(includeSources = false) {
    isLoadingCategories.value = true;
    try {
      const response = await apiGet<Category[]>("/categories", {
        include_sources: includeSources,
      });
      categories.value = response.data;
    } finally {
      isLoadingCategories.value = false;
    }
  }

  async function createCategory(data: Partial<Category>) {
    const response = await apiPost<Category>(
      "/categories",
      data as Record<string, unknown>,
    );
    categories.value.push(response.data);
    return response.data;
  }

  async function updateCategory(id: string, data: Partial<Category>) {
    const response = await apiPut<Category>(
      `/categories/${id}`,
      data as Record<string, unknown>,
    );
    const index = categories.value.findIndex((c) => c.id === id);
    if (index >= 0) categories.value[index] = response.data;
    return response.data;
  }

  async function deleteCategory(id: string) {
    await apiDelete(`/categories/${id}`);
    categories.value = categories.value.filter((c) => c.id !== id);
  }

  async function fetchSources(categoryId?: string) {
    isLoadingSources.value = true;
    try {
      const params: Record<string, unknown> = {};
      if (categoryId) params.category_id = categoryId;
      const response = await apiGet<Source[]>("/sources", params);
      sources.value = response.data;
    } finally {
      isLoadingSources.value = false;
    }
  }

  async function createSource(data: Partial<Source>) {
    const response = await apiPost<Source>(
      "/sources",
      data as Record<string, unknown>,
    );
    sources.value.push(response.data);
    return response.data;
  }

  async function updateSource(id: string, data: Partial<Source>) {
    const response = await apiPut<Source>(
      `/sources/${id}`,
      data as Record<string, unknown>,
    );
    const index = sources.value.findIndex((s) => s.id === id);
    if (index >= 0) sources.value[index] = response.data;
    return response.data;
  }

  async function deleteSource(id: string) {
    await apiDelete(`/sources/${id}`);
    sources.value = sources.value.filter((s) => s.id !== id);
  }

  async function getSourceHealth(id: string) {
    return await apiGet(`/sources/${id}/health`);
  }

  function init() {
    fetchCategories();
    fetchSources();
  }

  return {
    categories,
    sources,
    isLoadingCategories,
    isLoadingSources,
    fetchCategories,
    createCategory,
    updateCategory,
    deleteCategory,
    fetchSources,
    createSource,
    updateSource,
    deleteSource,
    getSourceHealth,
    init,
  };
});
