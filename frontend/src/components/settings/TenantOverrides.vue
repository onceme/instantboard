<script setup lang="ts">
import { ref } from "vue";
import axios from "axios";
import type { Category, TenantSettings } from "@/types";
import { categoriesApi } from "@/api/categories";
import { getTenantSettings, updateTenantSettings } from "@/api/tenant";
import { getApiErrorMessage } from "@/utils/api";
import ErrorAlert from "@/components/common/ErrorAlert.vue";

// Backend contract (backend/app/services/tenant.py): PUT /tenant/settings
// replaces both override maps wholesale, so a row whose input is left empty is
// simply omitted from the payload — which clears any existing override for it.
const REFRESH_MIN = 10;
const REFRESH_MAX = 86400;

const categories = ref<Category[]>([]);
// Inputs stay strings so "empty" (= no override) is representable
const refreshInputs = ref<Record<string, string>>({});
const colorInputs = ref<Record<string, string>>({});
const loading = ref(true);
const loadError = ref("");
const saving = ref(false);
const saveMessage = ref("");
const saveError = ref("");
// Bumped on every new save error so a dismissed ErrorAlert remounts and shows again
const saveErrorKey = ref(0);

// Backend 400 envelope: response.data.detail.error.details[] = {field, message}
function firstValidationDetail(err: unknown): string | undefined {
  if (!axios.isAxiosError(err)) return undefined;
  const data = err.response?.data as
    | {
        detail?: {
          error?: { details?: Array<{ field: string; message: string }> };
        };
      }
    | undefined;
  return data?.detail?.error?.details?.[0]?.message;
}

function fillInputs(settings: TenantSettings) {
  const refresh: Record<string, string> = {};
  const color: Record<string, string> = {};
  for (const cat of categories.value) {
    const interval = settings.refresh_overrides[cat.slug];
    refresh[cat.slug] = interval != null ? String(interval) : "";
    color[cat.slug] = settings.color_overrides[cat.slug] ?? "";
  }
  refreshInputs.value = refresh;
  colorInputs.value = color;
}

async function loadData() {
  loading.value = true;
  loadError.value = "";
  try {
    // page_size max is 100; system categories + max_categories (10) fit easily
    const [catRes, settingsRes] = await Promise.all([
      categoriesApi.list({ page_size: 100 }),
      getTenantSettings(),
    ]);
    categories.value = catRes.data;
    fillInputs(settingsRes.data);
  } catch (err) {
    loadError.value = getApiErrorMessage(err, "加载覆盖配置失败，请重试");
  } finally {
    loading.value = false;
  }
}

// Assemble the two maps from the current inputs; empty inputs are omitted so
// saving clears their overrides (wholesale-replace semantics on the backend)
async function save() {
  saveMessage.value = "";
  saveError.value = "";

  const payload: TenantSettings = {
    refresh_overrides: {},
    color_overrides: {},
  };
  for (const cat of categories.value) {
    // String(): v-model on type="number" inputs stores user-typed values as
    // numbers, only the prefilled ones are strings
    const rawRefresh = String(refreshInputs.value[cat.slug] ?? "").trim();
    if (rawRefresh !== "") {
      const seconds = Number(rawRefresh);
      if (!Number.isInteger(seconds)) {
        saveError.value = `「${cat.name}」的刷新频率必须是整数秒`;
        saveErrorKey.value += 1;
        return;
      }
      payload.refresh_overrides[cat.slug] = seconds;
    }
    const rawColor = String(colorInputs.value[cat.slug] ?? "").trim();
    if (rawColor !== "") {
      payload.color_overrides[cat.slug] = rawColor;
    }
  }

  saving.value = true;
  try {
    const response = await updateTenantSettings(payload);
    // Re-sync from the echoed result (normalizes color casing etc.)
    fillInputs(response.data);
    saveMessage.value = "已保存";
  } catch (err) {
    const status = axios.isAxiosError(err) ? err.response?.status : undefined;
    if (status === 403) {
      saveError.value = "无权限：仅租户管理员可修改覆盖配置";
    } else if (status === 400) {
      saveError.value =
        firstValidationDetail(err) ||
        getApiErrorMessage(err, "保存失败：请检查输入");
    } else {
      saveError.value = getApiErrorMessage(err, "保存失败，请重试");
    }
    saveErrorKey.value += 1;
  } finally {
    saving.value = false;
  }
}

loadData();
</script>

<template>
  <div class="tenant-overrides">
    <p class="panel-hint">
      为系统/自有分类覆盖刷新频率（{{ REFRESH_MIN }}-{{ REFRESH_MAX }}
      秒）与颜色；留空的条目保存后将清除已有覆盖，恢复默认值。
    </p>

    <ErrorAlert v-if="loadError" :message="loadError" />

    <template v-if="!loading && !loadError">
      <div class="override-list">
        <div class="override-row header">
          <span class="col-name">分类</span>
          <span class="col-default">默认值</span>
          <span class="col-input">刷新频率覆盖（秒）</span>
          <span class="col-input">颜色覆盖</span>
        </div>
        <div
          v-for="cat in categories"
          :key="cat.id"
          class="override-row"
          :data-slug="cat.slug"
        >
          <span class="col-name cat-name">{{ cat.name }}</span>
          <span class="col-default cat-default">
            {{ cat.refresh_interval_seconds }}s
            <span v-if="cat.color" class="default-color">
              <span
                class="color-swatch"
                :style="{ backgroundColor: cat.color }"
              />
              {{ cat.color }}
            </span>
          </span>
          <input
            v-model="refreshInputs[cat.slug]"
            type="number"
            class="col-input input-refresh"
            :min="REFRESH_MIN"
            :max="REFRESH_MAX"
            placeholder="留空=不覆盖"
          />
          <input
            v-model="colorInputs[cat.slug]"
            type="text"
            class="col-input input-color"
            placeholder="#RRGGBB，留空=不覆盖"
          />
        </div>
      </div>

      <div class="actions">
        <button class="save-btn" :disabled="saving" @click="save">
          {{ saving ? "保存中…" : "保存" }}
        </button>
        <span v-if="saveMessage" class="save-message">{{ saveMessage }}</span>
      </div>
      <ErrorAlert v-if="saveError" :key="saveErrorKey" :message="saveError" />
    </template>
  </div>
</template>

<style scoped>
.tenant-overrides {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}

.override-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.override-row {
  display: grid;
  grid-template-columns: minmax(90px, 1.2fr) minmax(110px, 1.3fr) 1fr 1fr;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.override-row.header {
  background-color: transparent;
  border: none;
  padding: 0 12px;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 600;
}

.cat-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}

.cat-default {
  font-size: 13px;
  color: var(--text-secondary);
}

.default-color {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.color-swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
}

.col-input {
  min-width: 0;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.save-btn {
  padding: 6px 16px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.save-btn:hover:not(:disabled) {
  background-color: var(--accent);
  color: white;
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.save-message {
  font-size: 13px;
  color: var(--success);
}

@media (max-width: 640px) {
  .override-row {
    grid-template-columns: 1fr 1fr;
  }

  .override-row.header .col-default,
  .override-row.header .col-name {
    display: none;
  }
}
</style>
