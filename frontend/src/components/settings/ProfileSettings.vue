<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
import { useTheme, THEME_MODE_OPTIONS } from "@/composables/useTheme";
import { computed, onMounted, ref } from "vue";
import { Sun, Moon, Monitor, Palette, Tag } from "lucide-vue-next";
import { SUBCATEGORY_MAP, DOMAIN_CONFIG } from "@/types";
import {
  getMyPreferences,
  updateMyPreferences,
  sanitizeFavoriteTags,
  MAX_FAVORITE_TAGS,
} from "@/api/user";
import { getApiErrorMessage } from "@/utils/api";

const authStore = useAuthStore();
const { theme, themeMode, setThemeMode, colorScheme, toggleColorScheme } =
  useTheme();

const userName = computed(() => authStore.user?.name || "未登录");
const userEmail = computed(() => authStore.user?.email || "");
const userRole = computed(() => authStore.user?.role || "");

const colorSchemeLabel = computed(() =>
  colorScheme.value === "chinese"
    ? "中国配色 (红涨绿跌)"
    : "国际配色 (绿涨红跌)",
);

// 关注话题（科技二级标签多选）：服务端回显 → 点选切换 → PUT 保存，
// "相关性"排序按这些标签给候选条目加权（见 tech-tab.md §3.5.1）
const tagGroups = computed(() =>
  Object.entries(SUBCATEGORY_MAP).map(([domain, subcats]) => ({
    domain,
    label: DOMAIN_CONFIG[domain]?.label ?? domain,
    tags: subcats,
  })),
);

const selectedTags = ref<string[]>([]);
const prefsLoading = ref(false);
const saving = ref(false);
const saved = ref(false);
const tagError = ref("");

async function loadFavoriteTags() {
  prefsLoading.value = true;
  tagError.value = "";
  try {
    const res = await getMyPreferences();
    selectedTags.value = sanitizeFavoriteTags(res.data?.favorite_tags ?? []);
  } catch (err) {
    tagError.value = getApiErrorMessage(err, "偏好加载失败");
  } finally {
    prefsLoading.value = false;
  }
}

function toggleTag(slug: string) {
  saved.value = false;
  const idx = selectedTags.value.indexOf(slug);
  if (idx >= 0) {
    selectedTags.value.splice(idx, 1);
  } else {
    selectedTags.value.push(slug);
  }
}

async function saveFavoriteTags() {
  tagError.value = "";
  saved.value = false;
  // 非法标签在客户端被过滤，不会进入提交体
  const tags = sanitizeFavoriteTags(selectedTags.value);
  if (tags.length > MAX_FAVORITE_TAGS) {
    tagError.value = `最多关注 ${MAX_FAVORITE_TAGS} 个话题`;
    return;
  }
  saving.value = true;
  try {
    const res = await updateMyPreferences({ favorite_tags: tags });
    selectedTags.value = res.data?.favorite_tags ?? tags;
    saved.value = true;
  } catch (err) {
    tagError.value = getApiErrorMessage(err, "保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(loadFavoriteTags);
</script>

<template>
  <div class="profile-settings">
    <div class="user-section">
      <div class="user-avatar">
        {{ userName.charAt(0) }}
      </div>
      <div class="user-info">
        <span class="user-name">{{ userName }}</span>
        <span class="user-email">{{ userEmail }}</span>
        <span class="user-role">{{ userRole }}</span>
      </div>
    </div>

    <div class="setting-item theme-item">
      <div class="setting-header">
        <Sun v-if="themeMode === 'light'" :size="18" />
        <Moon v-else-if="themeMode === 'dark'" :size="18" />
        <Monitor v-else :size="18" />
        <span class="setting-label">主题</span>
      </div>
      <span v-if="themeMode === 'system'" class="setting-value">
        当前：{{ theme === "dark" ? "暗色" : "亮色" }}
      </span>
      <div class="theme-options" role="radiogroup" aria-label="主题选择">
        <button
          v-for="option in THEME_MODE_OPTIONS"
          :key="option.value"
          type="button"
          class="theme-option"
          :class="{ active: themeMode === option.value }"
          role="radio"
          :aria-checked="themeMode === option.value"
          @click="setThemeMode(option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    </div>

    <div class="setting-item">
      <div class="setting-header">
        <Palette :size="18" />
        <span class="setting-label">涨跌配色</span>
      </div>
      <span class="setting-value">{{ colorSchemeLabel }}</span>
      <button class="toggle-btn" @click="toggleColorScheme">切换</button>
    </div>

    <div class="setting-item favorite-tags-item">
      <div class="setting-header">
        <Tag :size="18" />
        <span class="setting-label">关注话题</span>
        <span v-if="prefsLoading" class="setting-value">加载中…</span>
      </div>
      <p class="favorite-tags-hint">
        选择感兴趣的话题，科技频道"相关性"排序会优先展示匹配的资讯
      </p>
      <div
        v-for="group in tagGroups"
        :key="group.domain"
        class="favorite-tags-group"
      >
        <span class="favorite-tags-domain">{{ group.label }}</span>
        <div class="favorite-tags-list">
          <button
            v-for="t in group.tags"
            :key="t.slug"
            type="button"
            class="favorite-tag"
            :class="{ active: selectedTags.includes(t.slug) }"
            :aria-pressed="selectedTags.includes(t.slug)"
            @click="toggleTag(t.slug)"
          >
            {{ t.label }}
          </button>
        </div>
      </div>
      <div class="favorite-tags-actions">
        <button class="toggle-btn" :disabled="saving" @click="saveFavoriteTags">
          {{ saving ? "保存中…" : "保存" }}
        </button>
        <span v-if="saved" class="favorite-tags-saved">已保存</span>
        <span v-if="tagError" class="favorite-tags-error">{{ tagError }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.profile-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.user-section {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
}

.user-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background-color: var(--accent);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 600;
}

.user-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.user-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.user-email {
  font-size: 13px;
  color: var(--text-secondary);
}

.user-role {
  font-size: 12px;
  color: var(--text-muted);
}

.setting-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

/* Allow the segmented theme selector to wrap on narrow screens instead of
   overflowing the card */
.theme-item {
  flex-wrap: wrap;
}

.setting-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.setting-label {
  font-size: 14px;
  color: var(--text-primary);
}

.setting-value {
  font-size: 13px;
  color: var(--text-secondary);
}

.toggle-btn {
  padding: 4px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.toggle-btn:hover {
  background-color: var(--accent);
  color: white;
}

.toggle-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.theme-options {
  display: flex;
  gap: 4px;
  padding: 3px;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.theme-option {
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--text-secondary);
  background-color: transparent;
  white-space: nowrap;
  transition: all var(--transition-fast);
}

.theme-option:hover {
  color: var(--text-primary);
}

.theme-option.active {
  color: var(--accent);
  background-color: var(--bg-card);
  box-shadow: var(--shadow-sm);
}

.favorite-tags-item {
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
}

.favorite-tags-item .setting-header {
  flex: none;
}

.favorite-tags-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}

.favorite-tags-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.favorite-tags-domain {
  font-size: 12px;
  color: var(--text-secondary);
}

.favorite-tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.favorite-tag {
  padding: 4px 10px;
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--text-secondary);
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.favorite-tag:hover {
  color: var(--text-primary);
}

.favorite-tag.active {
  color: white;
  background-color: var(--accent);
  border-color: var(--accent);
}

.favorite-tags-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.favorite-tags-saved {
  font-size: 13px;
  color: var(--down-color, #10b981);
}

.favorite-tags-error {
  font-size: 13px;
  color: var(--error-color, #ef4444);
}
</style>
