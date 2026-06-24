import { apiGet, apiPost, apiPut, apiDelete } from '@/utils/api'
import type { Category } from '@/types'

export const categoriesApi = {
  list: (params?: Record<string, unknown>) => apiGet<Category[]>('/categories', params),
  get: (id: string) => apiGet<Category>(`/categories/${id}`),
  create: (data: Record<string, unknown>) => apiPost<Category>('/categories', data),
  update: (id: string, data: Record<string, unknown>) => apiPut<Category>(`/categories/${id}`, data),
  delete: (id: string) => apiDelete(`/categories/${id}`),
}
