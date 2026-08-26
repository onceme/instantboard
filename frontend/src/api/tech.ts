import { apiGet } from "@/utils/api";
import type { TechNewsItem, TechTopic, TechSort } from "@/types";

// Backend GET /api/v1/tech/news query params (api/v1/tech.py): domain/subcategory/tag
// stack as JSONB containment filters; tag is the hot-topic tag filter.
// Type alias (not interface) so it carries the implicit index signature that
// apiGet's Record<string, unknown> params require.
export type TechNewsParams = {
  domain?: string;
  subcategory?: string;
  tag?: string;
  sort?: TechSort;
  source_id?: string;
  since?: string;
  page?: number;
  page_size?: number;
};

export const techApi = {
  news: (params?: TechNewsParams) =>
    apiGet<TechNewsItem[]>("/tech/news", params),
  topics: () => apiGet<TechTopic[]>("/tech/topics"),
};
