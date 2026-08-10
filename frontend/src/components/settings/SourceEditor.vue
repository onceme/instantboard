<script setup lang="ts">
import { ref } from "vue";
import axios from "axios";
import type { Source, Category } from "@/types";
import { apiGet, apiPost, apiPut, getApiErrorMessage } from "@/utils/api";
import { Plus, ToggleLeft, ToggleRight } from "lucide-vue-next";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorAlert from "@/components/common/ErrorAlert.vue";

const NO_COLLECTOR_HINT = "无可用采集器，无法启用";

const sources = ref<Source[]>([]);
const categories = ref<Category[]>([]);
const loading = ref(false);
const showAddForm = ref(false);
const newSource = ref({
  name: "",
  category_id: "",
  source_type: "rss" as "rss" | "api" | "web_scrape" | "social",
  url: "",
  refresh_interval_seconds: 300,
});
const errorMessage = ref("");
// Bumped on every new error so the dismissed ErrorAlert remounts and shows again
const errorKey = ref(0);

function failWith(err: unknown, fallback: string) {
  errorMessage.value = getApiErrorMessage(err, fallback);
  errorKey.value += 1;
}

// 403 = the caller may not manage system sources; point at the admin role
function fallbackFor(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err) && err.response?.status === 403) {
    return "仅管理员可修改系统数据源";
  }
  return fallback;
}

// Only block flipping an inactive source on: enabling needs a collector
// (backend returns 400 NO_COLLECTOR_AVAILABLE otherwise), while disabling an
// active legacy source must stay possible. undefined = legacy data, untouched.
function enableBlocked(source: Source): boolean {
  return !source.is_active && source.collector_available === false;
}

async function fetchSources() {
  loading.value = true;
  try {
    const response = await apiGet<Source[]>("/sources");
    sources.value = response.data;
  } finally {
    loading.value = false;
  }
}

async function fetchCategories() {
  const response = await apiGet<Category[]>("/categories");
  categories.value = response.data;
}

async function addSource() {
  if (!newSource.value.name.trim() || !newSource.value.url.trim()) return;
  errorMessage.value = "";
  try {
    await apiPost<Source>("/sources", newSource.value);
    showAddForm.value = false;
    newSource.value = {
      name: "",
      category_id: "",
      source_type: "rss",
      url: "",
      refresh_interval_seconds: 300,
    };
    await fetchSources();
  } catch (err) {
    failWith(err, fallbackFor(err, "添加失败，请重试"));
  }
}

async function toggleSource(source: Source) {
  errorMessage.value = "";
  try {
    await apiPut<Source>(`/sources/${source.id}`, {
      is_active: !source.is_active,
    });
    await fetchSources();
  } catch (err) {
    failWith(err, fallbackFor(err, "操作失败，请重试"));
  }
}

function healthStatusClass(status: string): string {
  if (status === "healthy") return "status-healthy";
  if (status === "degraded") return "status-degraded";
  return "status-down";
}

function categoryName(categoryId: string): string {
  const cat = categories.value.find((c) => c.id === categoryId);
  return cat?.name || categoryId;
}

fetchSources();
fetchCategories();
</script>

<template>
  <div class="source-editor">
    <div class="add-section">
      <button class="add-btn" @click="showAddForm = !showAddForm">
        <Plus :size="16" />
        添加数据源
      </button>
    </div>

    <ErrorAlert
      v-if="errorMessage"
      :key="errorKey"
      :message="errorMessage"
    />

    <div v-if="showAddForm" class="add-form card">
      <input v-model="newSource.name" type="text" placeholder="数据源名称" />
      <select v-model="newSource.category_id">
        <option value="">选择分类</option>
        <option v-for="cat in categories" :key="cat.id" :value="cat.id">
          {{ cat.name }}
        </option>
      </select>
      <select v-model="newSource.source_type">
        <option value="rss">RSS</option>
        <option value="api">API</option>
        <option value="web_scrape">Web抓取</option>
        <option value="social">社交媒体</option>
      </select>
      <input v-model="newSource.url" type="text" placeholder="URL" />
      <button
        class="submit-btn"
        :disabled="!newSource.name.trim() || !newSource.url.trim()"
        @click="addSource"
      >
        确认添加
      </button>
    </div>

    <EmptyState
      v-if="sources.length === 0"
      title="暂无数据源"
      description="点击添加按钮创建数据源"
      icon="link"
    />

    <div v-else class="source-list">
      <div v-for="source in sources" :key="source.id" class="source-item">
        <div class="source-main">
          <span class="source-name">{{ source.name }}</span>
          <span class="source-type">{{ source.source_type }}</span>
          <span class="source-category">{{
            categoryName(source.category_id)
          }}</span>
          <span
            v-if="source.collector_available === false"
            class="collector-badge"
            :title="NO_COLLECTOR_HINT"
          >
            无可用采集器
          </span>
        </div>
        <div class="source-actions">
          <span
            :class="healthStatusClass(source.health_status)"
            class="health-badge"
          >
            {{ source.health_status }}
          </span>
          <button
            class="toggle-btn"
            :disabled="enableBlocked(source)"
            :title="enableBlocked(source) ? NO_COLLECTOR_HINT : ''"
            @click="toggleSource(source)"
          >
            <ToggleRight
              v-if="source.is_active"
              :size="18"
              :style="{ color: 'var(--success)' }"
            />
            <ToggleLeft
              v-else
              :size="18"
              :style="{ color: 'var(--text-muted)' }"
            />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.source-editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.add-section {
  display: flex;
  justify-content: flex-end;
}

.add-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.add-btn:hover {
  background-color: var(--accent);
  color: white;
}

.add-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
}

.submit-btn {
  padding: 8px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
  align-self: flex-end;
}

.submit-btn:hover:not(:disabled) {
  background-color: var(--accent);
  color: white;
}

.submit-btn:disabled {
  opacity: 0.5;
}

.source-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.source-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.source-main {
  display: flex;
  align-items: center;
  gap: 12px;
}

.source-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}

.source-type {
  font-size: 12px;
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background-color: var(--bg-secondary);
}

.source-category {
  font-size: 12px;
  color: var(--text-secondary);
}

.collector-badge {
  font-size: 12px;
  color: var(--warning);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background-color: rgba(245, 158, 11, 0.1);
}

.source-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.health-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

.status-healthy {
  color: var(--success);
  background-color: rgba(16, 185, 129, 0.1);
}

.status-degraded {
  color: var(--warning);
  background-color: rgba(245, 158, 11, 0.1);
}

.status-down {
  color: var(--danger);
  background-color: rgba(239, 68, 68, 0.1);
}

.toggle-btn {
  display: flex;
  align-items: center;
}

.toggle-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
