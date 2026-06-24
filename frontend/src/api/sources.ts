import { apiGet, apiPost, apiPut, apiDelete } from '@/utils/api'
import type { Source } from '@/types'

export const sourcesApi = {
  list: (params?: Record<string, unknown>) => apiGet<Source[]>('/sources', params),
  get: (id: string) => apiGet<Source>(`/sources/${id}`),
  create: (data: Record<string, unknown>) => apiPost<Source>('/sources', data),
  update: (id: string, data: Record<string, unknown>) => apiPut<Source>(`/sources/${id}`, data),
  delete: (id: string) => apiDelete(`/sources/${id}`),
  health: (id: string) => apiGet(`/sources/${id}/health`),
}
