import { apiDelete, apiPost } from "@/utils/api";

// Backend schemas/item.py ItemTagsResponse (POST/DELETE /items/{id}/tags)
export interface ItemTagsResult {
  item_id: string;
  topic_tags: string[];
}

export const itemsApi = {
  addItemTag: (itemId: string, tag: string) =>
    apiPost<ItemTagsResult>(`/items/${itemId}/tags`, { tag }),
  removeItemTag: (itemId: string, tag: string) =>
    apiDelete<ItemTagsResult>(`/items/${itemId}/tags/${tag}`),
};
