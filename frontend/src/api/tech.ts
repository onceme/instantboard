import { apiGet } from "@/utils/api";
import type { TechNewsItem, TechTopic } from "@/types";

export const techApi = {
  news: (params?: Record<string, unknown>) =>
    apiGet<TechNewsItem[]>("/tech/news", params),
  topics: () => apiGet<TechTopic[]>("/tech/topics"),
};
