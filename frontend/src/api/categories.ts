import { apiGet, apiPost, apiPut, apiDelete } from "@/utils/api";
import type { Category, TechNewsItem } from "@/types";

export const categoriesApi = {
  list: (params?: Record<string, unknown>) =>
    apiGet<Category[]>("/categories", params),
  get: (id: string) => apiGet<Category>(`/categories/${id}`),
  // Generic per-category item feed (GET /categories/{id}/items); the backend
  // reuses the tech-news item shape, hence TechNewsItem
  listItems: (categoryId: string, params?: Record<string, unknown>) =>
    apiGet<TechNewsItem[]>(`/categories/${categoryId}/items`, params),
  create: (data: Record<string, unknown>) =>
    apiPost<Category>("/categories", data),
  update: (id: string, data: Record<string, unknown>) =>
    apiPut<Category>(`/categories/${id}`, data),
  delete: (id: string) => apiDelete(`/categories/${id}`),
};
