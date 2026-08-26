<script setup lang="ts">
import { ref, computed } from "vue";
import axios from "axios";
import type { Category } from "@/types";
import {
  apiGet,
  apiPost,
  apiPut,
  apiDelete,
  getApiErrorMessage,
} from "@/utils/api";
import { categoriesApi } from "@/api/categories";
import { Plus, Pencil, Trash2, RefreshCw } from "lucide-vue-next";
import {
  CATEGORY_ICON_OPTIONS,
  resolveCategoryIcon,
  type CategoryIconOption,
} from "@/utils/categoryIcons";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorAlert from "@/components/common/ErrorAlert.vue";
import ConfirmationDialog from "@/components/common/ConfirmationDialog.vue";

const DEFAULT_ICON = "folder";
const DEFAULT_COLOR = "#3B82F6";
// Backend CategoryCreate/CategoryUpdate reject anything lower (ge=10)
const MIN_REFRESH_SECONDS = 10;

interface CategoryFormValues {
  name: string;
  description: string;
  slug: string;
  icon: string;
  color: string;
  // Raw strings so "left blank" stays representable (= server default on
  // create, keep the stored value on update)
  refreshInterval: string;
  keywords: string;
}

function blankForm(): CategoryFormValues {
  return {
    name: "",
    description: "",
    slug: "",
    icon: DEFAULT_ICON,
    color: DEFAULT_COLOR,
    refreshInterval: "",
    keywords: "",
  };
}

const categories = ref<Category[]>([]);
const loading = ref(false);

// The top "add" form and the inline row editor keep separate state so starting
// a row edit no longer pours its values into the add inputs (and vice versa)
const addForm = ref<CategoryFormValues>(blankForm());
const addError = ref("");
const editingId = ref<string | null>(null);
const editForm = ref<CategoryFormValues>(blankForm());
const editError = ref("");
const deleteError = ref("");
// Bumped on every new delete error so a dismissed ErrorAlert remounts and shows again
const deleteErrorKey = ref(0);

// Bulk re-tagging ("reclassify"): confirmation dialog → POST → result echo.
// Predefined (system) categories are exempt in the UI — their tagging rules are
// deployed system-wide — so the button only renders on custom rows.
const reclassifyTarget = ref<Category | null>(null);
const reclassifying = ref(false);
const reclassifyMessage = ref("");
const reclassifyError = ref("");
const reclassifyErrorKey = ref(0);

const customCategories = computed(() =>
  categories.value.filter((c) => c.type === "custom"),
);
const predefinedCategories = computed(() =>
  categories.value.filter((c) => c.type !== "custom"),
);

// Status-driven Chinese fallbacks (409 duplicate / 400 quota / 403 permission);
// the backend envelope message wins whenever getApiErrorMessage can extract one
function categoryFallback(err: unknown, action: string): string {
  const status = axios.isAxiosError(err) ? err.response?.status : undefined;
  switch (status) {
    case 409:
      return "分类名称已存在";
    case 403:
      return "仅管理员可管理分类";
    case 400:
      return `${action}失败：请检查输入或分类数量是否已达上限`;
    default:
      return `${action}失败，请重试`;
  }
}

// "AI, 机器人,," → ["AI", "机器人"] (each entry trimmed, blanks dropped)
function parseKeywords(raw: string): string[] {
  return raw
    .split(",")
    .map((keyword) => keyword.trim())
    .filter((keyword) => keyword !== "");
}

function intervalValidationError(raw: string): string {
  const trimmed = String(raw).trim();
  if (trimmed === "") return "";
  const seconds = Number(trimmed);
  if (!Number.isInteger(seconds) || seconds < MIN_REFRESH_SECONDS) {
    return `刷新频率需为不小于 ${MIN_REFRESH_SECONDS} 的整数秒`;
  }
  return "";
}

// Shared by create and update; blank slug/interval/keywords are omitted so the
// backend applies its defaults on create (slug slugified from name, 300s, no
// filter) and keeps the stored values on update
function buildCategoryPayload(
  form: CategoryFormValues,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name,
    description: form.description,
    icon: form.icon,
    color: form.color,
  };
  const slug = form.slug.trim();
  if (slug !== "") payload.slug = slug;
  const interval = String(form.refreshInterval).trim();
  if (interval !== "") payload.refresh_interval_seconds = Number(interval);
  const keywords = parseKeywords(form.keywords);
  if (keywords.length > 0) payload.keywords_filter = keywords;
  return payload;
}

// Curated icon list plus, when the currently stored icon is not among the
// options (e.g. "cpu"), an extra entry so editing never silently drops it
function iconOptionsFor(icon: string): CategoryIconOption[] {
  if (CATEGORY_ICON_OPTIONS.some((option) => option.name === icon)) {
    return CATEGORY_ICON_OPTIONS;
  }
  return [
    ...CATEGORY_ICON_OPTIONS,
    { name: icon, component: resolveCategoryIcon(icon) },
  ];
}

const addIconOptions = computed(() => iconOptionsFor(addForm.value.icon));
const editIconOptions = computed(() => iconOptionsFor(editForm.value.icon));

async function fetchCategories() {
  loading.value = true;
  try {
    const response = await apiGet<Category[]>("/categories");
    categories.value = response.data;
  } finally {
    loading.value = false;
  }
}

async function addCategory() {
  if (!addForm.value.name.trim()) return;
  const invalidInterval = intervalValidationError(addForm.value.refreshInterval);
  if (invalidInterval) {
    addError.value = invalidInterval;
    return;
  }
  addError.value = "";
  try {
    await apiPost<Category>("/categories", {
      ...buildCategoryPayload(addForm.value),
      type: "custom",
    });
    // Success: clear the inputs and refresh; failure keeps both via catch
    addForm.value = blankForm();
    await fetchCategories();
  } catch (err) {
    addError.value = getApiErrorMessage(err, categoryFallback(err, "添加"));
  }
}

async function updateCategory(id: string) {
  if (!editForm.value.name.trim()) return;
  const invalidInterval = intervalValidationError(
    editForm.value.refreshInterval,
  );
  if (invalidInterval) {
    editError.value = invalidInterval;
    return;
  }
  editError.value = "";
  try {
    await apiPut<Category>(`/categories/${id}`, {
      ...buildCategoryPayload(editForm.value),
    });
    editingId.value = null;
    editForm.value = blankForm();
    await fetchCategories();
  } catch (err) {
    // Stay in edit mode with the values and the error visible for retry
    editError.value = getApiErrorMessage(err, categoryFallback(err, "保存"));
  }
}

async function deleteCategory(id: string) {
  deleteError.value = "";
  try {
    await apiDelete(`/categories/${id}`);
    await fetchCategories();
  } catch (err) {
    deleteError.value = getApiErrorMessage(err, categoryFallback(err, "删除"));
    deleteErrorKey.value += 1;
  }
}

function startEdit(category: Category) {
  editingId.value = category.id;
  editForm.value = {
    name: category.name,
    description: category.description || "",
    slug: category.slug || "",
    icon: category.icon || DEFAULT_ICON,
    color: category.color || DEFAULT_COLOR,
    refreshInterval: category.refresh_interval_seconds
      ? String(category.refresh_interval_seconds)
      : "",
    // Responses carry no keywords_filter, so there is nothing to echo; leaving
    // the input blank keeps whatever keywords are stored server-side
    keywords: "",
  };
  editError.value = "";
}

function cancelEdit() {
  editingId.value = null;
  editForm.value = blankForm();
  editError.value = "";
}

function openReclassify(category: Category) {
  reclassifyTarget.value = category;
}

async function confirmReclassify() {
  const target = reclassifyTarget.value;
  reclassifyTarget.value = null;
  if (!target) return;
  reclassifying.value = true;
  reclassifyMessage.value = "";
  reclassifyError.value = "";
  try {
    const response = await categoriesApi.reclassify(target.id);
    reclassifyMessage.value = `已扫描 ${response.data.scanned} 条，更新 ${response.data.updated} 条`;
  } catch (err) {
    reclassifyError.value = getApiErrorMessage(
      err,
      categoryFallback(err, "重新分类"),
    );
    reclassifyErrorKey.value += 1;
  } finally {
    reclassifying.value = false;
  }
}

fetchCategories();
</script>

<template>
  <div class="category-editor">
    <div class="add-section">
      <div class="form-row">
        <input
          v-model="addForm.name"
          type="text"
          placeholder="新分类名称"
          class="input-name"
        />
        <input
          v-model="addForm.description"
          type="text"
          placeholder="描述(可选)"
          class="input-desc"
        />
      </div>
      <div class="form-row">
        <input
          v-model="addForm.slug"
          type="text"
          placeholder="slug(可选，留空自动生成)"
          class="input-slug"
        />
        <input
          v-model="addForm.refreshInterval"
          type="number"
          min="10"
          step="1"
          placeholder="刷新频率(秒，≥10，留空默认300)"
          title="建议范围 10–86400 秒；留空由后端使用默认值 300 秒"
          class="input-interval"
        />
        <label class="color-field">
          <span class="field-label">颜色</span>
          <input v-model="addForm.color" type="color" class="input-color" />
        </label>
      </div>
      <div class="form-row">
        <input
          v-model="addForm.keywords"
          type="text"
          placeholder="关键词过滤，逗号分隔(可选)"
          class="input-keywords"
        />
      </div>
      <div class="form-row">
        <div class="icon-picker" aria-label="分类图标">
          <button
            v-for="option in addIconOptions"
            :key="option.name"
            type="button"
            class="icon-option"
            :class="{ selected: addForm.icon === option.name }"
            :data-icon="option.name"
            :title="option.name"
            :aria-pressed="addForm.icon === option.name"
            @click="addForm.icon = option.name"
          >
            <component :is="option.component" :size="16" />
          </button>
        </div>
        <button
          class="add-btn"
          :disabled="!addForm.name.trim()"
          @click="addCategory"
        >
          <Plus :size="16" />
          添加
        </button>
      </div>
      <p v-if="addError" class="error-text">{{ addError }}</p>
    </div>

    <ErrorAlert
      v-if="deleteError"
      :key="deleteErrorKey"
      :message="deleteError"
    />

    <p v-if="reclassifyMessage" class="reclassify-message">
      {{ reclassifyMessage }}
    </p>
    <ErrorAlert
      v-if="reclassifyError"
      :key="`reclassify-${reclassifyErrorKey}`"
      :message="reclassifyError"
    />

    <div class="category-list">
      <h4 class="list-label">预定义分类</h4>
      <div
        v-for="cat in predefinedCategories"
        :key="cat.id"
        class="category-item predefined"
      >
        <span class="cat-name">{{ cat.name }}</span>
        <span class="cat-type">{{ cat.type }}</span>
        <span class="cat-slug">{{ cat.slug }}</span>
      </div>

      <h4 class="list-label">自定义分类</h4>
      <EmptyState
        v-if="customCategories.length === 0"
        title="暂无自定义分类"
        description="点击上方添加按钮创建"
        icon="folder"
      />
      <div
        v-for="cat in customCategories"
        :key="cat.id"
        class="category-item custom"
      >
        <div v-if="editingId === cat.id" class="edit-row">
          <div class="form-row">
            <input v-model="editForm.name" type="text" class="input-name" />
            <input
              v-model="editForm.description"
              type="text"
              placeholder="描述(可选)"
              class="input-desc"
            />
          </div>
          <div class="form-row">
            <input
              v-model="editForm.slug"
              type="text"
              placeholder="slug(留空保持不变)"
              class="input-slug"
            />
            <input
              v-model="editForm.refreshInterval"
              type="number"
              min="10"
              step="1"
              placeholder="刷新频率(秒，≥10，留空保持不变)"
              title="建议范围 10–86400 秒；留空保持当前设置"
              class="input-interval"
            />
            <label class="color-field">
              <span class="field-label">颜色</span>
              <input v-model="editForm.color" type="color" class="input-color" />
            </label>
          </div>
          <div class="form-row">
            <input
              v-model="editForm.keywords"
              type="text"
              placeholder="关键词，逗号分隔(留空保持现有设置)"
              class="input-keywords"
            />
          </div>
          <div class="form-row">
            <div class="icon-picker" aria-label="分类图标">
              <button
                v-for="option in editIconOptions"
                :key="option.name"
                type="button"
                class="icon-option"
                :class="{ selected: editForm.icon === option.name }"
                :data-icon="option.name"
                :title="option.name"
                :aria-pressed="editForm.icon === option.name"
                @click="editForm.icon = option.name"
              >
                <component :is="option.component" :size="16" />
              </button>
            </div>
            <button class="save-btn" @click="updateCategory(cat.id)">
              保存
            </button>
            <button class="cancel-btn" @click="cancelEdit">取消</button>
          </div>
          <p v-if="editError" class="error-text">{{ editError }}</p>
        </div>
        <div v-else class="display-row">
          <span class="cat-name">{{ cat.name }}</span>
          <span class="cat-desc">{{ cat.description }}</span>
          <button
            class="reclassify-btn"
            title="按最新的分类规则重建该分类下所有条目的标签"
            :disabled="reclassifying"
            @click="openReclassify(cat)"
          >
            <RefreshCw :size="14" :class="{ spinning: reclassifying }" />
            重新分类
          </button>
          <button class="edit-btn" @click="startEdit(cat)">
            <Pencil :size="14" />
          </button>
          <button class="delete-btn" @click="deleteCategory(cat.id)">
            <Trash2 :size="14" />
          </button>
        </div>
      </div>
    </div>

    <ConfirmationDialog
      :visible="reclassifyTarget !== null"
      title="重新分类"
      :message="
        reclassifyTarget
          ? `将按最新的分类规则重建「${reclassifyTarget.name}」分类下所有条目的标签，是否继续？`
          : ''
      "
      confirm-text="重新分类"
      @confirm="confirmReclassify"
      @cancel="reclassifyTarget = null"
    />
  </div>
</template>

<style scoped>
.category-editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.add-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  width: 100%;
}

/* min-width: 0 overrides the replaced-element min-content size that would
   otherwise keep the inputs wider than the row on narrow screens (360-414px);
   the flex-basis decides when the fields wrap onto their own rows instead of
   pushing the 添加 button half off-screen */
.input-name {
  flex: 1 1 140px;
  min-width: 0;
}

.input-desc {
  flex: 2 1 200px;
  min-width: 0;
}

.input-slug {
  flex: 1 1 160px;
  min-width: 0;
}

.input-interval {
  flex: 1 1 140px;
  min-width: 0;
}

.input-keywords {
  flex: 1 1 100%;
  min-width: 0;
}

.color-field {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.input-color {
  width: 40px;
  height: 32px;
  padding: 2px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-card);
  cursor: pointer;
}

.icon-picker {
  flex: 1 1 240px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.icon-option {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  color: var(--text-muted);
  background-color: transparent;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.icon-option:hover {
  color: var(--accent);
  background-color: rgba(59, 130, 246, 0.1);
}

.icon-option.selected {
  color: var(--accent);
  border-color: var(--accent);
  background-color: rgba(59, 130, 246, 0.1);
}

.add-btn {
  flex-shrink: 0;
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

.add-btn:hover:not(:disabled) {
  background-color: var(--accent);
  color: white;
}

.add-btn:disabled {
  opacity: 0.5;
}

.category-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.list-label {
  font-size: 13px;
  color: var(--text-muted);
  font-weight: 600;
  margin-top: 4px;
}

.category-item {
  padding: 8px 12px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.category-item.predefined {
  opacity: 0.6;
}

.display-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cat-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}

.cat-desc {
  color: var(--text-secondary);
  font-size: 13px;
  flex: 1;
}

.cat-type {
  font-size: 12px;
  color: var(--text-muted);
}

.cat-slug {
  font-size: 12px;
  color: var(--text-muted);
}

.edit-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* flex-basis 100% pushes the message onto its own line inside the wrapped rows */
.error-text {
  flex-basis: 100%;
  margin: 0;
  font-size: 13px;
  color: var(--danger);
}

.save-btn {
  flex-shrink: 0;
  padding: 4px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--success);
  border: 1px solid var(--success);
  background-color: transparent;
}

.cancel-btn {
  flex-shrink: 0;
  padding: 4px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--text-muted);
  border: 1px solid var(--border-color);
  background-color: transparent;
}

.edit-btn,
.delete-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: all var(--transition-fast);
}

.edit-btn:hover {
  color: var(--accent);
  background-color: rgba(59, 130, 246, 0.1);
}

.delete-btn:hover {
  color: var(--danger);
  background-color: rgba(239, 68, 68, 0.1);
}

.reclassify-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.reclassify-btn:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent);
}

.reclassify-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.reclassify-message {
  margin: 0;
  font-size: 13px;
  color: var(--success);
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
