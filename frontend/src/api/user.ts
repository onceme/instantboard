import { apiGet, apiPut } from "@/utils/api";
import type { UserPreferences } from "@/types";

// favorite_tags 约束与后端 schemas/user.py 一致：小写字母/数字/连字符 1-32，
// 上限 50 个。提交前用 sanitizeFavoriteTags 预校验，非法项不发送。
export const FAVORITE_TAG_PATTERN = /^[a-z0-9-]{1,32}$/;
export const MAX_FAVORITE_TAGS = 50;

export function getMyPreferences() {
  return apiGet<UserPreferences>("/users/me/preferences");
}

export function updateMyPreferences(payload: { favorite_tags: string[] }) {
  return apiPut<UserPreferences>("/users/me/preferences", payload);
}

// Lowercase, validate and de-duplicate tags (first-seen order). Malformed
// entries are dropped client-side so an invalid value is never submitted.
export function sanitizeFavoriteTags(tags: string[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const raw of tags) {
    const tag = raw.toLowerCase();
    if (!FAVORITE_TAG_PATTERN.test(tag) || seen.has(tag)) continue;
    seen.add(tag);
    result.push(tag);
  }
  return result;
}
